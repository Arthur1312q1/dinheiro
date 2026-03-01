"""
AZLEMA Live Trading — OKX ETH-USDT-SWAP Futures 1x
Render: configurar OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE

PAPER TRADING: definir PAPER_TRADING=true nas variáveis de ambiente (ou no código abaixo)
para operar sem dinheiro real. O histórico de trades é salvo em trades_history.json.
"""
import os, hmac, hashlib, base64, json, time, threading, traceback, logging, requests
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Dict, List
from pathlib import Path
from flask import Flask, jsonify, request as flask_request

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

# ── Paper Trading Flag ─────────────────────────────────────────────────────────
# Defina PAPER_TRADING=true para modo simulação (sem dinheiro real)
PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() in ("true", "1", "yes")
PAPER_BALANCE = float(os.environ.get("PAPER_BALANCE", "1000.0"))  # saldo inicial paper
HISTORY_FILE  = "trades_history.json"
BACKTEST_HISTORY_FILE = "backtest_history.json"

def _key():      return os.environ.get("OKX_API_KEY",    "").strip()
def _sec():      return os.environ.get("OKX_SECRET_KEY", "").strip()
def _pass():     return os.environ.get("OKX_PASSPHRASE", "").strip()
def _creds_ok(): return bool(_key() and _sec() and _pass())

# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
class TradeHistoryManager:
    """Gerencia histórico persistente de trades em JSON."""

    def __init__(self, filepath: str = HISTORY_FILE):
        self.filepath = filepath
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> Dict:
        try:
            if Path(self.filepath).exists():
                with open(self.filepath, 'r') as f:
                    return json.load(f)
        except Exception as e:
            log.warning(f"⚠️ Erro ao carregar histórico: {e}")
        return {"trades": [], "sessions": []}

    def _save(self):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self._data, f, indent=2, default=str)
        except Exception as e:
            log.warning(f"⚠️ Erro ao salvar histórico: {e}")

    def add_trade(self, trade: Dict):
        with self._lock:
            self._data["trades"].append(trade)
            self._save()

    def update_last_trade(self, updates: Dict):
        with self._lock:
            if self._data["trades"]:
                self._data["trades"][-1].update(updates)
                self._save()

    def get_open_trade(self) -> Optional[Dict]:
        with self._lock:
            for t in reversed(self._data["trades"]):
                if t.get("status") == "open":
                    return t
        return None

    def close_trade(self, trade_id: str, exit_price: float, exit_time: str, exit_reason: str, pnl: float):
        with self._lock:
            for t in self._data["trades"]:
                if t.get("id") == trade_id:
                    entry = t.get("entry_price", exit_price)
                    pnl_pct = ((exit_price - entry) / entry * 100) if t.get("action") == "BUY" \
                              else ((entry - exit_price) / entry * 100)
                    t.update({
                        "status": "closed",
                        "exit_price": exit_price,
                        "exit_time": exit_time,
                        "exit_reason": exit_reason,
                        "pnl_usdt": pnl,
                        "pnl_pct": round(pnl_pct, 4),
                    })
                    self._save()
                    return

    def get_all_trades(self) -> List[Dict]:
        with self._lock:
            return list(self._data["trades"])

    def get_stats(self) -> Dict:
        with self._lock:
            closed = [t for t in self._data["trades"] if t.get("status") == "closed"]
        if not closed:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "total_pnl": 0, "avg_win": 0, "avg_loss": 0, "profit_factor": 0,
                    "best_trade": 0, "worst_trade": 0, "avg_pnl": 0, "expectancy": 0}

        wins   = [t["pnl_usdt"] for t in closed if t.get("pnl_usdt", 0) > 0]
        losses = [t["pnl_usdt"] for t in closed if t.get("pnl_usdt", 0) <= 0]
        total_pnl = sum(t.get("pnl_usdt", 0) for t in closed)
        gross_win  = sum(wins)
        gross_loss = abs(sum(losses))
        n = len(closed)

        return {
            "total":         n,
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins) / n * 100, 2) if n else 0,
            "total_pnl":     round(total_pnl, 4),
            "avg_win":       round(sum(wins) / len(wins), 4) if wins else 0,
            "avg_loss":      round(sum(losses) / len(losses), 4) if losses else 0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
            "best_trade":    round(max(wins), 4) if wins else 0,
            "worst_trade":   round(min(losses), 4) if losses else 0,
            "avg_pnl":       round(total_pnl / n, 4) if n else 0,
            "expectancy":    round((len(wins)/n * (sum(wins)/len(wins) if wins else 0) +
                                    len(losses)/n * (sum(losses)/len(losses) if losses else 0)), 4) if n else 0,
        }

    def clear(self):
        with self._lock:
            self._data = {"trades": [], "sessions": []}
            self._save()


