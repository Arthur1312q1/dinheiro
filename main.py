"""
AZLEMA Live Trading — Bitget ETH-USDT-SWAP Futures 1x
Render: configurar BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE

CORREÇÕES APLICADAS:
══════════════════════════════════════════════════════════════════════
FIX-1  Duplicate Candle Processing
FIX-2  Signal Inversion / State Desync
FIX-3  Intrabar Stop Parity (GAP-FIX)
FIX-4  Execution Refactoring
FIX-5  Candle Open Price Parity
FIX-6  1-Candle Entry Delay (get_pending_orders + confirm_fill)
  - BUY/SELL de strategy.next() são IGNORADOS no live.
  - Entradas vêm de get_pending_orders() após processar o candle fechado.
  - confirm_fill() é o ÚNICO mecanismo de sincronização de estado
    (position_price, _highest, _lowest, trailing stop). Zero overrides
    manuais no loop live.
FIX-7  Zero-Delay Fill Price
  - fill_px usa _mark_price() (preço de mercado atual) em vez do
    open do candle N+1, eliminando o atraso residual de 1 candle
    na execução de entradas.
FIX-8  Exit Price Parity (saídas de next() com mark price)
  - EXIT_LONG/EXIT_SHORT retornados por next() agora também usam
    _mark_price() como exit_fill_px no modo live, em vez do preço
    teórico do candle fechado (a_price). Fallback: close do candle.
  - Logs, _add_log e history_mgr refletem o preço real de execução.
FIX-9  Refatoração Estrutural do Bloco "Novo Candle Detectado"
  - Sequência explícita de 4 passos: Update Strategy → Get Pending
    Orders → Execute Market Order → Confirm Fill Strategy.
  - Unificação do fluxo paper/live: early-continue em caso de falha,
    Passo 4 (confirm_fill) em caminho único após execução bem-sucedida.
FIX-10 Remoção do Delay Inicial de 1 Candle
  - Após warmup, processa imediatamente o último candle do histórico,
    executando possíveis sinais pendentes sem esperar o próximo
    fechamento real. Isso elimina a latência de uma vela no início
    da operação ao vivo.
FIX-11 Alinhamento Total com Mark Price (URGENTE)
  - _mark_price() agora é fonte única de verdade e usa retry ativo
    (até 5 tentativas, 0.5s) para obter preço real. Fallbacks para
    closed_candle['close'] foram completamente removidos.
  - Execuções de entrada e saída utilizam exclusivamente o mark price
    obtido no momento da ordem; se falhar, a ordem é cancelada com log.
  - Sleep dinâmico: 2s quando posição aberta, 15s quando flat – garante
    alta frequência para trailing stop sem sobrecarregar a API.
  - confirm_fill() recebe o preço exato de execução (fill_px) e atualiza
    a estratégia imediatamente, sincronizando o trailing stop com o
    preço real pago.
FIX-12 Mark Price Instantâneo no Trailing Stop (CORREÇÃO 1 — strategy)
  - update_trailing_live injeta current_price em eff_high/eff_low,
    garantindo que o stop seja verificado contra o mark price real
    e não apenas as extremas do candle REST (que têm atraso).
  - Gatilho duplo: eff_low/eff_high (candle + mark) E eff_curr direto.
FIX-13 Fallback Seguro no Exit Intra-Barra (CORREÇÃO 2 — main)
  - Eliminada chamada redundante a _mark_price() dentro do bloco
    if exit_act (trailing stop intra-barra).
  - exit_fill_px reutiliza ticker_px (já obtido como gatilho do stop)
    ou cai para px_exit (preço do stop calculado pela estratégia).
  - Evita cancelamento indevido de saídas por falha de rede ao tentar
    um segundo pull de mark price que não é necessário.
FIX-14 Loop de Trailing Stop Desacoplado do Fetch de Candle (URGENTE)
  - O trailing stop intra-barra agora é verificado em TODA iteração do
    loop quando há posição aberta (position_size != 0), ANTES do fetch
    de candle. Anteriormente, qualquer falha ou `continue` no fetch
    (candles is None, len < 2, etc.) pulava silenciosamente a verificação
    de stop, permitindo que posições ficassem abertas além do stop loss.
  - sleep_secs reduzido para 1 s quando em posição (antes: 2 s).
  - _mark_price_fast() adicionado: 2 tentativas × 0.2 s = max 0.4 s
    de bloqueio (vs 2.5 s do _mark_price completo), reduzindo latência
    total do loop de ~5-7 s para ~1.5-2.5 s por iteração.
  - Cache _forming_high/_forming_low: H/L do candle em formação é
    guardado após cada fetch bem-sucedido e reutilizado pelo trailing
    stop nas iterações onde o fetch falha. Na primeira iteração
    (valores default 0 / inf), o current_price (mark price) cobre
    ambas as extremas via FIX-12 (eff_high/eff_low).
  - Após EXIT intra-barra, o cache forming é resetado para garantir
    que a próxima posição comece com extremas limpas.
  - Stops obsoletos (long_stop/short_stop) são zerados em
    update_trailing_live quando position_size == 0 — evita leitura
    de valores residuais de trades anteriores pelo main.py.
  - is_entry_candle=True agora rastreia movimentos pós-fill favoráveis
    atualizando _highest/_lowest com current_price, permitindo ativação
    precoce do trailing se o preço mover o suficiente antes do próximo
    poll de candle (poll de ~1 s vs espera de fechamento de barra).
FIX-15 Snapshot de Preço Único por Ciclo de Candle (CORREÇÃO FINAL DE LATÊNCIA)
  - _mark_price() é capturado UMA ÚNICA VEZ por ciclo de candle fechado,
    imediatamente antes de qualquer envio de ordem (saída ou entrada).
    Esse snapshot_px é então reutilizado para:
      (a) bitget.close_long / close_short (exit_px enviado à exchange)
      (b) paper.close_long / close_short (exit_px simulado)
      (c) bitget.open_long / open_short (fill_px enviado à exchange)
      (d) strategy.confirm_fill(fill_px=snapshot_px) — sincroniza trailing stop
    Isso elimina chamadas redundantes a _mark_price() por ordem (eram N
    chamadas/ciclo, agora é sempre 1), garante paridade de preço entre
    o envio da ordem e o confirm_fill, e reduz latência intra-ciclo.
  - Se snapshot_px não puder ser obtido (mark price indisponível), o ciclo
    é marcado como processado (ts_raw retornado) mas nenhuma ordem é
    enviada — evitando ordens sem referência de preço.
  - Sequência explícita do ciclo de candle fechado:
      PASSO 1 → strategy.next(closed_candle)     [sinais da estratégia]
      PASSO 2 → get_pending_orders()             [entradas pendentes]
      PASSO 3 → snapshot_px = _mark_price()     [único pull de preço]
      PASSO 4 → executar saídas (exits)         [com snapshot_px]
      PASSO 5 → executar entradas (pending)     [com snapshot_px]
      PASSO 6 → confirm_fill(fill_px=snapshot_px) [sincroniza estratégia]
══════════════════════════════════════════════════════════════════════
"""
import os, hmac, hashlib, base64, json, time, threading, traceback, logging, requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from pathlib import Path
from flask import Flask, jsonify, request as flask_request

BRT = timezone(timedelta(hours=-3))

def brazil_now() -> datetime:
    return datetime.now(BRT)

def brazil_iso() -> str:
    return brazil_now().strftime('%Y-%m-%dT%H:%M:%S')

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import DataCollector

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('azlema')

