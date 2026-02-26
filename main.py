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

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('azlema')

# ── Config hardcoded ──────────────────────────────────────────────────────────
SYMBOL    = "ETH-USDT"
TIMEFRAME = "30m"
# Live: 300 candles = 1 request OKX = startup em ~5s
# IFM converge em ~50 bars, ZLEMA em ~20 bars → 300 é mais que suficiente
TOTAL_CANDLES    = 300
WARMUP_CANDLES   = 300
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

    def transfer_to_trading(self):
        """Transfere todo saldo USDT da conta Funding para Trading antes de operar."""
        try:
            # Buscar saldo na conta Funding (type=6)
            r = requests.get(self.BASE + "/api/v5/asset/balances",
                             headers=self._h("GET", "/api/v5/asset/balances"),
                             params={"ccy": "USDT"}, timeout=10).json()
            avail = 0.0
            for d in r.get("data", []):
                if d.get("ccy") == "USDT":
                    avail = float(d.get("availBal", 0) or 0)
            if avail > 0.01:
                body = {"ccy":"USDT","amt":str(avail),"from":"6","to":"18","type":"0"}
                t = self._post("/api/v5/asset/transfer", body)
                if t.get("code") == "0":
                    log.info(f"  ✅ Transferido {avail:.4f} USDT Funding → Trading")
                else:
                    log.warning(f"  ⚠️  Transfer: {t.get('msg')}")
            else:
                log.info(f"  ℹ️  Funding USDT={avail:.4f} (nada a transferir)")
        except Exception as e:
            log.warning(f"  ⚠️  transfer_to_trading: {e}")

    def balance(self, verbose=False):
        """Retorna saldo USDT disponível para trading (conta unificada OKX)."""
        try:
            data = self._get("/api/v5/account/balance", {"ccy": "USDT"})
            acct = data["data"][0]
            if verbose:
                for d in acct.get("details",[]):
                    if d["ccy"]=="USDT":
                        log.info(f"  📊 USDT: availBal={d.get('availBal')} availEq={d.get('availEq')} cashBal={d.get('cashBal')} frozenBal={d.get('frozenBal')} disEq={d.get('disEq')}")
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
        """Retorna ct_val cacheado (busca na API apenas uma vez no setup)."""
        return getattr(self, '_ct_val', 0.001)  # ETH-USDT-SWAP: 0.001 ETH/contrato

    def _fetch_ct_val(self):
        """Usa 0.001 ETH/contrato (valor correto para ETH-USDT-SWAP OKX).
        Loga o que a API retorna para referência, mas não usa — a API às vezes
        retorna ctVal em unidade diferente (ex: 0.1 em vez de 0.001)."""
        CT_FIXED = 0.001  # 1 contrato = 0.001 ETH (confirmado pelo usuário)
        try:
            r   = self._get("/api/v5/public/instruments",
                            {"instType": "SWAP", "instId": self.INST})
            api = float(r["data"][0]["ctVal"])
            log.info(f"  ℹ️  API ctVal={api} (ignorado) → usando fixo {CT_FIXED} ETH/contrato")
        except Exception as e:
            log.warning(f"  ⚠️  Não foi possível buscar ctVal da API: {e}")
        self._ct_val = CT_FIXED
        log.info(f"  ✅ ct_val={CT_FIXED} ETH/contrato | "
                 f"1 contrato ≈ {CT_FIXED * self.mark_price():.4f} USDT")
        return CT_FIXED

    def _cts(self, eth):
        """Converte ETH em número inteiro de contratos."""
        ct  = self.ct_val()
        cts = int(eth / ct)
        return max(1, cts)

    def _order(self, side, ps, sz):
        # ps='net' = conta em one-way mode: NÃO incluir posSide no body
        net_mode = (ps == 'net')
        # PERF: tenta o tdMode já conhecido primeiro (evita retry desnecessário ~200ms)
        td_primary   = getattr(self, '_td_mode', 'cross')
        td_secondary = 'isolated' if td_primary == 'cross' else 'cross'
        for td in [td_primary, td_secondary]:
            if net_mode:
                body = {"instId":self.INST,"tdMode":td,"side":side,"ordType":"market","sz":str(sz)}
            else:
                body = {"instId":self.INST,"tdMode":td,"side":side,"posSide":ps,"ordType":"market","sz":str(sz)}
            r  = self._post("/api/v5/trade/order", body)
            if r.get("code") == "0":
                sc = r.get("data",[{}])[0].get("sCode","")
                log.info(f"  ✅ ORDER {side} sz={sz} tdMode={td} sCode={sc}")
                self._td_mode = td
                return r
            d0 = r.get("data",[{}])[0] if r.get("data") else {}
            log.warning(f"  ⚠️  tdMode={td} sCode={d0.get('sCode')} sMsg={d0.get('sMsg')}")
        log.error(f"  ❌ ORDER {side} sz={sz} falhou em {td_primary} e {td_secondary}")
        return r

    def _fill(self, r):
        """Retorna avgPx sem bloquear — tenta 1x imediatamente, sem sleep."""
        try:
            oid = r["data"][0]["ordId"]
            return float(self._get("/api/v5/trade/order",{"instId":self.INST,"ordId":oid})["data"][0]["avgPx"])
        except: return None

    def _fill_async(self, r, callback, fallback_px: float):
        """
        Busca avgPx em background (não bloqueia o trade path).
        Chama callback(px) quando tiver o preço real; usa fallback_px imediatamente.
        """
        def _worker():
            for attempt in range(5):
                time.sleep(0.3 * (attempt + 1))   # 0.3s, 0.6s, 0.9s, 1.2s, 1.5s
                try:
                    oid = r["data"][0]["ordId"]
                    px  = float(self._get("/api/v5/trade/order",
                                         {"instId":self.INST,"ordId":oid})["data"][0]["avgPx"])
                    if px > 0:
                        callback(px)
                        return
                except: pass
            callback(fallback_px)   # fallback se não conseguir
        threading.Thread(target=_worker, daemon=True).start()

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
        # Detectar account level e modo atual
        try:
            cfg = self._get("/api/v5/account/config")
            d0  = cfg["data"][0]
            acct_lv = d0.get("acctLv", "2")
            pos_mode = d0.get("posMode", "net_mode")
            log.info(f"  ℹ️  acctLv={acct_lv} posMode={pos_mode}")
        except Exception as e:
            log.warning(f"  ⚠️  config: {e}")
            acct_lv = "2"; pos_mode = "net_mode"

        # tdMode: UTA (acctLv=3,4) usa cross; regular usa isolated
        self._td_mode = "cross" if acct_lv in ("3","4") else "isolated"
        log.info(f"  ℹ️  tdMode={self._td_mode} (acctLv={acct_lv})")

        # Tentar mudar para hedge mode
        r = self._post("/api/v5/account/set-position-mode", {"posMode":"long_short_mode"})
        if r.get("code") == "0":
            self._pos_mode = "long_short_mode"
            log.info("  ✅ Modo hedge ativado")
        else:
            self._pos_mode = pos_mode
            log.warning(f"  ⚠️  posMode: {r.get('msg')} → usando {pos_mode}")

        # Alavancagem 1x
        mgn = self._td_mode  # cross ou isolated
        if mgn == "isolated" and self._pos_mode == "long_short_mode":
            self._post("/api/v5/account/set-leverage",
                       {"instId":self.INST,"lever":"1","mgnMode":"isolated","posSide":"long"})
            rl = self._post("/api/v5/account/set-leverage",
                            {"instId":self.INST,"lever":"1","mgnMode":"isolated","posSide":"short"})
        else:
            rl = self._post("/api/v5/account/set-leverage",
                            {"instId":self.INST,"lever":"1","mgnMode":mgn})
        if rl.get("code") == "0":
            log.info("  ✅ Alavancagem 1x configurada")
        else:
            log.warning(f"  ⚠️  set-leverage: {rl.get('msg')}")

        # Tentar transferir Funding → Trading antes de checar saldo
        self.transfer_to_trading()
        # Busca ct_val real da API (cacheia para uso posterior)
        ct  = self._fetch_ct_val()
        bal = self.balance(verbose=True)  # loga campos completos 1x para diagnóstico
        px  = self.mark_price()
        min_usdt = ct * px  # margem mínima para 1 contrato em 1x
        log.info(f"  ✅ OKX conectada | Saldo: {bal:.4f} USDT | "
                 f"Mín. 1 contrato: {min_usdt:.2f} USDT")
        qty_eth = (bal * LiveTrader.PCT) / px if px > 0 else 0
        cts     = max(1, int(qty_eth / ct))
        log.info(f"  📐 {LiveTrader.PCT:.0%} saldo → {qty_eth:.4f} ETH → {cts} contratos "
                 f"({cts*ct:.4f} ETH = {cts*ct*px:.2f} USDT)")
        return bal > 0

# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════
class LiveTrader:
    # Reduzido de 0.95 → 0.80 para deixar margem suficiente
    # para fees + maintenance margin da OKX.
    PCT = 0.80

    def __init__(self):
        self.okx      = OKX()
        self.strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        self._running = False
        self._warming = False
        self.log: List[Dict] = []
        self._pnl_baseline = 0.0
        # Cache para /status e para o hot path de ordens
        self._cache_pos: Optional[Dict] = None
        self._cache_bal: float = 0.0
        self._cache_ct:  float = 0.001
        self._cache_qty: float = 0.0   # ETH pré-calculado — usado direto na ordem

    def _qty(self):
        """
        Retorna quantidade pré-calculada do cache.
        ZERO chamadas HTTP no hot path — tudo já foi resolvido no _refresh_cache().
        """
        qty = self._cache_qty
        if qty <= 0:
            log.warning("  ⚠️  _cache_qty=0 — recalculando na hora (fallback)")
            bal = self.okx.balance()
            px  = self.okx.mark_price()
            if bal <= 0 or px <= 0:
                return 0.0
            qty = (bal * self.PCT) / px
        log.info(f"  💰 qty cache={qty:.4f} ETH (bal={self._cache_bal:.2f} USDT)")
        return qty

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
        self._warming = True
        log.info(f"🔄 Warmup: {len(df)} candles (sem execução real)...")
        for _, row in df.iterrows():
            self.strategy.next({'open':float(row['open']),'high':float(row['high']),
                'low':float(row['low']),'close':float(row['close']),
                'timestamp':row.get('timestamp',0),'index':int(row.get('index',0))})
        # Salva baseline: PnL acumulado do histórico simulado
        # O PnL real (live) será sempre relativo a este momento
        self._pnl_baseline = self.strategy.net_profit
        self._warming = False
        self._refresh_cache()
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} EC={self.strategy.EC:.2f} "
                 f"| baseline_pnl={self._pnl_baseline:.2f} (histórico simulado, ignorado)")
        self._sync()
    
    @property
    def live_pnl(self):
        """PnL real acumulado APENAS das operações live (após o warmup)."""
        return self.strategy.net_profit - self._pnl_baseline

    def process(self, candle):
        ts       = candle.get('timestamp', datetime.utcnow())
        close_px = float(candle['close'])   # preço de fechamento do candle — usado como fill price
        log.info(f"\n── {ts} | O={candle['open']:.2f} H={candle['high']:.2f} L={candle['low']:.2f} C={close_px:.2f}")
        actions = self.strategy.next(candle)
        log.info(f"  P={self.strategy.Period} EC={self.strategy.EC:.2f} EMA={self.strategy.EMA:.2f} "
                 f"pos={self.strategy.position_size:+.4f} trail={'ON' if self.strategy._trail_active else 'off'} "
                 f"el={self.strategy._el} es={self.strategy._es}")

        # PERF: usa cache de posição — zero HTTP calls dentro do loop de ações.
        # `real` é atualizado localmente após cada ordem para refletir o estado atual
        # sem precisar buscar na exchange a cada iteração (~200ms economizados por ação).
        real = self._cache_pos

        for act in actions:
            kind = act.get('action','')

            if kind == 'EXIT_LONG' and real and real['side'] == 'long':
                log.info(f"  🔴 EXIT LONG ({act.get('exit_reason')})")
                qty = real['size'] * self.okx.ct_val()
                t0  = time.monotonic()
                r   = self.okx.close_long(qty)
                log.info(f"  ⚡ close_long latência={1000*(time.monotonic()-t0):.0f}ms")
                # PERF: usa close_px do candle — elimina mark_price() HTTP (~200ms).
                # _fill_async registra o avgPx real em background sem bloquear.
                self.strategy.confirm_exit('LONG', close_px, qty, ts, act.get('exit_reason',''))
                self._add_log("EXIT_LONG", close_px, qty, act.get('exit_reason',''))
                self.okx._fill_async(r, lambda px: log.info(f"  📋 EXIT_LONG fill real={px:.2f}"), close_px)
                real = None   # posição agora está flat — próximas ações veem isso

            elif kind == 'EXIT_SHORT' and real and real['side'] == 'short':
                log.info(f"  🔴 EXIT SHORT ({act.get('exit_reason')})")
                qty = real['size'] * self.okx.ct_val()
                t0  = time.monotonic()
                r   = self.okx.close_short(qty)
                log.info(f"  ⚡ close_short latência={1000*(time.monotonic()-t0):.0f}ms")
                self.strategy.confirm_exit('SHORT', close_px, qty, ts, act.get('exit_reason',''))
                self._add_log("EXIT_SHORT", close_px, qty, act.get('exit_reason',''))
                self.okx._fill_async(r, lambda px: log.info(f"  📋 EXIT_SHORT fill real={px:.2f}"), close_px)
                real = None   # posição agora está flat

            elif kind == 'BUY':
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️  BUY ignorado: qty=0"); continue
                if real and real['side'] == 'short':
                    # reversão: fecha short restante antes de abrir long
                    self.okx.close_short(real['size'] * self.okx.ct_val())
                    real = None
                log.info(f"  🟢 ENTER LONG {qty:.4f} ETH")
                t0      = time.monotonic()
                r, qty  = self.okx.open_long(qty)
                elapsed = 1000*(time.monotonic()-t0)
                log.info(f"  ⚡ open_long latência={elapsed:.0f}ms")
                if r.get("code") == "0":
                    # PERF: confirma fill com close_px — sem mark_price() HTTP
                    self.strategy.confirm_fill('BUY', close_px, qty, ts)
                    self._add_log("ENTER_LONG", close_px, qty)
                    self.okx._fill_async(r, lambda px: log.info(f"  📋 LONG fill real={px:.2f}"), close_px)
                    real = {'side': 'long', 'size': self.okx._cts(qty), 'avg_px': close_px}
                else:
                    log.error("  ❌ Ordem LONG rejeitada — estratégia NÃO atualizada")

            elif kind == 'SELL':
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️  SELL ignorado: qty=0"); continue
                if real and real['side'] == 'long':
                    # reversão: fecha long restante antes de abrir short
                    self.okx.close_long(real['size'] * self.okx.ct_val())
                    real = None
                log.info(f"  🟢 ENTER SHORT {qty:.4f} ETH")
                t0      = time.monotonic()
                r, qty  = self.okx.open_short(qty)
                elapsed = 1000*(time.monotonic()-t0)
                log.info(f"  ⚡ open_short latência={elapsed:.0f}ms")
                if r.get("code") == "0":
                    self.strategy.confirm_fill('SELL', close_px, qty, ts)
                    self._add_log("ENTER_SHORT", close_px, qty)
                    self.okx._fill_async(r, lambda px: log.info(f"  📋 SHORT fill real={px:.2f}"), close_px)
                    real = {'side': 'short', 'size': self.okx._cts(qty), 'avg_px': close_px}
                else:
                    log.error("  ❌ Ordem SHORT rejeitada — estratégia NÃO atualizada")

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
                if c:
                    self._refresh_cache()
                    self.process(c)
            except Exception as e:
                log.error(f"❌ {e}"); time.sleep(60)
        log.info("🔴 Trader encerrado")

    def _refresh_cache(self):
        """
        Atualiza cache em paralelo usando threads.
        Roda ANTES de cada candle → hot path de ordens usa cache, zero HTTP calls.
        """
        results = {}

        def _fetch_pos():
            try: results['pos'] = self.okx.position()
            except: results['pos'] = self._cache_pos

        def _fetch_bal_px():
            try:
                bal = self.okx.balance()
                px  = self.okx.mark_price()
                results['bal'] = bal
                results['px']  = px
            except:
                results['bal'] = self._cache_bal
                results['px']  = 0.0

        t1 = threading.Thread(target=_fetch_pos,    daemon=True)
        t2 = threading.Thread(target=_fetch_bal_px, daemon=True)
        t1.start(); t2.start()
        t1.join(timeout=5); t2.join(timeout=5)

        self._cache_pos = results.get('pos', self._cache_pos)
        bal = results.get('bal', self._cache_bal)
        px  = results.get('px',  0.0)

        if bal > 0:
            self._cache_bal = bal
        if px > 0 and bal > 0:
            self._cache_qty = (bal * self.PCT) / px
            log.info(f"  🔄 cache: bal={bal:.2f} px={px:.2f} qty={self._cache_qty:.4f} ETH "
                     f"({self.okx._cts(self._cache_qty)} cts)")
        self._cache_ct = 0.001  # fixo

    def stop(self): self._running = False