history_mgr = TradeHistoryManager(HISTORY_FILE)
backtest_mgr = TradeHistoryManager(BACKTEST_HISTORY_FILE)


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADER (simulação sem exchange real)
# ═══════════════════════════════════════════════════════════════════════════════
class PaperTrader:
    """Simula ordens sem enviar para a exchange."""

    def __init__(self, initial_balance: float = PAPER_BALANCE):
        self.balance   = initial_balance
        self.position  = None   # {'side': 'long'|'short', 'size': float, 'avg_px': float}
        self._trade_id = 0
        log.info(f"📄 PAPER TRADING ativo | Saldo inicial: {initial_balance:.2f} USDT")

    def _new_id(self) -> str:
        self._trade_id += 1
        return f"PAPER-{self._trade_id:05d}"

    def open_long(self, qty, bal_usdt=0, px=0):
        trade_id = self._new_id()
        ts = datetime.utcnow().isoformat()
        history_mgr.add_trade({
            "id": trade_id, "action": "BUY", "status": "open",
            "entry_time": ts, "entry_price": px,
            "qty": qty, "balance": self.balance,
            "mode": "paper",
        })
        self.position = {"side": "long", "size": qty, "avg_px": px, "id": trade_id}
        log.info(f"  📄 PAPER LONG aberto | px={px:.2f} qty={qty:.4f} id={trade_id}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def open_short(self, qty, bal_usdt=0, px=0):
        trade_id = self._new_id()
        ts = datetime.utcnow().isoformat()
        history_mgr.add_trade({
            "id": trade_id, "action": "SELL", "status": "open",
            "entry_time": ts, "entry_price": px,
            "qty": qty, "balance": self.balance,
            "mode": "paper",
        })
        self.position = {"side": "short", "size": qty, "avg_px": px, "id": trade_id}
        log.info(f"  📄 PAPER SHORT aberto | px={px:.2f} qty={qty:.4f} id={trade_id}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def close_long(self, qty, exit_px=0, reason="EXIT"):
        if not self.position or self.position["side"] != "long":
            return {"code": "0"}
        entry_px = self.position["avg_px"]
        pnl      = (exit_px - entry_px) * qty
        self.balance += pnl
        ts = datetime.utcnow().isoformat()
        history_mgr.close_trade(self.position["id"], exit_px, ts, reason, pnl)
        log.info(f"  📄 PAPER LONG fechado | px={exit_px:.2f} pnl={pnl:+.4f} USDT")
        self.position = None
        return {"code": "0"}

    def close_short(self, qty, exit_px=0, reason="EXIT"):
        if not self.position or self.position["side"] != "short":
            return {"code": "0"}
        entry_px = self.position["avg_px"]
        pnl      = (entry_px - exit_px) * qty
        self.balance += pnl
        ts = datetime.utcnow().isoformat()
        history_mgr.close_trade(self.position["id"], exit_px, ts, reason, pnl)
        log.info(f"  📄 PAPER SHORT fechado | px={exit_px:.2f} pnl={pnl:+.4f} USDT")
        self.position = None
        return {"code": "0"}

    def get_position(self):
        return self.position

    def get_balance(self):
        return self.balance


# ═══════════════════════════════════════════════════════════════════════════════
# OKX CLIENT (real trading)
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
            "OK-ACCESS-KEY":        _key(),
            "OK-ACCESS-SIGN":       self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": _pass(),
            "Content-Type":         "application/json",
        }

    def _get(self, path, params=None):
        qs = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
        return requests.get(self.BASE + path + qs,
                            headers=self._h("GET", path + qs), timeout=10).json()

    def _post(self, path, body):
        b = json.dumps(body)
        return requests.post(self.BASE + path,
                             headers=self._h("POST", path, b), data=b, timeout=10).json()

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
                    log.warning(f"  ⚠️ Transfer: {t.get('msg')}")
        except Exception as e:
            log.warning(f"  ⚠️ transfer_to_trading: {e}")

    def balance(self, verbose=False):
        try:
            data = self._get("/api/v5/account/balance", {"ccy": "USDT"})
            acct = data["data"][0]
            for d in acct.get("details", []):
                if d["ccy"] == "USDT":
                    avail = float(d.get("availBal", 0) or 0)
                    if avail > 0: return avail
                    eq = float(d.get("availEq", 0) or 0)
                    if eq > 0: return eq
            return float(acct.get("totalEq", 0) or 0)
        except Exception as e:
            log.error(f"  ❌ Erro ao buscar saldo: {e}")
        return 0.0

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

    CT_VAL_FIXED = 0.001

    def ct_val(self):
        return self.CT_VAL_FIXED

    def _fetch_ct_val(self):
        ct = self.CT_VAL_FIXED
        px = self.mark_price()
        log.info(f"  ✅ ctVal FIXO={ct} ETH/contrato | 1 contrato ≈ {ct * px:.4f} USDT")
        return ct

    def _cts(self, eth_qty: float, bal_usdt: float = 0.0, px: float = 0.0) -> int:
        ct  = self.ct_val()
        cts = max(1, int(eth_qty / ct))
        if bal_usdt > 0 and px > 0:
            custo_por_ct = ct * px
            max_cts = max(1, int((bal_usdt * 0.90) / custo_por_ct))
            cts = min(cts, max_cts)
        return cts

    def _order(self, side, ps, sz):
        net_mode = (ps == 'net')
        body = {"instId": self.INST, "tdMode": "cross", "side": side,
                "ordType": "market", "sz": str(sz)}
        if not net_mode:
            body["posSide"] = ps
        r  = self._post("/api/v5/trade/order", body)
        d0 = r.get("data", [{}])[0] if r.get("data") else {}
        if r.get("code") == "0":
            log.info(f"  ✅ ORDER {side} sz={sz} sCode={d0.get('sCode','')}")
        else:
            log.error(f"  ❌ ORDER {side} sz={sz} sCode={d0.get('sCode','')} sMsg={d0.get('sMsg','')}")
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

    def open_long(self, qty, bal_usdt=0.0, px=0.0):
        sz = self._cts(qty, bal_usdt, px)
        r  = self._order("buy", self._ps(True), sz)
        if r.get("code") == "0":
            ts = datetime.utcnow().isoformat()
            trade_id = r.get("data", [{}])[0].get("ordId", "?")
            history_mgr.add_trade({
                "id": str(trade_id), "action": "BUY", "status": "open",
                "entry_time": ts, "entry_price": px,
                "qty": qty, "balance": bal_usdt, "mode": "live",
            })
        return r, qty

    def open_short(self, qty, bal_usdt=0.0, px=0.0):
        sz = self._cts(qty, bal_usdt, px)
        r  = self._order("sell", self._ps(False), sz)
        if r.get("code") == "0":
            ts = datetime.utcnow().isoformat()
            trade_id = r.get("data", [{}])[0].get("ordId", "?")
            history_mgr.add_trade({
                "id": str(trade_id), "action": "SELL", "status": "open",
                "entry_time": ts, "entry_price": px,
                "qty": qty, "balance": bal_usdt, "mode": "live",
            })
        return r, qty

    def close_long(self, qty, exit_px=0, reason="EXIT"):
        r = self._order("sell", self._ps(True), self._cts(qty))
        if r.get("code") == "0":
            ts = datetime.utcnow().isoformat()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "BUY" and t.get("status") == "open":
                    entry = t.get("entry_price", exit_px)
                    pnl   = (exit_px - entry) * qty if exit_px else 0
                    history_mgr.close_trade(t["id"], exit_px, ts, reason, pnl)
                    break
        return r

    def close_short(self, qty, exit_px=0, reason="EXIT"):
        r = self._order("buy", self._ps(False), self._cts(qty))
        if r.get("code") == "0":
            ts = datetime.utcnow().isoformat()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "SELL" and t.get("status") == "open":
                    entry = t.get("entry_price", exit_px)
                    pnl   = (entry - exit_px) * qty if exit_px else 0
                    history_mgr.close_trade(t["id"], exit_px, ts, reason, pnl)
                    break
        return r

    def setup(self):
        try:
            cfg      = self._get("/api/v5/account/config")
            d0       = cfg["data"][0]
            acct_lv  = d0.get("acctLv", "2")
            pos_mode = d0.get("posMode", "net_mode")
            log.info(f"  ℹ️  acctLv={acct_lv} posMode={pos_mode}")
        except Exception as e:
            log.warning(f"  ⚠️ config: {e}")
            acct_lv = "2"; pos_mode = "net_mode"

        self._td_mode = "cross"
        r = self._post("/api/v5/account/set-position-mode", {"posMode": "long_short_mode"})
        if r.get("code") == "0":
            self._pos_mode = "long_short_mode"
            log.info("  ✅ Modo hedge ativado")
        else:
            self._pos_mode = pos_mode
            log.warning(f"  ⚠️ posMode: {r.get('msg')}")

        for ps in ("long", "short"):
            rl = self._post("/api/v5/account/set-leverage",
                            {"instId": self.INST, "lever": "1",
                             "mgnMode": "cross", "posSide": ps})
        if rl.get("code") == "0":
            log.info("  ✅ Alavancagem 1x cross")
        else:
            log.warning(f"  ⚠️ set-leverage: {rl.get('msg')}")

        self.transfer_to_trading()
        ct  = self._fetch_ct_val()
        bal = self.balance(verbose=True)
        px  = self.mark_price()
        log.info(f"  ✅ OKX conectada | Saldo: {bal:.4f} USDT")
        return bal > 0


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════
class LiveTrader:
    PCT = 0.80

    def __init__(self):
        if PAPER_TRADING:
            self.paper = PaperTrader(PAPER_BALANCE)
            self.okx   = None
        else:
            self.paper = None
            self.okx   = OKX()

        self.strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        self._running = False
        self._warming = False
        self.log: List[Dict] = []
        self._pnl_baseline   = 0.0
        self._cache_pos: Optional[Dict] = None
        self._cache_bal: float = PAPER_BALANCE if PAPER_TRADING else 0.0
        self._cache_px:  float = 0.0
        self._cache_ct:  float = 0.001
        self._cache_qty: float = 0.0
        self._last_candle_ts:    str = ""
        self._last_order_candle: str = ""

    def _is_paper(self):
        return PAPER_TRADING

    def _mark_price(self) -> float:
        """Obtém preço atual (paper ou real)."""
        try:
            r = requests.get(
                "https://www.okx.com/api/v5/public/mark-price",
                params={"instType": "SWAP", "instId": "ETH-USDT-SWAP"},
                timeout=10
            ).json()
            return float(r["data"][0]["markPx"])
        except:
            return self._cache_px

    def _qty(self) -> float:
        bal = self._cache_bal
        px  = self._cache_px
        ct  = 0.001
        if bal <= 0 or px <= 0:
            bal = PAPER_BALANCE if self._is_paper() else 0
            px  = self._mark_price()
            if bal <= 0 or px <= 0:
                return 0.0
        usdt_disp = bal * self.PCT * 0.90
        cts = max(1, int(usdt_disp / (ct * px)))
        qty = cts * ct
        log.info(f"  💰 qty={qty:.6f} ETH ({cts} cts) | bal={bal:.2f} USDT | px={px:.2f}")
        return qty

    def _sync(self):
        if self._is_paper():
            return
        real = self.okx.position()
        sp   = self.strategy.position_size
        if real is None and abs(sp) > 0:
            log.warning("  ⚠️ Estratégia LONG/SHORT mas OKX flat → resetando")
            self.strategy._reset_pos()
        elif real is not None and sp == 0:
            qty  = real["size"] * self.okx.ct_val()
            side = 'BUY' if real["side"] == "long" else 'SELL'
            self.strategy.confirm_fill(side, real["avg_px"], qty, datetime.utcnow())

    def _reset_strategy_pending(self, reason: str = ""):
        self.strategy._reset_pos()
        self.strategy._el    = False
        self.strategy._es    = False
        self.strategy._pBuy  = False
        self.strategy._pSell = False
        if reason:
            log.warning(f"  ⚠️ Estado pendente resetado: {reason}")

    def _add_log(self, action, price, qty, reason=""):
        self.log.append({
            "time":   datetime.utcnow().isoformat(),
            "action": action,
            "price":  price,
            "qty":    qty,
            "reason": reason,
        })

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
        self._last_candle_ts = str(df['timestamp'].iloc[-1])
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} "
                 f"EC={self.strategy.EC:.2f} | último ts={self._last_candle_ts}")
        self._sync()

    @property
    def live_pnl(self):
        return self.strategy.net_profit - self._pnl_baseline

    def process(self, candle: Dict):
        ts       = candle.get('timestamp', datetime.utcnow())
        close_px = float(candle['close'])
        log.info(f"\n── {ts} | O={candle['open']:.2f} H={candle['high']:.2f} "
                 f"L={candle['low']:.2f} C={close_px:.2f}")

        actions = self.strategy.next(candle)

        log.info(f"  P={self.strategy.Period} EC={self.strategy.EC:.2f} "
                 f"EMA={self.strategy.EMA:.2f} pos={self.strategy.position_size:+.6f} "
                 f"trail={'ON' if self.strategy._trail_active else 'off'} "
                 f"el={self.strategy._el} es={self.strategy._es}")

        real = self._cache_pos

        for act in actions:
            kind = act.get('action', '')

            # ── EXIT LONG ─────────────────────────────────────────────────────
            if kind == 'EXIT_LONG':
                reason = act.get('exit_reason', 'EXIT')
                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'long':
                        qty = pos['size']
                        log.info(f"  🔴 [PAPER] EXIT LONG ({reason})")
                        self.paper.close_long(qty, close_px, reason)
                        self._add_log("EXIT_LONG", close_px, qty, reason)
                        real = None
                elif real and real['side'] == 'long':
                    log.info(f"  🔴 EXIT LONG ({reason})")
                    qty = real['size'] * self.okx.ct_val()
                    self.okx.close_long(qty, close_px, reason)
                    self._add_log("EXIT_LONG", act.get('price', close_px), qty, reason)
                    real = None

            # ── EXIT SHORT ────────────────────────────────────────────────────
            elif kind == 'EXIT_SHORT':
                reason = act.get('exit_reason', 'EXIT')
                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        qty = pos['size']
                        log.info(f"  🔴 [PAPER] EXIT SHORT ({reason})")
                        self.paper.close_short(qty, close_px, reason)
                        self._add_log("EXIT_SHORT", close_px, qty, reason)
                        real = None
                elif real and real['side'] == 'short':
                    log.info(f"  🔴 EXIT SHORT ({reason})")
                    qty = real['size'] * self.okx.ct_val()
                    self.okx.close_short(qty, close_px, reason)
                    self._add_log("EXIT_SHORT", act.get('price', close_px), qty, reason)
                    real = None

            # ── ENTER LONG ────────────────────────────────────────────────────
            elif kind == 'BUY':
                if self._last_order_candle == str(ts):
                    log.warning("  ⚠️ BUY bloqueado: ordem já enviada neste candle")
                    continue
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️ BUY ignorado: qty=0")
                    continue

                if self._is_paper():
                    # Fecha short se houver
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        self.paper.close_short(pos['size'], close_px, "REVERSAL")
                    px = self._cache_px if self._cache_px > 0 else close_px
                    log.info(f"  🟢 [PAPER] ENTER LONG {qty:.6f} ETH @ {px:.2f}")
                    r, qty = self.paper.open_long(qty, self._cache_bal, px)
                    if r.get("code") == "0":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_LONG", px, qty)
                        real = {'side': 'long', 'size': qty, 'avg_px': px}
                else:
                    if real and real['side'] == 'short':
                        self.okx.close_short(real['size'] * self.okx.ct_val(), close_px, "REVERSAL")
                        real = None
                    bal = self._cache_bal
                    px  = self._cache_px if self._cache_px > 0 else close_px
                    cts = self.okx._cts(qty, bal, px)
                    log.info(f"  🟢 ENTER LONG {qty:.6f} ETH ({cts} cts)")
                    r, qty = self.okx.open_long(qty, bal, px)
                    if r.get("code") == "0":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_LONG", act.get('price', close_px), qty)
                        real = {'side': 'long', 'size': self.okx._cts(qty), 'avg_px': act.get('price', close_px)}
                    else:
                        self._reset_strategy_pending("BUY rejeitado")

            # ── ENTER SHORT ───────────────────────────────────────────────────
            elif kind == 'SELL':
                if self._last_order_candle == str(ts):
                    log.warning("  ⚠️ SELL bloqueado: ordem já enviada neste candle")
                    continue
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️ SELL ignorado: qty=0")
                    continue

                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'long':
                        self.paper.close_long(pos['size'], close_px, "REVERSAL")
                    px = self._cache_px if self._cache_px > 0 else close_px
                    log.info(f"  🟢 [PAPER] ENTER SHORT {qty:.6f} ETH @ {px:.2f}")
                    r, qty = self.paper.open_short(qty, self._cache_bal, px)
                    if r.get("code") == "0":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_SHORT", px, qty)
                        real = {'side': 'short', 'size': qty, 'avg_px': px}
                else:
                    if real and real['side'] == 'long':
                        self.okx.close_long(real['size'] * self.okx.ct_val(), close_px, "REVERSAL")
                        real = None
                    bal = self._cache_bal
                    px  = self._cache_px if self._cache_px > 0 else close_px
                    cts = self.okx._cts(qty, bal, px)
                    log.info(f"  🟢 ENTER SHORT {qty:.6f} ETH ({cts} cts)")
                    r, qty = self.okx.open_short(qty, bal, px)
                    if r.get("code") == "0":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_SHORT", act.get('price', close_px), qty)
                        real = {'side': 'short', 'size': self.okx._cts(qty), 'avg_px': act.get('price', close_px)}
                    else:
                        self._reset_strategy_pending("SELL rejeitado")

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
            c = r["data"][1]
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

    def run(self, df: pd.DataFrame):
        mode_str = "📄 PAPER TRADING" if self._is_paper() else "💰 LIVE TRADING"
        log.info("╔════════════════════════════════════╗")
        log.info(f"║  AZLEMA {mode_str}   ║")
        log.info("║  ETH-USDT-SWAP 1x   OKX          ║")
        log.info("╚════════════════════════════════════╝")

        if not self._is_paper():
            if not self.okx.setup():
                log.error("❌ Credenciais OKX inválidas"); return

        self.warmup(df)
        self._running = True
        tf = int(TIMEFRAME.replace('m','').replace('h','')) * \
             (60 if 'h' in TIMEFRAME else 1)

        while self._running:
            try:
                self._wait(tf)
                time.sleep(2)
                c = self._candle()
                if not c:
                    continue
                ts = str(c['timestamp'])
                if ts == self._last_candle_ts:
                    log.info(f"  ⏭️ Candle duplicado ignorado: {ts}")
                    continue
                self._last_candle_ts = ts
                self._refresh_cache()
                self.process(c)
            except Exception as e:
                log.error(f"❌ {e}")
                time.sleep(60)
        log.info("🔴 Trader encerrado")

    def _refresh_cache(self):
        results = {}

        if self._is_paper():
            results['bal'] = self.paper.get_balance()
            results['px']  = self._mark_price()
            results['pos'] = self.paper.get_position()
        else:
            def _fp():
                try:    results['pos'] = self.okx.position()
                except: results['pos'] = self._cache_pos

            def _fbp():
                try:
                    results['bal'] = self.okx.balance()
                    results['px']  = self.okx.mark_price()
                except:
                    results['bal'] = self._cache_bal
                    results['px']  = 0.0

            t1 = threading.Thread(target=_fp,  daemon=True)
            t2 = threading.Thread(target=_fbp, daemon=True)
            t1.start(); t2.start()
            t1.join(timeout=5); t2.join(timeout=5)

        self._cache_pos = results.get('pos', self._cache_pos)
        bal = results.get('bal', self._cache_bal)
        px  = results.get('px',  0.0)
        if bal > 0: self._cache_bal = bal
        if px  > 0: self._cache_px  = px
        ct = 0.001
        if px > 0 and bal > 0:
            cts = max(1, int((bal * self.PCT * 0.90) / (ct * px)))
            self._cache_qty = cts * ct
        self._cache_ct = ct

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST RUNNER
# ═══════════════════════════════════════════════════════════════════════════════
def run_backtest(symbol=SYMBOL, timeframe=TIMEFRAME, limit=500, initial_capital=1000.0) -> Dict:
    """Executa backtest e salva resultado no histórico."""
    log.info(f"🔬 Iniciando backtest: {symbol} {timeframe} {limit} candles...")
    try:
        df = DataCollector(symbol=symbol, timeframe=timeframe, limit=limit).fetch_ohlcv()
        if df.empty:
            return {"error": "Sem dados"}

        from backtest.engine import BacktestEngine
        cfg = dict(STRATEGY_CONFIG)
        cfg["initial_capital"] = initial_capital
        cfg["warmup_bars"] = min(50, limit // 5)
        strategy = AdaptiveZeroLagEMA(**cfg)
        engine   = BacktestEngine(strategy, df)
        results  = engine.run()

        # Salva no histórico de backtests
        record = {
            "id":         datetime.utcnow().isoformat(),
            "symbol":     symbol,
            "timeframe":  timeframe,
            "candles":    limit,
            "capital":    initial_capital,
            "total_pnl":  round(results.get("total_pnl_usdt", 0), 4),
            "final_bal":  round(results.get("final_balance", 0), 4),
            "win_rate":   round(results.get("win_rate", 0), 2),
            "total_trades": results.get("total_trades", 0),
            "max_drawdown": round(results.get("max_drawdown", 0), 4),
            "sharpe":       round(results.get("sharpe", 0), 4),
            "trades":       results.get("closed_trades", []),
        }

        # Calcula profit factor
        closed = results.get("closed_trades", [])
        gw = sum(t["pnl_usdt"] for t in closed if t.get("pnl_usdt", 0) > 0)
        gl = abs(sum(t["pnl_usdt"] for t in closed if t.get("pnl_usdt", 0) < 0))
        record["profit_factor"] = round(gw / gl, 3) if gl > 0 else float("inf")

        data = backtest_mgr._load()
        data.setdefault("trades", [])
        data.setdefault("sessions", [])
        data["sessions"].append(record)
        backtest_mgr._data = data
        backtest_mgr._save()

        log.info(f"  ✅ Backtest OK | PnL={record['total_pnl']:.2f} WR={record['win_rate']:.1f}%")
        return record
    except Exception as e:
        log.error(f"❌ Backtest error: {e}\n{traceback.format_exc()}")
        return {"error": str(e)}


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
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AZLEMA Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg:     #080c14;
  --bg2:    #0d1422;
  --bg3:    #111927;
  --border: #1a2535;
  --accent: #00d4ff;
  --green:  #00e57a;
  --red:    #ff4d6d;
  --yellow: #ffd94a;
  --text:   #c8d8ec;
  --muted:  #4a6080;
  --paper:  #9b6bff;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:'IBM Plex Sans',sans-serif; min-height:100vh; }
.mono { font-family:'IBM Plex Mono',monospace; }

/* Layout */
.shell { display:flex; flex-direction:column; min-height:100vh; }
.topbar { background:var(--bg2); border-bottom:1px solid var(--border); padding:14px 28px;
          display:flex; align-items:center; gap:20px; position:sticky; top:0; z-index:100; }
.logo { font-family:'IBM Plex Mono',monospace; font-size:1.1rem; font-weight:600;
        color:var(--accent); letter-spacing:2px; }
.badge-mode { padding:3px 10px; border-radius:3px; font-size:.68rem; font-weight:600;
              letter-spacing:1px; text-transform:uppercase; }
.badge-paper { background:rgba(155,107,255,.15); color:var(--paper); border:1px solid var(--paper); }
.badge-live  { background:rgba(0,229,122,.15);  color:var(--green); border:1px solid var(--green); }
.tabs { margin-left:auto; display:flex; gap:2px; background:var(--bg3); border-radius:6px; padding:3px; }
.tab { padding:7px 18px; border-radius:4px; font-size:.78rem; font-weight:500; cursor:pointer;
       color:var(--muted); border:none; background:transparent; transition:.2s; }
.tab.active { background:var(--bg2); color:var(--text); border:1px solid var(--border); }
.tab:hover:not(.active) { color:var(--text); }

.main { flex:1; padding:24px 28px; }
.panel { display:none; }
.panel.active { display:block; }

/* Controls */
.controls { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:24px; }
.btn { padding:10px 22px; border:none; border-radius:5px; font-size:.82rem; font-weight:600;
       cursor:pointer; letter-spacing:.5px; transition:.15s; font-family:'IBM Plex Sans',sans-serif; }
.btn-go   { background:var(--green); color:#000; }
.btn-go:hover   { filter:brightness(1.1); }
.btn-go:disabled { opacity:.3; cursor:default; }
.btn-stop { background:var(--red); color:#fff; }
.btn-stop:hover { filter:brightness(1.1); }
.btn-stop:disabled { opacity:.3; cursor:default; }
.btn-sec  { background:var(--bg3); color:var(--text); border:1px solid var(--border); }
.btn-sec:hover { border-color:var(--accent); color:var(--accent); }
.btn-accent { background:var(--accent); color:#000; }
.btn-accent:hover { filter:brightness(1.1); }
#sysmsg { font-size:.78rem; padding:5px 14px; border-radius:4px; display:none; }
.msg-ok { background:rgba(0,229,122,.12); color:var(--green); border:1px solid var(--green); }
.msg-er { background:rgba(255,77,109,.12); color:var(--red);   border:1px solid var(--red); }

/* Stat grid */
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:24px; }
.kpi { background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px 18px;
       position:relative; overflow:hidden; }
.kpi::before { content:''; position:absolute; top:0; left:0; width:3px; height:100%; background:var(--accent); }
.kpi.g::before { background:var(--green); }
.kpi.r::before { background:var(--red); }
.kpi.y::before { background:var(--yellow); }
.kpi.p::before { background:var(--paper); }
.kpi-lbl { font-size:.62rem; color:var(--muted); text-transform:uppercase; letter-spacing:1.2px; margin-bottom:6px; }
.kpi-val { font-family:'IBM Plex Mono',monospace; font-size:1.4rem; font-weight:600; color:var(--text); }
.kpi-val.g { color:var(--green); }
.kpi-val.r { color:var(--red); }
.kpi-val.y { color:var(--yellow); }
.kpi-val.p { color:var(--paper); }

/* Status indicator */
.status-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px; }
.dot-run  { background:var(--green); box-shadow:0 0 8px var(--green); animation:pulse 2s infinite; }
.dot-warm { background:var(--yellow); animation:pulse .8s infinite; }
.dot-stop { background:var(--muted); }
@keyframes pulse { 0%,100%{opacity:1}50%{opacity:.4} }

/* Table */
.card { background:var(--bg2); border:1px solid var(--border); border-radius:8px; margin-bottom:20px; }
.card-head { padding:14px 18px; border-bottom:1px solid var(--border);
             display:flex; align-items:center; justify-content:space-between; }
.card-title { font-size:.82rem; font-weight:600; color:var(--text); letter-spacing:.5px; }
.card-body { padding:0; }
.tbl-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; font-size:.76rem; }
th { padding:10px 14px; text-align:left; color:var(--muted); font-size:.64rem;
     text-transform:uppercase; letter-spacing:1px; border-bottom:1px solid var(--border);
     font-weight:500; white-space:nowrap; }
td { padding:10px 14px; border-bottom:1px solid rgba(26,37,53,.5);
     font-family:'IBM Plex Mono',monospace; font-size:.74rem; white-space:nowrap; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:rgba(255,255,255,.02); }
.g { color:var(--green); }
.r { color:var(--red); }
.y { color:var(--yellow); }
.p { color:var(--paper); }

/* Direction badge */
.dir { display:inline-block; padding:2px 8px; border-radius:3px; font-size:.64rem; font-weight:700;
       letter-spacing:.5px; text-transform:uppercase; }
.dir-l { background:rgba(0,229,122,.12); color:var(--green); border:1px solid rgba(0,229,122,.3); }
.dir-s { background:rgba(255,77,109,.12); color:var(--red);  border:1px solid rgba(255,77,109,.3); }
.dir-x { background:rgba(255,217,74,.1);  color:var(--yellow); }

/* Log terminal */
.terminal { background:#050810; border:1px solid var(--border); border-radius:8px;
            font-family:'IBM Plex Mono',monospace; font-size:.7rem; line-height:1.8;
            padding:14px; max-height:260px; overflow-y:auto; color:#5a7a9a; }
.terminal .lg { color:var(--green); }
.terminal .lr { color:var(--red); }
.terminal .ly { color:var(--yellow); }
.terminal .la { color:var(--accent); }

/* Backtest form */
.form-row { display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end; margin-bottom:24px; }
.form-group { display:flex; flex-direction:column; gap:5px; }
.form-group label { font-size:.64rem; color:var(--muted); text-transform:uppercase; letter-spacing:1px; }
.form-group input, .form-group select {
  background:var(--bg3); border:1px solid var(--border); color:var(--text);
  padding:8px 12px; border-radius:5px; font-family:'IBM Plex Mono',monospace; font-size:.8rem; width:150px; }
.form-group input:focus, .form-group select:focus {
  outline:none; border-color:var(--accent); }

/* Progress */
.progress-bar { height:3px; background:var(--border); border-radius:2px; margin-bottom:20px; overflow:hidden; }
.progress-fill { height:100%; background:linear-gradient(90deg,var(--accent),var(--green));
                 border-radius:2px; transition:width .3s; }

/* Scrollbar */
::-webkit-scrollbar { width:5px; height:5px; }
::-webkit-scrollbar-track { background:var(--bg); }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }

/* API quick-access bar */
.apibar { background:#06090f; border-bottom:1px solid var(--border);
          padding:8px 28px; display:flex; align-items:center; gap:18px;
          flex-wrap:wrap; position:sticky; top:53px; z-index:99; }
.apibar-label { font-size:.58rem; font-weight:700; letter-spacing:2px;
                color:var(--muted); text-transform:uppercase; white-space:nowrap; }
.apibar-group { display:flex; align-items:center; gap:5px; flex-wrap:wrap; }
.apibar-section { font-size:.58rem; color:var(--muted); letter-spacing:1px;
                  text-transform:uppercase; margin-right:2px; white-space:nowrap; }
.apibtn { display:inline-flex; align-items:center; padding:4px 10px;
          border-radius:4px; font-size:.68rem; font-weight:600; cursor:pointer;
          letter-spacing:.3px; border:none; text-decoration:none;
          font-family:'IBM Plex Mono',monospace; transition:.15s; white-space:nowrap; }
.apibtn:hover { filter:brightness(1.25); transform:translateY(-1px); }
.apibtn:active { transform:translateY(0); }
.apibtn-blue   { background:rgba(0,212,255,.12); color:#00d4ff; border:1px solid rgba(0,212,255,.25); }
.apibtn-green  { background:rgba(0,229,122,.12); color:#00e57a; border:1px solid rgba(0,229,122,.25); }
.apibtn-red    { background:rgba(255,77,109,.12); color:#ff4d6d; border:1px solid rgba(255,77,109,.25); }
.apibtn-yellow { background:rgba(255,217,74,.1);  color:#ffd94a; border:1px solid rgba(255,217,74,.25); }
.apibtn-purple { background:rgba(155,107,255,.12);color:#9b6bff; border:1px solid rgba(155,107,255,.25); }
#apibar-msg { font-size:.7rem; font-family:'IBM Plex Mono',monospace;
              padding:3px 10px; border-radius:4px; display:none; }
.abm-ok { background:rgba(0,229,122,.12); color:var(--green); border:1px solid rgba(0,229,122,.3); }
.abm-er { background:rgba(255,77,109,.12); color:var(--red);   border:1px solid rgba(255,77,109,.3); }
</style>
</head>
<body>
<div class="shell">
  <div class="topbar">
    <span class="logo">⚡ AZLEMA</span>
    <span id="modeBadge" class="badge-mode badge-paper">PAPER</span>
    <span class="mono" style="font-size:.72rem; color:var(--muted)">ETH-USDT-SWAP · 30m · OKX</span>
    <div class="tabs">
      <button class="tab active" onclick="switchTab('live')">Live</button>
      <button class="tab" onclick="switchTab('history')">Histórico</button>
      <button class="tab" onclick="switchTab('backtest')">Backtest</button>
    </div>
  </div>

  <!-- ── Quick-access API bar ── -->
  <div class="apibar">
    <span class="apibar-label">API RÁPIDA</span>
    <div class="apibar-group">
      <span class="apibar-section">STATUS</span>
      <a class="apibtn apibtn-blue"  href="/status"  target="_blank">/status</a>
      <a class="apibtn apibtn-blue"  href="/health"  target="_blank">/health</a>
      <a class="apibtn apibtn-blue"  href="/ping"    target="_blank">/ping</a>
    </div>
    <div class="apibar-group">
      <span class="apibar-section">TRADER</span>
      <button class="apibtn apibtn-green" onclick="apiPost('/start','Trader iniciado!')">POST /start</button>
      <button class="apibtn apibtn-red"   onclick="apiPost('/stop','Trader parado!')">POST /stop</button>
    </div>
    <div class="apibar-group">
      <span class="apibar-section">HISTÓRICO</span>
      <a class="apibtn apibtn-purple" href="/report" target="_blank">📊 Ver Relatório</a>
      <button class="apibtn apibtn-purple" onclick="exportJson('/history','trades_history')">⬇ JSON</button>
      <button class="apibtn apibtn-red"    onclick="if(confirm('Limpar histórico de trades?'))apiPost('/history/clear','Histórico limpo!')">🗑 Limpar</button>
    </div>
    <div class="apibar-group">
      <span class="apibar-section">BACKTEST</span>
      <a class="apibtn apibtn-yellow" href="/report" target="_blank">📊 Ver Relatório</a>
      <button class="apibtn apibtn-yellow" onclick="exportJson('/backtest/history','backtest_history')">⬇ JSON</button>
      <button class="apibtn apibtn-green"  onclick="quickBacktest()">▶ Rodar BT</button>
    </div>
    <div id="apibar-msg"></div>
  </div>

  <div class="main">

    <!-- ═══ LIVE PANEL ═══ -->
    <div class="panel active" id="panel-live">
      <div class="controls">
        <button class="btn btn-go"   id="btnStart" onclick="ctrl('start')">▶ Iniciar</button>
        <button class="btn btn-stop" id="btnStop"  onclick="ctrl('stop')">■ Parar</button>
        <span id="sysmsg"></span>
      </div>

      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-lbl">Status</div><div class="kpi-val" id="lv-status">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Saldo</div><div class="kpi-val g" id="lv-bal">—</div></div>
        <div class="kpi"><div class="kpi-lbl">PnL Sessão</div><div class="kpi-val" id="lv-pnl">—</div></div>
        <div class="kpi"><div class="kpi-lbl">Posição</div><div class="kpi-val" id="lv-pos">FLAT</div></div>
        <div class="kpi y"><div class="kpi-lbl">Period</div><div class="kpi-val y" id="lv-per">—</div></div>
        <div class="kpi"><div class="kpi-lbl">EC</div><div class="kpi-val mono" id="lv-ec">—</div></div>
        <div class="kpi"><div class="kpi-lbl">EMA</div><div class="kpi-val mono" id="lv-ema">—</div></div>
        <div class="kpi p"><div class="kpi-lbl">Trades Sessão</div><div class="kpi-val p" id="lv-tc">0</div></div>
      </div>

      <div class="card">
        <div class="card-head"><span class="card-title">ORDENS RECENTES</span></div>
        <div class="card-body">
          <div class="tbl-wrap">
            <table>
              <thead><tr><th>Hora</th><th>Ação</th><th>Preço</th><th>Qty ETH</th><th>Motivo</th></tr></thead>
              <tbody id="lv-trades"><tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">Aguardando...</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><span class="card-title">LOG DO SISTEMA</span></div>
        <div class="card-body"><div class="terminal" id="lv-log">aguardando...</div></div>
      </div>
    </div>

    <!-- ═══ HISTORY PANEL ═══ -->
    <div class="panel" id="panel-history">
      <div class="controls">
        <button class="btn btn-sec" onclick="loadHistory()">↺ Atualizar</button>
        <button class="btn btn-sec" onclick="if(confirm('Limpar histórico?'))clearHistory()">🗑 Limpar</button>
      </div>

      <div class="kpi-grid" id="hist-kpis">
        <div class="kpi"><div class="kpi-lbl">Total Trades</div><div class="kpi-val" id="h-total">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Win Rate</div><div class="kpi-val g" id="h-wr">—</div></div>
        <div class="kpi"><div class="kpi-lbl">PnL Total</div><div class="kpi-val" id="h-pnl">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Profit Factor</div><div class="kpi-val g" id="h-pf">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Avg Win</div><div class="kpi-val g" id="h-aw">—</div></div>
        <div class="kpi r"><div class="kpi-lbl">Avg Loss</div><div class="kpi-val r" id="h-al">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Melhor Trade</div><div class="kpi-val g" id="h-best">—</div></div>
        <div class="kpi r"><div class="kpi-lbl">Pior Trade</div><div class="kpi-val r" id="h-worst">—</div></div>
        <div class="kpi"><div class="kpi-lbl">Expectativa</div><div class="kpi-val" id="h-exp">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Wins</div><div class="kpi-val g" id="h-wins">—</div></div>
        <div class="kpi r"><div class="kpi-lbl">Losses</div><div class="kpi-val r" id="h-loss">—</div></div>
        <div class="kpi"><div class="kpi-lbl">Avg PnL</div><div class="kpi-val" id="h-avg">—</div></div>
      </div>

      <div class="card">
        <div class="card-head"><span class="card-title">TODOS OS TRADES</span></div>
        <div class="card-body">
          <div class="tbl-wrap">
            <table>
              <thead><tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
                         <th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th><th>Modo</th></tr></thead>
              <tbody id="hist-tbl"><tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">Carregando...</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ BACKTEST PANEL ═══ -->
    <div class="panel" id="panel-backtest">
      <div class="form-row">
        <div class="form-group">
          <label>Símbolo</label>
          <input id="bt-sym" value="ETH-USDT-SWAP">
        </div>
        <div class="form-group">
          <label>Timeframe</label>
          <select id="bt-tf">
            <option value="30m" selected>30m</option>
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="1d">1d</option>
            <option value="15m">15m</option>
          </select>
        </div>
        <div class="form-group">
          <label>Candles</label>
          <input id="bt-lim" type="number" value="500" min="100" max="5000">
        </div>
        <div class="form-group">
          <label>Capital Inicial</label>
          <input id="bt-cap" type="number" value="1000" min="100">
        </div>
        <button class="btn btn-accent" id="btnBT" onclick="runBacktest()">▶ Executar</button>
      </div>
      <div class="progress-bar"><div class="progress-fill" id="bt-prog" style="width:0%"></div></div>
      <div id="bt-result" style="display:none">
        <div class="kpi-grid" id="bt-kpis"></div>
        <div class="card">
          <div class="card-head"><span class="card-title">TRADES DO BACKTEST</span></div>
          <div class="card-body">
            <div class="tbl-wrap">
              <table>
                <thead><tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
                           <th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th></tr></thead>
                <tbody id="bt-tbl"></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:20px">
        <div class="card-head"><span class="card-title">HISTÓRICO DE BACKTESTS</span></div>
        <div class="card-body">
          <div class="tbl-wrap">
            <table>
              <thead><tr><th>Data</th><th>Símbolo</th><th>TF</th><th>Candles</th><th>PnL</th>
                         <th>Win Rate</th><th>Trades</th><th>PF</th><th>Drawdown</th><th>Sharpe</th></tr></thead>
              <tbody id="bt-hist-tbl"><tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Sem histórico</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

  </div><!-- /main -->
</div><!-- /shell -->

<script>
// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(t) {
  document.querySelectorAll('.tab').forEach((el,i)=>{
    const tabs=['live','history','backtest'];
    el.classList.toggle('active', tabs[i]===t);
  });
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
  document.getElementById('panel-'+t).classList.add('active');
  if(t==='history') loadHistory();
  if(t==='backtest') loadBtHistory();
}

// ── Live poll ─────────────────────────────────────────────────────────────────
async function poll(){
  try{
    const d=await(await fetch('/status')).json();
    const run=d.status==='running', warm=d.status==='warming';

    // Mode badge
    const mb=document.getElementById('modeBadge');
    mb.textContent=d.paper?'PAPER':'LIVE';
    mb.className='badge-mode '+(d.paper?'badge-paper':'badge-live');

    // Buttons
    document.getElementById('btnStart').disabled=run||warm;
    document.getElementById('btnStop').disabled=!(run||warm);

    // Status
    const se=document.getElementById('lv-status');
    if(run) se.innerHTML='<span class="status-dot dot-run"></span><span class="g">Rodando</span>';
    else if(warm) se.innerHTML='<span class="status-dot dot-warm"></span><span class="y">Warmup...</span>';
    else se.innerHTML='<span class="status-dot dot-stop"></span><span style="color:var(--muted)">Parado</span>';

    // KPIs
    if(d.bal!=null) document.getElementById('lv-bal').textContent=d.bal.toFixed(2)+' USDT';
    const pe=document.getElementById('lv-pnl');
    if(d.pnl!=null){
      pe.textContent=(d.pnl>=0?'+':'')+d.pnl.toFixed(4)+' USDT';
      pe.className='kpi-val '+(d.pnl>=0?'g':'r');
    }
    const pp=document.getElementById('lv-pos');
    if(d.pos){
      const s=d.pos.side||d.pos.side;
      pp.innerHTML=`<span class="${s==='long'?'g':'r'}">${s.toUpperCase()}</span>`;
    } else { pp.innerHTML='<span style="color:var(--muted)">FLAT</span>'; }
    if(d.period!=null) document.getElementById('lv-per').textContent=d.period;
    if(d.ec!=null) document.getElementById('lv-ec').textContent=d.ec.toFixed(2);
    if(d.ema!=null) document.getElementById('lv-ema').textContent=d.ema.toFixed(2);
    document.getElementById('lv-tc').textContent=d.tc||0;

    // Trades table
    const tb=document.getElementById('lv-trades');
    const tr=[...(d.trades||[])].reverse();
    if(tr.length){
      tb.innerHTML=tr.map(t=>{
        const ac=t.action||'';
        let cl='dir-x', lb=ac;
        if(ac.includes('LONG')) { cl='dir-l'; lb=ac.includes('ENTER')?'▲ LONG':'▼ EXIT L'; }
        else if(ac.includes('SHORT')) { cl='dir-s'; lb=ac.includes('ENTER')?'▼ SHORT':'▲ EXIT S'; }
        return`<tr>
          <td>${(t.time||'').split('T')[1]?.slice(0,8)||'—'}</td>
          <td><span class="dir ${cl}">${lb}</span></td>
          <td>${t.price?.toFixed(2)||'—'}</td>
          <td>${t.qty?.toFixed(6)||'—'}</td>
          <td style="color:var(--muted)">${t.reason||'—'}</td>
        </tr>`;
      }).join('');
    }

    // Log
    const lb=document.getElementById('lv-log');
    if(d.log&&d.log.length){
      lb.innerHTML=(d.log||[]).slice(-80).map(l=>{
        let cls='';
        if(/✅|LONG|BUY/.test(l)) cls='lg';
        else if(/❌|EXIT|TRAIL|SHORT/.test(l)) cls='lr';
        else if(/⚠️|WARN/.test(l)) cls='ly';
        else if(/AZLEMA|╔|╚/.test(l)) cls='la';
        return`<div class="${cls}">${l}</div>`;
      }).join('');
      lb.scrollTop=lb.scrollHeight;
    }
  }catch(e){console.error(e);}
}
poll(); setInterval(poll,4000);

// ── Controls ──────────────────────────────────────────────────────────────────
async function ctrl(a){
  const m=document.getElementById('sysmsg');
  m.style.display='inline-block';
  m.className=a==='start'?'msg-ok':'msg-er';
  m.textContent=a==='start'?'Iniciando...':'Parando...';
  try{
    const d=await(await fetch('/'+a,{method:'POST'})).json();
    m.className=d.error?'msg-er':'msg-ok';
    m.textContent=d.message||d.error||'OK';
  }catch{ m.className='msg-er'; m.textContent='Erro de rede'; }
  setTimeout(()=>m.style.display='none',5000);
  setTimeout(poll,1500);
}

// ── History ───────────────────────────────────────────────────────────────────
async function loadHistory(){
  try{
    const d=await(await fetch('/history')).json();
    const s=d.stats||{};
    const pf=s.profit_factor===Infinity||s.profit_factor>999?'∞':+(s.profit_factor||0).toFixed(3);
    document.getElementById('h-total').textContent=s.total||0;
    const wrEl=document.getElementById('h-wr');
    wrEl.textContent=(s.win_rate||0).toFixed(1)+'%';
    wrEl.className='kpi-val '+(s.win_rate>=50?'g':'r');
    const pnlEl=document.getElementById('h-pnl');
    pnlEl.textContent=(s.total_pnl>=0?'+':'')+( s.total_pnl||0).toFixed(4)+' USDT';
    pnlEl.className='kpi-val '+(s.total_pnl>=0?'g':'r');
    const pfEl=document.getElementById('h-pf');
    pfEl.textContent=pf;
    pfEl.className='kpi-val '+(s.profit_factor>1?'g':'r');
    document.getElementById('h-aw').textContent='+'+(s.avg_win||0).toFixed(4);
    document.getElementById('h-al').textContent=(s.avg_loss||0).toFixed(4);
    document.getElementById('h-best').textContent='+'+(s.best_trade||0).toFixed(4);
    document.getElementById('h-worst').textContent=(s.worst_trade||0).toFixed(4);
    const expEl=document.getElementById('h-exp');
    expEl.textContent=(s.expectancy||0).toFixed(4);
    expEl.className='kpi-val '+(s.expectancy>=0?'g':'r');
    document.getElementById('h-wins').textContent=s.wins||0;
    document.getElementById('h-loss').textContent=s.losses||0;
    const avgEl=document.getElementById('h-avg');
    avgEl.textContent=(s.avg_pnl>=0?'+':'')+( s.avg_pnl||0).toFixed(4);
    avgEl.className='kpi-val '+(s.avg_pnl>=0?'g':'r');

    const tb=document.getElementById('hist-tbl');
    const trades=(d.trades||[]).filter(t=>t.status==='closed').reverse();
    if(!trades.length){
      tb.innerHTML='<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">Nenhum trade fechado</td></tr>';
      return;
    }
    tb.innerHTML=trades.map((t,i)=>{
      const pnl=t.pnl_usdt||0;
      const pct=t.pnl_pct||0;
      const dir=t.action==='BUY'?'LONG':'SHORT';
      const dc=t.action==='BUY'?'dir-l':'dir-s';
      const pc=pnl>=0?'g':'r';
      const ep=t.exit_price?t.exit_price.toFixed(2):'—';
      const mode=t.mode==='paper'?'<span class="p">PAPER</span>':'<span class="g">LIVE</span>';
      return`<tr>
        <td>${i+1}</td>
        <td>${(t.entry_time||'—').replace('T',' ').slice(0,16)}</td>
        <td>${(t.exit_time||'—').replace('T',' ').slice(0,16)}</td>
        <td><span class="dir ${dc}">${dir}</span></td>
        <td>${(t.qty||0).toFixed(4)}</td>
        <td>${(t.entry_price||0).toFixed(2)}</td>
        <td>${ep}</td>
        <td class="${pc}">${pnl>=0?'+':''}${pnl.toFixed(4)}</td>
        <td class="${pc}">${pct>=0?'+':''}${pct.toFixed(2)}%</td>
        <td style="color:var(--muted)">${t.exit_reason||'—'}</td>
        <td>${mode}</td>
      </tr>`;
    }).join('');
  }catch(e){console.error(e);}
}

async function clearHistory(){
  await fetch('/history/clear',{method:'POST'});
  loadHistory();
}

// ── Backtest ──────────────────────────────────────────────────────────────────
async function runBacktest(){
  const btn=document.getElementById('btnBT');
  const prog=document.getElementById('bt-prog');
  btn.disabled=true; btn.textContent='Rodando...';
  prog.style.width='20%';
  document.getElementById('bt-result').style.display='none';
  try{
    const sym=document.getElementById('bt-sym').value;
    const tf=document.getElementById('bt-tf').value;
    const lim=document.getElementById('bt-lim').value;
    const cap=document.getElementById('bt-cap').value;
    prog.style.width='60%';
    const d=await(await fetch(`/backtest/run?symbol=${sym}&tf=${tf}&limit=${lim}&capital=${cap}`,{method:'POST'})).json();
    prog.style.width='100%';
    if(d.error){ alert('Erro: '+d.error); return; }
    renderBacktestResult(d);
    loadBtHistory();
  }catch(e){ alert('Erro: '+e); }
  finally{ btn.disabled=false; btn.textContent='▶ Executar'; setTimeout(()=>prog.style.width='0%',1000); }
}

function renderBacktestResult(d){
  const pf=d.profit_factor===Infinity||d.profit_factor>999?'∞':+(d.profit_factor||0).toFixed(3);
  const kpis=[
    ['PnL Total', (d.total_pnl>=0?'+':'')+d.total_pnl.toFixed(2)+' USDT', d.total_pnl>=0?'g':'r'],
    ['Saldo Final', d.final_bal.toFixed(2)+' USDT', ''],
    ['Win Rate', d.win_rate.toFixed(1)+'%', d.win_rate>=50?'g':'r'],
    ['Total Trades', d.total_trades, ''],
    ['Profit Factor', pf, d.profit_factor>1?'g':'r'],
    ['Max Drawdown', d.max_drawdown.toFixed(2)+'%', 'r'],
    ['Sharpe Ratio', d.sharpe.toFixed(3), d.sharpe>=1?'g': d.sharpe>=0?'y':'r'],
  ];
  document.getElementById('bt-kpis').innerHTML=kpis.map(([lbl,val,cls])=>
    `<div class="kpi ${cls}"><div class="kpi-lbl">${lbl}</div><div class="kpi-val ${cls}">${val}</div></div>`
  ).join('');

  const trades=(d.trades||[]).slice().reverse();
  document.getElementById('bt-tbl').innerHTML=trades.length?trades.map((t,i)=>{
    const pnl=t.pnl_usdt||0;
    const pct=t.pnl_percent||0;
    const dir=t.action==='BUY'?'LONG':'SHORT';
    const dc=t.action==='BUY'?'dir-l':'dir-s';
    const pc=pnl>=0?'g':'r';
    return`<tr>
      <td>${i+1}</td>
      <td>${(t.entry_time||'—').replace('T',' ').slice(0,16)}</td>
      <td>${(t.exit_time||'—').replace('T',' ').slice(0,16)}</td>
      <td><span class="dir ${dc}">${dir}</span></td>
      <td>${(t.qty||0).toFixed(4)}</td>
      <td>${(t.entry_price||0).toFixed(2)}</td>
      <td>${t.exit_price?(t.exit_price).toFixed(2):'—'}</td>
      <td class="${pc}">${pnl>=0?'+':''}${pnl.toFixed(4)}</td>
      <td class="${pc}">${pct>=0?'+':''}${pct.toFixed(2)}%</td>
      <td style="color:var(--muted)">${t.exit_comment||'—'}</td>
    </tr>`;
  }).join(''):'<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Sem trades</td></tr>';

  document.getElementById('bt-result').style.display='block';
}

async function loadBtHistory(){
  try{
    const d=await(await fetch('/backtest/history')).json();
    const sessions=(d.sessions||[]).slice().reverse();
    const tb=document.getElementById('bt-hist-tbl');
    if(!sessions.length){
      tb.innerHTML='<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Sem histórico</td></tr>';
      return;
    }
    tb.innerHTML=sessions.map(s=>{
      const pf=s.profit_factor===Infinity||s.profit_factor>999?'∞':+(s.profit_factor||0).toFixed(3);
      const pc=s.total_pnl>=0?'g':'r';
      const wrc=s.win_rate>=50?'g':'r';
      return`<tr>
        <td>${(s.id||'—').replace('T',' ').slice(0,16)}</td>
        <td>${s.symbol||'—'}</td>
        <td>${s.timeframe||'—'}</td>
        <td>${s.candles||0}</td>
        <td class="${pc}">${s.total_pnl>=0?'+':''}${(s.total_pnl||0).toFixed(2)}</td>
        <td class="${wrc}">${(s.win_rate||0).toFixed(1)}%</td>
        <td>${s.total_trades||0}</td>
        <td class="${s.profit_factor>1?'g':'r'}">${pf}</td>
        <td class="r">${(s.max_drawdown||0).toFixed(2)}%</td>
        <td class="${(s.sharpe||0)>=1?'g':(s.sharpe||0)>=0?'y':'r'}">${(s.sharpe||0).toFixed(3)}</td>
      </tr>`;
    }).join('');
  }catch(e){console.error(e);}
}

loadBtHistory();

// ── API bar helpers ───────────────────────────────────────────────────────────
async function apiPost(route, successMsg) {
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block';
  m.className = 'abm-ok';
  m.textContent = '...';
  try {
    const d = await (await fetch(route, { method: 'POST' })).json();
    m.className = d.error ? 'abm-er' : 'abm-ok';
    m.textContent = d.error || successMsg || d.message || 'OK';
  } catch (e) {
    m.className = 'abm-er';
    m.textContent = 'Erro: ' + e;
  }
  setTimeout(() => m.style.display = 'none', 3500);
  setTimeout(poll, 1200);
}

async function exportJson(route, filename) {
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok';
  m.textContent = 'Exportando...';
  try {
    const d = await (await fetch(route)).json();
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename + '_' + new Date().toISOString().slice(0,10) + '.json';
    a.click();
    URL.revokeObjectURL(url);
    m.textContent = '✓ Download iniciado';
  } catch (e) {
    m.className = 'abm-er';
    m.textContent = 'Erro: ' + e;
  }
  setTimeout(() => m.style.display = 'none', 3000);
}

async function quickBacktest() {
  const sym = prompt('Símbolo (ex: ETH-USDT-SWAP)', 'ETH-USDT-SWAP');
  if (!sym) return;
  const tf  = prompt('Timeframe (1m/5m/15m/30m/1h/4h/1d)', '30m');
  if (!tf)  return;
  const lim = prompt('Quantidade de candles', '500');
  if (!lim) return;
  const cap = prompt('Capital inicial (USDT)', '1000');
  if (!cap) return;

  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok';
  m.textContent = '⏳ Rodando backtest...';

  try {
    const d = await (await fetch(
      `/backtest/run?symbol=${encodeURIComponent(sym)}&tf=${tf}&limit=${lim}&capital=${cap}`,
      { method: 'POST' }
    )).json();
    if (d.error) { m.className='abm-er'; m.textContent='Erro: '+d.error; }
    else {
      m.textContent = `✓ BT OK | PnL: ${d.total_pnl>=0?'+':''}${(d.total_pnl||0).toFixed(2)} | WR: ${(d.win_rate||0).toFixed(1)}%`;
      switchTab('backtest');
      renderBacktestResult(d);
      loadBtHistory();
    }
  } catch(e) { m.className='abm-er'; m.textContent='Erro: '+e; }
  setTimeout(() => m.style.display = 'none', 6000);
}
</script>
</body>
</html>"""


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
        return jsonify({"status":"stopped","tc":0,"trades":[],"log":_logs[-80:],
                        "paper": PAPER_TRADING})
    s = "running" if t._running else ("warming" if t._warming else "stopped")
    return jsonify({
        "status":  s,
        "paper":   PAPER_TRADING,
        "pos":     t._cache_pos,
        "bal":     t._cache_bal,
        "ct":      t._cache_ct,
        "pnl":     t.live_pnl,
        "period":  t.strategy.Period,
        "ec":      t.strategy.EC,
        "ema":     t.strategy.EMA,
        "tc":      len(t.log),
        "trades":  t.log[-10:],
        "log":     _logs[-80:],
    })

@app.route('/start', methods=['POST'])
def start():
    with _lock:
        if _trader is not None:
            return jsonify({"message": "Já está rodando"})
        if not PAPER_TRADING and not _creds_ok():
            return jsonify({"error": "Chaves OKX não encontradas"}), 400
        threading.Thread(target=_thread, daemon=True).start()
        return jsonify({"message": f"ok ({'paper' if PAPER_TRADING else 'live'})"})

@app.route('/stop', methods=['POST'])
def stop():
    if _trader: _trader.stop()
    return jsonify({"message": "Parado"})

@app.route('/ping')
def ping(): return "pong"

@app.route('/health')
def health():
    return jsonify({"ok": True, "creds": _creds_ok(), "paper": PAPER_TRADING,
                    "trader": _trader is not None})

# ── History endpoints ──────────────────────────────────────────────────────────
@app.route('/history')
def get_history():
    return jsonify({
        "trades": history_mgr.get_all_trades(),
        "stats":  history_mgr.get_stats(),
    })

@app.route('/history/clear', methods=['POST'])
def clear_history():
    history_mgr.clear()
    return jsonify({"message": "Histórico limpo"})

# ── Backtest endpoints ─────────────────────────────────────────────────────────
@app.route('/backtest/run', methods=['POST'])
def api_backtest():
    sym    = flask_request.args.get('symbol',  SYMBOL)
    tf     = flask_request.args.get('tf',      TIMEFRAME)
    limit  = int(flask_request.args.get('limit',  500))
    capital= float(flask_request.args.get('capital', 1000.0))

    def _run():
        pass  # backtest runs in-thread for now

    result = run_backtest(sym, tf, limit, capital)
    return jsonify(result)

@app.route('/backtest/history')
def get_bt_history():
    data = backtest_mgr._load()
    return jsonify({"sessions": data.get("sessions", [])})


REPORT_HTML = '<!DOCTYPE html>\n<html lang="pt-BR">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>AZLEMA — Backtest Report</title>\n<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Syne:wght@400;500;600;700;800&display=swap" rel="stylesheet">\n<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>\n<script src="https://cdn.jsdelivr.net/npm/luxon@3.4.4/build/global/luxon.min.js"></script>\n<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1.3.1/dist/chartjs-adapter-luxon.min.js"></script>\n<style>\n*{margin:0;padding:0;box-sizing:border-box}\n:root{\n  --ink:#0a0d13;\n  --ink2:#0f1319;\n  --ink3:#141923;\n  --wire:#1c2433;\n  --wire2:#243040;\n  --fog:#334a66;\n  --mist:#5a7899;\n  --sky:#c5d8f0;\n  --white:#e8f0fa;\n  --long:#00d97e;\n  --long2:rgba(0,217,126,.12);\n  --short:#ff3d5a;\n  --short2:rgba(255,61,90,.12);\n  --gold:#f5a623;\n  --gold2:rgba(245,166,35,.1);\n  --cyan:#22e5ff;\n  --cyan2:rgba(34,229,255,.08);\n  --r:8px;\n}\nhtml{background:var(--ink);color:var(--sky);font-family:\'JetBrains Mono\',monospace;font-size:13px}\nbody{min-height:100vh}\n\n/* ─── layout ─── */\n.wrap{max-width:1440px;margin:0 auto;padding:0 24px 48px}\n\n/* ─── header ─── */\n.hdr{padding:28px 0 24px;display:flex;align-items:flex-start;gap:24px;border-bottom:1px solid var(--wire);margin-bottom:28px}\n.hdr-brand{display:flex;align-items:center;gap:12px}\n.logo-mark{width:36px;height:36px;background:linear-gradient(135deg,var(--cyan),var(--long));border-radius:8px;\n  display:flex;align-items:center;justify-content:center;font-family:\'Syne\',sans-serif;font-weight:800;\n  font-size:14px;color:#000;letter-spacing:-1px;flex-shrink:0}\n.hdr-text h1{font-family:\'Syne\',sans-serif;font-weight:700;font-size:1.25rem;color:var(--white);letter-spacing:-.5px}\n.hdr-text p{color:var(--mist);font-size:.72rem;margin-top:2px;letter-spacing:.5px}\n.hdr-meta{margin-left:auto;text-align:right;display:flex;flex-direction:column;gap:4px}\n.meta-chip{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:4px;\n  font-size:.68rem;letter-spacing:.5px;border:1px solid var(--wire2);color:var(--mist);background:var(--ink3)}\n.meta-chip .dot{width:6px;height:6px;border-radius:50%;background:var(--long);animation:blink 2s infinite}\n@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}\n\n/* ─── summary strip ─── */\n.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1px;\n  background:var(--wire);border:1px solid var(--wire);border-radius:var(--r);overflow:hidden;margin-bottom:28px}\n.sum-cell{background:var(--ink2);padding:16px 18px;position:relative}\n.sum-cell::after{content:\'\';position:absolute;bottom:0;left:18px;right:18px;height:2px;border-radius:2px}\n.sum-cell.pos::after{background:var(--long)}\n.sum-cell.neg::after{background:var(--short)}\n.sum-cell.neu::after{background:var(--fog)}\n.sum-cell.gold::after{background:var(--gold)}\n.sum-cell.cyan::after{background:var(--cyan)}\n.sum-lbl{font-size:.6rem;color:var(--fog);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px}\n.sum-val{font-family:\'Syne\',sans-serif;font-weight:700;font-size:1.5rem;color:var(--white)}\n.sum-val.pos{color:var(--long)}\n.sum-val.neg{color:var(--short)}\n.sum-val.gold{color:var(--gold)}\n.sum-val.cyan{color:var(--cyan)}\n.sum-sub{font-size:.65rem;color:var(--fog);margin-top:4px}\n\n/* ─── charts ─── */\n.charts-row{display:grid;grid-template-columns:1fr 320px;gap:16px;margin-bottom:28px}\n.chart-box{background:var(--ink2);border:1px solid var(--wire);border-radius:var(--r);padding:20px}\n.chart-box.full{grid-column:1/-1}\n.chart-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}\n.chart-title{font-family:\'Syne\',sans-serif;font-weight:600;font-size:.82rem;color:var(--white);letter-spacing:.3px}\n.chart-sub{font-size:.65rem;color:var(--fog)}\n.chart-canvas{position:relative;height:220px}\n.chart-canvas-sm{position:relative;height:220px}\n\n/* ─── trade timeline ─── */\n.section-head{display:flex;align-items:center;gap:12px;margin-bottom:16px}\n.section-title{font-family:\'Syne\',sans-serif;font-weight:700;font-size:.9rem;color:var(--white)}\n.count-badge{background:var(--ink3);border:1px solid var(--wire2);color:var(--mist);\n  padding:2px 8px;border-radius:12px;font-size:.68rem}\n\n/* filter pills */\n.filters{display:flex;gap:6px;margin-bottom:20px;flex-wrap:wrap}\n.pill{padding:5px 14px;border-radius:20px;font-size:.68rem;font-weight:500;cursor:pointer;\n  border:1px solid var(--wire2);color:var(--mist);background:transparent;\n  font-family:\'JetBrains Mono\',monospace;transition:.15s;letter-spacing:.3px}\n.pill:hover{border-color:var(--fog);color:var(--sky)}\n.pill.active{border-color:var(--cyan);color:var(--cyan);background:var(--cyan2)}\n.pill.long-pill.active{border-color:var(--long);color:var(--long);background:var(--long2)}\n.pill.short-pill.active{border-color:var(--short);color:var(--short);background:var(--short2)}\n\n/* trade cards */\n.trades-list{display:flex;flex-direction:column;gap:8px}\n.trade-card{background:var(--ink2);border:1px solid var(--wire);border-radius:var(--r);\n  padding:0;overflow:hidden;cursor:pointer;transition:.15s;position:relative}\n.trade-card:hover{border-color:var(--wire2);background:var(--ink3)}\n.trade-card.expanded .trade-expand{display:block}\n.trade-card.long-card{border-left:3px solid var(--long)}\n.trade-card.short-card{border-left:3px solid var(--short)}\n.trade-card.win .tc-pnl{color:var(--long)}\n.trade-card.loss .tc-pnl{color:var(--short)}\n\n.trade-main{display:grid;grid-template-columns:36px 1fr 1fr 1fr 1fr 1fr 110px;\n  align-items:center;padding:12px 16px;gap:12px}\n.tc-num{color:var(--fog);font-size:.68rem;text-align:center}\n.tc-dir{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:4px;\n  font-size:.7rem;font-weight:700;letter-spacing:.5px}\n.tc-dir.long{background:var(--long2);color:var(--long);border:1px solid rgba(0,217,126,.25)}\n.tc-dir.short{background:var(--short2);color:var(--short);border:1px solid rgba(255,61,90,.25)}\n.tc-field{display:flex;flex-direction:column;gap:2px}\n.tc-lbl{font-size:.58rem;color:var(--fog);letter-spacing:1px;text-transform:uppercase}\n.tc-val{font-size:.78rem;color:var(--sky)}\n.tc-pnl{font-family:\'Syne\',sans-serif;font-weight:700;font-size:1rem}\n.tc-pnl-pct{font-size:.68rem;margin-top:1px}\n.tc-pnl-pct.pos{color:var(--long)}\n.tc-pnl-pct.neg{color:var(--short)}\n\n/* expanded detail */\n.trade-expand{display:none;border-top:1px solid var(--wire);padding:16px;\n  background:var(--ink);animation:slideDown .2s ease}\n@keyframes slideDown{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:translateY(0)}}\n.expand-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}\n.expand-cell{background:var(--ink2);border:1px solid var(--wire);border-radius:6px;padding:12px 14px}\n.expand-lbl{font-size:.6rem;color:var(--fog);letter-spacing:1px;text-transform:uppercase;margin-bottom:5px}\n.expand-val{font-size:.85rem;color:var(--white)}\n\n/* price path visual */\n.price-path{display:flex;align-items:center;gap:8px;background:var(--ink2);\n  border:1px solid var(--wire);border-radius:6px;padding:12px 16px;margin-top:8px}\n.pp-entry{display:flex;flex-direction:column;gap:2px;flex:1}\n.pp-exit{display:flex;flex-direction:column;gap:2px;flex:1;text-align:right}\n.pp-lbl{font-size:.6rem;color:var(--fog);letter-spacing:1px;text-transform:uppercase}\n.pp-price{font-family:\'Syne\',sans-serif;font-weight:700;font-size:1.1rem;color:var(--white)}\n.pp-time{font-size:.65rem;color:var(--mist)}\n.pp-arrow{flex:2;display:flex;flex-direction:column;align-items:center;gap:4px;padding:0 8px}\n.pp-line{width:100%;height:2px;position:relative;border-radius:2px}\n.pp-line.pos{background:linear-gradient(90deg,var(--long),rgba(0,217,126,.3))}\n.pp-line.neg{background:linear-gradient(90deg,var(--short),rgba(255,61,90,.3))}\n.pp-line::before{content:\'▶\';position:absolute;right:-8px;top:50%;transform:translateY(-50%);\n  font-size:.6rem}\n.pp-line.pos::before{color:var(--long)}\n.pp-line.neg::before{color:var(--short)}\n.pp-dur{font-size:.63rem;color:var(--fog)}\n.pp-pnl-big{text-align:center;font-family:\'Syne\',sans-serif;font-weight:800;font-size:.9rem}\n.pp-pnl-big.pos{color:var(--long)}\n.pp-pnl-big.neg{color:var(--short)}\n\n/* reason tag */\n.reason-tag{display:inline-flex;align-items:center;padding:2px 8px;border-radius:3px;\n  font-size:.63rem;letter-spacing:.5px}\n.reason-tag.trail{background:rgba(245,166,35,.12);color:var(--gold);border:1px solid rgba(245,166,35,.3)}\n.reason-tag.sl{background:rgba(255,61,90,.12);color:var(--short);border:1px solid rgba(255,61,90,.3)}\n.reason-tag.rev{background:rgba(34,229,255,.1);color:var(--cyan);border:1px solid rgba(34,229,255,.3)}\n.reason-tag.other{background:var(--ink3);color:var(--mist);border:1px solid var(--wire2)}\n\n/* ─── empty state ─── */\n.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;\n  padding:64px 24px;color:var(--fog)}\n.empty-icon{font-size:3rem;margin-bottom:16px;opacity:.3}\n.empty-text{font-family:\'Syne\',sans-serif;font-weight:600;font-size:.9rem;color:var(--mist)}\n.empty-sub{font-size:.72rem;color:var(--fog);margin-top:6px}\n\n/* ─── scrollbar ─── */\n::-webkit-scrollbar{width:4px;height:4px}\n::-webkit-scrollbar-track{background:var(--ink)}\n::-webkit-scrollbar-thumb{background:var(--wire2);border-radius:4px}\n\n/* ─── section ─── */\n.section{background:var(--ink2);border:1px solid var(--wire);border-radius:var(--r);padding:20px;margin-bottom:28px}\n</style>\n</head>\n<body>\n<div class="wrap">\n\n  <div class="hdr">\n    <div class="hdr-brand">\n      <div class="logo-mark">AZ</div>\n      <div class="hdr-text">\n        <h1>Backtest Report</h1>\n        <p>ADAPTIVE ZERO LAG EMA v2 &nbsp;·&nbsp; ETH-USDT-SWAP &nbsp;·&nbsp; 30M</p>\n      </div>\n    </div>\n    <div class="hdr-meta">\n      <div class="meta-chip"><span class="dot"></span>PAPER TRADING</div>\n      <div class="meta-chip" id="meta-period">— candles · — dias</div>\n    </div>\n  </div>\n\n  <!-- summary strip -->\n  <div class="summary" id="summary"></div>\n\n  <!-- charts -->\n  <div class="charts-row">\n    <div class="chart-box">\n      <div class="chart-head">\n        <div>\n          <div class="chart-title">Curva de Equity</div>\n          <div class="chart-sub">Evolução do capital ao longo dos trades</div>\n        </div>\n      </div>\n      <div class="chart-canvas"><canvas id="equityChart"></canvas></div>\n    </div>\n    <div class="chart-box">\n      <div class="chart-head">\n        <div>\n          <div class="chart-title">Distribuição PnL</div>\n          <div class="chart-sub">Win vs Loss por trade</div>\n        </div>\n      </div>\n      <div class="chart-canvas-sm"><canvas id="distChart"></canvas></div>\n    </div>\n  </div>\n\n  <!-- trade list -->\n  <div class="section">\n    <div class="section-head">\n      <span class="section-title">Histórico de Trades</span>\n      <span class="count-badge" id="trade-count">0 trades</span>\n    </div>\n\n    <div class="filters">\n      <button class="pill active" data-filter="all" onclick="setFilter(\'all\',this)">Todos</button>\n      <button class="pill long-pill" data-filter="long" onclick="setFilter(\'long\',this)">▲ Long</button>\n      <button class="pill short-pill" data-filter="short" onclick="setFilter(\'short\',this)">▼ Short</button>\n      <button class="pill" data-filter="win" onclick="setFilter(\'win\',this)">✓ Wins</button>\n      <button class="pill" data-filter="loss" onclick="setFilter(\'loss\',this)">✗ Losses</button>\n    </div>\n\n    <div id="trades-list" class="trades-list"></div>\n  </div>\n\n</div>\n\n<script>\n// ── DEMO DATA (substitua por fetch(\'/backtest/history\') ou fetch(\'/history\')) ──\nconst DEMO_TRADES = generateDemoTrades();\n\nfunction generateDemoTrades() {\n  const trades = [];\n  let balance = 1000;\n  let price = 3200;\n  const start = new Date(\'2024-09-01T00:00:00Z\');\n  const sides = [\'BUY\',\'BUY\',\'SELL\',\'BUY\',\'SELL\',\'SELL\',\'BUY\',\'BUY\',\'SELL\',\'BUY\',\n                 \'SELL\',\'BUY\',\'BUY\',\'SELL\',\'BUY\',\'SELL\',\'BUY\',\'SELL\',\'BUY\',\'BUY\'];\n  const reasons = [\'TRAIL\',\'TRAIL\',\'SL\',\'TRAIL\',\'TRAIL\',\'REVERSAL\',\'TRAIL\',\'SL\',\n                   \'TRAIL\',\'TRAIL\',\'REVERSAL\',\'TRAIL\',\'SL\',\'TRAIL\',\'TRAIL\',\'SL\',\n                   \'TRAIL\',\'TRAIL\',\'REVERSAL\',\'TRAIL\'];\n  const pnlMults = [2.1,-0.6,1.8,3.2,-0.5,1.1,-0.7,2.4,1.6,-0.4,2.8,1.2,-0.8,3.5,1.9,-0.6,2.2,1.5,-0.3,2.7];\n\n  for(let i=0;i<20;i++){\n    const entryDate = new Date(start.getTime() + i*31*3600000 + Math.random()*5*3600000);\n    const durationH = 4 + Math.random()*48;\n    const exitDate  = new Date(entryDate.getTime() + durationH*3600000);\n    const action    = sides[i];\n    const ep        = price * (1 + (Math.random()-0.5)*0.02);\n    const pnlUSDT   = pnlMults[i] * balance * 0.008;\n    const xp        = action===\'BUY\'\n      ? ep * (1 + pnlUSDT / (balance * 0.3 * ep) * ep)\n      : ep * (1 - pnlUSDT / (balance * 0.3 * ep) * ep);\n    const qty       = (balance * 0.8 * 0.01) / ep * 100;\n    const pnlPct    = action===\'BUY\'\n      ? (xp - ep) / ep * 100\n      : (ep - xp) / ep * 100;\n    balance += pnlUSDT;\n    price = ep * (1 + (Math.random()-0.5)*0.04);\n\n    trades.push({\n      id: i+1,\n      action,\n      status: \'closed\',\n      entry_time: entryDate.toISOString(),\n      exit_time:  exitDate.toISOString(),\n      entry_price: +ep.toFixed(2),\n      exit_price:  +xp.toFixed(2),\n      qty:         +qty.toFixed(6),\n      pnl_usdt:    +pnlUSDT.toFixed(4),\n      pnl_pct:     +pnlPct.toFixed(3),\n      balance_after: +balance.toFixed(2),\n      exit_reason: reasons[i],\n      mode: \'paper\',\n    });\n  }\n  return trades;\n}\n\n// ── state ──\nlet _filter = \'all\';\nlet _trades = [];\nlet _equityChart, _distChart;\n\n// ── init ──\nasync function init(){\n  // Tenta buscar da API real, usa demo se falhar\n  try {\n    // Tenta histórico de trades ao vivo\n    const r1 = await fetch(\'/history\');\n    const d1 = await r1.json();\n    const live = (d1.trades||[]).filter(t=>t.status===\'closed\');\n\n    // Tenta histórico de backtests\n    const r2 = await fetch(\'/backtest/history\');\n    const d2 = await r2.json();\n    const bt  = (d2.sessions||[]).flatMap(s=>(s.trades||[]));\n\n    _trades = [...live, ...bt];\n    if(!_trades.length) _trades = DEMO_TRADES;\n  } catch(e) {\n    _trades = DEMO_TRADES;\n  }\n\n  buildSummary(_trades);\n  buildEquityChart(_trades);\n  buildDistChart(_trades);\n  renderTrades(_trades);\n  updateMeta(_trades);\n}\n\nfunction updateMeta(trades){\n  if(!trades.length) return;\n  const sorted = [...trades].sort((a,b)=>new Date(a.entry_time)-new Date(b.entry_time));\n  const first  = new Date(sorted[0].entry_time);\n  const last   = new Date(sorted[sorted.length-1].exit_time||sorted[sorted.length-1].entry_time);\n  const days   = Math.round((last-first)/86400000);\n  document.getElementById(\'meta-period\').textContent = `${trades.length} trades · ${days} dias`;\n}\n\n// ── summary ──\nfunction buildSummary(trades){\n  const closed = trades.filter(t=>t.pnl_usdt!=null);\n  const wins   = closed.filter(t=>t.pnl_usdt>0);\n  const losses = closed.filter(t=>t.pnl_usdt<=0);\n  const totalPnl = closed.reduce((a,t)=>a+t.pnl_usdt,0);\n  const gw  = wins.reduce((a,t)=>a+t.pnl_usdt,0);\n  const gl  = Math.abs(losses.reduce((a,t)=>a+t.pnl_usdt,0));\n  const pf  = gl>0 ? gw/gl : Infinity;\n  const wr  = closed.length ? wins.length/closed.length*100 : 0;\n  const lastBal = closed.length ? closed[closed.length-1].balance_after : 1000;\n  const avgW = wins.length ? gw/wins.length : 0;\n  const avgL = losses.length ? gl/losses.length : 0;\n\n  // Max drawdown\n  let peak=1000, maxDD=0, bal=1000;\n  closed.forEach(t=>{bal+=t.pnl_usdt; if(bal>peak)peak=bal; const dd=(peak-bal)/peak*100; if(dd>maxDD)maxDD=dd;});\n\n  const cells = [\n    {lbl:\'PnL Total\',val:(totalPnl>=0?\'+\':\'\')+totalPnl.toFixed(2)+\' USDT\',cls:totalPnl>=0?\'pos\':\'neg\',sub:\'desde o início\'},\n    {lbl:\'Win Rate\',val:wr.toFixed(1)+\'%\',cls:wr>=50?\'pos\':\'neg\',sub:`${wins.length}W / ${losses.length}L`},\n    {lbl:\'Profit Factor\',val:pf>999?\'∞\':pf.toFixed(2),cls:pf>1?\'pos\':\'neg\',sub:\'gross win / gross loss\'},\n    {lbl:\'Saldo Final\',val:\'$\'+lastBal.toFixed(2),cls:\'cyan\',sub:\'capital inicial: $1000\'},\n    {lbl:\'Avg Win\',val:\'+\'+avgW.toFixed(2)+\' USDT\',cls:\'pos\',sub:\'por trade vencedor\'},\n    {lbl:\'Avg Loss\',val:\'-\'+avgL.toFixed(2)+\' USDT\',cls:\'neg\',sub:\'por trade perdedor\'},\n    {lbl:\'Max Drawdown\',val:maxDD.toFixed(2)+\'%\',cls:\'neg\',sub:\'pior sequência\'},\n    {lbl:\'Total Trades\',val:closed.length,cls:\'gold\',sub:\'fechados\'},\n  ];\n\n  document.getElementById(\'summary\').innerHTML = cells.map(c=>\n    `<div class="sum-cell ${c.cls}">\n      <div class="sum-lbl">${c.lbl}</div>\n      <div class="sum-val ${c.cls}">${c.val}</div>\n      <div class="sum-sub">${c.sub}</div>\n    </div>`\n  ).join(\'\');\n}\n\n// ── equity chart ──\nfunction buildEquityChart(trades){\n  const closed = [...trades].filter(t=>t.pnl_usdt!=null)\n    .sort((a,b)=>new Date(a.entry_time)-new Date(b.entry_time));\n\n  let bal = 1000;\n  const pts = [{x:new Date(closed[0]?.entry_time||Date.now()).toISOString(), y:bal}];\n  closed.forEach(t=>{\n    bal += t.pnl_usdt;\n    pts.push({x:new Date(t.exit_time||t.entry_time).toISOString(), y:+bal.toFixed(2)});\n  });\n\n  const ctx = document.getElementById(\'equityChart\').getContext(\'2d\');\n  if(_equityChart) _equityChart.destroy();\n\n  const grad = ctx.createLinearGradient(0,0,0,220);\n  grad.addColorStop(0,\'rgba(34,229,255,.25)\');\n  grad.addColorStop(1,\'rgba(34,229,255,.01)\');\n\n  _equityChart = new Chart(ctx, {\n    type:\'line\',\n    data:{datasets:[{\n      label:\'Equity\',\n      data: pts.map(p=>({x:p.x, y:p.y})),\n      borderColor:\'#22e5ff\',\n      backgroundColor: grad,\n      borderWidth:2,\n      pointRadius:0,\n      pointHoverRadius:5,\n      pointHoverBackgroundColor:\'#22e5ff\',\n      tension:.35,\n      fill:true,\n    }]},\n    options:{\n      responsive:true, maintainAspectRatio:false,\n      interaction:{mode:\'index\',intersect:false},\n      plugins:{\n        legend:{display:false},\n        tooltip:{\n          backgroundColor:\'#0f1319\',\n          borderColor:\'#1c2433\',\n          borderWidth:1,\n          titleColor:\'#5a7899\',\n          bodyColor:\'#e8f0fa\',\n          titleFont:{family:\'JetBrains Mono\',size:11},\n          bodyFont:{family:\'JetBrains Mono\',size:12},\n          callbacks:{label:c=>\' $\'+c.raw.y.toFixed(2)}\n        }\n      },\n      scales:{\n        x:{type:\'time\',time:{unit:\'day\',displayFormats:{day:\'dd/MM\'}},\n          grid:{color:\'rgba(28,36,51,.8)\',drawBorder:false},\n          ticks:{color:\'#334a66\',font:{family:\'JetBrains Mono\',size:10}}},\n        y:{position:\'right\',\n          grid:{color:\'rgba(28,36,51,.8)\',drawBorder:false},\n          ticks:{color:\'#334a66\',font:{family:\'JetBrains Mono\',size:10},\n            callback:v=>\'$\'+v.toFixed(0)}}\n      }\n    }\n  });\n}\n\n// ── dist chart ──\nfunction buildDistChart(trades){\n  const closed = trades.filter(t=>t.pnl_usdt!=null);\n  const pnls   = closed.map(t=>t.pnl_usdt).sort((a,b)=>a-b);\n  const colors = pnls.map(p=>p>=0?\'rgba(0,217,126,.7)\':\'rgba(255,61,90,.7)\');\n  const borders= pnls.map(p=>p>=0?\'#00d97e\':\'#ff3d5a\');\n  const labels = closed.map((_,i)=>\'#\'+(i+1));\n\n  const ctx = document.getElementById(\'distChart\').getContext(\'2d\');\n  if(_distChart) _distChart.destroy();\n  _distChart = new Chart(ctx, {\n    type:\'bar\',\n    data:{\n      labels,\n      datasets:[{\n        data:pnls,\n        backgroundColor:colors,\n        borderColor:borders,\n        borderWidth:1,\n        borderRadius:3,\n      }]\n    },\n    options:{\n      responsive:true, maintainAspectRatio:false,\n      plugins:{\n        legend:{display:false},\n        tooltip:{\n          backgroundColor:\'#0f1319\',\n          borderColor:\'#1c2433\',\n          borderWidth:1,\n          titleColor:\'#5a7899\',\n          bodyColor:\'#e8f0fa\',\n          titleFont:{family:\'JetBrains Mono\',size:11},\n          bodyFont:{family:\'JetBrains Mono\',size:12},\n          callbacks:{label:c=>(c.raw>=0?\' +\':\' \')+c.raw.toFixed(4)+\' USDT\'}\n        }\n      },\n      scales:{\n        x:{display:false},\n        y:{position:\'right\',\n          grid:{color:\'rgba(28,36,51,.8)\',drawBorder:false},\n          ticks:{color:\'#334a66\',font:{family:\'JetBrains Mono\',size:10},\n            callback:v=>(v>=0?\'+\':\'\')+v.toFixed(1)}}\n      }\n    }\n  });\n}\n\n// ── filter ──\nfunction setFilter(f, el){\n  _filter = f;\n  document.querySelectorAll(\'.pill\').forEach(p=>p.classList.remove(\'active\'));\n  el.classList.add(\'active\');\n  renderTrades(_trades);\n}\n\n// ── render trades ──\nfunction renderTrades(trades){\n  const closed = trades.filter(t=>t.pnl_usdt!=null)\n    .sort((a,b)=>new Date(b.entry_time)-new Date(a.entry_time));\n\n  let filtered = closed;\n  if(_filter===\'long\')  filtered = closed.filter(t=>t.action===\'BUY\');\n  if(_filter===\'short\') filtered = closed.filter(t=>t.action===\'SELL\');\n  if(_filter===\'win\')   filtered = closed.filter(t=>t.pnl_usdt>0);\n  if(_filter===\'loss\')  filtered = closed.filter(t=>t.pnl_usdt<=0);\n\n  document.getElementById(\'trade-count\').textContent = filtered.length+\' trades\';\n\n  if(!filtered.length){\n    document.getElementById(\'trades-list\').innerHTML = `\n      <div class="empty">\n        <div class="empty-icon">📭</div>\n        <div class="empty-text">Nenhum trade encontrado</div>\n        <div class="empty-sub">Execute um backtest ou aguarde trades ao vivo</div>\n      </div>`;\n    return;\n  }\n\n  document.getElementById(\'trades-list\').innerHTML = filtered.map((t,idx)=>{\n    const isLong   = t.action===\'BUY\';\n    const isWin    = t.pnl_usdt>0;\n    const dirCls   = isLong?\'long\':\'short\';\n    const winCls   = isWin?\'win\':\'loss\';\n    const pnlSign  = t.pnl_usdt>=0?\'+\':\'\';\n    const pctSign  = t.pnl_pct>=0?\'+\':\'\';\n    const pnlColor = isWin?\'pos\':\'neg\';\n    const entryFmt = fmtDt(t.entry_time);\n    const exitFmt  = t.exit_time ? fmtDt(t.exit_time) : \'—\';\n    const dur      = t.exit_time ? fmtDur(t.entry_time,t.exit_time) : \'—\';\n    const reason   = reasonTag(t.exit_reason);\n    const change   = isLong\n      ? ((t.exit_price - t.entry_price)/t.entry_price*100)\n      : ((t.entry_price - t.exit_price)/t.entry_price*100);\n    const arrowCls = isWin?\'pos\':\'neg\';\n\n    return `\n    <div class="trade-card ${dirCls}-card ${winCls}" onclick="toggleExpand(this)">\n      <div class="trade-main">\n        <div class="tc-num">${filtered.length-idx}</div>\n        <div><span class="tc-dir ${dirCls}">${isLong?\'▲ LONG\':\'▼ SHORT\'}</span></div>\n        <div class="tc-field">\n          <div class="tc-lbl">Entrada</div>\n          <div class="tc-val">$${t.entry_price.toFixed(2)}</div>\n          <div class="tc-lbl" style="margin-top:3px">${entryFmt}</div>\n        </div>\n        <div class="tc-field">\n          <div class="tc-lbl">Saída</div>\n          <div class="tc-val">$${t.exit_price?t.exit_price.toFixed(2):\'—\'}</div>\n          <div class="tc-lbl" style="margin-top:3px">${exitFmt}</div>\n        </div>\n        <div class="tc-field">\n          <div class="tc-lbl">Duração</div>\n          <div class="tc-val">${dur}</div>\n          <div style="margin-top:4px">${reason}</div>\n        </div>\n        <div class="tc-field">\n          <div class="tc-lbl">Qty ETH</div>\n          <div class="tc-val">${(t.qty||0).toFixed(4)}</div>\n        </div>\n        <div class="tc-field" style="text-align:right">\n          <div class="tc-pnl">${pnlSign}${t.pnl_usdt.toFixed(2)}<span style="font-size:.65rem;font-family:JetBrains Mono"> USDT</span></div>\n          <div class="tc-pnl-pct ${pnlColor}">${pctSign}${(t.pnl_pct||change).toFixed(2)}%</div>\n        </div>\n      </div>\n      <div class="trade-expand">\n        <div class="price-path">\n          <div class="pp-entry">\n            <div class="pp-lbl">${isLong?\'▲ COMPRA\':\'▼ VENDA\'}</div>\n            <div class="pp-price">$${t.entry_price.toFixed(2)}</div>\n            <div class="pp-time">${fmtDtFull(t.entry_time)}</div>\n          </div>\n          <div class="pp-arrow">\n            <div class="pp-pnl-big ${arrowCls}">${pnlSign}${t.pnl_usdt.toFixed(4)} USDT</div>\n            <div class="pp-line ${arrowCls}"></div>\n            <div class="pp-dur">${dur} &nbsp;·&nbsp; ${pctSign}${(t.pnl_pct||change).toFixed(3)}%</div>\n          </div>\n          <div class="pp-exit" style="text-align:right">\n            <div class="pp-lbl">${isLong?\'▼ VENDA\':\'▲ COMPRA\'}</div>\n            <div class="pp-price">$${t.exit_price?(t.exit_price.toFixed(2)):\'—\'}</div>\n            <div class="pp-time">${t.exit_time?fmtDtFull(t.exit_time):\'aberto\'}</div>\n          </div>\n        </div>\n        <div class="expand-grid" style="margin-top:10px">\n          <div class="expand-cell">\n            <div class="expand-lbl">Variação de Preço</div>\n            <div class="expand-val" style="color:${isWin?\'var(--long)\':\'var(--short)\'}">\n              ${isLong?\'\':\'\'}${t.exit_price?(t.exit_price-t.entry_price>=0?\'+\':\'\')+(t.exit_price-t.entry_price).toFixed(2):\'—\'} USDT\n            </div>\n          </div>\n          <div class="expand-cell">\n            <div class="expand-lbl">Saldo Pós-Trade</div>\n            <div class="expand-val" style="color:var(--cyan)">$${(t.balance_after||0).toFixed(2)}</div>\n          </div>\n          <div class="expand-cell">\n            <div class="expand-lbl">Modo</div>\n            <div class="expand-val" style="color:${t.mode===\'paper\'?\'#9b6bff\':\'var(--long)\'}">\n              ${t.mode===\'paper\'?\'📄 PAPER\':\'💰 LIVE\'}\n            </div>\n          </div>\n          <div class="expand-cell">\n            <div class="expand-lbl">Motivo de Saída</div>\n            <div class="expand-val">${t.exit_reason||\'—\'}</div>\n          </div>\n        </div>\n      </div>\n    </div>`;\n  }).join(\'\');\n}\n\nfunction toggleExpand(el){ el.classList.toggle(\'expanded\'); }\n\nfunction reasonTag(r){\n  if(!r||r===\'—\') return \'\';\n  r=r.toUpperCase();\n  if(r.includes(\'TRAIL\')) return \'<span class="reason-tag trail">TRAIL</span>\';\n  if(r.includes(\'SL\'))    return \'<span class="reason-tag sl">SL</span>\';\n  if(r.includes(\'REV\'))   return \'<span class="reason-tag rev">REVERSAL</span>\';\n  return `<span class="reason-tag other">${r}</span>`;\n}\n\nfunction fmtDt(iso){\n  if(!iso) return \'—\';\n  const d=new Date(iso);\n  return d.toLocaleDateString(\'pt-BR\',{day:\'2-digit\',month:\'2-digit\'})+\' \'+\n         d.toLocaleTimeString(\'pt-BR\',{hour:\'2-digit\',minute:\'2-digit\'});\n}\n\nfunction fmtDtFull(iso){\n  if(!iso) return \'—\';\n  const d=new Date(iso);\n  return d.toLocaleDateString(\'pt-BR\',{day:\'2-digit\',month:\'2-digit\',year:\'2-digit\'})+\' \'+\n         d.toLocaleTimeString(\'pt-BR\',{hour:\'2-digit\',minute:\'2-digit\',second:\'2-digit\'});\n}\n\nfunction fmtDur(a,b){\n  const ms=new Date(b)-new Date(a);\n  if(ms<0) return \'—\';\n  const h=Math.floor(ms/3600000), m=Math.floor((ms%3600000)/60000);\n  if(h>=24){const d=Math.floor(h/24);return d+\'d \'+(h%24)+\'h\';}\n  return h+\'h \'+m+\'m\';\n}\n\ninit();\n</script>\n</body>\n</html>\n'

@app.route('/report')
def report_page():
    """Dashboard visual de backtest e histórico — estilo TradingView."""
    return REPORT_HTML


def _delayed_start():
    time.sleep(5)
    if PAPER_TRADING:
        log.info("📄 PAPER TRADING ativado — auto-start...")
        with _lock:
            if _trader is None:
                threading.Thread(target=_thread, daemon=True).start()
        return
    if not _creds_ok():
        log.warning("⚠️ Chaves OKX não encontradas — use o botão Iniciar.")
        return
    with _lock:
        if _trader is not None:
            return
        log.info("🚀 Chaves OK — auto-start...")
        threading.Thread(target=_thread, daemon=True).start()

threading.Thread(target=_delayed_start, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0',
            port=int(os.environ.get("PORT", 5000)),
            debug=False)