SYMBOL         = "ETH-USDT"
SYMBOL_ID      = "ETHUSDT"
TIMEFRAME      = "30m"
TOTAL_CANDLES  = 300
WARMUP_CANDLES = min(50, TOTAL_CANDLES // 5)
STRATEGY_CONFIG = {
    "adaptive_method": "Cos IFM", "threshold": 0.0,
    "fixed_sl_points": 2000, "fixed_tp_points": 55, "trail_offset": 15,
    "risk_percent": 0.01, "tick_size": 0.01, "initial_capital": 1000.0,
    "max_lots": 100, "default_period": 20, "warmup_bars": WARMUP_CANDLES,
}

_PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() in ("true", "1", "yes")
PAPER_BALANCE  = float(os.environ.get("PAPER_BALANCE", "1000.0"))
LIVE_PCT       = 0.95
HISTORY_FILE          = "trades_history.json"
BACKTEST_HISTORY_FILE = "backtest_history.json"

# ── Taxas Live (Bitget Taker Futures) ────────────────────────────────────────
# Mantidas em sync com engine.py → open_fee_pct / close_fee_pct.
# PnL líquido = PnL_bruto − open_fee − close_fee
OPEN_FEE_PCT  = float(os.environ.get("OPEN_FEE_PCT",  "0.06"))  # 0.06 = 0.06%
CLOSE_FEE_PCT = float(os.environ.get("CLOSE_FEE_PCT", "0.06"))  # 0.06 = 0.06%

def _calc_fee(price: float, qty: float, pct: float) -> float:
    """Taxa = valor_nocional × pct / 100 — idêntico a engine.py._fee()"""
    return abs(price * qty * pct / 100.0)

def get_paper_mode() -> bool:  return _PAPER_TRADING
def set_paper_mode(val: bool):
    global _PAPER_TRADING; _PAPER_TRADING = val

def _key():      return os.environ.get("BITGET_API_KEY",    "").strip()
def _sec():      return os.environ.get("BITGET_SECRET_KEY", "").strip()
def _pass():     return os.environ.get("BITGET_PASSPHRASE", "").strip()
def _creds_ok(): return bool(_key() and _sec() and _pass())

LOCK_FILE = "bot.lock"

def _acquire_lock() -> bool:
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.close(fd)
        log.debug("Lock de processo adquirido.")
        return True
    except FileExistsError:
        log.warning("Lock de processo já existe. Outro worker está rodando?")
        return False
    except Exception as e:
        log.error(f"Erro ao tentar criar lock: {e}")
        return False

def _release_lock():
    try:
        if Path(LOCK_FILE).exists():
            Path(LOCK_FILE).unlink()
            log.info("🔓 Cadeado (lock) removido com sucesso.")
    except Exception as e:
        log.error(f"Erro ao remover lock: {e}")

_release_lock()


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

    def close_trade(self, trade_id: str, exit_price: float, exit_time: str,
                    exit_reason: str, pnl: float):
        with self._lock:
            for t in self._data["trades"]:
                if t.get("id") == trade_id:
                    entry = t.get("entry_price", exit_price)
                    pnl_pct = ((exit_price - entry) / entry * 100) if t.get("action") == "BUY" \
                              else ((entry - exit_price) / entry * 100)
                    t.update({
                        "status":      "closed",
                        "exit_price":  exit_price,
                        "exit_time":   exit_time,
                        "exit_reason": exit_reason,
                        "pnl_usdt":    pnl,
                        "pnl_pct":     round(pnl_pct, 4),
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
        total_pnl  = sum(t.get("pnl_usdt", 0) for t in closed)
        gross_win  = sum(wins)
        gross_loss = abs(sum(losses))
        n = len(closed)
        return {
            "total":         n,
            "wins":          len(wins),
            "losses":        len(losses),
            "win_rate":      round(len(wins) / n * 100, 2) if n else 0,
            "total_pnl":     round(total_pnl, 4),
            "avg_win":       round(sum(wins)   / len(wins),   4) if wins   else 0,
            "avg_loss":      round(sum(losses) / len(losses), 4) if losses else 0,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf"),
            "best_trade":    round(max(wins),   4) if wins   else 0,
            "worst_trade":   round(min(losses), 4) if losses else 0,
            "avg_pnl":       round(total_pnl / n, 4) if n else 0,
            "expectancy":    round(
                (len(wins)/n   * (sum(wins)/len(wins)     if wins   else 0) +
                 len(losses)/n * (sum(losses)/len(losses) if losses else 0)), 4
            ) if n else 0,
        }

    def clear(self):
        with self._lock:
            self._data = {"trades": [], "sessions": []}
            self._save()


history_mgr  = TradeHistoryManager(HISTORY_FILE)
backtest_mgr = TradeHistoryManager(BACKTEST_HISTORY_FILE)


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
        open_fee = _calc_fee(px, qty, OPEN_FEE_PCT)
        history_mgr.add_trade({
            "id": trade_id, "action": "BUY", "status": "open",
            "entry_time": str(ts), "entry_price": px,
            "qty": qty, "balance": self.balance, "mode": "paper",
            "open_fee": round(open_fee, 6),
        })
        self.position = {"side": "long", "size": qty, "avg_px": px,
                         "id": trade_id, "open_fee": open_fee}
        log.info(f"  📄 PAPER LONG aberto | px={px:.2f} qty={qty:.4f} "
                 f"open_fee={open_fee:.4f}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def open_short(self, qty, bal_usdt=0, px=0, ts=None):
        trade_id = self._new_id()
        ts = ts or brazil_iso()
        open_fee = _calc_fee(px, qty, OPEN_FEE_PCT)
        history_mgr.add_trade({
            "id": trade_id, "action": "SELL", "status": "open",
            "entry_time": str(ts), "entry_price": px,
            "qty": qty, "balance": self.balance, "mode": "paper",
            "open_fee": round(open_fee, 6),
        })
        self.position = {"side": "short", "size": qty, "avg_px": px,
                         "id": trade_id, "open_fee": open_fee}
        log.info(f"  📄 PAPER SHORT aberto | px={px:.2f} qty={qty:.4f} "
                 f"open_fee={open_fee:.4f}")
        return {"code": "0", "data": [{"ordId": trade_id}]}, qty

    def close_long(self, qty, exit_px=0, reason="EXIT", ts=None):
        """Fecha posição long simulada.

        - exit_px  : preço de saída (mark price no momento do disparo).
        - PnL líquido = PnL_bruto − open_fee − close_fee  (paridade com engine.py).
        - Retorna _fill_px no dict para que LiveTrader use o preço correto em _add_log.
        """
        if not self.position or self.position["side"] != "long":
            return {"code": "0", "_fill_px": exit_px}
        entry_px  = self.position["avg_px"]
        trade_id  = self.position["id"]
        qty_real  = self.position["size"]
        open_fee  = self.position.get("open_fee", 0.0)
        close_fee = _calc_fee(exit_px, qty_real, CLOSE_FEE_PCT)
        pnl_gross = (exit_px - entry_px) * qty_real
        pnl_net   = pnl_gross - open_fee - close_fee
        self.position = None
        ts = str(ts) if ts else brazil_iso()
        try:
            history_mgr.close_trade(trade_id, exit_px, ts, reason,
                                    round(pnl_net, 6))
        except Exception as _e:
            log.warning(f"  ⚠️ close_trade (long) file error: {_e}")
        log.info(f"  📄 PAPER LONG fechado | px={exit_px:.2f} "
                 f"pnl_gross={pnl_gross:+.4f} fees={open_fee+close_fee:.4f} "
                 f"pnl_net={pnl_net:+.4f} USDT")
        return {"code": "0", "_fill_px": exit_px}

    def close_short(self, qty, exit_px=0, reason="EXIT", ts=None):
        """Fecha posição short simulada.

        - exit_px  : preço de saída (mark price no momento do disparo).
        - PnL líquido = PnL_bruto − open_fee − close_fee  (paridade com engine.py).
        - Retorna _fill_px no dict para que LiveTrader use o preço correto em _add_log.
        """
        if not self.position or self.position["side"] != "short":
            return {"code": "0", "_fill_px": exit_px}
        entry_px  = self.position["avg_px"]
        trade_id  = self.position["id"]
        qty_real  = self.position["size"]
        open_fee  = self.position.get("open_fee", 0.0)
        close_fee = _calc_fee(exit_px, qty_real, CLOSE_FEE_PCT)
        pnl_gross = (entry_px - exit_px) * qty_real
        pnl_net   = pnl_gross - open_fee - close_fee
        self.position = None
        ts = str(ts) if ts else brazil_iso()
        try:
            history_mgr.close_trade(trade_id, exit_px, ts, reason,
                                    round(pnl_net, 6))
        except Exception as _e:
            log.warning(f"  ⚠️ close_trade (short) file error: {_e}")
        log.info(f"  📄 PAPER SHORT fechado | px={exit_px:.2f} "
                 f"pnl_gross={pnl_gross:+.4f} fees={open_fee+close_fee:.4f} "
                 f"pnl_net={pnl_net:+.4f} USDT")
        return {"code": "0", "_fill_px": exit_px}

    def get_position(self): return self.position
    def get_balance(self):  return self.balance


class Bitget:
    BASE         = "https://api.bitget.com"
    SYMBOL       = "ETHUSDT"
    PRODUCT_TYPE = "usdt-futures"
    MARGIN       = "USDT"
    CT_VAL       = 0.01

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

    MIN_QTY_ETH = 0.01

    def _cts(self, qty_eth, bal=0, px=0):
        MIN_CTS = int(self.MIN_QTY_ETH / self.CT_VAL)
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
        r   = self._post("/api/v2/mix/order/place-order", body)
        d0  = r.get("data") or {}
        tag = f"{'CLOSE' if reduce_only else 'OPEN'}/{side.upper()}"
        if r.get("code") == "00000":
            log.info(f"  ✅ ORDER {tag} sz={sz_cts}cts={size_eth}ETH "
                     f"ordId={d0.get('orderId','?')}")
        else:
            log.error(f"  ❌ ORDER {tag} sz={sz_cts}cts={size_eth}ETH "
                      f"code={r.get('code','')} msg={r.get('msg','')}")
        return r

    def open_long(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        if sz == 0:
            return {"code": "SKIP", "msg": "Saldo insuficiente"}, 0.0
        r  = self._order("buy", False, sz)
        if r.get("code") == "00000":
            oid      = (r.get("data") or {}).get("orderId", "?")
            qty_eth  = sz * self.CT_VAL
            open_fee = _calc_fee(px, qty_eth, OPEN_FEE_PCT)
            history_mgr.add_trade({
                "id": str(oid), "action": "BUY", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": qty_eth, "balance": bal, "mode": "live",
                "open_fee": round(open_fee, 6),
            })
        return r, sz * self.CT_VAL

    def open_short(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        if sz == 0:
            return {"code": "SKIP", "msg": "Saldo insuficiente"}, 0.0
        r  = self._order("sell", False, sz)
        if r.get("code") == "00000":
            oid      = (r.get("data") or {}).get("orderId", "?")
            qty_eth  = sz * self.CT_VAL
            open_fee = _calc_fee(px, qty_eth, OPEN_FEE_PCT)
            history_mgr.add_trade({
                "id": str(oid), "action": "SELL", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": qty_eth, "balance": bal, "mode": "live",
                "open_fee": round(open_fee, 6),
            })
        return r, sz * self.CT_VAL

    def _fetch_fill_price(self, order_id: str,
                          max_attempts: int = 4,
                          delay: float = 0.25) -> Optional[float]:
        """
        Busca o preço médio de execução (fill) de uma ordem já enviada.

        Consulta /api/v2/mix/order/detail com polling ativo (o fill pode demorar
        alguns ms para aparecer). Retorna None se todas as tentativas falharem.

        Campos tentados em ordem de prioridade:
          priceAvg → fillPrice → avgPrice
        """
        for attempt in range(1, max_attempts + 1):
            try:
                r = self._get("/api/v2/mix/order/detail", {
                    "symbol":      self.SYMBOL,
                    "productType": self.PRODUCT_TYPE,
                    "orderId":     order_id,
                })
                if r.get("code") == "00000":
                    d   = r.get("data") or {}
                    raw = (d.get("priceAvg")
                           or d.get("fillPrice")
                           or d.get("avgPrice"))
                    if raw:
                        px = float(raw)
                        if px > 0:
                            log.info(
                                f"  🎯 [FILL-PRICE] orderId={order_id} "
                                f"fill_px={px:.2f} (tentativa {attempt})"
                            )
                            return px
            except Exception as _e:
                log.debug(f"  _fetch_fill_price tentativa {attempt}: {_e}")
            if attempt < max_attempts:
                time.sleep(delay)

        log.warning(
            f"  ⚠️ [FILL-PRICE] Não foi possível obter fill price "
            f"para orderId={order_id} — usando trigger_px como fallback"
        )
        return None

    def close_long(self, qty, trigger_px: float = 0.0, reason: str = "EXIT"):
        """
        Fecha posição long na Bitget.

        Fluxo de preço de execução (FIX-16):
          1. Envia ordem market → captura orderId.
          2. _fetch_fill_price(orderId) — preço REAL da exchange (até 4 polls × 0.25 s).
          3. Fallback: trigger_px (mark price capturado no momento do disparo).

        PnL gravado = PnL_bruto − open_fee − close_fee  (paridade com engine.py).
        Retorna r["_fill_px"] para que LiveTrader use o preço real em _add_log.

        Args:
            qty        : quantidade em ETH (convertida para contratos internamente).
            trigger_px : preço de mercado capturado ANTES de enviar a ordem
                         (usado como fallback se a API de detalhe falhar).
            reason     : motivo da saída ('TRAIL', 'SL', 'EXIT_LONG', 'REVERSAL', …).
        """
        sz  = self._cts(qty)
        r   = self._order("sell", True, sz)

        if r.get("code") == "00000":
            order_id = (r.get("data") or {}).get("orderId", "")

            # ── Preço Real de Execução ──────────────────────────────────────
            fill_px: float = trigger_px   # fallback inicial
            if order_id:
                fetched = self._fetch_fill_price(order_id)
                if fetched and fetched > 0:
                    fill_px = fetched
                    if abs(fill_px - trigger_px) / max(trigger_px, 1) > 0.005:
                        log.warning(
                            f"  ⚠️ [SLIPPAGE] close_long "
                            f"trigger={trigger_px:.2f} fill={fill_px:.2f} "
                            f"diff={fill_px - trigger_px:+.2f} USDT"
                        )

            # ── PnL líquido com taxas (= engine.py) ────────────────────────
            qty_eth = sz * self.CT_VAL
            ts      = brazil_iso()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "BUY" and t.get("status") == "open":
                    entry_price = t.get("entry_price", fill_px)
                    pnl_gross   = (fill_px - entry_price) * qty_eth
                    open_fee    = t.get("open_fee", 0.0)
                    close_fee   = _calc_fee(fill_px, qty_eth, CLOSE_FEE_PCT)
                    fees_total  = open_fee + close_fee
                    pnl_net     = pnl_gross - fees_total
                    history_mgr.close_trade(
                        t["id"], fill_px, ts, reason, round(pnl_net, 6)
                    )
                    log.info(
                        f"  💰 close_long | fill={fill_px:.2f} "
                        f"pnl_gross={pnl_gross:+.4f} "
                        f"fees={fees_total:.4f} "
                        f"pnl_net={pnl_net:+.4f} USDT"
                    )
                    break

            r["_fill_px"] = fill_px   # expõe para LiveTrader._add_log

        return r

    def close_short(self, qty, trigger_px: float = 0.0, reason: str = "EXIT"):
        """
        Fecha posição short na Bitget.

        Idêntico a close_long mas para posições vendidas:
          PnL_bruto = (entry_price − fill_px) × qty_eth.
        Retorna r["_fill_px"] para que LiveTrader use o preço real em _add_log.

        Args:
            qty        : quantidade em ETH (convertida para contratos internamente).
            trigger_px : preço de mercado capturado ANTES de enviar a ordem
                         (usado como fallback se a API de detalhe falhar).
            reason     : motivo da saída ('TRAIL', 'SL', 'EXIT_SHORT', 'REVERSAL', …).
        """
        sz  = self._cts(qty)
        r   = self._order("buy", True, sz)

        if r.get("code") == "00000":
            order_id = (r.get("data") or {}).get("orderId", "")

            # ── Preço Real de Execução ──────────────────────────────────────
            fill_px: float = trigger_px   # fallback inicial
            if order_id:
                fetched = self._fetch_fill_price(order_id)
                if fetched and fetched > 0:
                    fill_px = fetched
                    if abs(fill_px - trigger_px) / max(trigger_px, 1) > 0.005:
                        log.warning(
                            f"  ⚠️ [SLIPPAGE] close_short "
                            f"trigger={trigger_px:.2f} fill={fill_px:.2f} "
                            f"diff={fill_px - trigger_px:+.2f} USDT"
                        )

            # ── PnL líquido com taxas (= engine.py) ────────────────────────
            qty_eth = sz * self.CT_VAL
            ts      = brazil_iso()
            for t in reversed(history_mgr.get_all_trades()):
                if t.get("action") == "SELL" and t.get("status") == "open":
                    entry_price = t.get("entry_price", fill_px)
                    pnl_gross   = (entry_price - fill_px) * qty_eth
                    open_fee    = t.get("open_fee", 0.0)
                    close_fee   = _calc_fee(fill_px, qty_eth, CLOSE_FEE_PCT)
                    fees_total  = open_fee + close_fee
                    pnl_net     = pnl_gross - fees_total
                    history_mgr.close_trade(
                        t["id"], fill_px, ts, reason, round(pnl_net, 6)
                    )
                    log.info(
                        f"  💰 close_short | fill={fill_px:.2f} "
                        f"pnl_gross={pnl_gross:+.4f} "
                        f"fees={fees_total:.4f} "
                        f"pnl_net={pnl_net:+.4f} USDT"
                    )
                    break

            r["_fill_px"] = fill_px   # expõe para LiveTrader._add_log

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


class RealTimeStopMonitor:
    def __init__(self, trader: 'LiveTrader'):
        self.trader  = trader
        self._active = False

    def arm_with_state(self, *args, **kwargs):
        self._active = True

    def disarm(self):
        if self._active:
            log.debug("  🔕 StopMonitor desarmado")
        self._active = False


class LiveTrader:
    def __init__(self):
        self._paper_mode = get_paper_mode()
        if self._paper_mode:
            self.paper  = PaperTrader(PAPER_BALANCE)
            self.bitget = None
        else:
            self.paper  = None
            self.bitget = Bitget()

        self.strategy         = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        self._running         = False
        self._warming         = False
        self.log: List[Dict]  = []
        self._pnl_baseline    = 0.0
        self._cache_pos: Optional[Dict] = None
        self._cache_bal: float = PAPER_BALANCE if self._paper_mode else 0.0
        self._cache_px:  float = 0.0
        self._pos_lock        = threading.Lock()
        self._stop_monitor    = RealTimeStopMonitor(self)
        self._pending_entry_check = False   # flag para monitoramento após entrada

        # FIX-14: Cache do candle em formação — necessário para trailing stop
        # independente do sucesso do fetch de candle (loop desacoplado)
        self._forming_high: float = 0.0
        self._forming_low:  float = float('inf')
        self._forming_ts           = None

    def _is_paper(self) -> bool:
        return self._paper_mode

    def _get_mark_price_with_retry(self, max_attempts: int = 5, delay: float = 0.5) -> Optional[float]:
        """Tenta obter o mark price da Bitget com retries. Retorna None se falhar."""
        for attempt in range(1, max_attempts + 1):
            try:
                r = requests.get(
                    "https://api.bitget.com/api/v2/mix/market/symbol-price",
                    params={"symbol": "ETHUSDT", "productType": "usdt-futures"},
                    timeout=5
                ).json()
                if r.get("code") == "00000":
                    price = float(r["data"][0]["markPrice"])
                    if price > 0:
                        return price
            except Exception:
                pass
            if attempt < max_attempts:
                time.sleep(delay)
        log.error("  ❌ Não foi possível obter mark price após várias tentativas.")
        return None

    def _mark_price(self) -> Optional[float]:
        """Fonte única de verdade para preço de mercado. Sem fallback para close."""
        return self._get_mark_price_with_retry()

    def _mark_price_fast(self) -> Optional[float]:
        """
        Versão rápida para polling de trailing stop intra-barra.
        2 tentativas × 0.2 s = max 0.4 s de bloqueio (vs 2.5 s do _mark_price completo).
        """
        return self._get_mark_price_with_retry(max_attempts=2, delay=0.2)

    def close_long(self, reason: str, trigger_px: float):
        try:
            # 1. Captura o preço REAL exato no momento do fechamento
            real_exit_px = self._mark_price()

            if not self._is_paper():
                # Executa ordem a mercado na exchange
                self.bitget.close_long(abs(self.strategy.position_size), real_exit_px, reason)
            else:
                self._paper_close_long(real_exit_px, reason, brazil_iso())

            # 2. Registra log e atualiza estado com o PREÇO REAL
            self._add_log("SELL", real_exit_px, abs(self.strategy.position_size), reason)
            self.strategy.position_size = 0
            self.strategy.position_price = 0.0
            self._cache_pos = None
            log.info(f"🔴 LONG Fechado a {real_exit_px} (Gatilho teórico: {trigger_px}) - Motivo: {reason}")
        except Exception as e:
            log.error(f"Erro crítico ao fechar LONG: {e}")

    def close_short(self, reason: str, trigger_px: float):
        try:
            real_exit_px = self._mark_price()

            if not self._is_paper():
                self.bitget.close_short(abs(self.strategy.position_size), real_exit_px, reason)
            else:
                self._paper_close_short(real_exit_px, reason, brazil_iso())

            self._add_log("BUY", real_exit_px, abs(self.strategy.position_size), reason)
            self.strategy.position_size = 0
            self.strategy.position_price = 0.0
            self._cache_pos = None
            log.info(f"🟢 SHORT Fechado a {real_exit_px} (Gatilho teórico: {trigger_px}) - Motivo: {reason}")
        except Exception as e:
            log.error(f"Erro crítico ao fechar SHORT: {e}")

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
        self._stop_monitor.disarm()

        if len(df) % 2 != 0:
            df = df.iloc[1:].reset_index(drop=True)
            log.info(f"  📐 Paridade: descartado 1 candle → {len(df)} (par)")

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

        if self.strategy.position_size != 0:
            log.info(f"  ↩️ Posição virtual do warmup descartada: "
                     f"{self.strategy.position_size:+.6f} ETH "
                     f"@ {self.strategy.position_price:.2f}")

        self.strategy.position_size  = 0.0
        self.strategy.position_price = 0.0
        self.strategy._highest       = 0.0
        self.strategy._lowest        = float('inf')
        self.strategy._trail_active  = False
        self.strategy._monitored     = False

        self.strategy._pBuy          = False
        self.strategy._pSell         = False
        self.strategy._el            = False
        self.strategy._es            = False
        self.strategy._buy_prev      = False
        self.strategy._sell_prev     = False
        self.strategy._live_bar_count = 0

        self.strategy.net_profit     = 0.0
        self.strategy.balance        = self.strategy.ic

        self._pnl_baseline = 0.0
        self._warming      = False

        last_close = float(df['close'].iloc[-1])
        if last_close > 0:
            self._cache_px = last_close
        self._refresh_cache()
        log.info(f"  ✅ Warmup OK | Period={self.strategy.Period} | "
                 f"EC={self.strategy.EC:.2f} | EMA={self.strategy.EMA:.2f} | "
                 f"liveBarCount=0 (resetado) | "
                 f"buy_prev=False sell_prev=False")

    @property
    def live_pnl(self):
        return self.strategy.net_profit - self._pnl_baseline

    def _candle_single(self) -> Optional[List[List]]:
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
                timeout=5,
            ).json()
            if r.get("code") != "00000":
                if r.get("code") == "429":
                    log.warning("  ⚠️ Rate limit (429)")
                else:
                    log.error(f"  ❌ Bitget candles: code={r.get('code')} msg={r.get('msg')}")
                return None
            data = r.get("data", [])
            if len(data) < 2:
                log.debug("  ℹ️ Apenas 1 candle disponível")
                return None
            return data
        except Exception as e:
            log.error(f"  ❌ _candle_single erro: {e}")
            return None

    def _paper_close_long(self, price: float, reason: str, ts):
        pos = self.paper.get_position()
        if pos and pos['side'] == 'long':
            r = self.paper.close_long(pos['size'], price, reason, ts=ts)
            self._cache_pos = None
            return r
        return {"code": "0", "_fill_px": price}

    def _paper_close_short(self, price: float, reason: str, ts):
        pos = self.paper.get_position()
        if pos and pos['side'] == 'short':
            r = self.paper.close_short(pos['size'], price, reason, ts=ts)
            self._cache_pos = None
            return r
        return {"code": "0", "_fill_px": price}

    # ------------------------------------------------------------------
    # Processamento centralizado de um candle fechado (reutilizado)
    # ------------------------------------------------------------------
    def _process_closed_candle(self, closed_candle: Dict, ts_raw: int,
                               last_processed_ts: Optional[int]) -> Optional[int]:
        """
        Processa um candle fechado (estrategia, entradas/saídas).
        Retorna o timestamp processado (ts_raw) se tudo ocorreu bem,
        ou o valor anterior (last_processed_ts) em caso de falha.
        """
        with self._pos_lock:
            # ── PASSO 1: Atualizar estratégia com o candle fechado ─────────────
            actions = self.strategy.next(closed_candle)
            log.debug(f"  📊 strategy.next() → {len(actions)} ações")

            # ── PASSO 2: Obter ordens pendentes IMEDIATAMENTE após next() ──────
            # (FIX-15) Coletadas no mesmo ciclo, sem esperar próxima iteração.
            pending_orders = self.strategy.get_pending_orders()
            if pending_orders:
                log.debug(f"  📋 {len(pending_orders)} ordem(ns) pendente(s)")

            exits = [a for a in actions
                     if a.get('action') in ('EXIT_LONG', 'EXIT_SHORT')]

            # ── PASSO 3: Snapshot único de Mark Price ─────────────────────────
            # (FIX-15) _mark_price() chamado UMA VEZ por ciclo, imediatamente
            # antes do envio de qualquer ordem. O mesmo valor é usado para:
            #   • bitget/paper close_long|close_short  (exit_px real)
            #   • bitget/paper open_long|open_short    (fill_px real)
            #   • strategy.confirm_fill(fill_px=...)   (sincroniza trailing stop)
            # Isso garante paridade perfeita entre o preço enviado à exchange
            # e o preço injetado na estratégia, eliminando divergências de PnL.
            needs_price = bool(exits or pending_orders)
            snapshot_px: Optional[float] = None
            if needs_price:
                snapshot_px = self._mark_price()
                if snapshot_px is None:
                    log.error(
                        "  ❌ [FIX-15] Mark price indisponível — "
                        f"{len(exits)} saída(s) e {len(pending_orders)} "
                        "entrada(s) canceladas neste ciclo. "
                        "Candle marcado como processado para evitar reprocessamento."
                    )
                    return ts_raw   # candle consumido; não reprocessar
                log.info(
                    f"  📍 [FIX-15] snapshot_px={snapshot_px:.2f} "
                    f"({len(exits)} saída(s) | {len(pending_orders)} entrada(s))"
                )

            # ── PASSO 4: Processar saídas com snapshot_px ─────────────────────
            for act in exits:
                kind  = act.get('action', '')
                a_qty = float(act.get('qty') or 0)
                a_rsn = act.get('exit_reason', kind)
                a_ts  = act.get('timestamp', closed_candle['timestamp'])

                # snapshot_px é o trigger: mark price no momento do envio da ordem.
                # O fill real é buscado DENTRO de close_long/close_short (FIX-16).
                trigger_px = snapshot_px  # type: ignore[assignment]

                if kind == 'EXIT_LONG':
                    if self._is_paper():
                        r_close   = self._paper_close_long(trigger_px, a_rsn, a_ts)
                        fill_exit = r_close.get("_fill_px", trigger_px)
                    else:
                        try:
                            r_close = self.bitget.close_long(a_qty, trigger_px, a_rsn)
                        except Exception as _e:
                            log.error(f"  ❌ live close_long: {_e}")
                            continue
                        fill_exit = r_close.get("_fill_px", trigger_px)
                    self._add_log('EXIT_LONG', fill_exit, a_qty, a_rsn)
                    self._cache_pos = None
                    self._cache_bal = self.strategy.balance
                    if self._is_paper():
                        self.paper.balance = self.strategy.balance
                    log.info(f"  ✅ EXIT_LONG trigger={trigger_px:.2f} "
                             f"fill={fill_exit:.2f} | {a_rsn} "
                             f"| bal={self.strategy.balance:.2f}")

                elif kind == 'EXIT_SHORT':
                    if self._is_paper():
                        r_close   = self._paper_close_short(trigger_px, a_rsn, a_ts)
                        fill_exit = r_close.get("_fill_px", trigger_px)
                    else:
                        try:
                            r_close = self.bitget.close_short(a_qty, trigger_px, a_rsn)
                        except Exception as _e:
                            log.error(f"  ❌ live close_short: {_e}")
                            continue
                        fill_exit = r_close.get("_fill_px", trigger_px)
                    self._add_log('EXIT_SHORT', fill_exit, a_qty, a_rsn)
                    self._cache_pos = None
                    self._cache_bal = self.strategy.balance
                    if self._is_paper():
                        self.paper.balance = self.strategy.balance
                    log.info(f"  ✅ EXIT_SHORT trigger={trigger_px:.2f} "
                             f"fill={fill_exit:.2f} | {a_rsn} "
                             f"| bal={self.strategy.balance:.2f}")

            # ── PASSO 5 + 6: Executar entradas e confirmar fill ───────────────
            # fill_px = snapshot_px é o mesmo preço usado no _order() e no
            # confirm_fill(), garantindo que trailing stop e position_price
            # da estratégia reflitam o preço real de execução (FIX-15).
            for order in pending_orders:
                side  = order['side']
                o_qty = order['qty']
                if o_qty <= 0:
                    continue

                fill_px = snapshot_px  # type: ignore[assignment]
                # (fill_px não é None aqui: needs_price=True e snapshot verificado acima)

                if side == 'BUY':
                    if self._is_paper():
                        pos = self.paper.get_position()
                        if pos and pos['side'] == 'long':
                            continue
                        if pos and pos['side'] == 'short':
                            log.warning("  ⚠️ BUY: fechando short residual (reversal)")
                            self._paper_close_short(fill_px, 'REVERSAL', closed_candle['timestamp'])
                        log.info(f"  🟢 [PAPER] ENTER LONG {o_qty:.6f} ETH @ {fill_px:.2f}")
                        r, qty_f = self.paper.open_long(o_qty, self._cache_bal, fill_px, ts=closed_candle['timestamp'])
                        if r.get("code") != "0":
                            log.error("  ❌ paper.open_long falhou")
                            continue
                    else:
                        pos = self.bitget.position()
                        if pos and pos['side'] == 'long':
                            continue
                        if pos and pos['side'] == 'short':
                            log.info(f"  ↩️ LIVE REVERSAL: fechando SHORT @ {fill_px:.2f}")
                            try:
                                self.bitget.close_short(pos['size'], fill_px, "REVERSAL")
                            except Exception as _e:
                                log.error(f"  ❌ reversal close_short: {_e}")
                        log.info(f"  🟢 LIVE ENTER LONG {o_qty:.6f} ETH @ {fill_px:.2f} "
                                 f"(mark price — zero delay)")
                        r, qty_f = self.bitget.open_long(o_qty, self._cache_bal, fill_px)
                        if r.get("code") == "SKIP":
                            log.warning(f"  ⛔ LONG ignorado — {r.get('msg')}")
                            continue
                        if r.get("code") != "00000":
                            log.error("  ❌ bitget.open_long falhou")
                            continue

                    # PASSO 6: Confirmar fill na estratégia — mesmo snapshot_px
                    # usado no open_long acima (FIX-15: paridade garantida)
                    close_act = self.strategy.confirm_fill('BUY', fill_px, qty_f, closed_candle['timestamp'])
                    if close_act:
                        self._add_log(close_act.get('action', 'REVERSAL'),
                                      fill_px, qty_f, 'REVERSAL')
                        log.info(f"  ↩️ confirm_fill reversal: {close_act.get('action')} @ {fill_px:.2f}")
                    self._add_log("ENTER_LONG", fill_px, qty_f)
                    self._cache_pos = {'side': 'long', 'size': qty_f, 'avg_px': fill_px}
                    self._cache_bal = self.strategy.balance
                    if self._is_paper():
                        self.paper.balance = self.strategy.balance
                    self._pending_entry_check = True
                    self._last_entry_time = time.time()
                    log.info(f"  ✅ LONG confirmado | fill_px={fill_px:.2f} "
                             f"qty={qty_f:.4f} | bal={self.strategy.balance:.2f}")
                    log.debug("  🔒 [ENTRY-PENDING] monitoramento ativo no próximo poll")

                elif side == 'SELL':
                    if self._is_paper():
                        pos = self.paper.get_position()
                        if pos and pos['side'] == 'short':
                            continue
                        if pos and pos['side'] == 'long':
                            log.warning("  ⚠️ SELL: fechando long residual (reversal)")
                            self._paper_close_long(fill_px, 'REVERSAL', closed_candle['timestamp'])
                        log.info(f"  🔴 [PAPER] ENTER SHORT {o_qty:.6f} ETH @ {fill_px:.2f}")
                        r, qty_f = self.paper.open_short(o_qty, self._cache_bal, fill_px, ts=closed_candle['timestamp'])
                        if r.get("code") != "0":
                            log.error("  ❌ paper.open_short falhou")
                            continue
                    else:
                        pos = self.bitget.position()
                        if pos and pos['side'] == 'short':
                            continue
                        if pos and pos['side'] == 'long':
                            log.info(f"  ↩️ LIVE REVERSAL: fechando LONG @ {fill_px:.2f}")
                            try:
                                self.bitget.close_long(pos['size'], fill_px, "REVERSAL")
                            except Exception as _e:
                                log.error(f"  ❌ reversal close_long: {_e}")
                        log.info(f"  🔴 LIVE ENTER SHORT {o_qty:.6f} ETH @ {fill_px:.2f} "
                                 f"(mark price — zero delay)")
                        r, qty_f = self.bitget.open_short(o_qty, self._cache_bal, fill_px)
                        if r.get("code") == "SKIP":
                            log.warning(f"  ⛔ SHORT ignorado — {r.get('msg')}")
                            continue
                        if r.get("code") != "00000":
                            log.error("  ❌ bitget.open_short falhou")
                            continue

                    close_act = self.strategy.confirm_fill('SELL', fill_px, qty_f, closed_candle['timestamp'])
                    # PASSO 6 (SELL): mesmo snapshot_px para estratégia (FIX-15)
                    if close_act:
                        self._add_log(close_act.get('action', 'REVERSAL'),
                                      fill_px, qty_f, 'REVERSAL')
                        log.info(f"  ↩️ confirm_fill reversal: {close_act.get('action')} @ {fill_px:.2f}")
                    self._add_log("ENTER_SHORT", fill_px, qty_f)
                    self._cache_pos = {'side': 'short', 'size': qty_f, 'avg_px': fill_px}
                    self._cache_bal = self.strategy.balance
                    if self._is_paper():
                        self.paper.balance = self.strategy.balance
                    self._pending_entry_check = True
                    self._last_entry_time = time.time()
                    log.info(f"  ✅ SHORT confirmado | fill_px={fill_px:.2f} "
                             f"qty={qty_f:.4f} | bal={self.strategy.balance:.2f}")
                    log.debug("  🔒 [ENTRY-PENDING] monitoramento ativo no próximo poll")

            return ts_raw

    # ------------------------------------------------------------------

    def run(self, df: pd.DataFrame):
        mode_str = "📄 PAPER" if self._is_paper() else "💰 LIVE (95% saldo)"
        log.info(f"╔══════════════════════════════╗")
        log.info(f"║  AZLEMA {mode_str}")
        log.info(f"║  ETH-USDT-SWAP · Bitget · {TIMEFRAME}")
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
                log.info(f"  💰 Saldo real injetado: {bal:.4f} USDT")
            if px > 0:
                self._cache_px = px

        self.warmup(df)
        log.info("  ✅ Pronto. Aguardando candles ao vivo...")

        self._running = True

        # FIX-1: rastreia timestamp REAL do candle fechado (UNIX ms)
        last_processed_closed_ts: Optional[int] = None

        # FIX-10: Processar IMEDIATAMENTE o último candle do warmup
        # --------------------------------------------------------------
        last_candle = df.iloc[-1]
        try:
            ts_last = last_candle['timestamp']
            if isinstance(ts_last, datetime):
                ts_last_raw = int(ts_last.timestamp() * 1000)
            else:
                ts_last_raw = int(pd.Timestamp(ts_last).timestamp() * 1000)

            closed_candle = {
                'open':      float(last_candle['open']),
                'high':      float(last_candle['high']),
                'low':       float(last_candle['low']),
                'close':     float(last_candle['close']),
                'timestamp': ts_last,
                'index':     int(last_candle.get('index', 0)),
            }

            log.info(f"  🕯️ Processando candle inicial (último do warmup): "
                     f"O={closed_candle['open']:.2f} H={closed_candle['high']:.2f} "
                     f"L={closed_candle['low']:.2f} C={closed_candle['close']:.2f}")

            new_ts = self._process_closed_candle(closed_candle, ts_last_raw, last_processed_closed_ts)
            if new_ts is not None:
                last_processed_closed_ts = new_ts
                log.info(f"  ✔ Candle inicial processado (ts={ts_last_raw})")
            else:
                log.warning("  ⚠️ Processamento do candle inicial falhou, continuando normalmente")
        except Exception as e:
            log.error(f"  ❌ Erro ao processar candle inicial: {e}\n{traceback.format_exc()}")
        # --------------------------------------------------------------

        loop_exit_reason = None

        # ══════════════════════════════════════════════════════════════════════
        # CLOCK-SYNC — sincronização de relógio para latência zero na entrada
        # ══════════════════════════════════════════════════════════════════════
        #
        # MUDANÇAS em relação ao loop anterior:
        #
        # 1. SLEEP CONSTANTE: sleep_secs = 1 if has_position else 15 removido.
        #    O bot sempre dorme SLEEP_CONSTANT (0.5 s) para garantir precisão
        #    de milissegundos tanto com quanto sem posição aberta.
        #
        # 2. GATILHO POR RELÓGIO: o próximo fechamento é calculado pelo relógio
        #    do sistema (floor/ceil sobre epoch UTC) — não pela detecção passiva
        #    do campo [1] do endpoint REST.
        #
        # 3. PRÉ-FETCH DE SINAL: 1-2 s antes da virada captura o mark price e
        #    armazena em prefetch_snapshot_px, sem chamar a estratégia ainda.
        #
        # 4. EXECUÇÃO IMEDIATA NA VIRADA: quando secs_since_close cai em
        #    [0, FIRE_WINDOW_SECS], monta o candle com os dados do cache de
        #    formação e dispara strategy.next() + ordens usando o preço
        #    pré-capturado — zero chamadas extras a _mark_price().
        #
        # 5. FALLBACK REST (SEGURO): a chamada _candle_single() continua em
        #    toda iteração para (a) manter o cache forming atualizado e
        #    (b) processar via _process_closed_candle() qualquer candle que o
        #    clock-sync não tenha capturado (restart, clock drift, 1.ª iteração).
        # ══════════════════════════════════════════════════════════════════════

        # Constantes de temporização
        PREFETCH_SECS:    float = 1.5   # janela de pré-fetch antes da virada (s)
        FIRE_WINDOW_SECS: float = 3.0   # janela de disparo após a virada (s)
        SLEEP_CONSTANT:   float = 0.5   # sleep constante — sem mais 15 s flat

        # Mapa timeframe → segundos do intervalo
        _TF_SECS_MAP: Dict[str, int] = {
            '1m':  60,    '3m':  180,   '5m':  300,   '15m': 900,
            '30m': 1800,  '1h':  3600,  '2h':  7200,  '4h':  14400,
            '6h':  21600, '12h': 43200, '1d':  86400,
        }
        _interval_secs: int = _TF_SECS_MAP.get(TIMEFRAME.lower(), 1800)

        # Estado do clock-sync (local ao loop)
        prefetch_done:        bool            = False
        prefetch_snapshot_px: Optional[float] = None
        clock_fired_at:       Optional[float] = None   # boundary epoch já disparada
        forming_open_cache:   float           = 0.0    # open do candle em formação

        while self._running:
            try:
                if not self._running:
                    loop_exit_reason = "stop() chamado"
                    break

                now_epoch: float = time.time()

                # ── PRIORIDADE 0: Atualiza cache H/L do candle em formação ──────
                # Executado em TODA iteração — com ou sem posição aberta.
                # Garante que _forming_high/_forming_low reflitam o high/low real
                # da barra atual ANTES de qualquer verificação de stop, replicando
                # exatamente o comportamento do backtesting (SL vs H/L do candle).
                _candles_p0 = self._candle_single()
                if _candles_p0 is not None and len(_candles_p0) >= 2:
                    try:
                        _fr              = _candles_p0[0]
                        self._forming_high = float(_fr[2])
                        self._forming_low  = float(_fr[3])
                        self._forming_ts   = datetime.fromtimestamp(
                            int(_fr[0]) / 1000, tz=timezone.utc)
                        forming_open_cache = float(_fr[1])
                    except (ValueError, IndexError) as _e0:
                        log.warning(f"  ⚠️ [P0] Erro cache forming: {_e0}")

                    # Fallback REST: processa candle fechado (qualquer estado)
                    if len(_candles_p0[1]) >= 5:
                        try:
                            _prev_ts_raw_p0 = int(_candles_p0[1][0])
                            if (last_processed_closed_ts is None
                                    or _prev_ts_raw_p0 > last_processed_closed_ts):
                                _prev_ts_p0 = datetime.fromtimestamp(
                                    _prev_ts_raw_p0 / 1000, tz=timezone.utc)
                                _cc_p0: Dict = {
                                    'open':      float(_candles_p0[1][1]),
                                    'high':      float(_candles_p0[1][2]),
                                    'low':       float(_candles_p0[1][3]),
                                    'close':     float(_candles_p0[1][4]),
                                    'timestamp': _prev_ts_p0,
                                    'index':     self.strategy._bar + 1,
                                }
                                log.info(
                                    f"  🕯️ [FALLBACK-REST] Candle [{_prev_ts_raw_p0}]: "
                                    f"O={_cc_p0['open']:.2f} H={_cc_p0['high']:.2f} "
                                    f"L={_cc_p0['low']:.2f} C={_cc_p0['close']:.2f}"
                                )
                                _new_ts_p0 = self._process_closed_candle(
                                    _cc_p0, _prev_ts_raw_p0, last_processed_closed_ts)
                                if _new_ts_p0 is not None:
                                    last_processed_closed_ts = _new_ts_p0
                                    log.info(f"  ✔ [FALLBACK-REST] Candle {_prev_ts_raw_p0} processado")
                                else:
                                    log.warning(f"  ⚠️ [FALLBACK-REST] Processamento {_prev_ts_raw_p0} falhou")
                                self._refresh_cache()
                        except (ValueError, IndexError) as _e0b:
                            log.warning(f"  ⚠️ [P0] Erro fallback REST: {_e0b}")

                # ── PRIORIDADE 1: Verificação SL/trailing intrabar ──────────────
                # Usa H/L frescos (atualizados acima) — replica a lógica do
                # backtesting que verifica SL contra high/low de cada barra.
                if getattr(self.strategy, 'position_size', 0) != 0:
                    current_px = self._mark_price_fast()

                    time_since_entry = time.time() - getattr(self, '_last_entry_time', 0)

                    if time_since_entry > 3.0 and current_px and current_px > 0:
                        # eff_high/eff_low: máximo entre H/L do candle REST e
                        # mark price atual (cobre extremos não capturados pelo REST)
                        eff_high = max(
                            self._forming_high if self._forming_high > 0 else current_px,
                            current_px,
                        )
                        eff_low = min(
                            self._forming_low if self._forming_low < float('inf') else current_px,
                            current_px,
                        )
                        exit_act = self.strategy.update_trailing_live(
                            high=eff_high,
                            low=eff_low,
                            ts=(self._forming_ts or datetime.now(timezone.utc)),
                            current_price=current_px,
                        )
                        if exit_act:
                            e_signal = exit_act.get('action')
                            e_px     = exit_act.get('price', current_px)
                            e_rsn    = exit_act.get('exit_reason', 'STOP')
                            if e_signal == "EXIT_LONG":
                                self.close_long(e_rsn, e_px)
                            elif e_signal == "EXIT_SHORT":
                                self.close_short(e_rsn, e_px)

                    time.sleep(1)
                    continue  # candle já processado em P0; pula clock-sync
                else:
                    time.sleep(1)

                # ── CÁLCULO DO CICLO DO CANDLE (relógio local UTC) ───────────
                # current_boundary: última borda que passou  (floor)
                # next_boundary:    próxima borda a passar   (floor + interval)
                # secs_since_close: segundos decorridos desde o último close
                # secs_to_next:     segundos até o próximo close
                current_boundary_epoch: float = (
                    int(now_epoch // _interval_secs) * _interval_secs
                )
                next_boundary_epoch: float = current_boundary_epoch + _interval_secs
                secs_since_close:    float = now_epoch - current_boundary_epoch
                secs_to_next:        float = next_boundary_epoch - now_epoch

                # ── PRIORIDADE 2: Pré-Fetch de Sinal (Zero Latency Entry) ────
                # Captura o mark price 1-2 s antes da virada e guarda em
                # prefetch_snapshot_px. Na virada, esse preço é usado
                # diretamente — nenhuma chamada extra a _mark_price().
                if not prefetch_done and 0 < secs_to_next <= PREFETCH_SECS:
                    px_pre = self._mark_price()
                    if px_pre and px_pre > 0:
                        prefetch_snapshot_px = px_pre
                        prefetch_done        = True
                        log.info(
                            f"  ⏱️  [CLOCK-SYNC] Pré-fetch OK | "
                            f"snapshot_px={px_pre:.2f} | "
                            f"{secs_to_next:.3f}s até virada"
                        )
                    else:
                        log.warning(
                            "  ⚠️ [CLOCK-SYNC] Pré-fetch falhou — "
                            "mark price indisponível"
                        )

                # ── PRIORIDADE 3: Execução Imediata na Virada do Candle ──────
                # Dispara logo após a borda do relógio (dentro de FIRE_WINDOW_SECS),
                # sem aguardar confirmação REST. Usa o preço pré-capturado no
                # pré-fetch — latência de entrada próxima de zero.
                already_fired_this_boundary: bool = (
                    clock_fired_at is not None
                    and abs(clock_fired_at - current_boundary_epoch) < 1.0
                )

                if (not already_fired_this_boundary
                        and 0 <= secs_since_close <= FIRE_WINDOW_SECS):

                    fire_px              = prefetch_snapshot_px
                    clock_fired_at       = current_boundary_epoch   # marca disparado
                    prefetch_done        = False
                    prefetch_snapshot_px = None

                    if (fire_px and fire_px > 0
                            and self._forming_high > 0
                            and self._forming_ts is not None):

                        clk_ts_raw: int = int(self._forming_ts.timestamp() * 1000)

                        if (last_processed_closed_ts is None
                                or clk_ts_raw > last_processed_closed_ts):

                            clk_open: float = (forming_open_cache
                                               if forming_open_cache > 0 else fire_px)
                            clk_candle: Dict = {
                                'open':      clk_open,
                                'high':      max(self._forming_high, fire_px),
                                'low':       min(self._forming_low,  fire_px),
                                'close':     fire_px,
                                'timestamp': self._forming_ts,
                                'index':     self.strategy._bar + 1,
                            }

                            log.info(
                                f"  🚀 [CLOCK-SYNC] VIRADA! fire_px={fire_px:.2f} | "
                                f"O={clk_candle['open']:.2f} "
                                f"H={clk_candle['high']:.2f} "
                                f"L={clk_candle['low']:.2f} "
                                f"C={clk_candle['close']:.2f} | "
                                f"{secs_since_close:.3f}s após boundary"
                            )

                            # Execução inline — usa fire_px (pré-capturado)
                            # sem nenhuma chamada adicional a _mark_price().
                            with self._pos_lock:
                                actions_clk = self.strategy.next(clk_candle)
                                pending_clk = self.strategy.get_pending_orders()
                                exits_clk   = [
                                    a for a in actions_clk
                                    if a.get('action') in ('EXIT_LONG', 'EXIT_SHORT')
                                ]

                                if exits_clk or pending_clk:
                                    log.info(
                                        f"  📍 [CLOCK-SYNC] fire_px={fire_px:.2f} | "
                                        f"{len(exits_clk)} saída(s) | "
                                        f"{len(pending_clk)} entrada(s)"
                                    )

                                # ── Saídas ────────────────────────────────────
                                for act in exits_clk:
                                    kind  = act.get('action', '')
                                    a_qty = float(act.get('qty') or 0)
                                    a_rsn = act.get('exit_reason', kind)
                                    a_ts  = act.get('timestamp', clk_candle['timestamp'])

                                    if kind == 'EXIT_LONG':
                                        if self._is_paper():
                                            r_close   = self._paper_close_long(fire_px, a_rsn, a_ts)
                                            fill_clk  = r_close.get("_fill_px", fire_px)
                                        else:
                                            try:
                                                r_close = self.bitget.close_long(a_qty, fire_px, a_rsn)
                                            except Exception as _e:
                                                log.error(f"  ❌ [CLOCK] close_long: {_e}")
                                                continue
                                            fill_clk = r_close.get("_fill_px", fire_px)
                                        self._add_log('EXIT_LONG', fill_clk, a_qty, a_rsn)
                                        self._cache_pos = None
                                        self._cache_bal = self.strategy.balance
                                        if self._is_paper():
                                            self.paper.balance = self.strategy.balance
                                        log.info(
                                            f"  ✅ [CLOCK] EXIT_LONG "
                                            f"trigger={fire_px:.2f} fill={fill_clk:.2f} "
                                            f"| {a_rsn} | bal={self.strategy.balance:.2f}"
                                        )

                                    elif kind == 'EXIT_SHORT':
                                        if self._is_paper():
                                            r_close  = self._paper_close_short(fire_px, a_rsn, a_ts)
                                            fill_clk = r_close.get("_fill_px", fire_px)
                                        else:
                                            try:
                                                r_close = self.bitget.close_short(a_qty, fire_px, a_rsn)
                                            except Exception as _e:
                                                log.error(f"  ❌ [CLOCK] close_short: {_e}")
                                                continue
                                            fill_clk = r_close.get("_fill_px", fire_px)
                                        self._add_log('EXIT_SHORT', fill_clk, a_qty, a_rsn)
                                        self._cache_pos = None
                                        self._cache_bal = self.strategy.balance
                                        if self._is_paper():
                                            self.paper.balance = self.strategy.balance
                                        log.info(
                                            f"  ✅ [CLOCK] EXIT_SHORT "
                                            f"trigger={fire_px:.2f} fill={fill_clk:.2f} "
                                            f"| {a_rsn} | bal={self.strategy.balance:.2f}"
                                        )

                                # ── Entradas ──────────────────────────────────
                                for order in pending_clk:
                                    side  = order['side']
                                    o_qty = order['qty']
                                    if o_qty <= 0:
                                        continue

                                    fill_px = fire_px

                                    if side == 'BUY':
                                        if self._is_paper():
                                            pos = self.paper.get_position()
                                            if pos and pos['side'] == 'long':
                                                continue
                                            if pos and pos['side'] == 'short':
                                                log.warning(
                                                    "  ⚠️ [CLOCK] BUY: fechando short residual"
                                                )
                                                self._paper_close_short(
                                                    fill_px, 'REVERSAL', clk_candle['timestamp']
                                                )
                                            log.info(
                                                f"  🟢 [CLOCK/PAPER] ENTER LONG "
                                                f"{o_qty:.6f} ETH @ {fill_px:.2f}"
                                            )
                                            r, qty_f = self.paper.open_long(
                                                o_qty, self._cache_bal, fill_px,
                                                ts=clk_candle['timestamp']
                                            )
                                            if r.get("code") != "0":
                                                log.error("  ❌ [CLOCK] paper.open_long falhou")
                                                continue
                                        else:
                                            pos = self.bitget.position()
                                            if pos and pos['side'] == 'long':
                                                continue
                                            if pos and pos['side'] == 'short':
                                                log.info(
                                                    f"  ↩️ [CLOCK] REVERSAL: "
                                                    f"fechando SHORT @ {fill_px:.2f}"
                                                )
                                                try:
                                                    self.bitget.close_short(
                                                        pos['size'], fill_px, "REVERSAL"
                                                    )
                                                except Exception as _e:
                                                    log.error(
                                                        f"  ❌ [CLOCK] reversal close_short: {_e}"
                                                    )
                                            log.info(
                                                f"  🟢 [CLOCK/LIVE] ENTER LONG "
                                                f"{o_qty:.6f} ETH @ {fill_px:.2f} "
                                                f"(zero delay)"
                                            )
                                            r, qty_f = self.bitget.open_long(
                                                o_qty, self._cache_bal, fill_px
                                            )
                                            if r.get("code") == "SKIP":
                                                log.warning(
                                                    f"  ⛔ [CLOCK] LONG ignorado — "
                                                    f"{r.get('msg')}"
                                                )
                                                continue
                                            if r.get("code") != "00000":
                                                log.error("  ❌ [CLOCK] bitget.open_long falhou")
                                                continue

                                        close_act = self.strategy.confirm_fill(
                                            'BUY', fill_px, qty_f, clk_candle['timestamp']
                                        )
                                        if close_act:
                                            self._add_log(
                                                close_act.get('action', 'REVERSAL'),
                                                fill_px, qty_f, 'REVERSAL'
                                            )
                                            log.info(
                                                f"  ↩️ [CLOCK] confirm_fill reversal: "
                                                f"{close_act.get('action')} @ {fill_px:.2f}"
                                            )
                                        self._add_log("ENTER_LONG", fill_px, qty_f)
                                        self._cache_pos = {
                                            'side': 'long', 'size': qty_f, 'avg_px': fill_px
                                        }
                                        self._cache_bal = self.strategy.balance
                                        if self._is_paper():
                                            self.paper.balance = self.strategy.balance
                                        self._pending_entry_check = True
                                        self._last_entry_time = time.time()
                                        log.info(
                                            f"  ✅ [CLOCK] LONG confirmado | "
                                            f"fill_px={fill_px:.2f} qty={qty_f:.4f} | "
                                            f"bal={self.strategy.balance:.2f}"
                                        )

                                    elif side == 'SELL':
                                        if self._is_paper():
                                            pos = self.paper.get_position()
                                            if pos and pos['side'] == 'short':
                                                continue
                                            if pos and pos['side'] == 'long':
                                                log.warning(
                                                    "  ⚠️ [CLOCK] SELL: fechando long residual"
                                                )
                                                self._paper_close_long(
                                                    fill_px, 'REVERSAL', clk_candle['timestamp']
                                                )
                                            log.info(
                                                f"  🔴 [CLOCK/PAPER] ENTER SHORT "
                                                f"{o_qty:.6f} ETH @ {fill_px:.2f}"
                                            )
                                            r, qty_f = self.paper.open_short(
                                                o_qty, self._cache_bal, fill_px,
                                                ts=clk_candle['timestamp']
                                            )
                                            if r.get("code") != "0":
                                                log.error("  ❌ [CLOCK] paper.open_short falhou")
                                                continue
                                        else:
                                            pos = self.bitget.position()
                                            if pos and pos['side'] == 'short':
                                                continue
                                            if pos and pos['side'] == 'long':
                                                log.info(
                                                    f"  ↩️ [CLOCK] REVERSAL: "
                                                    f"fechando LONG @ {fill_px:.2f}"
                                                )
                                                try:
                                                    self.bitget.close_long(
                                                        pos['size'], fill_px, "REVERSAL"
                                                    )
                                                except Exception as _e:
                                                    log.error(
                                                        f"  ❌ [CLOCK] reversal close_long: {_e}"
                                                    )
                                            log.info(
                                                f"  🔴 [CLOCK/LIVE] ENTER SHORT "
                                                f"{o_qty:.6f} ETH @ {fill_px:.2f} "
                                                f"(zero delay)"
                                            )
                                            r, qty_f = self.bitget.open_short(
                                                o_qty, self._cache_bal, fill_px
                                            )
                                            if r.get("code") == "SKIP":
                                                log.warning(
                                                    f"  ⛔ [CLOCK] SHORT ignorado — "
                                                    f"{r.get('msg')}"
                                                )
                                                continue
                                            if r.get("code") != "00000":
                                                log.error("  ❌ [CLOCK] bitget.open_short falhou")
                                                continue

                                        close_act = self.strategy.confirm_fill(
                                            'SELL', fill_px, qty_f, clk_candle['timestamp']
                                        )
                                        if close_act:
                                            self._add_log(
                                                close_act.get('action', 'REVERSAL'),
                                                fill_px, qty_f, 'REVERSAL'
                                            )
                                            log.info(
                                                f"  ↩️ [CLOCK] confirm_fill reversal: "
                                                f"{close_act.get('action')} @ {fill_px:.2f}"
                                            )
                                        self._add_log("ENTER_SHORT", fill_px, qty_f)
                                        self._cache_pos = {
                                            'side': 'short', 'size': qty_f, 'avg_px': fill_px
                                        }
                                        self._cache_bal = self.strategy.balance
                                        if self._is_paper():
                                            self.paper.balance = self.strategy.balance
                                        self._pending_entry_check = True
                                        self._last_entry_time = time.time()
                                        log.info(
                                            f"  ✅ [CLOCK] SHORT confirmado | "
                                            f"fill_px={fill_px:.2f} qty={qty_f:.4f} | "
                                            f"bal={self.strategy.balance:.2f}"
                                        )

                            last_processed_closed_ts = clk_ts_raw
                            self._refresh_cache()
                            log.info(
                                f"  ✔ [CLOCK-SYNC] Candle ts={clk_ts_raw} processado "
                                f"e marcado"
                            )

                        else:
                            log.debug(
                                f"  ℹ️ [CLOCK-SYNC] ts={clk_ts_raw} já processado — "
                                f"skipping"
                            )

                    else:
                        log.warning(
                            f"  ⚠️ [CLOCK-SYNC] Sem dados suficientes para disparar "
                            f"(fire_px={fire_px}, "
                            f"forming_high={self._forming_high:.2f}, "
                            f"forming_ts={self._forming_ts}). "
                            f"Aguardando fallback REST."
                        )

                # ── PRIORIDADE 4 (FALLBACK SEGURO): Validação e Sync REST ────
                # Nota: o fetch e o update do cache de formação foram movidos para
                # PRIORIDADE 0 (topo do loop) para garantir H/L frescos antes do
                # bloco de monitoramento intrabar — candle já processado acima.

                time.sleep(SLEEP_CONSTANT)

            except Exception as e:
                log.error(f"❌ Erro no loop live: {e}\n{traceback.format_exc()}")
                time.sleep(60)
                continue

        if loop_exit_reason:
            log.info(f"🔴 Loop do trader encerrado. Motivo: {loop_exit_reason}")
        else:
            log.info("🔴 Loop do trader encerrado.")

    def _refresh_cache(self):
        px = self._mark_price()
        if px is not None and px > 0:
            self._cache_px = px

    def stop(self):
        log.info("🛑 Stop solicitado. Aguardando saída do loop...")
        self._running = False
        self._stop_monitor.disarm()
        for _ in range(20):
            if not self._running:
                break
            time.sleep(0.5)
        log.info("🛑 Trader parado.")


def run_backtest(symbol=SYMBOL, timeframe=TIMEFRAME, limit=500, initial_capital=1000.0,
                 open_fee_pct=0.0, close_fee_pct=0.0) -> Dict:
    log.info(f"🔬 Backtest: {symbol} {timeframe} {limit} candles | "
             f"taxas: abertura={open_fee_pct}% fechamento={close_fee_pct}%")
    try:
        df = DataCollector(symbol=symbol, timeframe=timeframe, limit=limit).fetch_ohlcv()
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

        closed  = results.get("closed_trades", [])
        fees_on = results.get("fees_enabled", False)
        pnl_key = "pnl_net" if fees_on else "pnl_usdt"
        gw = sum(t[pnl_key] for t in closed if t.get(pnl_key, 0) > 0)
        gl = abs(sum(t[pnl_key] for t in closed if t.get(pnl_key, 0) < 0))

        record = {
            "id":              brazil_iso(),
            "symbol":          symbol,
            "timeframe":       timeframe,
            "candles":         limit,
            "capital":         initial_capital,
            "open_fee_pct":    open_fee_pct,
            "close_fee_pct":   close_fee_pct,
            "total_fees_paid": round(results.get("total_fees_paid", 0), 4),
            "total_pnl":       round(results.get("total_pnl_usdt", 0), 4),
            "final_bal":       round(results.get("final_balance", 0), 4),
            "win_rate":        round(results.get("win_rate", 0), 2),
            "total_trades":    results.get("total_trades", 0),
            "max_drawdown":    round(results.get("max_drawdown", 0), 4),
            "sharpe":          round(results.get("sharpe", 0), 4),
            "profit_factor":   round(gw / gl, 3) if gl > 0 else float("inf"),
            "fees_enabled":    fees_on,
            "trades":          closed,
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


app       = Flask(__name__)
_trader:   Optional[LiveTrader] = None
_lock     = threading.Lock()
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
.topbar{background:var(--bg2);border-bottom:1px solid var(--border);padding:14px 28px;
        display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.logo{font-family:'IBM Plex Mono',monospace;font-size:1.1rem;font-weight:600;color:var(--accent);letter-spacing:2px}
.tabs{margin-left:auto;display:flex;gap:2px;background:var(--bg3);border-radius:6px;padding:3px}
.tab{padding:7px 18px;border-radius:4px;font-size:.78rem;font-weight:500;cursor:pointer;
     color:var(--muted);border:none;background:transparent;transition:.2s}
.tab.active{background:var(--bg2);color:var(--text);border:1px solid var(--border)}
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
.main{flex:1;padding:24px 28px}
.panel{display:none}.panel.active{display:block}
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
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}
.dot-run{background:var(--green);box-shadow:0 0 8px var(--green);animation:pulse 2s infinite}
.dot-warm{background:var(--yellow);animation:pulse .8s infinite}
.dot-stop{background:var(--muted)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
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
.terminal{background:#050810;border:1px solid var(--border);border-radius:8px;
  font-family:'IBM Plex Mono',monospace;font-size:.7rem;line-height:1.8;
  padding:14px;max-height:260px;overflow-y:auto;color:#5a7a9a}
.terminal .lg{color:var(--green)}.terminal .lr{color:var(--red)}
.terminal .ly{color:var(--yellow)}.terminal .la{color:var(--accent)}
.form-row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin-bottom:24px}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-group label{font-size:.64rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.form-group input,.form-group select{background:var(--bg3);border:1px solid var(--border);
  color:var(--text);padding:8px 12px;border-radius:5px;font-family:'IBM Plex Mono',monospace;font-size:.8rem;width:150px}
.progress-bar{height:3px;background:var(--border);border-radius:2px;margin-bottom:20px;overflow:hidden}
.progress-fill{height:100%;background:linear-gradient(90deg,var(--accent),var(--green));border-radius:2px;transition:width .3s}
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
  <div class="topbar">
    <span class="logo">⚡ AZLEMA</span>
    <span class="mono" style="font-size:.7rem;color:var(--muted)">ETH-USDT · 30m · Bitget</span>
    <div class="mode-toggle-wrap" title="Selecionar modo de operação">
      <button class="mode-btn paper-btn" id="btnPaper" onclick="setMode('paper')">📄 PAPER</button>
      <button class="mode-btn live-btn" id="btnLive" onclick="setMode('live')">💰 LIVE 95%</button>
    </div>
    <span class="mode-indicator" id="modeIndicator">—</span>
    <div class="tabs" style="margin-left:auto">
      <button class="tab active" onclick="switchTab('live')">Live</button>
      <button class="tab" onclick="switchTab('history')">Histórico</button>
      <button class="tab" onclick="switchTab('backtest')">Backtest</button>
    </div>
  </div>
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
            <thead> <tr><th>Hora</th><th>Ação</th><th>Preço</th><th>Qty ETH</th><th>Motivo</th></tr> </thead>
            <tbody id="lv-trades"> <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">Aguardando...</td></tr> </tbody>
             </table>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><span class="card-title">LOG DO SISTEMA</span></div>
        <div style="padding:0"><div class="terminal" id="lv-log">aguardando...</div></div>
      </div>
    </div>
    <div class="panel" id="panel-history">
      <div id="hist-session-banner" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:20px">
        <div style="display:flex;align-items:center;gap:10px">
          <span style="font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:1px">Sessão atual</span>
          <span id="hist-session-mode" class="mode-indicator mi-paper">📄 PAPER</span>
          <span id="hist-session-count" style="font-size:.72rem;font-family:'IBM Plex Mono',monospace;color:var(--muted)">0 trades</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn btn-sec" style="padding:7px 14px;font-size:.74rem" onclick="loadHistory()">↺ Atualizar</button>
          <button class="btn" style="padding:7px 16px;font-size:.74rem;background:rgba(0,212,255,.1);color:var(--accent);border:1px solid rgba(0,212,255,.3)" onclick="newPaperSession()">🆕 Nova Sessão</button>
          <button class="btn btn-sec" style="padding:7px 14px;font-size:.74rem;color:var(--red);border-color:rgba(255,77,109,.3)" onclick="if(confirm('Limpar TODO o histórico?'))clearHistory()">🗑 Limpar tudo</button>
        </div>
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
            <thead> <tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
                       <th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th><th>Modo</th></tr> </thead>
            <tbody id="hist-tbl"> <tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">Carregando...</td></tr> </tbody>
             </table>
        </div>
      </div>
    </div>
    <div class="panel" id="panel-backtest">
      <div class="form-row">
        <div class="form-group"><label>Símbolo</label><input id="bt-sym" value="ETH-USDT-SWAP"></div>
        <div class="form-group"><label>Timeframe</label>
          <select id="bt-tf"><option value="30m" selected>30m</option><option value="1h">1h</option><option value="4h">4h</option><option value="1d">1d</option><option value="15m">15m</option></select>
        </div>
        <div class="form-group"><label>Candles</label><input id="bt-lim" type="number" value="500" min="100" max="5000"></div>
        <div class="form-group"><label>Capital Inicial</label><input id="bt-cap" type="number" value="1000" min="100"></div>
        <div class="form-group"><label>Taxa Abertura %</label><input id="bt-ofee" type="number" value="0.06" min="0" max="1" step="0.01" style="width:120px"></div>
        <div class="form-group"><label>Taxa Fechamento %</label><input id="bt-cfee" type="number" value="0.06" min="0" max="1" step="0.01" style="width:120px"></div>
        <div style="display:flex;flex-direction:column;gap:5px;justify-content:flex-end">
          <button class="btn btn-accent" id="btnBT" onclick="runBacktest()">▶ Executar</button>
          <button class="btn btn-sec" style="font-size:.72rem;padding:6px 12px" onclick="document.getElementById('bt-ofee').value='0';document.getElementById('bt-cfee').value='0'">Sem taxas</button>
        </div>
      </div>
      <div class="progress-bar"><div class="progress-fill" id="bt-prog" style="width:0%"></div></div>
      <div id="bt-result" style="display:none">
        <div class="kpi-grid" id="bt-kpis"></div>
        <div class="card">
          <div class="card-head"><span class="card-title">TRADES DO BACKTEST</span></div>
          <div class="tbl-wrap">
               <table>
              <thead> <tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th><th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th></tr> </thead>
              <tbody id="bt-tbl"></tbody>
               </table>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:20px">
        <div class="card-head"><span class="card-title">HISTÓRICO DE BACKTESTS</span></div>
        <div class="tbl-wrap">
             <table>
            <thead> <tr><th>Data</th><th>Símbolo</th><th>TF</th><th>Candles</th><th>PnL</th><th>Win Rate</th><th>Trades</th><th>PF</th><th>Drawdown</th><th>Sharpe</th></tr> </thead>
            <tbody id="bt-hist-tbl"> <tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Sem histórico</td></tr> </tbody>
             </table>
        </div>
      </div>
    </div>
  </div>
</div>
<div class="mode-toast" id="modeToast"></div>
<script>
let _currentMode = 'paper';
function updateModeUI(mode) {
  _currentMode = mode;
  const isPaper = mode === 'paper';
  document.getElementById('btnPaper').classList.toggle('active', isPaper);
  document.getElementById('btnLive').classList.toggle('active', !isPaper);
  const ind = document.getElementById('modeIndicator');
  if (isPaper) { ind.textContent = '📄 Saldo Simulado'; ind.className = 'mode-indicator mi-paper'; }
  else { ind.textContent = '💰 95% Saldo Real'; ind.className = 'mode-indicator mi-live'; }
  const lv = document.getElementById('lv-mode');
  if (lv) { lv.textContent = isPaper ? 'PAPER' : 'LIVE · 95%'; lv.className = 'kpi-val ' + (isPaper ? 'p' : 'live-c'); }
  const kpiMode = document.getElementById('kpi-mode');
  if (kpiMode) kpiMode.className = 'kpi ' + (isPaper ? 'p' : 'live-kpi');
}
async function setMode(mode) {
  const running = document.getElementById('btnStop').disabled === false;
  if (running) {
    if (!confirm(`O trader está rodando. Parar e trocar para ${mode === 'paper' ? 'PAPER' : 'LIVE 95%'}?`)) return;
    await fetch('/stop', { method: 'POST' });
    await new Promise(r => setTimeout(r, 1000));
  }
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = 'Alterando modo...';
  try {
    const d = await (await fetch('/mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) })).json();
    if (d.error) { m.className = 'abm-er'; m.textContent = d.error; }
    else { updateModeUI(mode); showToast(mode); m.textContent = d.message || 'OK'; }
  } catch (e) { m.className = 'abm-er'; m.textContent = 'Erro: ' + e; }
  setTimeout(() => m.style.display = 'none', 4000);
}
function showToast(mode) {
  const t = document.getElementById('modeToast');
  t.className = 'mode-toast ' + (mode === 'paper' ? 'toast-paper' : 'toast-live');
  t.textContent = mode === 'paper' ? '📄 Modo PAPER ativado — trades simulados' : '💰 Modo LIVE ativado — usando 95% do saldo real na Bitget';
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 4000);
}
function switchTab(t) {
  document.querySelectorAll('.tab').forEach((el,i) => { el.classList.toggle('active', ['live','history','backtest'][i] === t); });
  document.querySelectorAll('.panel').forEach(el => el.classList.remove('active'));
  document.getElementById('panel-' + t).classList.add('active');
  if (t === 'history') loadHistory();
  if (t === 'backtest') loadBtHistory();
}
async function poll() {
  try {
    const d = await (await fetch('/status')).json();
    const run = d.status === 'running', warm = d.status === 'warming';
    updateModeUI(d.paper ? 'paper' : 'live');
    document.getElementById('btnStart').disabled = run || warm;
    document.getElementById('btnStop').disabled  = !(run || warm);
    const se = document.getElementById('lv-status');
    if (run) se.innerHTML = '<span class="status-dot dot-run"></span><span class="g">Rodando</span>';
    else if (warm) se.innerHTML = '<span class="status-dot dot-warm"></span><span class="y">Warmup...</span>';
    else se.innerHTML = '<span class="status-dot dot-stop"></span><span style="color:var(--muted)">Parado</span>';
    if (d.bal  != null) document.getElementById('lv-bal').textContent  = d.bal.toFixed(2) + ' USDT';
    const pe = document.getElementById('lv-pnl');
    if (d.pnl  != null) { pe.textContent = (d.pnl >= 0 ? '+' : '') + d.pnl.toFixed(4) + ' USDT'; pe.className = 'kpi-val ' + (d.pnl >= 0 ? 'g' : 'r'); }
    const pp = document.getElementById('lv-pos');
    if (d.pos) { const s = d.pos.side; pp.innerHTML = `<span class="${s === 'long' ? 'g' : 'r'}">${s.toUpperCase()}</span>`; }
    else { pp.innerHTML = '<span style="color:var(--muted)">FLAT</span>'; }
    if (d.period != null) document.getElementById('lv-per').textContent = d.period;
    if (d.ec     != null) document.getElementById('lv-ec').textContent  = d.ec.toFixed(2);
    if (d.ema    != null) document.getElementById('lv-ema').textContent = d.ema.toFixed(2);
    const tb = document.getElementById('lv-trades');
    const tr = [...(d.trades || [])].reverse();
    if (tr.length) {
      tb.innerHTML = tr.map(t => {
        const ac = t.action || ''; let cl = 'dir', lb = ac;
        if (ac.includes('LONG'))  { cl = 'dir dir-l'; lb = ac.includes('ENTER') ? '▲ LONG'  : '▼ EXIT L'; }
        if (ac.includes('SHORT')) { cl = 'dir dir-s'; lb = ac.includes('ENTER') ? '▼ SHORT' : '▲ EXIT S'; }
        return `<tr><td>${(t.time||'').split('T')[1]?.slice(0,8)||'—'}</td><td><span class="${cl}">${lb}</span></td><td>${t.price?.toFixed(2)||'—'}</td><td>${t.qty?.toFixed(6)||'—'}</td><td style="color:var(--muted)">${t.reason||'—'}</td></tr>`;
      }).join('');
    }
    const lb = document.getElementById('lv-log');
    if (d.log && d.log.length) {
      lb.innerHTML = d.log.slice(-80).map(l => {
        let cls = '';
        if (/✅|LONG|BUY/.test(l)) cls = 'lg'; else if (/❌|EXIT|SHORT/.test(l)) cls = 'lr';
        else if (/⚠️|WARN|ENTRY-CHECK/.test(l)) cls = 'ly'; else if (/AZLEMA|╔|╚/.test(l)) cls = 'la';
        return `<div class="${cls}">${l}</div>`;
      }).join('');
      lb.scrollTop = lb.scrollHeight;
    }
  } catch(e) { console.error(e); }
}
poll(); setInterval(poll, 4000);
async function ctrl(a) {
  const m = document.getElementById('sysmsg');
  m.style.display = 'inline-block'; m.className = a === 'start' ? 'msg-ok' : 'msg-er';
  m.textContent = a === 'start' ? 'Iniciando...' : 'Parando...';
  try {
    const d = await (await fetch('/' + a, { method: 'POST' })).json();
    m.className = d.error ? 'msg-er' : 'msg-ok'; m.textContent = d.message || d.error || 'OK';
  } catch { m.className = 'msg-er'; m.textContent = 'Erro de rede'; }
  setTimeout(() => m.style.display = 'none', 5000); setTimeout(poll, 1500);
}
async function loadHistory() {
  try {
    const d = await (await fetch('/history')).json();
    const s = d.stats || {};
    const isPaper = _currentMode === 'paper';
    const modeEl = document.getElementById('hist-session-mode');
    const countEl = document.getElementById('hist-session-count');
    const closedCount = (d.trades||[]).filter(t => t.status === 'closed').length;
    if (modeEl) { modeEl.textContent = isPaper ? '📄 PAPER' : '💰 LIVE'; modeEl.className = 'mode-indicator ' + (isPaper ? 'mi-paper' : 'mi-live'); }
    if (countEl) countEl.textContent = closedCount + ' trade' + (closedCount !== 1 ? 's' : '') + ' fechado' + (closedCount !== 1 ? 's' : '');
    const pf = s.profit_factor === Infinity || s.profit_factor > 999 ? '∞' : +(s.profit_factor||0).toFixed(3);
    document.getElementById('h-total').textContent = s.total || 0;
    const wrEl = document.getElementById('h-wr'); wrEl.textContent = (s.win_rate||0).toFixed(1) + '%'; wrEl.className = 'kpi-val ' + (s.win_rate >= 50 ? 'g' : 'r');
    const pnlEl = document.getElementById('h-pnl'); pnlEl.textContent = (s.total_pnl >= 0 ? '+' : '') + (s.total_pnl||0).toFixed(4) + ' USDT'; pnlEl.className = 'kpi-val ' + (s.total_pnl >= 0 ? 'g' : 'r');
    const pfEl = document.getElementById('h-pf'); pfEl.textContent = pf; pfEl.className = 'kpi-val ' + (s.profit_factor > 1 ? 'g' : 'r');
    document.getElementById('h-aw').textContent = '+' + (s.avg_win||0).toFixed(4);
    document.getElementById('h-al').textContent = (s.avg_loss||0).toFixed(4);
    document.getElementById('h-best').textContent = '+' + (s.best_trade||0).toFixed(4);
    document.getElementById('h-worst').textContent = (s.worst_trade||0).toFixed(4);
    const tb = document.getElementById('hist-tbl');
    const trades = (d.trades || []).filter(t => t.status === 'closed').reverse();
    if (!trades.length) { tb.innerHTML = '<tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">Nenhum trade fechado</td></tr>'; return; }
    tb.innerHTML = trades.map((t, i) => {
      const pnl = t.pnl_usdt || 0, pct = t.pnl_pct || 0;
      const dir = t.action === 'BUY' ? 'LONG' : 'SHORT', dc = t.action === 'BUY' ? 'dir dir-l' : 'dir dir-s';
      const pc = pnl >= 0 ? 'g' : 'r', ep = t.exit_price ? t.exit_price.toFixed(2) : '—';
      const mode = t.mode === 'paper' ? '<span class="p">PAPER</span>' : '<span class="g">LIVE</span>';
      return `<tr><td>${i+1}</td><td class="mono" style="font-size:.7rem">${(t.entry_time||'—').replace('T',' ').slice(0,19)}</td><td class="mono" style="font-size:.7rem">${(t.exit_time||'—').replace('T',' ').slice(0,19)}</td><td><span class="${dc}">${dir}</span></td><td>${(t.qty||0).toFixed(4)}</td><td>${(t.entry_price||0).toFixed(2)}</td><td>${ep}</td><td class="${pc}">${pnl>=0?'+':''}${pnl.toFixed(4)}</td><td class="${pc}">${pct>=0?'+':''}${pct.toFixed(2)}%</td><td style="color:var(--muted)">${t.exit_reason||'—'}</td><td>${mode}</td></tr>`;
    }).join('');
  } catch(e) { console.error(e); }
}
async function clearHistory() { await fetch('/history/clear', { method: 'POST' }); loadHistory(); }
async function newPaperSession() {
  if (!confirm('Iniciar nova sessão? Isso vai limpar os trades Paper/Live atuais.')) return;
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = '🆕 Nova sessão iniciada';
  await fetch('/history/clear', { method: 'POST' }); loadHistory();
  setTimeout(() => m.style.display = 'none', 3000);
}
async function runBacktest() {
  const btn = document.getElementById('btnBT'), prog = document.getElementById('bt-prog');
  btn.disabled = true; btn.textContent = 'Rodando...'; prog.style.width = '20%';
  document.getElementById('bt-result').style.display = 'none';
  try {
    const sym = document.getElementById('bt-sym').value, tf = document.getElementById('bt-tf').value;
    const lim = document.getElementById('bt-lim').value, cap = document.getElementById('bt-cap').value;
    const ofee = document.getElementById('bt-ofee').value, cfee = document.getElementById('bt-cfee').value;
    prog.style.width = '60%';
    const d = await (await fetch(`/backtest/run?symbol=${sym}&tf=${tf}&limit=${lim}&capital=${cap}&open_fee=${ofee}&close_fee=${cfee}`, {method:'POST'})).json();
    prog.style.width = '100%';
    if (d.error) { alert('Erro: ' + d.error); return; }
    renderBacktestResult(d); loadBtHistory();
  } catch(e) { alert('Erro: ' + e); }
  finally { btn.disabled = false; btn.textContent = '▶ Executar'; setTimeout(() => prog.style.width = '0%', 1000); }
}
function renderBacktestResult(d) {
  const pf = d.profit_factor === Infinity || d.profit_factor > 999 ? '∞' : +(d.profit_factor||0).toFixed(3);
  const hasFees = d.fees_enabled && (d.open_fee_pct > 0 || d.close_fee_pct > 0);
  const pnlLabel = hasFees ? 'PnL Líquido' : 'PnL Total';
  const kpis = [
    [pnlLabel, (d.total_pnl >= 0 ? '+' : '') + d.total_pnl.toFixed(2) + ' USDT', d.total_pnl >= 0 ? 'g' : 'r'],
    ['Saldo Final', d.final_bal.toFixed(2) + ' USDT', ''],
    ['Win Rate', d.win_rate.toFixed(1) + '%', d.win_rate >= 50 ? 'g' : 'r'],
    ['Total Trades', d.total_trades, ''],
    ['Profit Factor', pf, d.profit_factor > 1 ? 'g' : 'r'],
    ['Max Drawdown', d.max_drawdown.toFixed(2) + '%', 'r'],
    ['Sharpe Ratio', d.sharpe.toFixed(3), d.sharpe >= 1 ? 'g' : d.sharpe >= 0 ? 'y' : 'r'],
  ];
  if (hasFees) kpis.push(['Taxas Pagas', '-' + (d.total_fees_paid||0).toFixed(4) + ' USDT', 'r']);
  document.getElementById('bt-kpis').innerHTML = kpis.map(([lbl,val,cls]) => `<div class="kpi ${cls}"><div class="kpi-lbl">${lbl}</div><div class="kpi-val ${cls}">${val}</div></div>`).join('');
  const trades = (d.trades || []).slice().reverse();
  document.getElementById('bt-tbl').innerHTML = trades.length ? trades.map((t,i) => {
    const pnlB = t.pnl_usdt || 0, pnlN = t.pnl_net != null ? t.pnl_net : pnlB;
    const pct = hasFees ? (t.pnl_pct_net || 0) : (t.pnl_percent || 0);
    const fees = t.fees_total || 0, dir = t.action === 'BUY' ? 'LONG' : 'SHORT';
    const dc = t.action === 'BUY' ? 'dir dir-l' : 'dir dir-s', pcB = pnlB >= 0 ? 'g' : 'r', pcN = pnlN >= 0 ? 'g' : 'r';
    const feeCols = hasFees ? `<td class="r" style="font-size:.68rem">-${fees.toFixed(4)}</td><td class="${pcN}">${pnlN>=0?'+':''}${pnlN.toFixed(4)}</td>` : '';
    return `<tr><td>${i+1}</td><td class="mono" style="font-size:.7rem">${(t.entry_time||'—').replace('T',' ').slice(0,19)}</td><td class="mono" style="font-size:.7rem">${(t.exit_time||'—').replace('T',' ').slice(0,19)}</td><td><span class="${dc}">${dir}</span></td><td>${(t.qty||0).toFixed(4)}</td><td>${(t.entry_price||0).toFixed(2)}</td><td>${t.exit_price?t.exit_price.toFixed(2):'—'}</td><td class="${pcB}">${pnlB>=0?'+':''}${pnlB.toFixed(4)}</td>${feeCols}<td class="${pcN}">${pct>=0?'+':''}${pct.toFixed(2)}%</td><td style="color:var(--muted)">${t.exit_comment||'—'}</td></tr>`;
  }).join('') : '<tr><td colspan="10" style="text-align:center;color:var(--muted)">Sem trades</td></tr>';
  document.getElementById('bt-result').style.display = 'block';
}
async function loadBtHistory() {
  try {
    const d = await (await fetch('/backtest/history')).json();
    const sessions = (d.sessions || []).slice().reverse();
    const tb = document.getElementById('bt-hist-tbl');
    if (!sessions.length) { tb.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Sem histórico</td></tr>'; return; }
    tb.innerHTML = sessions.map(s => {
      const pf = s.profit_factor === Infinity || s.profit_factor > 999 ? '∞' : +(s.profit_factor||0).toFixed(3);
      const pc = s.total_pnl >= 0 ? 'g' : 'r';
      return `<tr><td>${(s.id||'—').replace('T',' ').slice(0,19)}</td><td>${s.symbol||'—'}</td><td>${s.timeframe||'—'}</td><td>${s.candles||0}</td><td class="${pc}">${s.total_pnl>=0?'+':''}${(s.total_pnl||0).toFixed(2)}</td><td class="${s.win_rate>=50?'g':'r'}">${(s.win_rate||0).toFixed(1)}%</td><td>${s.total_trades||0}</td><td class="${s.profit_factor>1?'g':'r'}">${pf}</td><td class="r">${(s.max_drawdown||0).toFixed(2)}%</td><td class="${(s.sharpe||0)>=1?'g':(s.sharpe||0)>=0?'y':'r'}">${(s.sharpe||0).toFixed(3)}</td></tr>`;
    }).join('');
  } catch(e) { console.error(e); }
}
loadBtHistory();
async function apiPost(route, successMsg) {
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = '...';
  try {
    const d = await (await fetch(route, { method: 'POST' })).json();
    m.className = d.error ? 'abm-er' : 'abm-ok'; m.textContent = d.error || successMsg || d.message || 'OK';
  } catch(e) { m.className = 'abm-er'; m.textContent = 'Erro: ' + e; }
  setTimeout(() => m.style.display = 'none', 3500); setTimeout(poll, 1200);
}
async function exportJson(route, filename) {
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = 'Exportando...';
  try {
    const d = await (await fetch(route)).json();
    const blob = new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = filename + '_' + new Date().toISOString().slice(0,10) + '.json';
    a.click(); URL.revokeObjectURL(url); m.textContent = '✓ Download iniciado';
  } catch(e) { m.className = 'abm-er'; m.textContent = 'Erro: ' + e; }
  setTimeout(() => m.style.display = 'none', 3000);
}
async function quickBacktest() {
  const sym = prompt('Símbolo (ex: ETH-USDT-SWAP)', 'ETH-USDT-SWAP'); if (!sym) return;
  const tf  = prompt('Timeframe', '30m'); if (!tf) return;
  const lim = prompt('Candles', '500'); if (!lim) return;
  const cap = prompt('Capital inicial (USDT)', '1000'); if (!cap) return;
  const m = document.getElementById('apibar-msg');
  m.style.display = 'inline-block'; m.className = 'abm-ok'; m.textContent = '⏳ Rodando...';
  try {
    const d = await (await fetch(`/backtest/run?symbol=${encodeURIComponent(sym)}&tf=${tf}&limit=${lim}&capital=${cap}`, { method: 'POST' })).json();
    if (d.error) { m.className='abm-er'; m.textContent='Erro: '+d.error; }
    else { m.textContent = `✓ PnL: ${d.total_pnl>=0?'+':''}${(d.total_pnl||0).toFixed(2)} | WR: ${(d.win_rate||0).toFixed(1)}%`; switchTab('backtest'); renderBacktestResult(d); loadBtHistory(); }
  } catch(e) { m.className='abm-er'; m.textContent='Erro: '+e; }
  setTimeout(() => m.style.display = 'none', 6000);
}
</script>
</body>
</html>"""


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
        _release_lock()
        log.info("🔄 Pronto para re-iniciar")


@app.route('/')
def index(): return DASH

@app.route('/status')
def status():
    t = _trader
    if t is None:
        return jsonify({"status": "stopped", "tc": 0, "trades": [],
                        "log": _logs[-80:], "paper": get_paper_mode()})
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
            "mode":  "paper" if get_paper_mode() else "live",
            "paper": get_paper_mode(),
            "pct":   100 if get_paper_mode() else int(LIVE_PCT * 100),
            "creds": _creds_ok(),
        })
    data = flask_request.get_json(silent=True) or {}
    mode = data.get("mode", "paper").lower()
    if mode == "live":
        if not _creds_ok():
            return jsonify({"error": "❌ Configure BITGET_API_KEY, BITGET_SECRET_KEY e "
                                     "BITGET_PASSPHRASE no Render antes de usar o modo LIVE."}), 400
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

        if Path(LOCK_FILE).exists() and _trader is None:
            log.warning("Limpando lock órfão encontrado no início manual.")
            _release_lock()

        if not _acquire_lock():
            return jsonify({"error": "Outro processo já está rodando o trader. Use --workers=1 no Gunicorn."}), 400

        _starting = True
        threading.Thread(target=_thread, daemon=True).start()
        mode_str = "paper" if get_paper_mode() else "live (95% saldo Bitget)"
        return jsonify({"message": f"Iniciado em modo {mode_str}"})

@app.route('/stop', methods=['POST'])
def stop():
    if _trader: _trader.stop()
    _release_lock()
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
    return jsonify({"trades": history_mgr.get_all_trades(),
                    "stats":  history_mgr.get_stats()})

@app.route('/history/clear', methods=['POST'])
def clear_history():
    history_mgr.clear()
    return jsonify({"message": "Histórico limpo"})

@app.route('/backtest/run', methods=['POST'])
def api_backtest():
    sym           = flask_request.args.get('symbol',    "ETH-USDT-SWAP")
    tf            = flask_request.args.get('tf',        TIMEFRAME)
    limit         = int(flask_request.args.get('limit',   500))
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
    return ("<h2 style='font-family:monospace;color:#f0b90b;background:#0e1219;padding:40px'>"
            "📊 Use /backtest/history para ver os dados JSON ou integre com o painel.</h2>")


def _delayed_start():
    global _starting
    time.sleep(5)
    with _lock:
        if _trader is not None or _starting:
            log.debug("Trader já rodando ou iniciando, auto-start ignorado.")
            return
        is_paper = get_paper_mode()
        if not is_paper and not _creds_ok():
            log.warning("⚠️ Chaves Bitget não configuradas — use o botão Iniciar.")
            return
        if not _acquire_lock():
            log.warning("⚠️ Lock de processo já existe. Auto-start ignorado (outro worker rodando?).")
            return
        _starting = True
        mode_str = "PAPER TRADING" if is_paper else "LIVE (Bitget)"
        log.info(f"🚀 Auto-start {mode_str}...")
        threading.Thread(target=_thread, daemon=True).start()

threading.Thread(target=_delayed_start, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0',
            port=int(os.environ.get("PORT", 5000)),
            debug=False)