# ═══════════════════════════════════════════════════════════════════════════════
# FLASK + DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)
_trader: Optional[LiveTrader] = None
_lock  = threading.Lock()
_logs: List[str] = []

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
<div class="sub">ETH-USDT-SWAP &middot; Futures 1x &middot; OKX &middot; 80% do saldo</div>
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
    log.info("📥 Baixando 300 candles OKX...")
    try:
        df = DataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=TOTAL_CANDLES).fetch_ohlcv()
        log.info(f"  ✅ {len(df)} candles")
        if df.empty:
            log.error("❌ OKX sem dados"); return
        df = df.reset_index(drop=True)
        df['index'] = df.index
        _trader = LiveTrader()
        _trader.run(df)
    except Exception as e:
        log.error(f"❌ {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
    finally:
        with _lock:
            _trader = None
        log.info("🔄 Pronto para re-iniciar")

@app.route('/')
def index(): return DASH

@app.route('/status')
def status():
    t = _trader
    if t is None:
        return jsonify({"status":"stopped","tc":0,"trades":[],"log":_logs[-80:]})
    s = "running" if t._running else ("warming" if t._warming else "stopped")
    return jsonify({"status":s,
                    "pos":t._cache_pos,"bal":t._cache_bal,"ct":t._cache_ct,
                    "pnl":t.live_pnl,
                    "period":t.strategy.Period,"ec":t.strategy.EC,"ema":t.strategy.EMA,
                    "tc":len(t.log),"trades":t.log[-10:],"log":_logs[-80:]})

@app.route('/start', methods=['POST'])
def start():
    # FIX 2: _lock protege tanto o /start quanto o _delayed_start,
    # garantindo que apenas UM thread do trader roda por vez.
    with _lock:
        if _trader is not None:
            return jsonify({"message":"Já está rodando"})
        if not _creds_ok():
            return jsonify({"error":"Chaves OKX não encontradas"}), 400
        threading.Thread(target=_thread, daemon=True).start()
        return jsonify({"message":"ok"})

@app.route('/stop', methods=['POST'])
def stop():
    if _trader: _trader.stop()
    return jsonify({"message":"Parado"})

@app.route('/ping')
def ping(): return "pong"

@app.route('/health')
def health(): return jsonify({"ok":True,"creds":_creds_ok(),"trader":_trader is not None})

# Auto-start quando gunicorn importa o módulo.
# FIX 2: _delayed_start agora usa o mesmo _lock do /start,
# impedindo que dois traders rodem simultaneamente caso o usuário
# clique em "Iniciar" enquanto o auto-start ainda está em andamento.
def _delayed_start():
    time.sleep(5)
    if not _creds_ok():
        log.warning("⚠️  Chaves OKX não encontradas — use o botão Iniciar.")
        return
    with _lock:
        if _trader is not None:
            log.info("ℹ️  Trader já iniciado por outra thread — _delayed_start ignorado.")
            return
        log.info("🚀 Chaves OK — iniciando trader (auto-start)...")
        threading.Thread(target=_thread, daemon=True).start()

threading.Thread(target=_delayed_start, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",5000)), debug=False)
