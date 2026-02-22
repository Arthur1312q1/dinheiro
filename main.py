"""
AZLEMA Live Trading — OKX ETH-USDT-SWAP Futures 1x
Render: configurar APENAS OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE
"""
import os, hmac, hashlib, base64, json, time, threading, traceback, logging, requests
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Dict, List
from flask import Flask, jsonify

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import DataCollector
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('azlema')

# ── Config hardcoded ──────────────────────────────────────────────────────────
SYMBOL    = "ETH-USDT"
TIMEFRAME = "30m"
TOTAL_CANDLES    = 5500   # 1000 warmup + 4500 trading
WARMUP_CANDLES   = 1000
STRATEGY_CONFIG  = {
    "adaptive_method": "Cos IFM", "threshold": 0.0,
    "fixed_sl_points": 2000, "fixed_tp_points": 55, "trail_offset": 15,
    "risk_percent": 0.01, "tick_size": 0.01, "initial_capital": 1000.0,
    "max_lots": 100, "default_period": 20, "warmup_bars": WARMUP_CANDLES,
}

# ── Credenciais: lidas SEMPRE do ambiente, nunca em variável de módulo ────────
def _key():  return os.environ.get("OKX_API_KEY",    "").strip()
def _sec():  return os.environ.get("OKX_SECRET_KEY", "").strip()
def _pass(): return os.environ.get("OKX_PASSPHRASE", "").strip()
def _creds_ok(): return bool(_key() and _sec() and _pass())

