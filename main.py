"""
AZLEMA Live Trading — OKX ETH-USDT-SWAP Futures 1x
Render: configurar APENAS OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE

CORREÇÕES vs versão anterior:
  FIX-A: ctVal usa valor real da API (não hardcoded 0.001).
          API retorna 0.1 → sz calculado corretamente → sem sCode=51008.
  FIX-B: _wait() remove +3 e max(3). Usa +0 e max(1).
          Trade chega à exchange em ~1s da abertura do candle, não 4s.
  FIX-C: Após ordem rejeitada, reset completo do estado pendente da estratégia.
          Impede que es/el residual gere uma segunda ordem contraditória 30min depois.
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

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL    = "ETH-USDT-SWAP"
TIMEFRAME = "30m"
TOTAL_CANDLES  = 300
WARMUP_CANDLES = 300
STRATEGY_CONFIG = {
    "adaptive_method": "Cos IFM", "threshold": 0.0,
    "fixed_sl_points": 2000, "fixed_tp_points": 55, "trail_offset": 15,
    "risk_percent": 0.01, "tick_size": 0.01, "initial_capital": 1000.0,
    "max_lots": 100, "default_period": 20, "warmup_bars": WARMUP_CANDLES,
}

def _key():      return os.environ.get("OKX_API_KEY",    "").strip()
def _sec():      return os.environ.get("OKX_SECRET_KEY", "").strip()
def _pass():     return os.environ.get("OKX_PASSPHRASE", "").strip()
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
        return {
            "OK-ACCESS-KEY":       _key(),
            "OK-ACCESS-SIGN":      self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": _pass(),
            "Content-Type":        "application/json",
        }

    def _get(self, path, params=None):
        qs = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
        return requests.get(self.BASE + path + qs,
                            headers=self._h("GET", path + qs), timeout=10).json()

    def _post(self, path, body):
        b = json.dumps(body)
        return requests.post(self.BASE + path,
                             headers=self._h("POST", path, b), data=b, timeout=10).json()

    # ── Saldo ─────────────────────────────────────────────────────────────────
    def transfer_to_trading(self):
        try:
            r = requests.get(self.BASE + "/api/v5/asset/balances",
                             headers=self._h("GET", "/api/v5/asset/balances"),
                             params={"ccy": "USDT"}, timeout=10).json()
            avail = 0.0
            for d in r.get("data", []):
                if d.get("ccy") == "USDT":
                    avail = float(d.get("availBal", 0) or 0)
            if avail > 0.01:
                t = self._post("/api/v5/asset/transfer",
                               {"ccy": "USDT", "amt": str(avail),
                                "from": "6", "to": "18", "type": "0"})
                if t.get("code") == "0":
                    log.info(f"  ✅ Transferido {avail:.4f} USDT Funding → Trading")
                else:
                    log.warning(f"  ⚠️  Transfer: {t.get('msg')}")
            else:
                log.info(f"  ℹ️  Funding USDT={avail:.4f} (nada a transferir)")
        except Exception as e:
            log.warning(f"  ⚠️  transfer_to_trading: {e}")

    def balance(self, verbose=False):
        try:
            data = self._get("/api/v5/account/balance", {"ccy": "USDT"})
            acct = data["data"][0]
            if verbose:
                for d in acct.get("details", []):
                    if d["ccy"] == "USDT":
                        log.info(f"  📊 USDT: availBal={d.get('availBal')} "
                                 f"availEq={d.get('availEq')} cashBal={d.get('cashBal')} "
                                 f"frozenBal={d.get('frozenBal')} disEq={d.get('disEq')}")
            for d in acct.get("details", []):
                if d["ccy"] == "USDT":
                    avail = float(d.get("availBal", 0) or 0)
                    if avail > 0: return avail
                    eq = float(d.get("availEq", 0) or 0)
                    if eq > 0: return eq
            total = float(acct.get("totalEq", 0) or 0)
            if total > 0:
                log.info(f"  ℹ️  Usando totalEq={total:.4f} USDT")
                return total
        except Exception as e:
            log.error(f"  ❌ Erro ao buscar saldo: {e}")
        return 0.0

    # ── Posição / Preço ───────────────────────────────────────────────────────
    def position(self):
        try:
            for p in self._get("/api/v5/account/positions",
                               {"instType": "SWAP", "instId": self.INST}).get("data", []):
                sz = float(p.get("pos", 0))
                if sz != 0:
                    return {"side": p["posSide"], "size": abs(sz),
                            "avg_px": float(p.get("avgPx", 0))}
        except:
            pass
        return None

    def mark_price(self):
        try:
            return float(self._get("/api/v5/public/mark-price",
                                   {"instType": "SWAP", "instId": self.INST})
                         ["data"][0]["markPx"])
        except:
            pass
        try:
            return float(self._get("/api/v5/market/ticker",
                                   {"instId": self.INST})["data"][0]["last"])
        except:
            return 0.0

    # ── FIX-A: ctVal vem da API, não hardcoded ────────────────────────────────
    # Versão anterior ignorava o valor real (0.1 ETH/contrato) e usava 0.001,
    # fazendo o bot enviar sz=2 quando a OKX interpretava como 0.2 ETH = ~$384,
    # causando sCode=51008 com apenas $7 de saldo.
    def ct_val(self):
        return getattr(self, '_ct_val', 0.01)   # fallback seguro

    def _fetch_ct_val(self):
        try:
            r  = self._get("/api/v5/public/instruments",
                           {"instType": "SWAP", "instId": self.INST})
            ct = float(r["data"][0]["ctVal"])
            self._ct_val = ct
            px = self.mark_price()
            log.info(f"  ✅ ctVal API={ct} ETH/contrato | "
                     f"1 contrato ≈ {ct * px:.4f} USDT")
            return ct
        except Exception as e:
            log.warning(f"  ⚠️  _fetch_ct_val falhou: {e} → usando fallback 0.01")
            self._ct_val = 0.01
            return 0.01

    def _cts(self, eth_qty: float) -> int:
        """Converte quantidade em ETH para número de contratos (mínimo 1)."""
        ct  = self.ct_val()
        cts = int(eth_qty / ct)
        return max(1, cts)

    # ── Ordens ────────────────────────────────────────────────────────────────
    def _order(self, side, ps, sz):
        net_mode  = (ps == 'net')
        body = {
            "instId":  self.INST,
            "tdMode":  "cross",
            "side":    side,
            "ordType": "market",
            "sz":      str(sz),
        }
        if not net_mode:
            body["posSide"] = ps
        r  = self._post("/api/v5/trade/order", body)
        d0 = r.get("data", [{}])[0] if r.get("data") else {}
        if r.get("code") == "0":
            log.info(f"  ✅ ORDER {side} sz={sz} sCode={d0.get('sCode','')}")
        else:
            log.error(f"  ❌ ORDER {side} sz={sz} "
                      f"sCode={d0.get('sCode','')} sMsg={d0.get('sMsg','')}")
        return r

    def _fill_async(self, r, callback, fallback_px: float):
        def _worker():
            for attempt in range(5):
                time.sleep(0.3 * (attempt + 1))
                try:
                    oid = r["data"][0]["ordId"]
                    px  = float(self._get("/api/v5/trade/order",
                                         {"instId": self.INST, "ordId": oid})
                                ["data"][0]["avgPx"])
                    if px > 0:
                        callback(px)
                        return
                except:
                    pass
            callback(fallback_px)
        threading.Thread(target=_worker, daemon=True).start()

    def _ps(self, side_long: bool) -> str:
        mode = getattr(self, '_pos_mode', 'long_short_mode')
        if mode == 'net_mode':
            return 'net'
        return 'long' if side_long else 'short'

    def open_long(self, qty):
        r = self._order("buy",  self._ps(True),  self._cts(qty))
        return r, qty

    def open_short(self, qty):
        r = self._order("sell", self._ps(False), self._cts(qty))
        return r, qty

    def close_long(self, qty):
        return self._order("sell", self._ps(True),  self._cts(qty))

    def close_short(self, qty):
        return self._order("buy",  self._ps(False), self._cts(qty))

    # ── Setup inicial ─────────────────────────────────────────────────────────
    def setup(self):
        try:
            cfg      = self._get("/api/v5/account/config")
            d0       = cfg["data"][0]
            acct_lv  = d0.get("acctLv",  "2")
            pos_mode = d0.get("posMode", "net_mode")
            log.info(f"  ℹ️  acctLv={acct_lv} posMode={pos_mode}")
        except Exception as e:
            log.warning(f"  ⚠️  config: {e}")
            acct_lv = "2"; pos_mode = "net_mode"

        self._td_mode = "cross"
        log.info(f"  ℹ️  tdMode=cross (acctLv={acct_lv})")

        r = self._post("/api/v5/account/set-position-mode",
                       {"posMode": "long_short_mode"})
        if r.get("code") == "0":
            self._pos_mode = "long_short_mode"
            log.info("  ✅ Modo hedge ativado")
        else:
            self._pos_mode = pos_mode
            log.warning(f"  ⚠️  posMode: {r.get('msg')} → {pos_mode}")

        for ps in ("long", "short"):
            rl = self._post("/api/v5/account/set-leverage",
                            {"instId": self.INST, "lever": "1",
                             "mgnMode": "cross", "posSide": ps})
        if rl.get("code") == "0":
            log.info("  ✅ Alavancagem 1x cross")
        else:
            log.warning(f"  ⚠️  set-leverage: {rl.get('msg')}")

        self.transfer_to_trading()
        ct  = self._fetch_ct_val()       # FIX-A: usa valor real da API
        bal = self.balance(verbose=True)
        px  = self.mark_price()
        min_usdt = ct * px
        log.info(f"  ✅ OKX conectada | Saldo: {bal:.4f} USDT | "
                 f"1 contrato mín: {min_usdt:.2f} USDT")
        qty_eth = (bal * LiveTrader.PCT) / px if px > 0 else 0
        cts     = max(1, int(qty_eth / ct))
        log.info(f"  📐 {LiveTrader.PCT:.0%} saldo → {qty_eth:.6f} ETH → {cts} contratos "
                 f"({cts*ct:.6f} ETH = {cts*ct*px:.2f} USDT)")
        return bal > 0


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════
class LiveTrader:
    PCT = 0.80

    def __init__(self):
        self.okx      = OKX()
        self.strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        self._running = False
        self._warming = False
        self.log: List[Dict] = []
        self._pnl_baseline   = 0.0
        self._cache_pos: Optional[Dict] = None
        self._cache_bal: float = 0.0
        self._cache_ct:  float = 0.01
        self._cache_qty: float = 0.0
        # Deduplicação de candle (evita processar o mesmo candle duas vezes)
        self._last_candle_ts: str = ""

    # ── Quantidade ────────────────────────────────────────────────────────────
    def _qty(self) -> float:
        qty = self._cache_qty
        if qty <= 0:
            log.warning("  ⚠️  _cache_qty=0 — recalculando")
            bal = self.okx.balance()
            px  = self.okx.mark_price()
            if bal <= 0 or px <= 0:
                return 0.0
            qty = (bal * self.PCT) / px
        log.info(f"  💰 qty={qty:.6f} ETH | bal={self._cache_bal:.2f} USDT | "
                 f"cts={self.okx._cts(qty)}")
        return qty

    # ── Sincroniza estado da estratégia com a exchange ────────────────────────
    def _sync(self):
        real = self.okx.position()
        sp   = self.strategy.position_size
        if real is None and abs(sp) > 0:
            log.warning("  ⚠️  Estratégia LONG/SHORT mas OKX flat → resetando")
            self.strategy._reset_pos()
        elif real is not None and sp == 0:
            qty  = real["size"] * self.okx.ct_val()
            side = 'BUY' if real["side"] == "long" else 'SELL'
            log.warning(f"  ⚠️  OKX tem {side} {qty:.6f} ETH → sincronizando")
            self.strategy.confirm_fill(side, real["avg_px"], qty, datetime.utcnow())

    # ── FIX-C: reset completo do estado pendente após ordem rejeitada ─────────
    # Sem isso, es/el residual gera uma segunda ordem contraditória 30min depois.
    def _reset_strategy_pending(self, reason: str = ""):
        self.strategy._reset_pos()
        self.strategy._el    = False
        self.strategy._es    = False
        self.strategy._pBuy  = False
        self.strategy._pSell = False
        if reason:
            log.warning(f"  ⚠️  Estado pendente resetado: {reason}")

    def _add_log(self, action, price, qty, reason=""):
        self.log.append({
            "time":   datetime.utcnow().isoformat(),
            "action": action,
            "price":  price,
            "qty":    qty,
            "reason": reason,
        })

    # ── Warmup ────────────────────────────────────────────────────────────────
    def warmup(self, df: pd.DataFrame):
        self._warming = True
        log.info(f"🔄 Warmup: {len(df)} candles (sem execução real)...")
        for _, row in df.iterrows():
            self.strategy.next({
                'open':      float(row['open']),
                'high':      float(row['high']),
                'low':       float(row['low']),
                'close':     float(row['close']),
                'timestamp': row.get('timestamp', 0),
                'index':     int(row.get('index', 0)),
            })
        self._pnl_baseline   = self.strategy.net_profit
        self._warming        = False
        self._refresh_cache()
        # Grava ts do último candle do warmup → loop descarta candle duplicado
        self._last_candle_ts = str(df['timestamp'].iloc[-1])
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} "
                 f"EC={self.strategy.EC:.2f} | último ts={self._last_candle_ts}")
        self._sync()

    @property
    def live_pnl(self):
        return self.strategy.net_profit - self._pnl_baseline

    # ── Processa um candle fechado ─────────────────────────────────────────────
    def process(self, candle: Dict):
        ts       = candle.get('timestamp', datetime.utcnow())
        close_px = float(candle['close'])
        log.info(f"\n── {ts} | O={candle['open']:.2f} H={candle['high']:.2f} "
                 f"L={candle['low']:.2f} C={close_px:.2f}")

        # A ESTRATÉGIA decide tudo — main.py apenas executa
        actions = self.strategy.next(candle)

        log.info(f"  P={self.strategy.Period} EC={self.strategy.EC:.2f} "
                 f"EMA={self.strategy.EMA:.2f} pos={self.strategy.position_size:+.6f} "
                 f"trail={'ON' if self.strategy._trail_active else 'off'} "
                 f"el={self.strategy._el} es={self.strategy._es}")

        real = self._cache_pos

        for act in actions:
            kind = act.get('action', '')

            # ── EXIT LONG ─────────────────────────────────────────────────────
            if kind == 'EXIT_LONG' and real and real['side'] == 'long':
                log.info(f"  🔴 EXIT LONG ({act.get('exit_reason','')})")
                qty = real['size'] * self.okx.ct_val()
                t0  = time.monotonic()
                r   = self.okx.close_long(qty)
                log.info(f"  ⚡ close_long {1000*(time.monotonic()-t0):.0f}ms")
                self._add_log("EXIT_LONG", act.get('price', close_px),
                              qty, act.get('exit_reason', ''))
                self.okx._fill_async(r,
                    lambda px: log.info(f"  📋 EXIT_LONG fill={px:.2f}"), close_px)
                real = None

            # ── EXIT SHORT ────────────────────────────────────────────────────
            elif kind == 'EXIT_SHORT' and real and real['side'] == 'short':
                log.info(f"  🔴 EXIT SHORT ({act.get('exit_reason','')})")
                qty = real['size'] * self.okx.ct_val()
                t0  = time.monotonic()
                r   = self.okx.close_short(qty)
                log.info(f"  ⚡ close_short {1000*(time.monotonic()-t0):.0f}ms")
                self._add_log("EXIT_SHORT", act.get('price', close_px),
                              qty, act.get('exit_reason', ''))
                self.okx._fill_async(r,
                    lambda px: log.info(f"  📋 EXIT_SHORT fill={px:.2f}"), close_px)
                real = None

            # ── ENTER LONG ────────────────────────────────────────────────────
            elif kind == 'BUY':
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️  BUY ignorado: qty=0")
                    continue
                if real and real['side'] == 'short':
                    self.okx.close_short(real['size'] * self.okx.ct_val())
                    real = None
                log.info(f"  🟢 ENTER LONG {qty:.6f} ETH "
                         f"({self.okx._cts(qty)} cts)")
                t0      = time.monotonic()
                r, qty  = self.okx.open_long(qty)
                elapsed = 1000 * (time.monotonic() - t0)
                log.info(f"  ⚡ open_long {elapsed:.0f}ms")
                if r.get("code") == "0":
                    self._add_log("ENTER_LONG", act.get('price', close_px), qty)
                    self.okx._fill_async(r,
                        lambda px: log.info(f"  📋 LONG fill={px:.2f}"), close_px)
                    real = {'side': 'long',
                            'size': self.okx._cts(qty),
                            'avg_px': act.get('price', close_px)}
                else:
                    # FIX-C: ordem rejeitada → zera pendências para não gerar
                    # ordem oposta contraditória no próximo candle
                    self._reset_strategy_pending(
                        "BUY rejeitado — pendências zeradas para evitar SHORT sequencial")

            # ── ENTER SHORT ───────────────────────────────────────────────────
            elif kind == 'SELL':
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️  SELL ignorado: qty=0")
                    continue
                if real and real['side'] == 'long':
                    self.okx.close_long(real['size'] * self.okx.ct_val())
                    real = None
                log.info(f"  🟢 ENTER SHORT {qty:.6f} ETH "
                         f"({self.okx._cts(qty)} cts)")
                t0      = time.monotonic()
                r, qty  = self.okx.open_short(qty)
                elapsed = 1000 * (time.monotonic() - t0)
                log.info(f"  ⚡ open_short {elapsed:.0f}ms")
                if r.get("code") == "0":
                    self._add_log("ENTER_SHORT", act.get('price', close_px), qty)
                    self.okx._fill_async(r,
                        lambda px: log.info(f"  📋 SHORT fill={px:.2f}"), close_px)
                    real = {'side': 'short',
                            'size': self.okx._cts(qty),
                            'avg_px': act.get('price', close_px)}
                else:
                    # FIX-C: mesma lógica para SELL rejeitado
                    self._reset_strategy_pending(
                        "SELL rejeitado — pendências zeradas para evitar LONG sequencial")

    # ── FIX-B: timing sem delay excessivo ─────────────────────────────────────
    # Versão anterior: +3 e max(3) → ordem 4s depois da abertura do candle.
    # Nova versão: +0 e max(1) → ordem ~1s após abertura (candle já estável na API).
    def _wait(self, tf: int = 30):
        now  = datetime.utcnow()
        secs = (tf - now.minute % tf) * 60 - now.second
        if secs <= 0:
            secs += tf * 60
        log.info(f"⏰ Aguardando {secs:.0f}s até próximo close...")
        time.sleep(max(1, secs))

    def _candle(self) -> Optional[Dict]:
        TF = {"1m":"1m","5m":"5m","15m":"15m","30m":"30m","1h":"1H","4h":"4H"}
        try:
            r = requests.get(
                "https://www.okx.com/api/v5/market/candles",
                params={"instId": "ETH-USDT-SWAP",
                        "bar":    TF.get(TIMEFRAME, "30m"),
                        "limit":  "2"},
                timeout=10,
            ).json()
            c = r["data"][1]   # índice 1 = último candle FECHADO (índice 0 = em formação)
            return {
                'open':      float(c[1]),
                'high':      float(c[2]),
                'low':       float(c[3]),
                'close':     float(c[4]),
                'timestamp': datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc),
                'index':     self.strategy._bar + 1,
            }
        except Exception as e:
            log.error(f"Erro _candle: {e}")
            return None

    # ── Loop principal ─────────────────────────────────────────────────────────
    def run(self, df: pd.DataFrame):
        log.info("╔════════════════════════════════════╗")
        log.info("║  AZLEMA LIVE — ETH-USDT-SWAP 1x   ║")
        log.info("╚════════════════════════════════════╝")
        if not self.okx.setup():
            log.error("❌ Credenciais OKX inválidas"); return
        self.warmup(df)
        self._running = True
        tf = int(TIMEFRAME.replace('m','').replace('h','')) * \
             (60 if 'h' in TIMEFRAME else 1)
        while self._running:
            try:
                self._wait(tf)          # FIX-B: espera sem +3s desnecessário
                c = self._candle()
                if not c:
                    continue
                # Deduplicação de timestamp (evita reprocessar candle do warmup)
                ts = str(c['timestamp'])
                if ts == self._last_candle_ts:
                    continue
                self._last_candle_ts = ts
                self._refresh_cache()
                self.process(c)
            except Exception as e:
                log.error(f"❌ {e}")
                time.sleep(60)
        log.info("🔴 Trader encerrado")

    # ── Cache paralelo ─────────────────────────────────────────────────────────
    def _refresh_cache(self):
        results = {}

        def _fetch_pos():
            try:    results['pos'] = self.okx.position()
            except: results['pos'] = self._cache_pos

        def _fetch_bal_px():
            try:
                results['bal'] = self.okx.balance()
                results['px']  = self.okx.mark_price()
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
        if bal > 0: self._cache_bal = bal
        if px > 0 and bal > 0:
            self._cache_qty = (bal * self.PCT) / px
            log.info(f"  🔄 cache: bal={bal:.2f} px={px:.2f} "
                     f"qty={self._cache_qty:.6f} ETH "
                     f"({self.okx._cts(self._cache_qty)} cts)")
        self._cache_ct = self.okx.ct_val()

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK + DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
app    = Flask(__name__)
_trader: Optional[LiveTrader] = None
_lock  = threading.Lock()
_logs: List[str] = []

class _LogCap(logging.Handler):
    def emit(self, r):
        _logs.append(self.format(r))
        if len(_logs) > 300: _logs.pop(0)

_lh = _LogCap()
_lh.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S'))
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
.dg{background:#22d3a0;box-shadow:0 0 6px #22d3a055}.dr{background:#f87171}
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
  <div class="card"><div class="lbl">PnL Live</div><div class="val" id="pnl">—</div></div>
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
    if(p) pe.innerHTML=`<span class="${p.side==='long'?'g':'r'}">${p.side.toUpperCase()} ${(p.size*(d.ct||0.01)).toFixed(4)}</span>`;
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
      return`<tr><td>${(t.time||'').split('T')[1]?.slice(0,8)||''}</td><td><span class="tg ${c}">${t.action}</span></td><td>${t.price?.toFixed(2)||'—'}</td><td>${t.qty?.toFixed(6)||'—'}</td><td style="color:#334">${t.reason||''}</td></tr>`;
    }).join('');
    const lb=document.getElementById('lb');
    if(d.log&&d.log.length){lb.innerHTML=(d.log||[]).slice(-80).map(l=>`<div style="color:${/❌|ERROR/.test(l)?'#f87171':/✅|🟢/.test(l)?'#22d3a0':/🔴|EXIT|TRAIL/.test(l)?'#fbbf24':/#334|⚠️/.test(l)?'#f59e0b':'#334'}">${l}</div>`).join('');lb.scrollTop=lb.scrollHeight;}
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
    log.info("📥 Baixando candles OKX...")
    try:
        df = DataCollector(symbol=SYMBOL, timeframe=TIMEFRAME,
                           limit=TOTAL_CANDLES).fetch_ohlcv()
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
    return jsonify({
        "status": s,
        "pos":    t._cache_pos,
        "bal":    t._cache_bal,
        "ct":     t._cache_ct,
        "pnl":    t.live_pnl,
        "period": t.strategy.Period,
        "ec":     t.strategy.EC,
        "ema":    t.strategy.EMA,
        "tc":     len(t.log),
        "trades": t.log[-10:],
        "log":    _logs[-80:],
    })

@app.route('/start', methods=['POST'])
def start():
    with _lock:
        if _trader is not None:
            return jsonify({"message": "Já está rodando"})
        if not _creds_ok():
            return jsonify({"error": "Chaves OKX não encontradas"}), 400
        threading.Thread(target=_thread, daemon=True).start()
        return jsonify({"message": "ok"})

@app.route('/stop', methods=['POST'])
def stop():
    if _trader: _trader.stop()
    return jsonify({"message": "Parado"})

@app.route('/ping')
def ping(): return "pong"

@app.route('/health')
def health():
    return jsonify({"ok": True, "creds": _creds_ok(),
                    "trader": _trader is not None})


def _delayed_start():
    time.sleep(5)
    if not _creds_ok():
        log.warning("⚠️  Chaves OKX não encontradas — use o botão Iniciar.")
        return
    with _lock:
        if _trader is not None:
            log.info("ℹ️  Trader já ativo — _delayed_start ignorado.")
            return
        log.info("🚀 Chaves OK — auto-start...")
        threading.Thread(target=_thread, daemon=True).start()

threading.Thread(target=_delayed_start, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0',
            port=int(os.environ.get("PORT", 5000)),
            debug=False)
