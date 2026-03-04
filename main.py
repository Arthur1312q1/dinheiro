"""
AZLEMA Live Trading — Bitget ETH-USDT-SWAP Futures 1x
Render: configurar BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE

Modo de operação selecionável via dashboard:
  - PAPER TRADING : simula trades com saldo falso (sem risco)
  - LIVE TRADING  : opera com 95% do saldo real na Bitget
"""
import os, hmac, hashlib, base64, json, time, threading, traceback, logging, requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from pathlib import Path
from flask import Flask, jsonify, request as flask_request

# ── Fuso horário Brasil (Brasília, UTC-3, sem horário de verão) ───────────────
BRT = timezone(timedelta(hours=-3))

def brazil_now() -> datetime:
    """Retorna datetime atual no horário de Brasília."""
    return datetime.now(BRT)

def brazil_iso() -> str:
    """Retorna ISO string no horário de Brasília (sem microsegundos)."""
    return brazil_now().strftime('%Y-%m-%dT%H:%M:%S')

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import DataCollector

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('azlema')

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL    = "ETH-USDT"          # Bitget symbol
SYMBOL_ID = "ETHUSDT"              # Bitget v2 symbol (usdt-futures)
TIMEFRAME = "30m"
TOTAL_CANDLES  = 300
WARMUP_CANDLES = 300
STRATEGY_CONFIG = {
    "adaptive_method": "Cos IFM", "threshold": 0.0,
    "fixed_sl_points": 2000, "fixed_tp_points": 55, "trail_offset": 15,
    "risk_percent": 0.01, "tick_size": 0.01, "initial_capital": 1000.0,
    "max_lots": 100, "default_period": 20, "warmup_bars": WARMUP_CANDLES,
}

# ── Modo de operação (mutável via dashboard) ──────────────────────────────────
# True = Paper Trading (saldo falso) | False = Live Trading (Bitget real, 95% saldo)
_PAPER_TRADING   = os.environ.get("PAPER_TRADING", "true").lower() in ("true", "1", "yes")
PAPER_BALANCE    = float(os.environ.get("PAPER_BALANCE", "1000.0"))
LIVE_PCT         = 0.95         # Usa 95% do saldo real em live
HISTORY_FILE     = "trades_history.json"
BACKTEST_HISTORY_FILE = "backtest_history.json"

def get_paper_mode() -> bool:
    return _PAPER_TRADING

def set_paper_mode(val: bool):
    global _PAPER_TRADING
    _PAPER_TRADING = val

def _key():      return os.environ.get("BITGET_API_KEY",    "").strip()
def _sec():      return os.environ.get("BITGET_SECRET_KEY", "").strip()
def _pass():     return os.environ.get("BITGET_PASSPHRASE", "").strip()
def _creds_ok(): return bool(_key() and _sec() and _pass())


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
class TradeHistoryManager:
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


history_mgr  = TradeHistoryManager(HISTORY_FILE)
backtest_mgr = TradeHistoryManager(BACKTEST_HISTORY_FILE)


# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADER
# ═══════════════════════════════════════════════════════════════════════════════
class PaperTrader:
    def __init__(self, initial_balance: float = PAPER_BALANCE):
        self.balance   = initial_balance
        self.position  = None
        self._trade_id = 0
        log.info(f"📄 PAPER TRADING ativo | Saldo inicial: {initial_balance:.2f} USDT")

    def _new_id(self) -> str:
        self._trade_id += 1
        return f"PAPER-{self._trade_id:05d}"

    def open_long(self, qty, bal_usdt=0, px=0):
        trade_id = self._new_id()
        ts = brazil_iso()
        history_mgr.add_trade({
            "id": trade_id, "action": "BUY", "status": "open",
            "entry_time": ts, "entry_price": px,
            "qty": qty, "balance": self.balance, "mode": "paper",
        })
        self.position = {"side": "long", "size": qty, "avg_px": px, "id": trade_id}
        log.info(f"  📄 PAPER LONG aberto | px={px:.2f} qty={qty:.4f}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def open_short(self, qty, bal_usdt=0, px=0):
        trade_id = self._new_id()
        ts = brazil_iso()
        history_mgr.add_trade({
            "id": trade_id, "action": "SELL", "status": "open",
            "entry_time": ts, "entry_price": px,
            "qty": qty, "balance": self.balance, "mode": "paper",
        })
        self.position = {"side": "short", "size": qty, "avg_px": px, "id": trade_id}
        log.info(f"  📄 PAPER SHORT aberto | px={px:.2f} qty={qty:.4f}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def close_long(self, qty, exit_px=0, reason="EXIT"):
        if not self.position or self.position["side"] != "long":
            return {"code": "0"}
        entry_px = self.position["avg_px"]
        pnl      = (exit_px - entry_px) * qty
        self.balance += pnl
        ts = brazil_iso()
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
        ts = brazil_iso()
        history_mgr.close_trade(self.position["id"], exit_px, ts, reason, pnl)
        log.info(f"  📄 PAPER SHORT fechado | px={exit_px:.2f} pnl={pnl:+.4f} USDT")
        self.position = None
        return {"code": "0"}

    def get_position(self): return self.position
    def get_balance(self):  return self.balance


# ═══════════════════════════════════════════════════════════════════════════════
# BITGET CLIENT (real trading — 95% do saldo)
# ═══════════════════════════════════════════════════════════════════════════════
class Bitget:
    """
    Cliente Bitget API v2 (Mix USDT Futures perpetuo).
    """
    BASE         = "https://api.bitget.com"
    SYMBOL       = "ETHUSDT"
    PRODUCT_TYPE = "usdt-futures"
    MARGIN       = "USDT"
    CT_VAL       = 0.001

    def _sign(self, ts, method, path, body=""):
        msg = ts + method.upper() + path + body
        return base64.b64encode(
            hmac.new(_sec().encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()

    def _headers(self, method, path, body=""):
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY":        _key(),
            "ACCESS-SIGN":       self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP":  ts,
            "ACCESS-PASSPHRASE": _pass(),
            "Content-Type":      "application/json",
            "locale":            "en-US",
        }

    def _get(self, path, params=None):
        qs = ("?" + "&".join(f"{k}={v}" for k,v in params.items())) if params else ""
        r  = requests.get(self.BASE+path+qs, headers=self._headers("GET",path+qs), timeout=10)
        return r.json()

    def _post(self, path, body):
        b = json.dumps(body)
        r = requests.post(self.BASE+path, headers=self._headers("POST",path,b), data=b, timeout=10)
        return r.json()

    def mark_price(self):
        try:
            r = self._get("/api/v2/mix/market/symbol-price",
                          {"symbol": self.SYMBOL, "productType": self.PRODUCT_TYPE})
            return float(r["data"][0]["markPrice"])
        except:
            pass
        try:
            r = self._get("/api/v2/mix/market/ticker",
                          {"symbol": self.SYMBOL, "productType": self.PRODUCT_TYPE})
            return float(r["data"][0]["lastPr"])
        except:
            return 0.0

    def balance(self):
        try:
            r = self._get("/api/v2/mix/account/account",
                          {"symbol": self.SYMBOL,
                           "productType": self.PRODUCT_TYPE,
                           "marginCoin": self.MARGIN})
            return float(r["data"].get("available", 0) or 0)
        except Exception as e:
            log.error(f"  Bitget balance erro: {e}")
        return 0.0

    def position(self):
        try:
            r = self._get("/api/v2/mix/position/all-position",
                          {"productType": self.PRODUCT_TYPE, "marginCoin": self.MARGIN})
            for p in r.get("data", []):
                if p.get("symbol") == self.SYMBOL:
                    sz = float(p.get("total", 0))
                    if sz > 0:
                        return {"side": p.get("holdSide","long"), "size": sz,
                                "avg_px": float(p.get("openPriceAvg", 0))}
        except:
            pass
        return None

    def _cts(self, qty_eth, bal=0, px=0):
        cts = max(1, int(qty_eth / self.CT_VAL))
        if bal > 0 and px > 0:
            cts = min(cts, max(1, int((bal * LIVE_PCT) / (self.CT_VAL * px))))
        return cts

    def _order(self, side, trade_side, sz):
        body = {
            "symbol":      self.SYMBOL,
            "productType": self.PRODUCT_TYPE,
            "marginMode":  "crossed",
            "marginCoin":  self.MARGIN,
            "size":        str(sz),
            "side":        side,
            "tradeSide":   trade_side,
            "orderType":   "market",
        }
        r  = self._post("/api/v2/mix/order/place-order", body)
        d0 = r.get("data") or {}
        if r.get("code") == "00000":
            log.info(f"  ✅ ORDER {side}/{trade_side} sz={sz} ordId={d0.get('orderId','?')}")
        else:
            log.error(f"  ❌ ORDER {side}/{trade_side} sz={sz} code={r.get('code','')} msg={r.get('msg','')}")
        return r

    def open_long(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        r  = self._order("buy", "open", sz)
        if r.get("code") == "00000":
            oid = (r.get("data") or {}).get("orderId", "?")
            history_mgr.add_trade({"id": str(oid), "action": "BUY", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": qty, "balance": bal, "mode": "live"})
        return r, qty

    def open_short(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        r  = self._order("sell", "open", sz)
        if r.get("code") == "00000":
            oid = (r.get("data") or {}).get("orderId", "?")
            history_mgr.add_trade({"id": str(oid), "action": "SELL", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": qty, "balance": bal, "mode": "live"})
        return r, qty

    def close_long(self, qty, exit_px=0, reason="EXIT"):
        sz = self._cts(qty)
        r  = self._order("sell", "close", sz)
        if r.get("code") == "00000":
            ts = brazil_iso()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "BUY" and t.get("status") == "open":
                    pnl = (exit_px - t.get("entry_price", exit_px)) * qty
                    history_mgr.close_trade(t["id"], exit_px, ts, reason, pnl)
                    break
        return r

    def close_short(self, qty, exit_px=0, reason="EXIT"):
        sz = self._cts(qty)
        r  = self._order("buy", "close", sz)
        if r.get("code") == "00000":
            ts = brazil_iso()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "SELL" and t.get("status") == "open":
                    pnl = (t.get("entry_price", exit_px) - exit_px) * qty
                    history_mgr.close_trade(t["id"], exit_px, ts, reason, pnl)
                    break
        return r

    def ct_val(self):
        return self.CT_VAL

    def setup(self):
        for hold in ("long", "short"):
            try:
                r = self._post("/api/v2/mix/account/set-leverage", {
                    "symbol": self.SYMBOL, "productType": self.PRODUCT_TYPE,
                    "marginCoin": self.MARGIN, "leverage": "1", "holdSide": hold})
                if r.get("code") == "00000":
                    log.info(f"  Alavancagem 1x ({hold})")
                else:
                    log.warning(f"  setLeverage {hold}: {r.get('msg','')}")
            except Exception as e:
                log.warning(f"  setup leverage {hold}: {e}")
        bal = self.balance()
        px  = self.mark_price()
        log.info(f"  Bitget v2 | Saldo: {bal:.4f} USDT | Preco: {px:.2f}")
        return bal > 0 or px > 0

class LiveTrader:
    def __init__(self):
        self._paper_mode = get_paper_mode()
        if self._paper_mode:
            self.paper = PaperTrader(PAPER_BALANCE)
            self.bitget = None
        else:
            self.paper  = None
            self.bitget = Bitget()

        self.strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        self._running = False
        self._warming = False
        self.log: List[Dict] = []
        self._pnl_baseline   = 0.0
        self._cache_pos: Optional[Dict] = None
        self._cache_bal: float = PAPER_BALANCE if self._paper_mode else 0.0
        self._cache_px:  float = 0.0
        self._last_candle_ts:    str = ""
        self._last_order_candle: str = ""

    def _is_paper(self) -> bool:
        return self._paper_mode

    def _mark_price(self) -> float:
        try:
            r = requests.get(
                "https://api.bitget.com/api/v2/mix/market/symbol-price",
                params={"symbol": "ETHUSDT", "productType": "usdt-futures"},
                timeout=10
            ).json()
            return float(r["data"][0]["markPrice"])
        except:
            return self._cache_px

    def _qty(self) -> float:
        bal = self._cache_bal
        px  = self._cache_px
        ct  = 0.001
        if bal <= 0:
            log.warning(f"  ⚠️ _qty: saldo inválido bal={bal}")
            return 0.0
        if px <= 0:
            px = self._mark_price()
            if px <= 0:
                log.warning(f"  ⚠️ _qty: preço inválido px={px} — sem trade")
                return 0.0
            self._cache_px = px
        pct       = 1.0 if self._is_paper() else LIVE_PCT
        usdt_disp = bal * pct * 0.90
        cts       = max(1, int(usdt_disp / (ct * px)))
        qty       = cts * ct
        log.info(f"  💰 qty={qty:.6f} ETH ({cts} cts) | bal={bal:.2f} | px={px:.2f} | "
                 f"{'PAPER' if self._is_paper() else 'LIVE 95%'}")
        return qty

    def _add_log(self, action, price, qty, reason=""):
        self.log.append({
            "time":   brazil_iso(),
            "action": action,
            "price":  price,
            "qty":    qty,
            "reason": reason,
        })

    def warmup(self, df: pd.DataFrame):
        self._warming = True
        log.info(f"🔄 Warmup: {len(df)} candles...")
        for _, row in df.iterrows():
            self.strategy.next({
                'open':      float(row['open']),
                'high':      float(row['high']),
                'low':       float(row['low']),
                'close':     float(row['close']),
                'timestamp': row.get('timestamp', 0),
                'index':     int(row.get('index', 0)),
            })
        self._pnl_baseline = self.strategy.net_profit
        self._warming      = False
        # Inicializa _cache_px com o último close do warmup como fallback seguro
        last_close = float(df['close'].iloc[-1])
        if last_close > 0:
            self._cache_px = last_close
        self._refresh_cache()
        # NÃO seta _last_candle_ts aqui — o timestamp OKX difere do Bitget
        # e causaria o primeiro candle real ser descartado como duplicado
        self._last_candle_ts = ""
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} | "
                 f"EC={self.strategy.EC:.2f} | px_cache={self._cache_px:.2f}")

    @property
    def live_pnl(self):
        return self.strategy.net_profit - self._pnl_baseline

    def process(self, candle: Dict):
        ts       = candle.get('timestamp', brazil_now())
        close_px = float(candle['close'])

        # NÃO sobrescreve _cache_px com preço histórico do candle fechado.
        # _cache_px é atualizado por _refresh_cache() com o mark price atual.
        # Usa close_px apenas como fallback se o cache ainda está vazio.
        if self._cache_px <= 0 and close_px > 0:
            self._cache_px = close_px

        log.info(f"\n── {ts} | C={close_px:.2f} | bal={self._cache_bal:.2f} | "
                 f"strat_pos={self.strategy.position_size:+.4f} | "
                 f"pos={self._cache_pos} | "
                 f"paper={'SIM' if self._is_paper() else 'NAO'}")

        actions = self.strategy.next(candle) or []
        # 'real' rastreia a posição atual ao longo do loop.
        # Para paper: autoritativa é self.paper.get_position() (sempre fresco).
        # Para live:  autoritativa é real (atualizado via API e localmente).
        real = self._cache_pos

        log.info(f"  📊 Estratégia: {len(actions)} sinal(is): "
                 f"{[a.get('action','?') for a in actions]}")

        for act in actions:
            kind = act.get('action', '')

            # ── EXIT LONG ────────────────────────────────────────────────────
            if kind == 'EXIT_LONG':
                reason = act.get('exit_reason', 'EXIT')

                if self._is_paper():
                    pos = self.paper.get_position()          # sempre fresco
                    if pos and pos['side'] == 'long':
                        # Usa preço exato do stop/trail da estratégia (simulação realista)
                        _exit_px = act.get('price') or (self._cache_px if self._cache_px > 0 else close_px)
                        self.paper.close_long(pos['size'], _exit_px, reason)
                        self._add_log("EXIT_LONG", _exit_px, pos['size'], reason)
                        real = None
                        self._cache_pos = None
                    else:
                        log.warning(f"  ⚠️ EXIT_LONG ignorado — paper pos={pos}")

                else:  # LIVE
                    # Se cache estiver desatualizado, re-consulta Bitget antes de desistir
                    if not (real and real['side'] == 'long'):
                        log.info("  🔄 EXIT_LONG: real stale/None — re-query Bitget...")
                        real = self.bitget.position()
                        self._cache_pos = real

                    if real and real['side'] == 'long':
                        # real['size'] vem da API = contratos
                        qty = real['size'] * self.bitget.ct_val()
                        _exit_px = self._cache_px if self._cache_px > 0 else close_px
                        self.bitget.close_long(qty, _exit_px, reason)
                        self._add_log("EXIT_LONG", _exit_px, qty, reason)
                        real = None
                        self._cache_pos = None
                    else:
                        log.warning(f"  ⚠️ EXIT_LONG ignorado — live pos={real}")

            # ── EXIT SHORT ───────────────────────────────────────────────────
            elif kind == 'EXIT_SHORT':
                reason = act.get('exit_reason', 'EXIT')

                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        _exit_px = act.get('price') or (self._cache_px if self._cache_px > 0 else close_px)
                        self.paper.close_short(pos['size'], _exit_px, reason)
                        self._add_log("EXIT_SHORT", _exit_px, pos['size'], reason)
                        real = None
                        self._cache_pos = None
                    else:
                        log.warning(f"  ⚠️ EXIT_SHORT ignorado — paper pos={pos}")

                else:  # LIVE
                    if not (real and real['side'] == 'short'):
                        log.info("  🔄 EXIT_SHORT: real stale/None — re-query Bitget...")
                        real = self.bitget.position()
                        self._cache_pos = real

                    if real and real['side'] == 'short':
                        qty = real['size'] * self.bitget.ct_val()
                        _exit_px = self._cache_px if self._cache_px > 0 else close_px
                        self.bitget.close_short(qty, _exit_px, reason)
                        self._add_log("EXIT_SHORT", _exit_px, qty, reason)
                        real = None
                        self._cache_pos = None
                    else:
                        log.warning(f"  ⚠️ EXIT_SHORT ignorado — live pos={real}")

            # ── ENTER LONG (BUY) ─────────────────────────────────────────────
            elif kind == 'BUY':
                if self._last_order_candle == str(ts):
                    log.info("  ⏭️ BUY ignorado — já operou neste candle")
                    continue
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️ BUY ignorado — qty=0 (saldo ou preço inválido)")
                    continue

                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        # Fecha short antes de abrir long (reversão)
                        _rev_px = act.get('price') or (self._cache_px if self._cache_px > 0 else close_px)
                        self.paper.close_short(pos['size'], _rev_px, "REVERSAL")
                        real = None
                        self._cache_pos = None
                    px = self._cache_px if self._cache_px > 0 else close_px
                    log.info(f"  🟢 [PAPER] ENTER LONG {qty:.6f} ETH @ {px:.2f}")
                    r, qty_filled = self.paper.open_long(qty, self._cache_bal, px)
                    if r.get("code") == "0":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_LONG", px, qty_filled)
                        real = {'side': 'long', 'size': qty_filled, 'avg_px': px}
                        self._cache_pos = real

                else:  # LIVE
                    if real and real['side'] == 'short':
                        # Fecha short via reversão
                        _rev_px = self._cache_px if self._cache_px > 0 else close_px
                        self.bitget.close_short(real['size'] * self.bitget.ct_val(), _rev_px, "REVERSAL")
                        real = None
                        self._cache_pos = None
                    bal = self._cache_bal
                    px  = self._cache_px if self._cache_px > 0 else close_px
                    log.info(f"  🟢 LIVE ENTER LONG {qty:.6f} ETH @ {px:.2f} (95% saldo)")
                    r, qty_filled = self.bitget.open_long(qty, bal, px)
                    if r.get("code") == "00000":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_LONG", px, qty_filled)
                        # Converte ETH → contratos para manter consistência com API Bitget
                        cts = max(1, int(qty_filled / self.bitget.ct_val()))
                        real = {'side': 'long', 'size': cts, 'avg_px': px}
                        self._cache_pos = real

            # ── ENTER SHORT (SELL) ───────────────────────────────────────────
            elif kind == 'SELL':
                if self._last_order_candle == str(ts):
                    log.info("  ⏭️ SELL ignorado — já operou neste candle")
                    continue
                qty = self._qty()
                if qty <= 0:
                    log.warning("  ⚠️ SELL ignorado — qty=0 (saldo ou preço inválido)")
                    continue

                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'long':
                        # Fecha long antes de abrir short (reversão)
                        _rev_px = act.get('price') or (self._cache_px if self._cache_px > 0 else close_px)
                        self.paper.close_long(pos['size'], _rev_px, "REVERSAL")
                        real = None
                        self._cache_pos = None
                    px = self._cache_px if self._cache_px > 0 else close_px
                    log.info(f"  🔴 [PAPER] ENTER SHORT {qty:.6f} ETH @ {px:.2f}")
                    r, qty_filled = self.paper.open_short(qty, self._cache_bal, px)
                    if r.get("code") == "0":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_SHORT", px, qty_filled)
                        real = {'side': 'short', 'size': qty_filled, 'avg_px': px}
                        self._cache_pos = real

                else:  # LIVE
                    if real and real['side'] == 'long':
                        # Fecha long via reversão
                        _rev_px = self._cache_px if self._cache_px > 0 else close_px
                        self.bitget.close_long(real['size'] * self.bitget.ct_val(), _rev_px, "REVERSAL")
                        real = None
                        self._cache_pos = None
                    bal = self._cache_bal
                    px  = self._cache_px if self._cache_px > 0 else close_px
                    log.info(f"  🔴 LIVE ENTER SHORT {qty:.6f} ETH @ {px:.2f} (95% saldo)")
                    r, qty_filled = self.bitget.open_short(qty, bal, px)
                    if r.get("code") == "00000":
                        self._last_order_candle = str(ts)
                        self._add_log("ENTER_SHORT", px, qty_filled)
                        cts = max(1, int(qty_filled / self.bitget.ct_val()))
                        real = {'side': 'short', 'size': cts, 'avg_px': px}
                        self._cache_pos = real

        # Persiste cache de posição final para o próximo candle
        self._cache_pos = real

    def _wait(self, tf: int = 30):
        """Aguarda até o fechamento exato do próximo candle (precisão de ms)."""
        now     = brazil_now()
        tf_secs = tf * 60
        elapsed = (now.minute % tf) * 60 + now.second + now.microsecond / 1_000_000
        secs    = tf_secs - elapsed
        # < 1s do fechamento → espera o próximo ciclo completo
        if secs < 1:
            secs += tf_secs
        log.info(f"⏰ Aguardando {secs:.1f}s até próximo close ({tf}m)...")
        time.sleep(secs)

    def _candle(self) -> Optional[Dict]:
        # Bitget v2 granularity values
        TF = {"1m":"1m","3m":"3m","5m":"5m","15m":"15m","30m":"30m",
              "1h":"1H","2h":"2H","4h":"4H","6h":"6H","12h":"12H","1d":"1D"}
        tf = TF.get(TIMEFRAME, "30m")
        try:
            r = requests.get(
                "https://api.bitget.com/api/v2/mix/market/candles",
                params={
                    "symbol":      "ETHUSDT",
                    "productType": "usdt-futures",
                    "granularity": tf,
                    "limit":       "3",
                },
                timeout=10,
            ).json()
            if r.get("code") != "00000":
                log.error(f"  ❌ Bitget candles API: code={r.get('code')} msg={r.get('msg')}")
                return None
            data = r.get("data", [])
            if len(data) < 2:
                log.warning(f"  ⚠️ Bitget retornou menos de 2 candles (len={len(data)})")
                return None
            # data[0] = barra atual (aberta), data[1] = última barra fechada
            c = data[1]
            candle = {
                'open':      float(c[1]),
                'high':      float(c[2]),
                'low':       float(c[3]),
                'close':     float(c[4]),
                'timestamp': datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc),
                'index':     self.strategy._bar + 1,
            }
            log.info(f"  🕯️ Candle: O={candle['open']:.2f} H={candle['high']:.2f} "
                     f"L={candle['low']:.2f} C={candle['close']:.2f} @ {candle['timestamp']}")
            return candle
        except Exception as e:
            log.error(f"  ❌ _candle erro: {e}")
            return None

    def run(self, df: pd.DataFrame):
        mode_str = "📄 PAPER" if self._is_paper() else f"💰 LIVE (95% saldo)"
        log.info(f"╔══════════════════════════════╗")
        log.info(f"║  AZLEMA {mode_str}")
        log.info(f"║  ETH-USDT-SWAP · Bitget")
        log.info(f"╚══════════════════════════════╝")

        if not self._is_paper():
            if not _creds_ok():
                log.error("❌ Credenciais Bitget não configuradas"); return
            if not self.bitget.setup():
                log.error("❌ Falha ao conectar na Bitget"); return

        self.warmup(df)
        log.info(f"  ✅ Pronto para receber candles ao vivo da Bitget")
        self._running = True
        tf = int(TIMEFRAME.replace('m','').replace('h','')) * \
             (60 if 'h' in TIMEFRAME else 1)

        while self._running:
            try:
                self._wait(tf)

                # Retry rápido sem sleep fixo — candle disponível quase imediato
                # quando o servidor está na mesma região que a Bitget API
                c = None
                for _attempt in range(10):
                    raw = self._candle()
                    if raw is None:
                        time.sleep(0.2)
                        continue
                    if str(raw['timestamp']) == self._last_candle_ts:
                        # Candle ainda não fechou na API — tenta de novo em 200ms
                        if _attempt < 9:
                            log.info(f"  ⏳ Aguardando novo candle na API (tentativa {_attempt+1}/10)...")
                            time.sleep(0.2)
                            continue
                        log.warning("  ⚠️ Candle não atualizou após 10 tentativas — próximo ciclo")
                        break
                    c = raw
                    break

                if c is None:
                    continue

                ts = str(c['timestamp'])
                log.info(f"  ✅ Novo candle: {ts} (latência: {_attempt} retry(s))")
                self._last_candle_ts = ts
                self._refresh_cache()
                self.process(c)
            except Exception as e:
                log.error(f"❌ Erro no loop principal: {e}\n{traceback.format_exc()}")
                time.sleep(60)
        log.info("🔴 Trader encerrado")

    def _refresh_cache(self):
        if self._is_paper():
            self._cache_bal = self.paper.get_balance()
            px = self._mark_price()
            if px > 0:
                self._cache_px = px          # só atualiza se API retornou preço válido
            self._cache_pos = self.paper.get_position()
            log.info(f"  🔄 cache | bal={self._cache_bal:.2f} px={self._cache_px:.2f} "
                     f"pos={self._cache_pos}")
        else:
            results = {}
            def _fp():
                try:    results['pos'] = self.bitget.position()
                except: results['pos'] = self._cache_pos
            def _fbp():
                try:
                    results['bal'] = self.bitget.balance()
                    results['px']  = self.bitget.mark_price()
                except:
                    results['bal'] = self._cache_bal
                    results['px']  = 0.0
            t1 = threading.Thread(target=_fp, daemon=True)
            t2 = threading.Thread(target=_fbp, daemon=True)
            t1.start(); t2.start()
            t1.join(timeout=5); t2.join(timeout=5)
            self._cache_pos = results.get('pos', self._cache_pos)
            bal = results.get('bal', self._cache_bal)
            px  = results.get('px',  0.0)
            if bal > 0: self._cache_bal = bal
            if px  > 0: self._cache_px  = px

    def stop(self):
        self._running = False


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════
def run_backtest(symbol=SYMBOL, timeframe=TIMEFRAME, limit=500, initial_capital=1000.0,
                 open_fee_pct=0.0, close_fee_pct=0.0) -> Dict:
    log.info(f"🔬 Backtest: {symbol} {timeframe} {limit} candles | "
             f"taxas: abertura={open_fee_pct}% fechamento={close_fee_pct}%")
    try:
        dc_sym = symbol if '-' in symbol else symbol
        df = DataCollector(symbol=dc_sym, timeframe=timeframe, limit=limit).fetch_ohlcv()
        if df.empty:
            return {"error": "Sem dados"}

        from backtest.engine import BacktestEngine
        cfg = dict(STRATEGY_CONFIG)
        cfg["initial_capital"] = initial_capital
        cfg["warmup_bars"]     = min(50, limit // 5)
        strategy = AdaptiveZeroLagEMA(**cfg)
        engine   = BacktestEngine(strategy, df,
                                  open_fee_pct=open_fee_pct,
                                  close_fee_pct=close_fee_pct)
        results  = engine.run()

        closed     = results.get("closed_trades", [])
        fees_on    = results.get("fees_enabled", False)
        pnl_key    = "pnl_net" if fees_on else "pnl_usdt"
        gw = sum(t[pnl_key] for t in closed if t.get(pnl_key, 0) > 0)
        gl = abs(sum(t[pnl_key] for t in closed if t.get(pnl_key, 0) < 0))

        record = {
            "id":             brazil_iso(),
            "symbol":         symbol,
            "timeframe":      timeframe,
            "candles":        limit,
            "capital":        initial_capital,
            "open_fee_pct":   open_fee_pct,
            "close_fee_pct":  close_fee_pct,
            "total_fees_paid": round(results.get("total_fees_paid", 0), 4),
            "total_pnl":      round(results.get("total_pnl_usdt", 0), 4),
            "final_bal":      round(results.get("final_balance", 0), 4),
            "win_rate":       round(results.get("win_rate", 0), 2),
            "total_trades":   results.get("total_trades", 0),
            "max_drawdown":   round(results.get("max_drawdown", 0), 4),
            "sharpe":         round(results.get("sharpe", 0), 4),
            "profit_factor":  round(gw / gl, 3) if gl > 0 else float("inf"),
            "fees_enabled":   fees_on,
            "trades":         closed,
        }

        data = backtest_mgr._load()
        data.setdefault("trades", [])
        data.setdefault("sessions", [])
        data["sessions"].append(record)
        backtest_mgr._data = data
        backtest_mgr._save()

        log.info(f"  ✅ BT OK | PnL={record['total_pnl']:.2f} WR={record['win_rate']:.1f}%")
        return record
    except Exception as e:
        log.error(f"❌ Backtest: {e}\n{traceback.format_exc()}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════
app   = Flask(__name__)
_trader: Optional[LiveTrader] = None
_lock  = threading.Lock()
_logs: List[str] = []

class _LogCap(logging.Handler):
    def emit(self, r):
        _logs.append(self.format(r))
        if len(_logs) > 300: _logs.pop(0)

class _BRTFormatter(logging.Formatter):
    """Formatter que exibe hora no fuso de Brasília (UTC-3)."""
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tz=BRT)
        return ct.strftime(datefmt or '%H:%M:%S')

_lh = _LogCap()
_lh.setFormatter(_BRTFormatter('%(asctime)s %(message)s', '%H:%M:%S'))
log.addHandler(_lh)

DASH = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AZLEMA Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg:#080c14; --bg2:#0d1422; --bg3:#111927; --border:#1a2535;
  --accent:#00d4ff; --green:#00e57a; --red:#ff4d6d; --yellow:#ffd94a;
  --text:#c8d8ec; --muted:#4a6080; --paper:#9b6bff; --live:#f5a623;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:'IBM Plex Sans',sans-serif;min-height:100vh}
.mono{font-family:'IBM Plex Mono',monospace}
.shell{display:flex;flex-direction:column;min-height:100vh}

/* Topbar */
.topbar{background:var(--bg2);border-bottom:1px solid var(--border);padding:14px 28px;
        display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.logo{font-family:'IBM Plex Mono',monospace;font-size:1.1rem;font-weight:600;color:var(--accent);letter-spacing:2px}
.tabs{margin-left:auto;display:flex;gap:2px;background:var(--bg3);border-radius:6px;padding:3px}
.tab{padding:7px 18px;border-radius:4px;font-size:.78rem;font-weight:500;cursor:pointer;
     color:var(--muted);border:none;background:transparent;transition:.2s}
.tab.active{background:var(--bg2);color:var(--text);border:1px solid var(--border)}

/* MODE TOGGLE — componente principal */
.mode-toggle-wrap{display:flex;align-items:center;gap:0;border-radius:8px;overflow:hidden;
  border:1px solid var(--border);background:var(--bg3);padding:3px;gap:3px}
.mode-btn{padding:8px 20px;border:none;border-radius:6px;font-size:.78rem;font-weight:700;
  cursor:pointer;letter-spacing:.8px;text-transform:uppercase;font-family:'IBM Plex Mono',monospace;
  transition:all .2s;opacity:.45;background:transparent;white-space:nowrap}
.mode-btn.paper-btn{color:var(--paper)}
.mode-btn.live-btn{color:var(--live)}
.mode-btn.active{opacity:1;transform:none}
.mode-btn.paper-btn.active{background:rgba(155,107,255,.18);border:1px solid rgba(155,107,255,.5);
  box-shadow:0 0 12px rgba(155,107,255,.25)}
.mode-btn.live-btn.active{background:rgba(245,166,35,.15);border:1px solid rgba(245,166,35,.5);
  box-shadow:0 0 12px rgba(245,166,35,.25)}
.mode-btn:hover:not(.active){opacity:.75}
.mode-indicator{font-size:.65rem;font-family:'IBM Plex Mono',monospace;padding:4px 10px;
  border-radius:4px;white-space:nowrap}
.mi-paper{background:rgba(155,107,255,.12);color:var(--paper);border:1px solid rgba(155,107,255,.3)}
.mi-live{background:rgba(245,166,35,.12);color:var(--live);border:1px solid rgba(245,166,35,.3)}

/* API bar */
.apibar{background:#06090f;border-bottom:1px solid var(--border);padding:8px 28px;
        display:flex;align-items:center;gap:16px;flex-wrap:wrap;position:sticky;top:53px;z-index:99}
.apibar-label{font-size:.58rem;font-weight:700;letter-spacing:2px;color:var(--muted);text-transform:uppercase}
.apibar-group{display:flex;align-items:center;gap:5px;flex-wrap:wrap}
.apibar-section{font-size:.58rem;color:var(--muted);letter-spacing:1px;text-transform:uppercase;margin-right:2px}
.apibtn{display:inline-flex;align-items:center;padding:4px 10px;border-radius:4px;font-size:.68rem;
  font-weight:600;cursor:pointer;border:none;text-decoration:none;font-family:'IBM Plex Mono',monospace;
  transition:.15s;white-space:nowrap}
.apibtn:hover{filter:brightness(1.25);transform:translateY(-1px)}
.apibtn-blue{background:rgba(0,212,255,.12);color:#00d4ff;border:1px solid rgba(0,212,255,.25)}
.apibtn-green{background:rgba(0,229,122,.12);color:#00e57a;border:1px solid rgba(0,229,122,.25)}
.apibtn-red{background:rgba(255,77,109,.12);color:#ff4d6d;border:1px solid rgba(255,77,109,.25)}
.apibtn-yellow{background:rgba(255,217,74,.1);color:#ffd94a;border:1px solid rgba(255,217,74,.25)}
.apibtn-purple{background:rgba(155,107,255,.12);color:#9b6bff;border:1px solid rgba(155,107,255,.25)}
#apibar-msg{font-size:.7rem;font-family:'IBM Plex Mono',monospace;padding:3px 10px;border-radius:4px;display:none}
.abm-ok{background:rgba(0,229,122,.12);color:var(--green);border:1px solid rgba(0,229,122,.3)}
.abm-er{background:rgba(255,77,109,.12);color:var(--red);border:1px solid rgba(255,77,109,.3)}

/* Main */
.main{flex:1;padding:24px 28px}
.panel{display:none}.panel.active{display:block}

/* Controls */
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:24px}
.btn{padding:10px 22px;border:none;border-radius:5px;font-size:.82rem;font-weight:600;
     cursor:pointer;letter-spacing:.5px;transition:.15s;font-family:'IBM Plex Sans',sans-serif}
.btn-go{background:var(--green);color:#000}.btn-go:hover{filter:brightness(1.1)}
.btn-go:disabled,.btn-stop:disabled{opacity:.3;cursor:default}
.btn-stop{background:var(--red);color:#fff}.btn-stop:hover{filter:brightness(1.1)}
.btn-sec{background:var(--bg3);color:var(--text);border:1px solid var(--border)}
.btn-sec:hover{border-color:var(--accent);color:var(--accent)}
.btn-accent{background:var(--accent);color:#000}.btn-accent:hover{filter:brightness(1.1)}
#sysmsg{font-size:.78rem;padding:5px 14px;border-radius:4px;display:none}
.msg-ok{background:rgba(0,229,122,.12);color:var(--green);border:1px solid var(--green)}
.msg-er{background:rgba(255,77,109,.12);color:var(--red);border:1px solid var(--red)}

/* KPI */
.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px 18px;position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--accent)}
.kpi.g::before{background:var(--green)}.kpi.r::before{background:var(--red)}
.kpi.y::before{background:var(--yellow)}.kpi.p::before{background:var(--paper)}
.kpi.live-kpi::before{background:var(--live)}
.kpi-lbl{font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:1.2px;margin-bottom:6px}
.kpi-val{font-family:'IBM Plex Mono',monospace;font-size:1.4rem;font-weight:600;color:var(--text)}
.kpi-val.g{color:var(--green)}.kpi-val.r{color:var(--red)}.kpi-val.y{color:var(--yellow)}
.kpi-val.p{color:var(--paper)}.kpi-val.live-c{color:var(--live)}

/* Status */
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}
.dot-run{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.dot-warm{background:var(--yellow);animation:pulse .8s infinite}
.dot-stop{background:var(--muted)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

/* Cards/tables */
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;margin-bottom:20px}
.card-head{padding:14px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between}
.card-title{font-size:.82rem;font-weight:600;color:var(--text);letter-spacing:.5px}
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:.76rem}
th{padding:10px 14px;text-align:left;color:var(--muted);font-size:.64rem;text-transform:uppercase;
   letter-spacing:1px;border-bottom:1px solid var(--border);font-weight:500;white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid rgba(26,37,53,.5);font-family:'IBM Plex Mono',monospace;font-size:.74rem;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,.02)}
.g{color:var(--green)}.r{color:var(--red)}.y{color:var(--yellow)}.p{color:var(--paper)}
.dir{display:inline-block;padding:2px 8px;border-radius:3px;font-size:.64rem;font-weight:700;letter-spacing:.5px;text-transform:uppercase}
.dir-l{background:rgba(0,229,122,.12);color:var(--green);border:1px solid rgba(0,229,122,.3)}
.dir-s{background:rgba(255,77,109,.12);color:var(--red);border:1px solid rgba(255,77,109,.3)}

/* Terminal */
.terminal{background:#050810;border:1px solid var(--border);border-radius:8px;
  font-family:'IBM Plex Mono',monospace;font-size:.7rem;line-height:1.8;
  padding:14px;max-height:260px;overflow-y:auto;color:#5a7a9a}
.terminal .lg{color:var(--green)}.terminal .lr{color:var(--red)}
.terminal .ly{color:var(--yellow)}.terminal .la{color:var(--accent)}

/* Backtest */
.form-row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-bottom:24px}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-group label{font-size:.64rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.form-group input,.form-group select{background:var(--bg3);border:1px solid var(--border);
  color:var(--text);padding:8px 12px;border-radius:5px;font-family:'IBM Plex Mono',monospace;font-size:.8rem;width:150px}
.progress-bar{height:3px;background:var(--border);border-radius:2px;margin-bottom:20px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--green));border-radius:2px;transition:width .3s}

/* Mode change toast */
.mode-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
  padding:12px 28px;border-radius:8px;font-family:'IBM Plex Mono',monospace;font-size:.82rem;
  font-weight:600;z-index:999;display:none;animation:fadeUp .3s ease}
@keyframes fadeUp{from{opacity:0;transform:translate(-50%,10px)}to{opacity:1;transform:translate(-50%,0)}}
.toast-paper{background:rgba(155,107,255,.2);color:#c8a0ff;border:1px solid rgba(155,107,255,.5)}
.toast-live{background:rgba(245,166,35,.2);color:#ffd080;border:1px solid rgba(245,166,35,.5)}

::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>
<div class="shell">

  <!-- ═══ TOPBAR ═══ -->
  <div class="topbar">
    <span class="logo">⚡ AZLEMA</span>
    <span class="mono" style="font-size:.7rem;color:var(--muted)">ETH-USDT · 30m · Bitget</span>

    <!-- TOGGLE PAPER / LIVE -->
    <div class="mode-toggle-wrap" title="Selecionar modo de operação">
      <button class="mode-btn paper-btn" id="btnPaper" onclick="setMode('paper')">
        📄 PAPER
      </button>
      <button class="mode-btn live-btn" id="btnLive" onclick="setMode('live')">
        💰 LIVE 95%
      </button>
    </div>
    <span class="mode-indicator" id="modeIndicator">—</span>

    <div class="tabs" style="margin-left:auto">
      <button class="tab active" onclick="switchTab('live')">Live</button>
      <button class="tab" onclick="switchTab('history')">Histórico</button>
      <button class="tab" onclick="switchTab('backtest')">Backtest</button>
    </div>
  </div>

  <!-- ═══ API BAR ═══ -->
  <div class="apibar">
    <span class="apibar-label">API RÁPIDA</span>
    <div class="apibar-group">
      <span class="apibar-section">STATUS</span>
      <a class="apibtn apibtn-blue" href="/status" target="_blank">/status</a>
      <a class="apibtn apibtn-blue" href="/health" target="_blank">/health</a>
    </div>
    <div class="apibar-group">
      <span class="apibar-section">TRADER</span>
      <button class="apibtn apibtn-green" onclick="apiPost('/start','Trader iniciado!')">▶ /start</button>
      <button class="apibtn apibtn-red"   onclick="apiPost('/stop','Trader parado!')">■ /stop</button>
    </div>
    <div class="apibar-group">
      <span class="apibar-section">HISTÓRICO</span>
      <a class="apibtn apibtn-purple" href="/report" target="_blank">📊 Relatório</a>
      <button class="apibtn apibtn-purple" onclick="exportJson('/history','trades_history')">⬇ JSON</button>
      <button class="apibtn apibtn-red" onclick="if(confirm('Limpar histórico?'))apiPost('/history/clear','Limpo!')">🗑</button>
    </div>
    <div class="apibar-group">
      <span class="apibar-section">BACKTEST</span>
      <button class="apibtn apibtn-yellow" onclick="quickBacktest()">▶ Rodar BT</button>
      <button class="apibtn apibtn-yellow" onclick="exportJson('/backtest/history','bt_history')">⬇ JSON</button>
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
        <div class="kpi live-kpi" id="kpi-mode">
          <div class="kpi-lbl">Modo / Exposição</div>
          <div class="kpi-val live-c" id="lv-mode">—</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><span class="card-title">ORDENS RECENTES</span></div>
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>Hora</th><th>Ação</th><th>Preço</th><th>Qty ETH</th><th>Motivo</th></tr></thead>
            <tbody id="lv-trades"><tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">Aguardando...</td></tr></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><span class="card-title">LOG DO SISTEMA</span></div>
        <div style="padding:0"><div class="terminal" id="lv-log">aguardando...</div></div>
      </div>
    </div>

    <!-- ═══ HISTORY PANEL ═══ -->
    <div class="panel" id="panel-history">
      <!-- Banner: sessão atual -->
      <div id="hist-session-banner" style="
        display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
        background:var(--bg2);border:1px solid var(--border);border-radius:8px;
        padding:14px 18px;margin-bottom:20px">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px">Sessão atual</span>
          <span id="hist-session-mode" class="mode-indicator mi-paper">📄 PAPER</span>
          <span id="hist-session-count" style="font-size:.72rem;font-family:'IBM Plex Mono',monospace;color:var(--muted)">0 trades</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn btn-sec" style="padding:7px 14px;font-size:.74rem" onclick="loadHistory()">↺ Atualizar</button>
          <button class="btn" style="padding:7px 16px;font-size:.74rem;background:rgba(0,212,255,.1);color:var(--accent);border:1px solid rgba(0,212,255,.3)"
            onclick="newPaperSession()"
            title="Limpa os trades desta sessão para começar uma nova — não afeta o histórico de Backtest">
            🆕 Nova Sessão
          </button>
          <button class="btn btn-sec" style="padding:7px 14px;font-size:.74rem;color:var(--red);border-color:rgba(255,77,109,.3)"
            onclick="if(confirm('Limpar TODO o histórico de Paper/Live?'))clearHistory()">🗑 Limpar tudo</button>
        </div>
      </div>
      <!-- Nota de separação -->
      <div style="font-size:.7rem;color:var(--muted);margin-bottom:16px;font-family:'IBM Plex Mono',monospace;
        background:rgba(0,212,255,.04);border:1px solid rgba(0,212,255,.1);border-radius:6px;padding:10px 14px">
        ℹ️ Esta aba mostra apenas trades <strong style="color:var(--text)">Paper / Live</strong>.
        Os trades de <strong style="color:var(--yellow)">Backtest</strong> ficam separados na aba → <strong style="color:var(--yellow)">Backtest</strong>.
      </div>
      <div class="kpi-grid">
        <div class="kpi"><div class="kpi-lbl">Total Trades</div><div class="kpi-val" id="h-total">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Win Rate</div><div class="kpi-val g" id="h-wr">—</div></div>
        <div class="kpi"><div class="kpi-lbl">PnL Total</div><div class="kpi-val" id="h-pnl">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Profit Factor</div><div class="kpi-val g" id="h-pf">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Avg Win</div><div class="kpi-val g" id="h-aw">—</div></div>
        <div class="kpi r"><div class="kpi-lbl">Avg Loss</div><div class="kpi-val r" id="h-al">—</div></div>
        <div class="kpi g"><div class="kpi-lbl">Melhor Trade</div><div class="kpi-val g" id="h-best">—</div></div>
        <div class="kpi r"><div class="kpi-lbl">Pior Trade</div><div class="kpi-val r" id="h-worst">—</div></div>
      </div>
      <div class="card">
        <div class="card-head"><span class="card-title">TODOS OS TRADES</span></div>
        <div class="tbl-wrap">
          <table>
            <thead><tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
                       <th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th><th>Modo</th></tr></thead>
            <tbody id="hist-tbl"><tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">Carregando...</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ═══ BACKTEST PANEL ═══ -->
    <div class="panel" id="panel-backtest">
      <div class="form-row">
        <div class="form-group"><label>Símbolo</label><input id="bt-sym" value="ETH-USDT-SWAP"></div>
        <div class="form-group"><label>Timeframe</label>
          <select id="bt-tf">
            <option value="30m" selected>30m</option>
            <option value="1h">1h</option><option value="4h">4h</option>
            <option value="1d">1d</option><option value="15m">15m</option>
          </select>
        </div>
        <div class="form-group"><label>Candles</label><input id="bt-lim" type="number" value="500" min="100" max="5000"></div>
        <div class="form-group"><label>Capital Inicial</label><input id="bt-cap" type="number" value="1000" min="100"></div>
        <div class="form-group" title="Taxa cobrada ao abrir posição (% do valor nocional). Bitget taker: 0.06%">
          <label>Taxa Abertura %</label>
          <input id="bt-ofee" type="number" value="0.06" min="0" max="1" step="0.01" style="width:120px">
        </div>
        <div class="form-group" title="Taxa cobrada ao fechar posição (% do valor nocional). Bitget taker: 0.06%">
          <label>Taxa Fechamento %</label>
          <input id="bt-cfee" type="number" value="0.06" min="0" max="1" step="0.01" style="width:120px">
        </div>
        <div style="display:flex;flex-direction:column;gap:5px;justify-content:flex-end">
          <button class="btn btn-accent" id="btnBT" onclick="runBacktest()">▶ Executar</button>
          <button class="btn btn-sec" style="font-size:.72rem;padding:6px 12px"
            onclick="document.getElementById('bt-ofee').value='0';document.getElementById('bt-cfee').value='0'"
            title="Remover taxas para ver resultado sem custos">Sem taxas</button>
        </div>
      </div>
      <div style="font-size:.68rem;color:var(--muted);font-family:'IBM Plex Mono',monospace;
        background:rgba(255,217,74,.04);border:1px solid rgba(255,217,74,.12);border-radius:5px;
        padding:8px 14px;margin-bottom:18px">
        💡 <strong style="color:var(--yellow)">Bitget Futures (taker):</strong> 0.06% abertura + 0.06% fechamento &nbsp;|&nbsp;
        <strong style="color:var(--yellow)">Maker:</strong> 0.02% + 0.02% &nbsp;|&nbsp;
        <strong style="color:var(--muted)">0% = sem simulação de taxas</strong>
      </div>
      <div class="progress-bar"><div class="progress-fill" id="bt-prog" style="width:0%"></div></div>
      <div id="bt-result" style="display:none">
        <div class="kpi-grid" id="bt-kpis"></div>
        <div class="card">
          <div class="card-head"><span class="card-title">TRADES DO BACKTEST</span></div>
          <div class="tbl-wrap">
            <table>
              <thead><tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
                         <th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th></tr></thead>
              <tbody id="bt-tbl"></tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:20px">
        <div class="card-head"><span class="card-title">HISTÓRICO DE BACKTESTS</span></div>
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
</div>

<!-- Mode toast -->
<div class="mode-toast" id="modeToast"></div>

<script>
// ── Modo atual ────────────────────────────────────────────────────────────────
let _currentMode = 'paper';   // 'paper' | 'live'

function updateModeUI(mode) {
  _currentMode = mode;
  const isPaper = mode === 'paper';

  document.getElementById('btnPaper').classList.toggle('active', isPaper);
  document.getElementById('btnLive').classList.toggle('active', !isPaper);

  const ind = document.getElementById('modeIndicator');
  if (isPaper) {
    ind.textContent = '📄 Saldo Simulado';
    ind.className = 'mode-indicator mi-paper';
  } else {
    ind.textContent = '💰 95% Saldo Real';
    ind.className = 'mode-indicator mi-live';
  }

  const lv = document.getElementById('lv-mode');
  if (lv) {
    lv.textContent = isPaper ? 'PAPER' : 'LIVE · 95%';
    lv.className   = 'kpi-val ' + (isPaper ? 'p' : 'live-c');
  }

  const kpiMode = document.getElementById('kpi-mode');
  if (kpiMode) kpiMode.className = 'kpi ' + (isPaper ? 'p' : 'live-kpi');
}

async function setMode(mode) {
  // Se trader está rodando, avisa antes de trocar
  const running = document.getElementById('btnStop').disabled === false;
  if (running) {
    if (!confirm(`O trader está rodando. Parar e trocar para ${mode === 'paper' ? 'PAPER' : 'LIVE 95%'}?`))
      return;
    await fetch('/stop', { method: 'POST' });
    await new Promise(r => setTimeout(r, 1000));
  }

  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok';
  m.textContent = 'Alterando modo...';

  try {
    const d = await (await fetch('/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    })).json();

    if (d.error) {
      m.className = 'abm-er'; m.textContent = d.error;
    } else {
      updateModeUI(mode);
      showToast(mode);
      m.textContent = d.message || 'OK';
    }
  } catch (e) {
    m.className = 'abm-er'; m.textContent = 'Erro: ' + e;
  }
  setTimeout(() => m.style.display = 'none', 4000);
}

function showToast(mode) {
  const t = document.getElementById('modeToast');
  t.className = 'mode-toast ' + (mode === 'paper' ? 'toast-paper' : 'toast-live');
  t.textContent = mode === 'paper'
    ? '📄 Modo PAPER ativado — trades simulados'
    : '💰 Modo LIVE ativado — usando 95% do saldo real na Bitget';
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 4000);
}

// ── Tabs ──────────────────────────────────────────────────────────────────────
function switchTab(t) {
  document.querySelectorAll('.tab').forEach((el,i) => {
    el.classList.toggle('active', ['live','history','backtest'][i] === t);
  });
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
  document.getElementById('panel-' + t).classList.add('active');
  if (t === 'history') loadHistory();
  if (t === 'backtest') loadBtHistory();
}

// ── Live poll ─────────────────────────────────────────────────────────────────
async function poll() {
  try {
    const d = await (await fetch('/status')).json();
    const run  = d.status === 'running';
    const warm = d.status === 'warming';

    // Atualiza modo vindo do server
    updateModeUI(d.paper ? 'paper' : 'live');

    document.getElementById('btnStart').disabled = run || warm;
    document.getElementById('btnStop').disabled  = !(run || warm);

    const se = document.getElementById('lv-status');
    if (run)        se.innerHTML = '<span class="status-dot dot-run"></span><span class="g">Rodando</span>';
    else if (warm)  se.innerHTML = '<span class="status-dot dot-warm"></span><span class="y">Warmup...</span>';
    else            se.innerHTML = '<span class="status-dot dot-stop"></span><span style="color:var(--muted)">Parado</span>';

    if (d.bal  != null) document.getElementById('lv-bal').textContent  = d.bal.toFixed(2)  + ' USDT';
    const pe = document.getElementById('lv-pnl');
    if (d.pnl  != null) {
      pe.textContent  = (d.pnl >= 0 ? '+' : '') + d.pnl.toFixed(4) + ' USDT';
      pe.className = 'kpi-val ' + (d.pnl >= 0 ? 'g' : 'r');
    }
    const pp = document.getElementById('lv-pos');
    if (d.pos) {
      const s = d.pos.side;
      pp.innerHTML = `<span class="${s === 'long' ? 'g' : 'r'}">${s.toUpperCase()}</span>`;
    } else { pp.innerHTML = '<span style="color:var(--muted)">FLAT</span>'; }
    if (d.period != null) document.getElementById('lv-per').textContent  = d.period;
    if (d.ec     != null) document.getElementById('lv-ec').textContent   = d.ec.toFixed(2);
    if (d.ema    != null) document.getElementById('lv-ema').textContent  = d.ema.toFixed(2);

    // Trades recentes
    const tb = document.getElementById('lv-trades');
    const tr = [...(d.trades || [])].reverse();
    if (tr.length) {
      tb.innerHTML = tr.map(t => {
        const ac = t.action || '';
        let cl = 'dir', lb = ac;
        if (ac.includes('LONG'))  { cl = 'dir dir-l'; lb = ac.includes('ENTER') ? '▲ LONG'  : '▼ EXIT L'; }
        if (ac.includes('SHORT')) { cl = 'dir dir-s'; lb = ac.includes('ENTER') ? '▼ SHORT' : '▲ EXIT S'; }
        return `<tr>
          <td>${(t.time || '').split('T')[1]?.slice(0,8) || '—'}</td>
          <td><span class="${cl}">${lb}</span></td>
          <td>${t.price?.toFixed(2) || '—'}</td>
          <td>${t.qty?.toFixed(6)   || '—'}</td>
          <td style="color:var(--muted)">${t.reason || '—'}</td>
        </tr>`;
      }).join('');
    }

    // Log
    const lb = document.getElementById('lv-log');
    if (d.log && d.log.length) {
      lb.innerHTML = d.log.slice(-80).map(l => {
        let cls = '';
        if (/✅|LONG|BUY/.test(l))    cls = 'lg';
        else if (/❌|EXIT|SHORT/.test(l)) cls = 'lr';
        else if (/⚠️|WARN/.test(l))   cls = 'ly';
        else if (/AZLEMA|╔|╚/.test(l))  cls = 'la';
        return `<div class="${cls}">${l}</div>`;
      }).join('');
      lb.scrollTop = lb.scrollHeight;
    }
  } catch(e) { console.error(e); }
}
poll(); setInterval(poll, 4000);

// ── Controls ──────────────────────────────────────────────────────────────────
async function ctrl(a) {
  const m = document.getElementById('sysmsg');
  m.style.display = 'inline-block';
  m.className = a === 'start' ? 'msg-ok' : 'msg-er';
  m.textContent = a === 'start' ? 'Iniciando...' : 'Parando...';
  try {
    const d = await (await fetch('/' + a, { method: 'POST' })).json();
    m.className = d.error ? 'msg-er' : 'msg-ok';
    m.textContent = d.message || d.error || 'OK';
  } catch { m.className = 'msg-er'; m.textContent = 'Erro de rede'; }
  setTimeout(() => m.style.display = 'none', 5000);
  setTimeout(poll, 1500);
}

// ── History ───────────────────────────────────────────────────────────────────
async function loadHistory() {
  try {
    const d = await (await fetch('/history')).json();
    const s = d.stats || {};

    // Atualiza banner de sessão
    const isPaper    = _currentMode === 'paper';
    const modeEl     = document.getElementById('hist-session-mode');
    const countEl    = document.getElementById('hist-session-count');
    const closedCount = (d.trades||[]).filter(t => t.status === 'closed').length;
    if (modeEl) {
      modeEl.textContent = isPaper ? '📄 PAPER' : '💰 LIVE';
      modeEl.className   = 'mode-indicator ' + (isPaper ? 'mi-paper' : 'mi-live');
    }
    if (countEl) countEl.textContent = closedCount + ' trade' + (closedCount !== 1 ? 's' : '') + ' fechado' + (closedCount !== 1 ? 's' : '');

    const pf = s.profit_factor === Infinity || s.profit_factor > 999 ? '∞' : +(s.profit_factor||0).toFixed(3);
    document.getElementById('h-total').textContent  = s.total || 0;
    const wrEl = document.getElementById('h-wr');
    wrEl.textContent  = (s.win_rate||0).toFixed(1) + '%';
    wrEl.className = 'kpi-val ' + (s.win_rate >= 50 ? 'g' : 'r');
    const pnlEl = document.getElementById('h-pnl');
    pnlEl.textContent = (s.total_pnl >= 0 ? '+' : '') + (s.total_pnl||0).toFixed(4) + ' USDT';
    pnlEl.className = 'kpi-val ' + (s.total_pnl >= 0 ? 'g' : 'r');
    const pfEl = document.getElementById('h-pf');
    pfEl.textContent  = pf;
    pfEl.className = 'kpi-val ' + (s.profit_factor > 1 ? 'g' : 'r');
    document.getElementById('h-aw').textContent    = '+' + (s.avg_win||0).toFixed(4);
    document.getElementById('h-al').textContent    = (s.avg_loss||0).toFixed(4);
    document.getElementById('h-best').textContent  = '+' + (s.best_trade||0).toFixed(4);
    document.getElementById('h-worst').textContent = (s.worst_trade||0).toFixed(4);

    const tb = document.getElementById('hist-tbl');
    const trades = (d.trades || []).filter(t => t.status === 'closed').reverse();
    if (!trades.length) {
      tb.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Nenhum trade fechado</td></tr>';
      return;
    }
    tb.innerHTML = trades.map((t, i) => {
      const pnl    = t.pnl_usdt || 0;
      const pct    = t.pnl_pct  || 0;
      const dir    = t.action === 'BUY' ? 'LONG' : 'SHORT';
      const dc     = t.action === 'BUY' ? 'dir dir-l' : 'dir dir-s';
      const pc     = pnl >= 0 ? 'g' : 'r';
      const ep     = t.exit_price ? t.exit_price.toFixed(2) : '—';
      const mode   = t.mode === 'paper'
        ? '<span class="p">PAPER</span>'
        : '<span class="g">LIVE</span>';
      const entryFmt = (t.entry_time||'—').replace('T',' ').slice(0,19);
      const exitFmt  = (t.exit_time ||'—').replace('T',' ').slice(0,19);
      return `<tr>
        <td>${i+1}</td>
        <td class="mono" style="font-size:.7rem">${entryFmt}</td>
        <td class="mono" style="font-size:.7rem">${exitFmt}</td>
        <td><span class="${dc}">${dir}</span></td>
        <td>${(t.qty||0).toFixed(4)}</td>
        <td>${(t.entry_price||0).toFixed(2)}</td>
        <td>${ep}</td>
        <td class="${pc}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(4)}</td>
        <td class="${pc}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
        <td style="color:var(--muted)">${t.exit_reason||'—'}</td>
        <td>${mode}</td>
      </tr>`;
    }).join('');
  } catch(e) { console.error(e); }
}

async function clearHistory() {
  await fetch('/history/clear', { method: 'POST' });
  loadHistory();
}

async function newPaperSession() {
  if (!confirm('Iniciar nova sessão? Isso vai limpar os trades Paper/Live atuais. Os resultados de Backtest NAO serao afetados.')) return;
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok';
  m.textContent = '🆕 Nova sessão iniciada';
  await fetch('/history/clear', { method: 'POST' });
  loadHistory();
  setTimeout(() => m.style.display = 'none', 3000);
}

// ── Backtest ──────────────────────────────────────────────────────────────────
async function runBacktest() {
  const btn  = document.getElementById('btnBT');
  const prog = document.getElementById('bt-prog');
  btn.disabled = true; btn.textContent = 'Rodando...';
  prog.style.width = '20%';
  document.getElementById('bt-result').style.display = 'none';
  try {
    const sym  = document.getElementById('bt-sym').value;
    const tf   = document.getElementById('bt-tf').value;
    const lim  = document.getElementById('bt-lim').value;
    const cap  = document.getElementById('bt-cap').value;
    const ofee = document.getElementById('bt-ofee').value;
    const cfee = document.getElementById('bt-cfee').value;
    prog.style.width = '60%';
    const url = `/backtest/run?symbol=${sym}&tf=${tf}&limit=${lim}&capital=${cap}&open_fee=${ofee}&close_fee=${cfee}`;
    const d = await (await fetch(url, {method:'POST'})).json();
    prog.style.width = '100%';
    if (d.error) { alert('Erro: ' + d.error); return; }
    renderBacktestResult(d);
    loadBtHistory();
  } catch(e) { alert('Erro: ' + e); }
  finally { btn.disabled = false; btn.textContent = '▶ Executar'; setTimeout(() => prog.style.width = '0%', 1000); }
}

function renderBacktestResult(d) {
  const pf      = d.profit_factor === Infinity || d.profit_factor > 999 ? '∞' : +(d.profit_factor||0).toFixed(3);
  const hasFees = d.fees_enabled && (d.open_fee_pct > 0 || d.close_fee_pct > 0);
  const pnlLabel = hasFees ? 'PnL Líquido' : 'PnL Total';

  const kpis = [
    [pnlLabel,       (d.total_pnl >= 0 ? '+' : '') + d.total_pnl.toFixed(2) + ' USDT', d.total_pnl >= 0 ? 'g' : 'r'],
    ['Saldo Final',  d.final_bal.toFixed(2) + ' USDT', ''],
    ['Win Rate',     d.win_rate.toFixed(1) + '%',  d.win_rate >= 50 ? 'g' : 'r'],
    ['Total Trades', d.total_trades, ''],
    ['Profit Factor',pf,             d.profit_factor > 1 ? 'g' : 'r'],
    ['Max Drawdown', d.max_drawdown.toFixed(2) + '%', 'r'],
    ['Sharpe Ratio', d.sharpe.toFixed(3),  d.sharpe >= 1 ? 'g' : d.sharpe >= 0 ? 'y' : 'r'],
  ];
  if (hasFees) {
    kpis.push(['Taxas Pagas', '-' + (d.total_fees_paid||0).toFixed(4) + ' USDT', 'r']);
  }

  document.getElementById('bt-kpis').innerHTML = kpis.map(([lbl,val,cls]) =>
    `<div class="kpi ${cls}"><div class="kpi-lbl">${lbl}</div><div class="kpi-val ${cls}">${val}</div></div>`
  ).join('');

  // Banner de taxas
  const btResult = document.getElementById('bt-result');
  let feesBanner = document.getElementById('bt-fees-banner');
  if (!feesBanner) {
    feesBanner = document.createElement('div');
    feesBanner.id = 'bt-fees-banner';
    feesBanner.style.cssText = 'font-size:.7rem;font-family:"IBM Plex Mono",monospace;padding:8px 14px;border-radius:5px;margin-bottom:16px;';
    btResult.insertBefore(feesBanner, btResult.firstChild);
  }
  if (hasFees) {
    feesBanner.style.background = 'rgba(255,77,109,.06)';
    feesBanner.style.border = '1px solid rgba(255,77,109,.2)';
    feesBanner.style.color = '#ff4d6d';
    feesBanner.innerHTML = `💸 Taxas simuladas: <strong>${d.open_fee_pct}%</strong> abertura + <strong>${d.close_fee_pct}%</strong> fechamento &nbsp;|&nbsp; Total pago: <strong>-${(d.total_fees_paid||0).toFixed(4)} USDT</strong> &nbsp;|&nbsp; PnL já descontado`;
  } else {
    feesBanner.style.background = 'rgba(255,217,74,.05)';
    feesBanner.style.border = '1px solid rgba(255,217,74,.15)';
    feesBanner.style.color = '#ffd94a';
    feesBanner.innerHTML = '⚠️ Sem taxas simuladas — resultado sem custo de corretagem (0% abertura e fechamento)';
  }

  // Cabeçalho da tabela: adiciona coluna PnL Líq se houver taxas
  const tblHead = document.querySelector('#bt-tbl')?.closest('table')?.querySelector('thead tr');
  if (tblHead) {
    tblHead.innerHTML = `<th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
      <th>P. Entrada</th><th>P. Saída</th>
      <th>PnL Bruto</th>${hasFees ? '<th>Taxas</th><th>PnL Líq.</th>' : ''}
      <th>PnL %</th><th>Motivo</th>`;
  }
  const colSpan = hasFees ? 13 : 10;

  const trades = (d.trades || []).slice().reverse();
  document.getElementById('bt-tbl').innerHTML = trades.length ? trades.map((t,i) => {
    const pnlB  = t.pnl_usdt  || 0;
    const pnlN  = t.pnl_net   != null ? t.pnl_net : pnlB;
    const pct   = hasFees ? (t.pnl_pct_net || 0) : (t.pnl_percent || 0);
    const fees  = t.fees_total || 0;
    const dir   = t.action === 'BUY' ? 'LONG' : 'SHORT';
    const dc    = t.action === 'BUY' ? 'dir dir-l' : 'dir dir-s';
    const pcB   = pnlB >= 0 ? 'g' : 'r';
    const pcN   = pnlN >= 0 ? 'g' : 'r';
    const entryFmt = (t.entry_time||'—').replace('T',' ').slice(0,19);
    const exitFmt  = (t.exit_time ||'—').replace('T',' ').slice(0,19);
    const feeCols  = hasFees
      ? `<td class="r" style="font-size:.68rem">-${fees.toFixed(4)}</td>
         <td class="${pcN}">${pnlN >= 0 ? '+' : ''}${pnlN.toFixed(4)}</td>`
      : '';
    return `<tr>
      <td>${i+1}</td>
      <td class="mono" style="font-size:.7rem">${entryFmt}</td>
      <td class="mono" style="font-size:.7rem">${exitFmt}</td>
      <td><span class="${dc}">${dir}</span></td>
      <td>${(t.qty||0).toFixed(4)}</td>
      <td>${(t.entry_price||0).toFixed(2)}</td>
      <td>${t.exit_price ? t.exit_price.toFixed(2) : '—'}</td>
      <td class="${pcB}">${pnlB >= 0 ? '+' : ''}${pnlB.toFixed(4)}</td>
      ${feeCols}
      <td class="${pcN}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
      <td style="color:var(--muted)">${t.exit_comment||'—'}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="${colSpan}" style="text-align:center;color:var(--muted)">Sem trades</td></tr>`;

  document.getElementById('bt-result').style.display = 'block';
}

async function loadBtHistory() {
  try {
    const d = await (await fetch('/backtest/history')).json();
    const sessions = (d.sessions || []).slice().reverse();
    const tb = document.getElementById('bt-hist-tbl');
    if (!sessions.length) {
      tb.innerHTML = '<tr><td colspan="12" style="text-align:center;color:var(--muted);padding:20px">Sem histórico</td></tr>';
      return;
    }
    // Atualiza cabeçalho para incluir colunas de taxa
    const thead = tb.closest('table')?.querySelector('thead tr');
    if (thead) {
      thead.innerHTML = `<th>Data</th><th>Símbolo</th><th>TF</th><th>Candles</th>
        <th>PnL</th><th>Win Rate</th><th>Trades</th><th>PF</th>
        <th>Drawdown</th><th>Sharpe</th><th>Tax.Aber.</th><th>Tax.Fech.</th>`;
    }
    tb.innerHTML = sessions.map(s => {
      const pf      = s.profit_factor === Infinity || s.profit_factor > 999 ? '∞' : +(s.profit_factor||0).toFixed(3);
      const pc      = s.total_pnl >= 0 ? 'g' : 'r';
      const hasFees = s.fees_enabled || s.open_fee_pct > 0 || s.close_fee_pct > 0;
      const feeTag  = hasFees
        ? `<span style="color:#ff4d6d;font-size:.65rem">${(s.open_fee_pct||0).toFixed(2)}%</span>`
        : `<span style="color:var(--muted);font-size:.65rem">0%</span>`;
      const cFeeTag = hasFees
        ? `<span style="color:#ff4d6d;font-size:.65rem">${(s.close_fee_pct||0).toFixed(2)}%</span>`
        : `<span style="color:var(--muted);font-size:.65rem">0%</span>`;
      return `<tr>
        <td>${(s.id||'—').replace('T',' ').slice(0,19)}</td>
        <td>${s.symbol||'—'}</td><td>${s.timeframe||'—'}</td><td>${s.candles||0}</td>
        <td class="${pc}">${s.total_pnl >= 0 ? '+' : ''}${(s.total_pnl||0).toFixed(2)}</td>
        <td class="${s.win_rate >= 50 ? 'g' : 'r'}">${(s.win_rate||0).toFixed(1)}%</td>
        <td>${s.total_trades||0}</td>
        <td class="${s.profit_factor > 1 ? 'g' : 'r'}">${pf}</td>
        <td class="r">${(s.max_drawdown||0).toFixed(2)}%</td>
        <td class="${(s.sharpe||0) >= 1 ? 'g' : (s.sharpe||0) >= 0 ? 'y' : 'r'}">${(s.sharpe||0).toFixed(3)}</td>
        <td>${feeTag}</td><td>${cFeeTag}</td>
      </tr>`;
    }).join('');
  } catch(e) { console.error(e); }
}

loadBtHistory();

// ── API bar helpers ───────────────────────────────────────────────────────────
async function apiPost(route, successMsg) {
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = '...';
  try {
    const d = await (await fetch(route, { method: 'POST' })).json();
    m.className = d.error ? 'abm-er' : 'abm-ok';
    m.textContent = d.error || successMsg || d.message || 'OK';
  } catch(e) { m.className = 'abm-er'; m.textContent = 'Erro: ' + e; }
  setTimeout(() => m.style.display = 'none', 3500);
  setTimeout(poll, 1200);
}

async function exportJson(route, filename) {
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = 'Exportando...';
  try {
    const d = await (await fetch(route)).json();
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = filename + '_' + new Date().toISOString().slice(0,10) + '.json';
    a.click(); URL.revokeObjectURL(url);
    m.textContent = '✓ Download iniciado';
  } catch(e) { m.className = 'abm-er'; m.textContent = 'Erro: ' + e; }
  setTimeout(() => m.style.display = 'none', 3000);
}

async function quickBacktest() {
  const sym = prompt('Símbolo (ex: ETH-USDT-SWAP)', 'ETH-USDT-SWAP');
  if (!sym) return;
  const tf  = prompt('Timeframe', '30m');
  if (!tf)  return;
  const lim = prompt('Candles', '500');
  if (!lim) return;
  const cap = prompt('Capital inicial (USDT)', '1000');
  if (!cap) return;
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = '⏳ Rodando...';
  try {
    const d = await (await fetch(
      `/backtest/run?symbol=${encodeURIComponent(sym)}&tf=${tf}&limit=${lim}&capital=${cap}`,
      { method: 'POST' }
    )).json();
    if (d.error) { m.className='abm-er'; m.textContent='Erro: '+d.error; }
    else {
      m.textContent = `✓ PnL: ${d.total_pnl>=0?'+':''}${(d.total_pnl||0).toFixed(2)} | WR: ${(d.win_rate||0).toFixed(1)}%`;
      switchTab('backtest'); renderBacktestResult(d); loadBtHistory();
    }
  } catch(e) { m.className='abm-er'; m.textContent='Erro: '+e; }
  setTimeout(() => m.style.display = 'none', 6000);
}
</script>
</body>
</html>"""


def _thread():
    global _trader
    log.info("📥 Baixando candles Bitget...")
    try:
        # DataCollector usa OKX para histórico (mais candles disponíveis)
        df = DataCollector(symbol="ETH-USDT-SWAP", timeframe=TIMEFRAME,
                           limit=TOTAL_CANDLES).fetch_ohlcv()
        log.info(f"  ✅ {len(df)} candles")
        if df.empty:
            log.error("❌ Sem dados"); return
        df = df.reset_index(drop=True)
        df['index'] = df.index
        _trader = LiveTrader()
        _trader.run(df)
    except Exception as e:
        log.error(f"❌ {type(e).__name__}: {e}\n{traceback.format_exc()}")
    finally:
        with _lock:
            _trader = None
        log.info("🔄 Pronto para re-iniciar")


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index(): return DASH

@app.route('/status')
def status():
    t = _trader
    if t is None:
        return jsonify({"status": "stopped", "tc": 0, "trades": [], "log": _logs[-80:],
                        "paper": get_paper_mode()})
    s = "running" if t._running else ("warming" if t._warming else "stopped")
    return jsonify({
        "status":  s,
        "paper":   get_paper_mode(),
        "pos":     t._cache_pos,
        "bal":     t._cache_bal,
        "pnl":     t.live_pnl,
        "period":  t.strategy.Period,
        "ec":      t.strategy.EC,
        "ema":     t.strategy.EMA,
        "tc":      len(t.log),
        "trades":  t.log[-10:],
        "log":     _logs[-80:],
    })

@app.route('/mode', methods=['GET', 'POST'])
def mode_endpoint():
    """GET → retorna modo atual. POST → altera modo (paper|live)."""
    if flask_request.method == 'GET':
        return jsonify({
            "mode":   "paper" if get_paper_mode() else "live",
            "paper":  get_paper_mode(),
            "pct":    100 if get_paper_mode() else int(LIVE_PCT * 100),
            "creds":  _creds_ok(),
        })

    data = flask_request.get_json(silent=True) or {}
    mode = data.get("mode", "paper").lower()

    if mode == "live":
        if not _creds_ok():
            return jsonify({"error": "❌ Configure BITGET_API_KEY, BITGET_SECRET_KEY e BITGET_PASSPHRASE no Render antes de usar o modo LIVE."}), 400
        set_paper_mode(False)
        log.info("🔄 Modo alterado → LIVE (95% saldo real na Bitget)")
        return jsonify({"message": "💰 Modo LIVE ativado — 95% do saldo real", "paper": False})
    else:
        set_paper_mode(True)
        log.info("🔄 Modo alterado → PAPER (saldo simulado)")
        return jsonify({"message": "📄 Modo PAPER ativado — saldo simulado", "paper": True})

@app.route('/start', methods=['POST'])
def start():
    with _lock:
        if _trader is not None:
            return jsonify({"message": "Já está rodando"})
        if not get_paper_mode() and not _creds_ok():
            return jsonify({"error": "Configure as chaves Bitget antes de iniciar em modo LIVE"}), 400
        threading.Thread(target=_thread, daemon=True).start()
        mode_str = "paper" if get_paper_mode() else "live (95% saldo Bitget)"
        return jsonify({"message": f"Iniciado em modo {mode_str}"})

@app.route('/stop', methods=['POST'])
def stop():
    if _trader: _trader.stop()
    return jsonify({"message": "Parado"})

@app.route('/ping')
def ping(): return "pong"

@app.route('/health')
def health():
    return jsonify({
        "ok":     True,
        "creds":  _creds_ok(),
        "paper":  get_paper_mode(),
        "mode":   "paper" if get_paper_mode() else "live",
        "trader": _trader is not None,
    })

@app.route('/history')
def get_history():
    return jsonify({"trades": history_mgr.get_all_trades(), "stats": history_mgr.get_stats()})

@app.route('/history/clear', methods=['POST'])
def clear_history():
    history_mgr.clear()
    return jsonify({"message": "Histórico limpo"})

@app.route('/backtest/run', methods=['POST'])
def api_backtest():
    sym           = flask_request.args.get('symbol',        "ETH-USDT-SWAP")
    tf            = flask_request.args.get('tf',            TIMEFRAME)
    limit         = int(flask_request.args.get('limit',     500))
    capital       = float(flask_request.args.get('capital', 1000.0))
    open_fee_pct  = float(flask_request.args.get('open_fee',  0.0))
    close_fee_pct = float(flask_request.args.get('close_fee', 0.0))
    result = run_backtest(sym, tf, limit, capital, open_fee_pct, close_fee_pct)
    return jsonify(result)

@app.route('/backtest/history')
def get_bt_history():
    data = backtest_mgr._load()
    return jsonify({"sessions": data.get("sessions", [])})

@app.route('/report')
def report_page():
    return "<h2 style='font-family:monospace;color:#f0b90b;background:#0e1219;padding:40px'>📊 Use /backtest/history para ver os dados JSON ou integre com o painel.</h2>"


def _delayed_start():
    time.sleep(5)
    if get_paper_mode():
        log.info("📄 PAPER TRADING — auto-start...")
        with _lock:
            if _trader is None:
                threading.Thread(target=_thread, daemon=True).start()
        return
    if not _creds_ok():
        log.warning("⚠️ Chaves Bitget não encontradas — use o botão Iniciar após configurar.")
        return
    with _lock:
        if _trader is not None:
            return
        log.info("🚀 Chaves Bitget OK — auto-start LIVE...")
        threading.Thread(target=_thread, daemon=True).start()


threading.Thread(target=_delayed_start, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0',
            port=int(os.environ.get("PORT", 5000)),
            debug=False)