# ═══════════════════════════════════════════════════════════════════════════════
# OKX CLIENT
# ═══════════════════════════════════════════════════════════════════════════════
class OKX:
    BASE = "https://www.okx.com"
    INST = "ETH-USDT-SWAP"

    def _sign(self, ts, method, path, body=""):
        msg = ts + method.upper() + path + body
        return base64.b64encode(
            hmac.new(_sec().encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()

    def _h(self, method, path, body=""):
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        return {"OK-ACCESS-KEY": _key(), "OK-ACCESS-SIGN": self._sign(ts, method, path, body),
                "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": _pass(),
                "Content-Type": "application/json"}

    def _get(self, path, params=None):
        qs = ("?" + "&".join(f"{k}={v}" for k,v in params.items())) if params else ""
        return requests.get(self.BASE+path+qs, headers=self._h("GET",path+qs), timeout=10).json()

    def _post(self, path, body):
        b = json.dumps(body)
        return requests.post(self.BASE+path, headers=self._h("POST",path,b), data=b, timeout=10).json()

    def balance(self):
        """Retorna saldo USDT disponível para trading (conta unificada OKX)."""
        try:
            data = self._get("/api/v5/account/balance", {"ccy": "USDT"})
            acct = data["data"][0]
            # Tenta availBal direto; se zero, usa availEq (saldo ajustado cross)
            for d in acct.get("details", []):
                if d["ccy"] == "USDT":
                    avail = float(d.get("availBal", 0) or 0)
                    if avail > 0:
                        return avail
                    # availEq inclui PnL não realizado em cross margin
                    eq = float(d.get("availEq", 0) or 0)
                    if eq > 0:
                        return eq
            # Fallback: equity total da conta em USDT
            total = float(acct.get("totalEq", 0) or 0)
            if total > 0:
                log.info(f"  ℹ️  Usando totalEq={total:.4f} USDT (conta unificada)")
                return total
        except Exception as e:
            log.error(f"  ❌ Erro ao buscar saldo: {e}")
        return 0.0

    def position(self):
        try:
            for p in self._get("/api/v5/account/positions",{"instType":"SWAP","instId":self.INST}).get("data",[]):
                sz = float(p.get("pos",0))
                if sz != 0: return {"side":p["posSide"],"size":abs(sz),"avg_px":float(p.get("avgPx",0))}
        except: pass
        return None

    def mark_price(self):
        try: return float(self._get("/api/v5/public/mark-price",{"instType":"SWAP","instId":self.INST})["data"][0]["markPx"])
        except: pass
        try: return float(self._get("/api/v5/market/ticker",{"instId":self.INST})["data"][0]["last"])
        except: return 0.0

    def ct_val(self):
        try: return float(self._get("/api/v5/public/instruments",{"instType":"SWAP","instId":self.INST})["data"][0]["ctVal"])
        except: return 0.01

    def _cts(self, eth): return max(1, int(eth / self.ct_val()))

    def _order(self, side, ps, sz):
        body = {"instId":self.INST,"tdMode":"cross","side":side,"posSide":ps,"ordType":"market","sz":str(sz)}
        r    = self._post("/api/v5/trade/order", body)
        ok   = r.get("code") == "0"
        if ok:
            sc = r.get("data",[{}])[0].get("sCode","")
            log.info(f"  ✅ ORDER {side}/{ps} sz={sz} sCode={sc}")
        else:
            # Log COMPLETO para diagnóstico
            d0 = r.get("data",[{}])[0] if r.get("data") else {}
            log.error(f"  ❌ ORDER {side}/{ps} sz={sz} FALHOU")
            log.error(f"     code={r.get('code')} msg={r.get('msg')}")
            log.error(f"     sCode={d0.get('sCode')} sMsg={d0.get('sMsg')}")
            log.error(f"     body={body}")
        return r

    def _fill(self, r):
        try:
            oid = r["data"][0]["ordId"]; time.sleep(1)
            return float(self._get("/api/v5/trade/order",{"instId":self.INST,"ordId":oid})["data"][0]["avgPx"])
        except: return None

    def _ps(self, side_long):
        """Retorna posSide correto conforme o modo de posição."""
        mode = getattr(self, '_pos_mode', 'long_short_mode')
        if mode == 'net_mode':
            return 'net'
        return 'long' if side_long else 'short'

    def open_long(self, qty):
        r = self._order("buy",  self._ps(True),  self._cts(qty)); return r, qty
    def open_short(self, qty):
        r = self._order("sell", self._ps(False), self._cts(qty)); return r, qty
    def close_long(self, qty):
        return self._order("sell", self._ps(True),  self._cts(qty))
    def close_short(self, qty):
        return self._order("buy",  self._ps(False), self._cts(qty))

    def get_pos_mode(self):
        """Retorna o modo de posição atual: 'long_short_mode' ou 'net_mode'."""
        try:
            r = self._get("/api/v5/account/config")
            return r["data"][0].get("posMode", "net_mode")
        except:
            return "net_mode"

    def setup(self):
        # Detectar modo atual antes de tentar mudar
        current_mode = self.get_pos_mode()
        log.info(f"  ℹ️  Position mode atual: {current_mode}")

        # Tentar mudar para hedge mode (long_short_mode)
        r = self._post("/api/v5/account/set-position-mode", {"posMode":"long_short_mode"})
        if r.get("code") == "0":
            self._pos_mode = "long_short_mode"
            log.info("  ✅ Modo hedge (long_short_mode) ativado")
        else:
            # Conta pode não suportar ou já ter posição aberta → usar modo atual
            self._pos_mode = current_mode
            log.warning(f"  ⚠️  Não mudou position mode: {r.get('msg')} → usando {current_mode}")

        # Alavancagem 1x
        rl = self._post("/api/v5/account/set-leverage",
                        {"instId":self.INST,"lever":"1","mgnMode":"cross"})
        if rl.get("code") == "0":
            log.info("  ✅ Alavancagem 1x configurada")
        else:
            log.warning(f"  ⚠️  set-leverage: {rl.get('msg')}")

        bal = self.balance()
        log.info(f"  ✅ OKX conectada | Saldo: {bal:.4f} USDT")
        if bal < 20:
            log.warning(f"  ⚠️  Saldo baixo ({bal:.2f} USDT) — mínimo ~20 USDT para 1 contrato ETH-USDT-SWAP 1x")
        return bal > 0

# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════
class LiveTrader:
    PCT = 0.95

    def __init__(self):
        self.okx      = OKX()
        self.strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        self._running = False
        self.log: List[Dict] = []
        self._pnl_baseline = 0.0  # PnL histórico do warmup (excluído do live PnL)

    def _qty(self):
        bal = self.okx.balance(); px = self.okx.mark_price()
        if bal <= 0 or px <= 0: return 0.0
        q = (bal * self.PCT) / px
        log.info(f"  💰 {bal:.2f} USDT × {self.PCT:.0%} / {px:.2f} = {q:.4f} ETH")
        return q

    def _sync(self):
        real = self.okx.position(); sp = self.strategy.position_size
        if real is None and abs(sp) > 0:
            log.warning("  ⚠️  Estratégia tem posição mas OKX flat → resetando")
            self.strategy._reset_pos()
        elif real is not None and sp == 0:
            qty = real["size"] * self.okx.ct_val()
            side = 'BUY' if real["side"] == "long" else 'SELL'
            log.warning(f"  ⚠️  OKX tem {side} {qty:.4f} ETH → sincronizando")
            self.strategy.confirm_fill(side, real["avg_px"], qty, datetime.utcnow())

    def _add_log(self, action, price, qty, reason=""):
        self.log.append({"time":datetime.utcnow().isoformat(),"action":action,
                         "price":price,"qty":qty,"reason":reason})

    def warmup(self, df):
        log.info(f"🔄 Warmup: {len(df)} candles (sem execução real)...")
        for _, row in df.iterrows():
            self.strategy.next({'open':float(row['open']),'high':float(row['high']),
                'low':float(row['low']),'close':float(row['close']),
                'timestamp':row.get('timestamp',0),'index':int(row.get('index',0))})
        # Salva baseline: PnL acumulado do histórico simulado
        # O PnL real (live) será sempre relativo a este momento
        self._pnl_baseline = self.strategy.net_profit
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} EC={self.strategy.EC:.2f} "
                 f"| baseline_pnl={self._pnl_baseline:.2f} (histórico simulado, ignorado)")
        self._sync()
    
    @property
    def live_pnl(self):
        """PnL real acumulado APENAS das operações live (após o warmup)."""
        return self.strategy.net_profit - self._pnl_baseline

    def process(self, candle):
        ts = candle.get('timestamp', datetime.utcnow())
        log.info(f"\n── {ts} | O={candle['open']:.2f} H={candle['high']:.2f} L={candle['low']:.2f} C={candle['close']:.2f}")
        actions = self.strategy.next(candle)
        log.info(f"  P={self.strategy.Period} EC={self.strategy.EC:.2f} EMA={self.strategy.EMA:.2f} "
                 f"pos={self.strategy.position_size:+.4f} trail={'ON' if self.strategy._trail_active else 'off'} "
                 f"el={self.strategy._el} es={self.strategy._es}")

        for act in actions:
            kind = act.get('action',''); real = self.okx.position()
            if kind == 'EXIT_LONG' and real and real['side'] == 'long':
                log.info(f"  🔴 EXIT LONG ({act.get('exit_reason')})")
                qty = real['size'] * self.okx.ct_val()
                r = self.okx.close_long(qty)
                px = self.okx._fill(r) or act['price']
                self.strategy.confirm_exit('LONG', px, qty, ts, act.get('exit_reason',''))
                self._add_log("EXIT_LONG", px, qty, act.get('exit_reason',''))
            elif kind == 'EXIT_SHORT' and real and real['side'] == 'short':
                log.info(f"  🔴 EXIT SHORT ({act.get('exit_reason')})")
                qty = real['size'] * self.okx.ct_val()
                r = self.okx.close_short(qty)
                px = self.okx._fill(r) or act['price']
                self.strategy.confirm_exit('SHORT', px, qty, ts, act.get('exit_reason',''))
                self._add_log("EXIT_SHORT", px, qty, act.get('exit_reason',''))

        for order in self.strategy.get_pending_orders():
            side = order['side']; qty = self._qty()
            if qty <= 0: continue
            if side == 'BUY':
                real = self.okx.position()
                if real and real['side'] == 'short':
                    self.okx.close_short(real['size'] * self.okx.ct_val())
                log.info(f"  🟢 ENTER LONG {qty:.4f} ETH")
                r, qty = self.okx.open_long(qty)
                px = self.okx._fill(r) or self.okx.mark_price()
                self.strategy.confirm_fill('BUY', px, qty, ts)
                self._add_log("ENTER_LONG", px, qty)
            elif side == 'SELL':
                real = self.okx.position()
                if real and real['side'] == 'long':
                    self.okx.close_long(real['size'] * self.okx.ct_val())
                log.info(f"  🟢 ENTER SHORT {qty:.4f} ETH")
                r, qty = self.okx.open_short(qty)
                px = self.okx._fill(r) or self.okx.mark_price()
                self.strategy.confirm_fill('SELL', px, qty, ts)
                self._add_log("ENTER_SHORT", px, qty)

    def _wait(self, tf=30):
        now  = datetime.utcnow()
        secs = (tf - now.minute % tf) * 60 - now.second + 3
        log.info(f"⏰ Aguardando {secs:.0f}s até próximo close...")
        time.sleep(max(3, secs))

    def _candle(self):
        TF = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","4h":"4H"}
        try:
            r = requests.get("https://www.okx.com/api/v5/market/candles",
                params={"instId":"ETH-USDT-SWAP","bar":TF.get(TIMEFRAME,"30m"),"limit":"2"},timeout=10).json()
            c = r["data"][1]
            return {'open':float(c[1]),'high':float(c[2]),'low':float(c[3]),'close':float(c[4]),
                    'timestamp':datetime.fromtimestamp(int(c[0])/1000,tz=timezone.utc),
                    'index':self.strategy._bar+1}
        except Exception as e: log.error(f"Erro candle: {e}"); return None

    def run(self, df):
        log.info("╔════════════════════════════════════╗")
        log.info("║  AZLEMA LIVE — ETH-USDT-SWAP 1x   ║")
        log.info("╚════════════════════════════════════╝")
        if not self.okx.setup():
            log.error("❌ Credenciais OKX inválidas"); return
        self.warmup(df)
        self._running = True
        tf = int(TIMEFRAME.replace('m','').replace('h','')) * (60 if 'h' in TIMEFRAME else 1)
        while self._running:
            try:
                self._wait(tf)
                c = self._candle()
                if c: self.process(c)
            except Exception as e:
                log.error(f"❌ {e}"); time.sleep(60)
        log.info("🔴 Trader encerrado")

    def stop(self): self._running = False

