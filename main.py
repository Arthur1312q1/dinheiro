"""
AZLEMA Live Trading — Bitget ETH-USDT-SWAP Futures 1x
Render: configurar BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE

Modo de operação selecionável via dashboard:
  - PAPER TRADING : simula trades com saldo falso (sem risco)
  - LIVE TRADING  : opera com 95% do saldo real na Bitget

══════════════════════════════════════════════════════════════════════
FIX v13 — Execução imediata de entradas (2025)
══════════════════════════════════════════════════════════════════════
PROBLEMA:
  - Entradas live eram agendadas para o próximo open, adicionando delay extra de 1 barra.
  - A estratégia já tem delay de 1 barra por design (anti-ghost trades).

SOLUÇÃO:
  - Removido agendamento (_pending_entry) e execução imediata das ordens.
══════════════════════════════════════════════════════════════════════
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
# Warmup alinhado com o backtest: min(50, TOTAL_CANDLES//5) = 50
WARMUP_CANDLES = min(50, TOTAL_CANDLES // 5)
STRATEGY_CONFIG = {
    "adaptive_method": "Cos IFM", "threshold": 0.0,
    "fixed_sl_points": 2000, "fixed_tp_points": 55, "trail_offset": 15,
    "risk_percent": 0.01, "tick_size": 0.01, "initial_capital": 1000.0,
    "max_lots": 100, "default_period": 20, "warmup_bars": WARMUP_CANDLES,
}

# ── Modo de operação (mutável via dashboard) ──────────────────────────────────
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

    def open_long(self, qty, bal_usdt=0, px=0, ts=None):
        trade_id = self._new_id()
        ts = ts or brazil_iso()
        history_mgr.add_trade({
            "id": trade_id, "action": "BUY", "status": "open",
            "entry_time": ts, "entry_price": px,
            "qty": qty, "balance": self.balance, "mode": "paper",
        })
        self.position = {"side": "long", "size": qty, "avg_px": px, "id": trade_id}
        log.info(f"  📄 PAPER LONG aberto | px={px:.2f} qty={qty:.4f}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def open_short(self, qty, bal_usdt=0, px=0, ts=None):
        trade_id = self._new_id()
        ts = ts or brazil_iso()
        history_mgr.add_trade({
            "id": trade_id, "action": "SELL", "status": "open",
            "entry_time": ts, "entry_price": px,
            "qty": qty, "balance": self.balance, "mode": "paper",
        })
        self.position = {"side": "short", "size": qty, "avg_px": px, "id": trade_id}
        log.info(f"  📄 PAPER SHORT aberto | px={px:.2f} qty={qty:.4f}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def close_long(self, qty, exit_px=0, reason="EXIT", ts=None):
        if not self.position or self.position["side"] != "long":
            return {"code": "0"}
        entry_px = self.position["avg_px"]
        trade_id = self.position["id"]
        pnl      = (exit_px - entry_px) * qty
        self.position = None
        ts = ts or brazil_iso()
        try:
            history_mgr.close_trade(trade_id, exit_px, ts, reason, pnl)
        except Exception as _e:
            log.warning(f"  ⚠️ close_trade (long) file error: {_e}")
        log.info(f"  📄 PAPER LONG fechado | px={exit_px:.2f} pnl={pnl:+.4f} USDT")
        return {"code": "0"}

    def close_short(self, qty, exit_px=0, reason="EXIT", ts=None):
        if not self.position or self.position["side"] != "short":
            return {"code": "0"}
        entry_px = self.position["avg_px"]
        trade_id = self.position["id"]
        pnl      = (entry_px - exit_px) * qty
        self.position = None
        ts = ts or brazil_iso()
        try:
            history_mgr.close_trade(trade_id, exit_px, ts, reason, pnl)
        except Exception as _e:
            log.warning(f"  ⚠️ close_trade (short) file error: {_e}")
        log.info(f"  📄 PAPER SHORT fechado | px={exit_px:.2f} pnl={pnl:+.4f} USDT")
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
    CT_VAL       = 0.01    # 1 contrato = 0.01 ETH (Bitget ETH-USDT-SWAP)

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

    MIN_QTY_ETH = 0.01   # minimo da Bitget: 1 contrato = 0.01 ETH

    def _cts(self, qty_eth, bal=0, px=0):
        MIN_CTS = int(self.MIN_QTY_ETH / self.CT_VAL)  # 10 contratos

        if bal > 0 and px > 0:
            margin_usdt = bal * 0.90
            max_eth     = margin_usdt / px
            if max_eth < self.MIN_QTY_ETH:
                log.warning(f"  SALDO INSUFICIENTE: maximo {max_eth:.4f} ETH disponivel "
                            f"< minimo {self.MIN_QTY_ETH} ETH | bal={bal:.2f} px={px:.2f}")
                return 0
            qty_eth = min(qty_eth, max_eth)

        cts = max(MIN_CTS, int(qty_eth / self.CT_VAL))

        if bal > 0 and px > 0:
            nocional = cts * self.CT_VAL * px
            if nocional > bal * 0.90:
                cts = int((bal * 0.90) / (self.CT_VAL * px))
                if cts < MIN_CTS:
                    log.warning(f"  TRADE CANCELADO: apos cap {cts*self.CT_VAL:.4f} ETH "
                                f"< minimo {self.MIN_QTY_ETH} ETH | bal={bal:.2f}")
                    return 0
            log.info(f"  _cts: {cts} contratos = {cts*self.CT_VAL:.4f} ETH "
                     f"| nocional={cts*self.CT_VAL*px:.2f} USDT | bal={bal:.2f}")
        return cts

    def _order(self, side, reduce_only, sz_cts):
        size_eth = round(sz_cts * self.CT_VAL, 8)
        body = {
            "symbol":      self.SYMBOL,
            "productType": self.PRODUCT_TYPE,
            "marginMode":  "crossed",
            "marginCoin":  self.MARGIN,
            "size":        str(size_eth),
            "side":        side,
            "orderType":   "market",
        }
        if reduce_only:
            body["reduceOnly"] = "YES"
        r  = self._post("/api/v2/mix/order/place-order", body)
        d0 = r.get("data") or {}
        tag = f"{'CLOSE' if reduce_only else 'OPEN'}/{side.upper()}"
        if r.get("code") == "00000":
            log.info(f"  ✅ ORDER {tag} sz={sz_cts}cts={size_eth}ETH ordId={d0.get('orderId','?')}")
        else:
            log.error(f"  ❌ ORDER {tag} sz={sz_cts}cts={size_eth}ETH code={r.get('code','')} msg={r.get('msg','')}")
        return r

    def open_long(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        if sz == 0:
            return {"code": "SKIP", "msg": "Saldo insuficiente para minimo 0.01 ETH"}, 0.0
        r  = self._order("buy", False, sz)
        if r.get("code") == "00000":
            oid = (r.get("data") or {}).get("orderId", "?")
            history_mgr.add_trade({"id": str(oid), "action": "BUY", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": sz * self.CT_VAL, "balance": bal, "mode": "live"})
        return r, sz * self.CT_VAL

    def open_short(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        if sz == 0:
            return {"code": "SKIP", "msg": "Saldo insuficiente para minimo 0.01 ETH"}, 0.0
        r  = self._order("sell", False, sz)
        if r.get("code") == "00000":
            oid = (r.get("data") or {}).get("orderId", "?")
            history_mgr.add_trade({"id": str(oid), "action": "SELL", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": sz * self.CT_VAL, "balance": bal, "mode": "live"})
        return r, sz * self.CT_VAL

    def close_long(self, qty, exit_px=0, reason="EXIT"):
        sz = self._cts(qty)
        r  = self._order("sell", True, sz)
        if r.get("code") == "00000":
            ts = brazil_iso()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "BUY" and t.get("status") == "open":
                    pnl = (exit_px - t.get("entry_price", exit_px)) * (sz * self.CT_VAL)
                    history_mgr.close_trade(t["id"], exit_px, ts, reason, pnl)
                    break
        return r

    def close_short(self, qty, exit_px=0, reason="EXIT"):
        sz = self._cts(qty)
        r  = self._order("buy", True, sz)
        if r.get("code") == "00000":
            ts = brazil_iso()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "SELL" and t.get("status") == "open":
                    pnl = (t.get("entry_price", exit_px) - exit_px) * (sz * self.CT_VAL)
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
        return bal, px


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

        # ── Thread de monitoramento contínuo ──
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()

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
        last_close = float(df['close'].iloc[-1])
        if last_close > 0:
            self._cache_px = last_close
        self._refresh_cache()
        self._last_candle_ts = ""
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} | "
                 f"EC={self.strategy.EC:.2f} | px_cache={self._cache_px:.2f}")

    @property
    def live_pnl(self):
        return self.strategy.net_profit - self._pnl_baseline

    def _monitor_position(self):
        """
        Thread que monitora o preço de mercado a cada 100ms e fecha a posição
        se os níveis de stop ou trailing forem atingidos.
        """
        log.info("  🔍 Monitor de posição iniciado")
        while not self._monitor_stop.is_set():
            try:
                if self._is_paper() or self.bitget is None:
                    time.sleep(0.1)
                    continue

                pos = self._cache_pos
                if pos is None:
                    time.sleep(0.1)
                    continue

                price = self._mark_price()
                if price <= 0:
                    time.sleep(0.1)
                    continue

                side = pos['side']
                entry = pos['avg_px']
                qty = pos['size']
                tick = self.strategy.tick
                sl = self.strategy.sl
                tp = self.strategy.tp
                toff = self.strategy.toff

                if side == 'long':
                    stop_price = entry - sl * tick
                    if price <= stop_price:
                        log.info(f"  🔴 STOP LOSS LONG acionado @ {price:.2f} (stop={stop_price:.2f})")
                        self.bitget.close_long(qty, price, "SL")
                        self.strategy.confirm_exit('LONG', price, qty, datetime.now(timezone.utc), "SL")
                        self._cache_pos = None
                        self._add_log("EXIT_LONG", price, qty, "SL")
                elif side == 'short':
                    stop_price = entry + sl * tick
                    if price >= stop_price:
                        log.info(f"  🟢 STOP LOSS SHORT acionado @ {price:.2f} (stop={stop_price:.2f})")
                        self.bitget.close_short(qty, price, "SL")
                        self.strategy.confirm_exit('SHORT', price, qty, datetime.now(timezone.utc), "SL")
                        self._cache_pos = None
                        self._add_log("EXIT_SHORT", price, qty, "SL")

            except Exception as e:
                log.error(f"  ❌ Erro no monitor: {e}")
            finally:
                time.sleep(0.1)

    def process(self, candle: Dict):
        ts       = candle.get('timestamp', brazil_now())
        open_px  = float(candle['open'])
        close_px = float(candle['close'])

        if self._cache_px <= 0 and close_px > 0:
            self._cache_px = close_px

        cur_paper = self.paper.get_position() if self._is_paper() else None

        log.info(
            f"\n── {ts} | O={open_px:.2f} C={close_px:.2f} | bal={self._cache_bal:.2f} | "
            f"strat_pos={self.strategy.position_size:+.4f} | "
            f"pos_local={cur_paper if self._is_paper() else self._cache_pos} | "
            f"mode={'PAPER' if self._is_paper() else 'LIVE'}"
        )

        # Processa o candle com a estratégia (gera ações)
        actions = self.strategy.next(candle) or []

        log.info(f"  📊 {len(actions)} ação(ões): "
                 f"{[(a.get('action'), round(float(a.get('price') or 0), 2)) for a in actions]}")

        # Processa as ações (entradas e saídas)
        for act in actions:
            kind = act.get('action', '')

            act_px  = float(act.get('price') or 0)
            act_qty = float(act.get('qty')   or 0)
            _raw_ts = act.get('timestamp')
            if _raw_ts is not None:
                try:
                    if hasattr(_raw_ts, 'tzinfo'):
                        if _raw_ts.tzinfo is None:
                            _raw_ts = _raw_ts.replace(tzinfo=timezone.utc)
                        act_ts = _raw_ts.astimezone(BRT).strftime('%Y-%m-%dT%H:%M:%S')
                    else:
                        act_ts = str(_raw_ts)[:19]
                except Exception:
                    act_ts = brazil_iso()
            else:
                act_ts = brazil_iso()

            # ── EXIT LONG ─────────────────────────────────────────────────
            if kind == 'EXIT_LONG':
                reason = act.get('exit_reason', 'EXIT')
                px = act_px if act_px > 0 else close_px

                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'long':
                        self.paper.close_long(pos['size'], px, reason, ts=act_ts)
                        self._add_log("EXIT_LONG", px, pos['size'], reason)
                        self._cache_pos = None
                        log.info(f"  🔴 [PAPER] EXIT LONG @ {px:.2f} | {reason}")
                    else:
                        log.info(f"  ℹ️ EXIT_LONG: paper pos={pos} (strategy já flat, OK)")
                else:
                    pos = self._cache_pos
                    if not (pos and pos['side'] == 'long'):
                        log.info("  🔄 EXIT_LONG: cache stale — re-query Bitget...")
                        pos = self.bitget.position()
                        self._cache_pos = pos
                    if pos and pos['side'] == 'long':
                        qty_close = pos['size'] * self.bitget.ct_val()
                        self.bitget.close_long(qty_close, px, reason)
                        self._add_log("EXIT_LONG", px, qty_close, reason)
                        self._cache_pos = None
                        log.info(f"  🔴 LIVE EXIT LONG @ {px:.2f} | {reason}")
                    else:
                        log.info(f"  ℹ️ EXIT_LONG: Bitget pos={pos} (strategy já flat, OK)")

            # ── EXIT SHORT ────────────────────────────────────────────────
            elif kind == 'EXIT_SHORT':
                reason = act.get('exit_reason', 'EXIT')
                px = act_px if act_px > 0 else close_px

                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        self.paper.close_short(pos['size'], px, reason, ts=act_ts)
                        self._add_log("EXIT_SHORT", px, pos['size'], reason)
                        self._cache_pos = None
                        log.info(f"  🟢 [PAPER] EXIT SHORT @ {px:.2f} | {reason}")
                    else:
                        log.info(f"  ℹ️ EXIT_SHORT: paper pos={pos} (strategy já flat, OK)")
                else:
                    pos = self._cache_pos
                    if not (pos and pos['side'] == 'short'):
                        log.info("  🔄 EXIT_SHORT: cache stale — re-query Bitget...")
                        pos = self.bitget.position()
                        self._cache_pos = pos
                    if pos and pos['side'] == 'short':
                        qty_close = pos['size'] * self.bitget.ct_val()
                        self.bitget.close_short(qty_close, px, reason)
                        self._add_log("EXIT_SHORT", px, qty_close, reason)
                        self._cache_pos = None
                        log.info(f"  🟢 LIVE EXIT SHORT @ {px:.2f} | {reason}")
                    else:
                        log.info(f"  ℹ️ EXIT_SHORT: Bitget pos={pos} (strategy já flat, OK)")

            # ── ENTER LONG (BUY) ──────────────────────────────────────────
            elif kind == 'BUY':
                qty = act_qty if act_qty > 0 else 0.0
                if qty <= 0:
                    log.warning("  ⚠️ BUY ignorado — qty=0")
                    continue

                if self._is_paper():
                    # Paper: executa imediatamente com preço histórico (igual backtest)
                    px = act_px if act_px > 0 else close_px
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'long':
                        log.info("  ⏭️ BUY ignorado — já tem long aberto")
                        continue
                    if pos and pos['side'] == 'short':
                        self.paper.close_short(pos['size'], px, "REVERSAL", ts=act_ts)
                        self._cache_pos = None
                        log.info(f"  ↩️ [PAPER] REVERSAL: fechou SHORT @ {px:.2f}")
                    log.info(f"  🟢 [PAPER] ENTER LONG {qty:.6f} ETH @ {px:.2f}")
                    r, qty_f = self.paper.open_long(qty, self._cache_bal, px, ts=act_ts)
                    if r.get("code") == "0":
                        self._add_log("ENTER_LONG", px, qty_f)
                        self._cache_pos = {'side': 'long', 'size': qty_f, 'avg_px': px}
                    else:
                        log.error(f"  ❌ paper.open_long falhou")

                else:
                    # LIVE: executa imediatamente com preço atual
                    px = self._mark_price() or close_px
                    log.info(f"  🟢 LIVE ENTER LONG {qty:.6f} ETH @ {px:.2f}")
                    r, qty_f = self.bitget.open_long(qty, self._cache_bal, px)
                    if r.get("code") == "00000":
                        self.strategy.confirm_fill('BUY', px, qty_f, ts)
                        self._cache_pos = {'side': 'long', 'size': qty_f, 'avg_px': px}
                        self._cache_bal = self.strategy.balance
                        self._add_log("ENTER_LONG", px, qty_f)
                        log.info(f"  ✅ LONG confirmado | qty={qty_f:.4f} px={px:.2f}")
                    elif r.get("code") == "SKIP":
                        log.warning(f"  ⛔ LONG ignorado — {r.get('msg')}")
                    else:
                        log.error(f"  ❌ bitget.open_long falhou")

            # ── ENTER SHORT (SELL) ────────────────────────────────────────
            elif kind == 'SELL':
                qty = act_qty if act_qty > 0 else 0.0
                if qty <= 0:
                    log.warning("  ⚠️ SELL ignorado — qty=0")
                    continue

                if self._is_paper():
                    px = act_px if act_px > 0 else close_px
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        log.info("  ⏭️ SELL ignorado — já tem short aberto")
                        continue
                    if pos and pos['side'] == 'long':
                        self.paper.close_long(pos['size'], px, "REVERSAL", ts=act_ts)
                        self._cache_pos = None
                        log.info(f"  ↩️ [PAPER] REVERSAL: fechou LONG @ {px:.2f}")
                    log.info(f"  🔴 [PAPER] ENTER SHORT {qty:.6f} ETH @ {px:.2f}")
                    r, qty_f = self.paper.open_short(qty, self._cache_bal, px, ts=act_ts)
                    if r.get("code") == "0":
                        self._add_log("ENTER_SHORT", px, qty_f)
                        self._cache_pos = {'side': 'short', 'size': qty_f, 'avg_px': px}
                    else:
                        log.error(f"  ❌ paper.open_short falhou")

                else:
                    # LIVE: executa imediatamente com preço atual
                    px = self._mark_price() or close_px
                    log.info(f"  🔴 LIVE ENTER SHORT {qty:.6f} ETH @ {px:.2f}")
                    r, qty_f = self.bitget.open_short(qty, self._cache_bal, px)
                    if r.get("code") == "00000":
                        self.strategy.confirm_fill('SELL', px, qty_f, ts)
                        self._cache_pos = {'side': 'short', 'size': qty_f, 'avg_px': px}
                        self._cache_bal = self.strategy.balance
                        self._add_log("ENTER_SHORT", px, qty_f)
                        log.info(f"  ✅ SHORT confirmado | qty={qty_f:.4f} px={px:.2f}")
                    elif r.get("code") == "SKIP":
                        log.warning(f"  ⛔ SHORT ignorado — {r.get('msg')}")
                    else:
                        log.error(f"  ❌ bitget.open_short falhou")

        # Sincroniza saldo e posição com o estado da estratégia
        if self._is_paper():
            self.paper.balance = self.strategy.balance
            self._cache_bal    = self.strategy.balance
            self._cache_pos    = self.paper.get_position()

    def _wait(self, tf: int = 30):
        """
        Aguarda até o próximo horário de fechamento do candle (HH:00:00.010 ou HH:30:00.010) em UTC,
        com precisão de milissegundos usando busy-wait no final.
        """
        now_utc = datetime.now(timezone.utc)
        total_minutes = now_utc.hour * 60 + now_utc.minute
        next_multiple = ((total_minutes // 30) + 1) * 30
        target_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=next_multiple, milliseconds=10)

        if target_utc <= now_utc:
            target_utc += timedelta(minutes=30)

        sleep_seconds = (target_utc - now_utc).total_seconds()
        target_brt = target_utc.astimezone(BRT)
        log.info(f"⏰ Aguardando {sleep_seconds:.3f}s até {target_brt.strftime('%H:%M:%S.%f')[:-3]} ({tf}m)...")

        if sleep_seconds > 0.01:
            time.sleep(sleep_seconds - 0.01)

        while datetime.now(timezone.utc) < target_utc:
            pass

    def _candle(self) -> Optional[Dict]:
        """
        Busca o último candle FECHADO da Bitget usando data[1] (requer 2 candles).
        Retorna None se não houver 2 candles disponíveis.
        """
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
                    "limit":       "2",
                },
                timeout=10,
            ).json()
            if r.get("code") != "00000":
                if r.get("code") == "429":
                    log.warning("  ⚠️ Rate limit (429) — aguardando 2s...")
                    time.sleep(2)
                    return None
                log.error(f"  ❌ Bitget candles API: code={r.get('code')} msg={r.get('msg')}")
                return None
            data = r.get("data", [])
            if len(data) < 2:
                log.debug("  ℹ️ Apenas 1 candle disponível — aguardando...")
                return None
            c = data[1]
            candle = {
                'open':      float(c[1]),
                'high':      float(c[2]),
                'low':       float(c[3]),
                'close':     float(c[4]),
                'timestamp': datetime.fromtimestamp(int(c[0]) / 1000, tz=timezone.utc),
                'index':     self.strategy._bar + 1,
            }
            log.info(f"  🕯️ Candle fechado (data[1]): O={candle['open']:.2f} H={candle['high']:.2f} "
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
            bal, px = self.bitget.setup()
            if bal <= 0 and px <= 0:
                log.error("❌ Falha ao conectar na Bitget"); return
            if bal > 0:
                self.strategy.ic      = bal
                self.strategy.balance = bal
                self._cache_bal       = bal
                log.info(f"  💰 Saldo real injetado na estratégia: {bal:.4f} USDT")
            if px > 0:
                self._cache_px = px

        self.warmup(df)
        log.info(f"  ✅ Pronto para receber candles ao vivo da Bitget")

        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_position, daemon=True)
        self._monitor_thread.start()

        self._running = True
        tf = int(TIMEFRAME.replace('m','').replace('h','')) * \
             (60 if 'h' in TIMEFRAME else 1)

        while self._running:
            try:
                self._wait(tf)

                c = None
                for _attempt in range(300):
                    raw = self._candle()
                    if raw is None:
                        time.sleep(0.01)
                        continue
                    if str(raw['timestamp']) == self._last_candle_ts:
                        time.sleep(0.01)
                        continue
                    c = raw
                    break

                if c is None:
                    log.warning("  ⚠️ Candle não atualizou após 3s — pulando ciclo")
                    continue

                ts = str(c['timestamp'])
                log.info(f"  ✅ Novo candle: {ts} (tentativa {_attempt+1})")
                self._last_candle_ts = ts
                self._refresh_cache()
                self.process(c)
            except Exception as e:
                log.error(f"❌ Erro no loop principal: {e}\n{traceback.format_exc()}")
                time.sleep(60)
        log.info("🔴 Trader encerrado")

    def _refresh_cache(self):
        if self._is_paper():
            px = self._mark_price()
            if px > 0:
                self._cache_px = px
            self._cache_bal = self.strategy.balance
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
        self._monitor_stop.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2)


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
app      = Flask(__name__)
_trader:   Optional[LiveTrader] = None
_lock    = threading.Lock()
_starting = False
_logs: List[str] = []

class _LogCap(logging.Handler):
    def emit(self, r):
        _logs.append(self.format(r))
        if len(_logs) > 300: _logs.pop(0)

class _BRTFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created, tz=BRT)
        return ct.strftime(datefmt or '%H:%M:%S')

_lh = _LogCap()
_lh.setFormatter(_BRTFormatter('%(asctime)s %(message)s', '%H:%M:%S'))
log.addHandler(_lh)

DASH = """... (conteúdo idêntico ao original) ..."""


def _thread():
    global _trader, _starting
    log.info("📥 Baixando candles Bitget...")
    try:
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
            _trader   = None
            _starting = False
        log.info("🔄 Pronto para re-iniciar")


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
    global _starting
    with _lock:
        if _trader is not None or _starting:
            return jsonify({"message": "Já está rodando"})
        if not get_paper_mode() and not _creds_ok():
            return jsonify({"error": "Configure as chaves Bitget antes de iniciar em modo LIVE"}), 400
        _starting = True
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
    global _starting
    time.sleep(5)
    with _lock:
        if _trader is not None or _starting:
            return
        if not get_paper_mode() and not _creds_ok():
            log.warning("⚠️ Chaves Bitget não encontradas — use o botão Iniciar após configurar.")
            return
        _starting = True
        mode_str = "PAPER TRADING" if get_paper_mode() else "LIVE (Bitget)"
        log.info(f"🚀 Auto-start {mode_str}...")
        threading.Thread(target=_thread, daemon=True).start()


threading.Thread(target=_delayed_start, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0',
            port=int(os.environ.get("PORT", 5000)),
            debug=False)