# ═══════════════════════════════════════════════════════════════════════════════
# FLASK + DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)
_trader: Optional[LiveTrader] = None
_lock   = threading.Lock()
_logs:  List[str] = []

class _LogCap(logging.Handler):
    def emit(self, r):
        _logs.append(self.format(r))
        if len(_logs) > 300: _logs.pop(0)

_lh = _LogCap(); _lh.setFormatter(logging.Formatter('%(asctime)s %(message)s','%H:%M:%S'))
log.addHandler(_lh)

DASH = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AZLEMA</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#060a12;color:#dde4f0;font-family:'Segoe UI',system-ui,sans-serif;padding:20px 24px;min-height:100vh}
h1{color:#60a5fa;font-size:1.4rem;letter-spacing:2px;margin-bottom:3px}
.sub{color:#334;font-size:.78rem;margin-bottom:20px}
.row{display:flex;gap:10px;margin-bottom:20px;align-items:center;flex-wrap:wrap}
.btn{padding:11px 28px;border:none;border-radius:8px;font-size:.92rem;font-weight:700;cursor:pointer;letter-spacing:.5px}
.gs{background:#22d3a0;color:#000}.gs:hover{background:#1bc492}.gs:disabled{opacity:.3;cursor:default}
.rs{background:#f87171;color:#000}.rs:hover{background:#e05555}.rs:disabled{opacity:.3;cursor:default}
#msg{font-size:.8rem;padding:5px 12px;border-radius:5px;display:none;margin-left:6px}
.ok{background:#082218;color:#22d3a0}.er{background:#220808;color:#f87171}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:9px;margin-bottom:20px}
.card{background:#0d1520;border:1px solid #162030;border-radius:8px;padding:14px}
.lbl{font-size:.65rem;color:#334;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px}
.val{font-size:1.25rem;font-weight:700}
.g{color:#22d3a0}.r{color:#f87171}.b{color:#60a5fa}.y{color:#fbbf24}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.dg{background:#22d3a0;box-shadow:0 0 6px #22d3a055}
.dr{background:#f87171}
.dy{background:#fbbf24;animation:pp 1s infinite}
@keyframes pp{0%,100%{opacity:1}50%{opacity:.2}}
.sec{color:#334;font-size:.65rem;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
table{width:100%;border-collapse:collapse;font-size:.78rem;margin-bottom:18px}
th{text-align:left;padding:6px 10px;color:#334;font-size:.63rem;text-transform:uppercase;border-bottom:1px solid #162030}
td{padding:7px 10px;border-bottom:1px solid #0d1520}
tr:hover td{background:#0d1520}
.tg{display:inline-block;padding:1px 6px;border-radius:3px;font-size:.65rem;font-weight:700}
.el{background:#082218;color:#22d3a0}.es{background:#220808;color:#f87171}.ex{background:#18140a;color:#fbbf24}
.lb{background:#04070d;border:1px solid #162030;border-radius:7px;padding:12px;max-height:240px;overflow-y:auto;font-family:monospace;font-size:.7rem;line-height:1.7}
</style>
</head>
<body>
<h1>⚡ AZLEMA Live</h1>
<div class="sub">ETH-USDT-SWAP &middot; Futures 1x &middot; OKX &middot; 95% do saldo</div>
<div class="row">
  <button class="btn gs" id="bs" onclick="ctrl('start')">&#9654; Iniciar</button>
  <button class="btn rs" id="bp" onclick="ctrl('stop')">&#9632; Parar</button>
  <span id="msg"></span>
</div>
<div class="grid">
  <div class="card"><div class="lbl">Status</div><div class="val" id="st">—</div></div>
  <div class="card"><div class="lbl">Posição</div><div class="val" id="pos">—</div></div>
  <div class="card"><div class="lbl">Saldo OKX</div><div class="val b" id="bal">—</div></div>
  <div class="card"><div class="lbl">PnL acum.</div><div class="val" id="pnl">—</div></div>
  <div class="card"><div class="lbl">Period</div><div class="val b" id="per">—</div></div>
  <div class="card"><div class="lbl">Trades</div><div class="val y" id="tc">—</div></div>
  <div class="card"><div class="lbl">EC</div><div class="val" id="ec">—</div></div>
  <div class="card"><div class="lbl">EMA</div><div class="val" id="ema">—</div></div>
</div>
<div class="sec">Últimas operações</div>
<table>
  <thead><tr><th>Hora</th><th>Ação</th><th>Preço</th><th>ETH</th><th>Razão</th></tr></thead>
  <tbody id="tb"><tr><td colspan="5" style="color:#334;text-align:center;padding:14px">Aguardando...</td></tr></tbody>
</table>
<div class="sec">Log</div>
<div class="lb" id="lb">aguardando...</div>
<script>
async function poll(){
  try{
    const d=await(await fetch('/status')).json();
    const run=d.status==='running',warm=d.status==='warming';
    document.getElementById('bs').disabled=run||warm;
    document.getElementById('bp').disabled=!(run||warm);
    const se=document.getElementById('st');
    if(run) se.innerHTML='<span class="dot dg"></span><span class="g">Rodando</span>';
    else if(warm) se.innerHTML='<span class="dot dy"></span><span class="y">Inicializando...</span>';
    else se.innerHTML='<span class="dot dr"></span><span style="color:#445">Parado</span>';
    const p=d.pos;const pe=document.getElementById('pos');
    if(p) pe.innerHTML=`<span class="${p.side==='long'?'g':'r'}">${p.side.toUpperCase()} ${(p.size*(d.ct||0.01)).toFixed(3)}</span>`;
    else pe.innerHTML='<span style="color:#334">Flat</span>';
    if(d.bal!=null) document.getElementById('bal').textContent=d.bal.toFixed(2)+' USDT';
    const pe2=document.getElementById('pnl');
    if(d.pnl!=null){pe2.textContent=(d.pnl>=0?'+':'')+d.pnl.toFixed(4)+' USDT';pe2.className='val '+(d.pnl>=0?'g':'r');}
    if(d.period!=null) document.getElementById('per').textContent=d.period;
    if(d.ec!=null) document.getElementById('ec').textContent=d.ec.toFixed(2);
    if(d.ema!=null) document.getElementById('ema').textContent=d.ema.toFixed(2);
    document.getElementById('tc').textContent=d.tc||0;
    const tb=document.getElementById('tb');
    const tr=[...(d.trades||[])].reverse();
    if(tr.length) tb.innerHTML=tr.map(t=>{
      const c=t.action.includes('ENTER')&&t.action.includes('LONG')?'el':t.action.includes('ENTER')&&t.action.includes('SHORT')?'es':'ex';
      return`<tr><td>${(t.time||'').split('T')[1]?.slice(0,8)||''}</td><td><span class="tg ${c}">${t.action}</span></td><td>${t.price?.toFixed(2)||'—'}</td><td>${t.qty?.toFixed(4)||'—'}</td><td style="color:#334">${t.reason||''}</td></tr>`;
    }).join('');
    const lb=document.getElementById('lb');
    if(d.log&&d.log.length){lb.innerHTML=(d.log||[]).slice(-80).map(l=>`<div style="color:${/❌|ERROR/.test(l)?'#f87171':/✅|🟢/.test(l)?'#22d3a0':/🔴|EXIT|TRAIL/.test(l)?'#fbbf24':'#334'}">${l}</div>`).join('');lb.scrollTop=lb.scrollHeight;}
  }catch(e){console.error(e);}
}
async function ctrl(a){
  const m=document.getElementById('msg');
  m.style.display='inline-block';m.className=a==='start'?'ok':'er';
  m.textContent=a==='start'?'Iniciando...':'Parando...';
  try{const d=await(await fetch('/'+a,{method:'POST'})).json();m.className=d.error?'er':'ok';m.textContent=d.message||d.error||'OK';}
  catch{m.className='er';m.textContent='Erro de rede';}
  setTimeout(()=>m.style.display='none',5000);
  setTimeout(poll,1500);
}
poll();setInterval(poll,4000);
</script>
</body></html>"""

def _thread():
    global _trader
    log.info("📥 Baixando histórico OKX...")
    try:
        df = DataCollector(symbol=SYMBOL,timeframe=TIMEFRAME,limit=TOTAL_CANDLES).fetch_ohlcv()
        if df.empty: log.error("❌ Sem dados históricos"); return
        df = df.reset_index(drop=True); df['index'] = df.index
        _trader = LiveTrader()
        _trader.run(df)
    except Exception as e:
        log.error(f"❌ {e}\n{traceback.format_exc()}")
        if _trader: _trader._running = False

def _auto():
    if not _creds_ok():
        log.warning("⚠️  Chaves OKX não encontradas no ambiente — use o botão Iniciar.")
        return
    log.info("🚀 Chaves OKX OK — iniciando trader automaticamente...")
    threading.Thread(target=_thread, daemon=True).start()

@app.route('/')
def index(): return DASH

@app.route('/status')
def status():
    t = _trader
    if t is None:
        return jsonify({"status":"stopped","tc":0,"trades":[],"log":_logs[-80:]})
    real=None; bal=None; ct=0.01
    try: real=t.okx.position(); bal=t.okx.balance(); ct=t.okx.ct_val()
    except: pass
    return jsonify({"status":"running" if t._running else "warming",
                    "pos":real,"bal":bal,"ct":ct,
                    "pnl":t.live_pnl,
                    "period":t.strategy.Period,"ec":t.strategy.EC,"ema":t.strategy.EMA,
                    "tc":len(t.log),"trades":t.log[-10:],"log":_logs[-80:]})

@app.route('/start', methods=['POST'])
def start():
    global _trader
    with _lock:
        if _trader and _trader._running:
            return jsonify({"message":"Já está rodando"})
        if not _creds_ok():
            return jsonify({"error":"Chaves OKX não encontradas no Render"}), 400
        threading.Thread(target=_thread, daemon=True).start()
        return jsonify({"message":"Iniciando — aguarde ~60s para o warmup concluir"})

@app.route('/stop', methods=['POST'])
def stop():
    if _trader: _trader.stop()
    return jsonify({"message":"Parado com sucesso"})

@app.route('/ping')
def ping(): return "pong"

@app.route('/health')
def health(): return jsonify({"ok":True,"creds":_creds_ok(),"running":bool(_trader and _trader._running)})

# Auto-start: executado quando gunicorn importa o módulo
_auto()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",5000)), debug=False)
