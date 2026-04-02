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
FIX-16 Sincronização de Relógio (Clock-Sync) — Zero Latency Entry (URGENTE)
  - Remoção do sleep dinâmico: sleep_secs = 1 / 15 substituído por
    CONSTANT_SLEEP = 0.5 s fixo — loop sempre preciso, independente
    de posição aberta ou fechada.
  - Gatilho por timestamp local: o bot calcula o próximo fechamento com
    math.ceil(now / tf_secs) * tf_secs, eliminando a dependência do
    endpoint REST de candles para detectar virada de barra.
  - 5 prioridades explícitas por iteração do loop:
      P1 Trailing stop intra-barra   — a cada 0.5 s, com position
      P2 Pré-fetch de sinal (T−2 s) — strategy.next() antecipado
      P3 Execução clock (T≤0)       — disparo imediato de ordens
      P4 Validação REST (T+3 s)     — atualiza cache do novo candle
      P5 REST fallback               — só se P2/P3 falharam
  - Pré-fetch (P2): 2 s antes do fechamento, captura mark price,
    constrói candle sintético (O/H/L do cache + C=mark price) e roda
    strategy.next() antecipado. Saídas e entradas ficam em memória.
  - Execução clock (P3): ao cruzar T=0, obtém mark price fresco,
    chama _execute_clock_orders() e confirma fill — tudo sem REST.
    Fallback para snapshot do pré-fetch se _mark_price() falhar.
  - Validação REST (P4): apenas atualiza _forming_open/high/low do
    novo candle; strategy.next() não é chamado novamente.
  - REST fallback (P5): somente quando P2 falhou (mark price
    indisponível no pré-fetch); processa via _process_closed_candle()
    normal, garantindo que nenhum candle seja perdido.
  - _execute_clock_orders(): método novo com lógica espelhada ao
    PASSO 4+5+6 de _process_closed_candle(), porém desacoplado de REST.
  - _forming_open adicionado ao cache de candle em formação, necessário
    para construir o candle sintético do pré-fetch.
FIX‑KEEPALIVE: Pinger interno para evitar idle no Render
FIX‑LOCK: Remoção do lock com atexit
══════════════════════════════════════════════════════════════════════
"""
import os, hmac, hashlib, base64, json, time, threading, traceback, logging, math, requests, atexit
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
# FIX‑KEEPALIVE: import do pinger
from keepalive.pinger import KeepAlivePinger

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

# ── Clock-Sync: mapeamento de timeframe → segundos ──────────────────────────
TIMEFRAME_SECS: Dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
    "6h": 21600, "12h": 43200, "1d": 86400,
}
CONSTANT_SLEEP      = 0.5    # sleep fixo do loop principal em segundos (era dinâmico 1 s / 15 s)
PREFETCH_LEAD_SECS  = 2.0    # antecedência do pré-fetch antes do fechamento (segundos)
REST_VALIDATE_DELAY = 3.0    # aguarda X s após T=0 para buscar o novo candle via REST
# FIX‑KEEPALIVE: timeout para reset do ciclo clock‑sync (evita travamento)
CLOCK_SYNC_TIMEOUT = 30.0    # segundos

_PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() in ("true", "1", "yes")
PAPER_BALANCE  = float(os.environ.get("PAPER_BALANCE", "1000.0"))
LIVE_PCT       = 0.95
HISTORY_FILE          = "trades_history.json"
BACKTEST_HISTORY_FILE = "backtest_history.json"

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

# FIX‑LOCK: registrar remoção automática ao encerrar o processo
atexit.register(_release_lock)

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
        history_mgr.add_trade({
            "id": trade_id, "action": "BUY", "status": "open",
            "entry_time": str(ts), "entry_price": px,
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
            "entry_time": str(ts), "entry_price": px,
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
        ts = str(ts) if ts else brazil_iso()
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
        ts = str(ts) if ts else brazil_iso()
        try:
            history_mgr.close_trade(trade_id, exit_px, ts, reason, pnl)
        except Exception as _e:
            log.warning(f"  ⚠️ close_trade (short) file error: {_e}")
        log.info(f"  📄 PAPER SHORT fechado | px={exit_px:.2f} pnl={pnl:+.4f} USDT")
        return {"code": "0"}

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
            oid = (r.get("data") or {}).get("orderId", "?")
            history_mgr.add_trade({
                "id": str(oid), "action": "BUY", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": sz * self.CT_VAL, "balance": bal, "mode": "live",
            })
        return r, sz * self.CT_VAL

    def open_short(self, qty, bal=0, px=0):
        sz = self._cts(qty, bal, px)
        if sz == 0:
            return {"code": "SKIP", "msg": "Saldo insuficiente"}, 0.0
        r  = self._order("sell", False, sz)
        if r.get("code") == "00000":
            oid = (r.get("data") or {}).get("orderId", "?")
            history_mgr.add_trade({
                "id": str(oid), "action": "SELL", "status": "open",
                "entry_time": brazil_iso(), "entry_price": px,
                "qty": sz * self.CT_VAL, "balance": bal, "mode": "live",
            })
        return r, sz * self.CT_VAL

    def close_long(self, qty, exit_px=0, reason="EXIT"):
        """Fecha posição long na Bitget.

        Args:
            qty:     quantidade em ETH (convertida para contratos internamente).
            exit_px: preço REAL de mercado (mark price / ticker_px) capturado
                     no momento do disparo — NÃO o valor teórico do stop.
                     Usado somente para registro de PnL no histórico; o
                     fill real da exchange pode diferir levemente (slippage).
            reason:  motivo da saída (ex: 'STOP', 'EXIT_LONG', 'REVERSAL').
        """
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
        """Fecha posição short na Bitget.

        Args:
            qty:     quantidade em ETH (convertida para contratos internamente).
            exit_px: preço REAL de mercado (mark price / ticker_px) capturado
                     no momento do disparo — NÃO o valor teórico do stop.
                     Usado somente para registro de PnL no histórico; o
                     fill real da exchange pode diferir levemente (slippage).
            reason:  motivo da saída (ex: 'STOP', 'EXIT_SHORT', 'REVERSAL').
        """
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
        self._forming_open: float = 0.0   # CLOCK-SYNC: open do candle em formação
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
            self.paper.close_long(pos['size'], price, reason, ts=ts)
            self._cache_pos = None

    def _paper_close_short(self, price: float, reason: str, ts):
        pos = self.paper.get_position()
        if pos and pos['side'] == 'short':
            self.paper.close_short(pos['size'], price, reason, ts=ts)
            self._cache_pos = None

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

                # snapshot_px é o preço real de mercado no milissegundo do envio
                # da ordem — NÃO o valor teórico do stop da estratégia (FIX-15).
                exit_fill_px = snapshot_px  # type: ignore[assignment]

                if kind == 'EXIT_LONG':
                    if self._is_paper():
                        self._paper_close_long(exit_fill_px, a_rsn, a_ts)
                    else:
                        try:
                            self.bitget.close_long(a_qty, exit_fill_px, a_rsn)
                        except Exception as _e:
                            log.error(f"  ❌ live close_long: {_e}")
                            continue
                    self._add_log('EXIT_LONG', exit_fill_px, a_qty, a_rsn)
                    self._cache_pos = None
                    self._cache_bal = self.strategy.balance
                    if self._is_paper():
                        self.paper.balance = self.strategy.balance
                    log.info(f"  ✅ EXIT_LONG @ {exit_fill_px:.2f} (mark price real) "
                             f"| {a_rsn} | bal={self.strategy.balance:.2f}")

                elif kind == 'EXIT_SHORT':
                    if self._is_paper():
                        self._paper_close_short(exit_fill_px, a_rsn, a_ts)
                    else:
                        try:
                            self.bitget.close_short(a_qty, exit_fill_px, a_rsn)
                        except Exception as _e:
                            log.error(f"  ❌ live close_short: {_e}")
                            continue
                    self._add_log('EXIT_SHORT', exit_fill_px, a_qty, a_rsn)
                    self._cache_pos = None
                    self._cache_bal = self.strategy.balance
                    if self._is_paper():
                        self.paper.balance = self.strategy.balance
                    log.info(f"  ✅ EXIT_SHORT @ {exit_fill_px:.2f} (mark price real) "
                             f"| {a_rsn} | bal={self.strategy.balance:.2f}")

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
                    log.info(f"  ✅ SHORT confirmado | fill_px={fill_px:.2f} "
                             f"qty={qty_f:.4f} | bal={self.strategy.balance:.2f}")
                    log.debug("  🔒 [ENTRY-PENDING] monitoramento ativo no próximo poll")

            return ts_raw

    # ------------------------------------------------------------------
    # Execução clock-sync: dispara ordens pré-carregadas no instante T=0
    # ------------------------------------------------------------------
    def _execute_clock_orders(
        self,
        exits:        List[Dict],
        orders:       List[Dict],
        exec_px:      float,
        candle_ts_ms: int,
    ) -> None:
        """
        Dispara saídas e entradas pré-carregadas pelo pre-fetch exatamente
        quando o relógio local indica que o candle fechou (T=0), sem aguardar
        a confirmação REST da Bitget.

        Parâmetros:
            exits        : lista de ações EXIT_LONG / EXIT_SHORT do pré-fetch.
            orders       : lista de pending orders do pré-fetch.
            exec_px      : mark price capturado em T=0 (fallback: snapshot do pré-fetch).
            candle_ts_ms : timestamp predito do candle fechado (UNIX ms).

        Lógica idêntica ao PASSO 4+5+6 de _process_closed_candle(), porém
        desacoplada de qualquer chamada REST — garante execução de milissegundo.
        """
        ts_dt = datetime.fromtimestamp(candle_ts_ms / 1000, tz=timezone.utc)

        # ── SAÍDAS ─────────────────────────────────────────────────────────
        for act in exits:
            kind  = act.get('action', '')
            a_qty = float(act.get('qty') or 0)
            a_rsn = act.get('exit_reason', kind)

            if kind == 'EXIT_LONG':
                if self._is_paper():
                    self._paper_close_long(exec_px, a_rsn, ts_dt)
                else:
                    try:
                        self.bitget.close_long(a_qty, exec_px, a_rsn)
                    except Exception as _e:
                        log.error(f"  ❌ [CLOCK] close_long: {_e}")
                        continue
                self._add_log('EXIT_LONG', exec_px, a_qty, a_rsn)
                self._cache_pos = None
                self._cache_bal = self.strategy.balance
                if self._is_paper():
                    self.paper.balance = self.strategy.balance
                log.info(
                    f"  ✅ [CLOCK] EXIT_LONG @ {exec_px:.2f} "
                    f"| {a_rsn} | bal={self.strategy.balance:.2f}"
                )

            elif kind == 'EXIT_SHORT':
                if self._is_paper():
                    self._paper_close_short(exec_px, a_rsn, ts_dt)
                else:
                    try:
                        self.bitget.close_short(a_qty, exec_px, a_rsn)
                    except Exception as _e:
                        log.error(f"  ❌ [CLOCK] close_short: {_e}")
                        continue
                self._add_log('EXIT_SHORT', exec_px, a_qty, a_rsn)
                self._cache_pos = None
                self._cache_bal = self.strategy.balance
                if self._is_paper():
                    self.paper.balance = self.strategy.balance
                log.info(
                    f"  ✅ [CLOCK] EXIT_SHORT @ {exec_px:.2f} "
                    f"| {a_rsn} | bal={self.strategy.balance:.2f}"
                )

        # ── ENTRADAS ───────────────────────────────────────────────────────
        for order in orders:
            side  = order['side']
            o_qty = order['qty']
            if o_qty <= 0:
                continue

            fill_px = exec_px

            if side == 'BUY':
                qty_f = o_qty
                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'long':
                        continue
                    if pos and pos['side'] == 'short':
                        log.warning("  ⚠️ [CLOCK] BUY: fechando short residual (reversal)")
                        self._paper_close_short(fill_px, 'REVERSAL', ts_dt)
                    log.info(f"  🟢 [CLOCK][PAPER] ENTER LONG {o_qty:.6f} ETH @ {fill_px:.2f}")
                    r, qty_f = self.paper.open_long(o_qty, self._cache_bal, fill_px, ts=ts_dt)
                    if r.get("code") != "0":
                        log.error("  ❌ [CLOCK] paper.open_long falhou")
                        continue
                else:
                    pos = self.bitget.position()
                    if pos and pos['side'] == 'long':
                        continue
                    if pos and pos['side'] == 'short':
                        log.info(f"  ↩️ [CLOCK] REVERSAL: fechando SHORT @ {fill_px:.2f}")
                        try:
                            self.bitget.close_short(pos['size'], fill_px, "REVERSAL")
                        except Exception as _e:
                            log.error(f"  ❌ [CLOCK] reversal close_short: {_e}")
                    log.info(
                        f"  🟢 [CLOCK] LIVE ENTER LONG {o_qty:.6f} ETH @ {fill_px:.2f} "
                        "(clock-sync — zero delay)"
                    )
                    r, qty_f = self.bitget.open_long(o_qty, self._cache_bal, fill_px)
                    if r.get("code") == "SKIP":
                        log.warning(f"  ⛔ [CLOCK] LONG ignorado — {r.get('msg')}")
                        continue
                    if r.get("code") != "00000":
                        log.error("  ❌ [CLOCK] bitget.open_long falhou")
                        continue

                close_act = self.strategy.confirm_fill('BUY', fill_px, qty_f, ts_dt)
                if close_act:
                    self._add_log(
                        close_act.get('action', 'REVERSAL'), fill_px, qty_f, 'REVERSAL')
                    log.info(
                        f"  ↩️ [CLOCK] confirm_fill reversal: "
                        f"{close_act.get('action')} @ {fill_px:.2f}"
                    )
                self._add_log("ENTER_LONG", fill_px, qty_f)
                self._cache_pos = {'side': 'long', 'size': qty_f, 'avg_px': fill_px}
                self._cache_bal = self.strategy.balance
                if self._is_paper():
                    self.paper.balance = self.strategy.balance
                self._pending_entry_check = True
                log.info(
                    f"  ✅ [CLOCK] LONG confirmado | fill_px={fill_px:.2f} "
                    f"qty={qty_f:.4f} | bal={self.strategy.balance:.2f}"
                )

            elif side == 'SELL':
                qty_f = o_qty
                if self._is_paper():
                    pos = self.paper.get_position()
                    if pos and pos['side'] == 'short':
                        continue
                    if pos and pos['side'] == 'long':
                        log.warning("  ⚠️ [CLOCK] SELL: fechando long residual (reversal)")
                        self._paper_close_long(fill_px, 'REVERSAL', ts_dt)
                    log.info(f"  🔴 [CLOCK][PAPER] ENTER SHORT {o_qty:.6f} ETH @ {fill_px:.2f}")
                    r, qty_f = self.paper.open_short(o_qty, self._cache_bal, fill_px, ts=ts_dt)
                    if r.get("code") != "0":
                        log.error("  ❌ [CLOCK] paper.open_short falhou")
                        continue
                else:
                    pos = self.bitget.position()
                    if pos and pos['side'] == 'short':
                        continue
                    if pos and pos['side'] == 'long':
                        log.info(f"  ↩️ [CLOCK] REVERSAL: fechando LONG @ {fill_px:.2f}")
                        try:
                            self.bitget.close_long(pos['size'], fill_px, "REVERSAL")
                        except Exception as _e:
                            log.error(f"  ❌ [CLOCK] reversal close_long: {_e}")
                    log.info(
                        f"  🔴 [CLOCK] LIVE ENTER SHORT {o_qty:.6f} ETH @ {fill_px:.2f} "
                        "(clock-sync — zero delay)"
                    )
                    r, qty_f = self.bitget.open_short(o_qty, self._cache_bal, fill_px)
                    if r.get("code") == "SKIP":
                        log.warning(f"  ⛔ [CLOCK] SHORT ignorado — {r.get('msg')}")
                        continue
                    if r.get("code") != "00000":
                        log.error("  ❌ [CLOCK] bitget.open_short falhou")
                        continue

                close_act = self.strategy.confirm_fill('SELL', fill_px, qty_f, ts_dt)
                if close_act:
                    self._add_log(
                        close_act.get('action', 'REVERSAL'), fill_px, qty_f, 'REVERSAL')
                    log.info(
                        f"  ↩️ [CLOCK] confirm_fill reversal: "
                        f"{close_act.get('action')} @ {fill_px:.2f}"
                    )
                self._add_log("ENTER_SHORT", fill_px, qty_f)
                self._cache_pos = {'side': 'short', 'size': qty_f, 'avg_px': fill_px}
                self._cache_bal = self.strategy.balance
                if self._is_paper():
                    self.paper.balance = self.strategy.balance
                self._pending_entry_check = True
                log.info(
                    f"  ✅ [CLOCK] SHORT confirmado | fill_px={fill_px:.2f} "
                    f"qty={qty_f:.4f} | bal={self.strategy.balance:.2f}"
                )

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

        # ── CLOCK-SYNC: estado do ciclo preditivo ────────────────────────────
        # Cada variável cobre exatamente um ciclo candle → reset em ciclo completo.
        _prefetch_done:         bool  = False   # pré-fetch executado para este candle
        _prefetch_exits:        list  = []      # saídas coletadas no pré-fetch
        _prefetch_orders:       list  = []      # entradas coletadas no pré-fetch
        _prefetch_snapshot_px:  float = 0.0    # mark price do pré-fetch
        _prefetch_candle_ts_ms: int   = 0       # ts predito (ms) do candle a fechar
        _clock_executed:        bool  = False   # execução clock disparada
        _clock_executed_at:     float = 0.0    # unix ts do momento da execução
        _rest_validated:        bool  = False   # validação REST pós-execução concluída
        _clock_cycle_start:     float = 0.0    # FIX‑KEEPALIVE: inicio do ciclo para timeout

        while self._running:
            try:
                if not self._running:
                    loop_exit_reason = "stop() chamado"
                    break

                now     = time.time()
                tf_secs = TIMEFRAME_SECS.get(TIMEFRAME, 1800)

                # ── Relógio local: próximo fechamento do candle ───────────────
                # Para 30 m, os fechamentos ocorrem exatamente nos múltiplos de
                # 1800 s na escala Unix: 00:00, 00:30, 01:00, …
                next_close_unix = math.ceil(now / tf_secs) * tf_secs
                time_to_close   = next_close_unix - now       # + = falta; - = passou
                predicted_ts_ms = int(next_close_unix * 1000) # ts do candle fechando

                # FIX‑KEEPALIVE: timeout para reset do ciclo clock-sync
                if _clock_executed and not _rest_validated and (now - _clock_executed_at) > CLOCK_SYNC_TIMEOUT:
                    log.warning("  ⚠️ [CLOCK‑SYNC] Timeout na validação REST — forçando reset do ciclo")
                    _prefetch_done = False
                    _clock_executed = False
                    _rest_validated = False
                    _clock_cycle_start = 0.0

                # ── PRIORIDADE 1: Trailing stop intra-barra ───────────────────
                # Executa a CADA iteração quando há posição aberta, independente
                # do sucesso das outras prioridades. Usa _mark_price_fast
                # (2 tentativas × 0.2 s) para manter latência baixa no loop de 0.5 s.
                has_position = self.strategy.position_size != 0

                if has_position:
                    ticker_px = self._mark_price_fast()
                    if ticker_px and ticker_px > 0:
                        self._cache_px = ticker_px
                    else:
                        ticker_px = 0.0

                    log.debug(
                        f"  🔍 [TRAIL-POLL] px={ticker_px:.2f} "
                        f"stop_L={self.strategy.long_stop:.2f} "
                        f"stop_S={self.strategy.short_stop:.2f} "
                        f"trail={'ON' if self.strategy._trail_active else 'off'}"
                    )

                    with self._pos_lock:
                        is_entry = self._pending_entry_check
                        exit_act = self.strategy.update_trailing_live(
                            high=(self._forming_high
                                  if self._forming_high > 0 else ticker_px),
                            low=(self._forming_low
                                 if self._forming_low < float('inf') else ticker_px),
                            ts=(self._forming_ts or datetime.now(timezone.utc)),
                            is_entry_candle=is_entry,
                            current_price=ticker_px,
                        )
                        if is_entry:
                            self._pending_entry_check = False

                        if exit_act:
                            side_exit    = exit_act['action']
                            qty_exit     = exit_act['qty']
                            px_exit      = exit_act['price']
                            rsn_exit     = exit_act.get('exit_reason', 'STOP')
                            # FIX-13: reutiliza ticker_px já obtido como gatilho
                            exit_fill_px = (ticker_px
                                            if ticker_px and ticker_px > 0
                                            else px_exit)
                            ts_exit = self._forming_ts or datetime.now(timezone.utc)

                            if self._is_paper():
                                if side_exit == 'EXIT_LONG':
                                    self._paper_close_long(exit_fill_px, rsn_exit, ts_exit)
                                else:
                                    self._paper_close_short(exit_fill_px, rsn_exit, ts_exit)
                            else:
                                try:
                                    if side_exit == 'EXIT_LONG':
                                        self.bitget.close_long(qty_exit, exit_fill_px, rsn_exit)
                                    else:
                                        self.bitget.close_short(qty_exit, exit_fill_px, rsn_exit)
                                except Exception as _e:
                                    log.error(f"  ❌ close via trailing live: {_e}")

                            self._cache_pos = None
                            self._cache_bal = self.strategy.balance
                            if self._is_paper():
                                self.paper.balance = self.strategy.balance

                            self._add_log(side_exit, exit_fill_px, qty_exit, rsn_exit)
                            log.info(
                                f"  ✅ EXIT intra-barra | {side_exit} @ {exit_fill_px:.2f} "
                                f"| motivo={rsn_exit} | bal={self.strategy.balance:.2f}"
                            )
                            # Reseta cache de candle em formação e estado do pré-fetch
                            self._forming_high = 0.0
                            self._forming_low  = float('inf')
                            # Invalida pré-fetch pendente: posição mudou intra-barra
                            _prefetch_done  = False
                            _clock_executed = False
                            _rest_validated = False
                            _clock_cycle_start = 0.0

                # ── PRIORIDADE 2: PRÉ-FETCH de sinal (T − PREFETCH_LEAD_SECS) ─
                # Cerca de 2 s antes do fechamento oficial, captura o mark price,
                # constrói um candle sintético com os dados em formação e roda
                # strategy.next() antecipado. As ordens resultantes são guardadas
                # em memória para disparo imediato na PRIORIDADE 3.
                if (
                    time_to_close <= PREFETCH_LEAD_SECS
                    and time_to_close > 0               # candle ainda não fechou
                    and not _prefetch_done
                    and predicted_ts_ms != last_processed_closed_ts
                ):
                    snap_pre = self._mark_price_fast()
                    if snap_pre and snap_pre > 0:
                        # Candle sintético: usa H/L/O do candle em formação
                        # e o mark price atual como close sintético.
                        _fo = self._forming_open if self._forming_open > 0 else snap_pre
                        _fh = (max(self._forming_high, snap_pre)
                               if self._forming_high > 0 else snap_pre)
                        _fl = (min(self._forming_low,  snap_pre)
                               if self._forming_low < float('inf') else snap_pre)
                        synthetic_candle = {
                            'open':      _fo,
                            'high':      _fh,
                            'low':       _fl,
                            'close':     snap_pre,
                            'timestamp': datetime.fromtimestamp(
                                next_close_unix - tf_secs, tz=timezone.utc),
                            'index':     self.strategy._bar + 1,
                        }

                        with self._pos_lock:
                            pf_actions = self.strategy.next(synthetic_candle)
                            pf_pending  = self.strategy.get_pending_orders()

                        _prefetch_exits       = [a for a in pf_actions
                                                  if a.get('action') in
                                                  ('EXIT_LONG', 'EXIT_SHORT')]
                        _prefetch_orders      = pf_pending
                        _prefetch_snapshot_px = snap_pre
                        _prefetch_candle_ts_ms = predicted_ts_ms
                        _prefetch_done        = True
                        _clock_executed       = False
                        _rest_validated       = False
                        _clock_cycle_start    = now

                        log.info(
                            f"  ⚡ [PRÉ-FETCH T-{time_to_close:.2f}s] "
                            f"px={snap_pre:.2f} | sintético: "
                            f"O={_fo:.2f} H={_fh:.2f} L={_fl:.2f} C={snap_pre:.2f} | "
                            f"{len(_prefetch_exits)} saída(s) | "
                            f"{len(_prefetch_orders)} entrada(s) pré-carregada(s)"
                        )
                    else:
                        log.warning(
                            f"  ⚠️ [PRÉ-FETCH T-{time_to_close:.2f}s] mark price "
                            "indisponível — aguardando REST fallback (P5)"
                        )

                # ── PRIORIDADE 3: EXECUÇÃO CLOCK (T ≤ 0) ─────────────────────
                # No instante exato em que o relógio local indica que o candle
                # fechou, dispara as ordens pré-carregadas com um mark price fresco
                # obtido agora — ANTES de qualquer confirmação via REST API da Bitget.
                if (
                    time_to_close <= 0
                    and _prefetch_done
                    and not _clock_executed
                    and _prefetch_candle_ts_ms != last_processed_closed_ts
                ):
                    overshoot_ms = abs(time_to_close) * 1000
                    log.info(
                        f"  🕐 [CLOCK-EXEC T+{overshoot_ms:.0f}ms] "
                        f"Disparando {len(_prefetch_exits)} saída(s) + "
                        f"{len(_prefetch_orders)} entrada(s) pré-carregada(s)..."
                    )

                    # Mark price fresco em T=0; fallback para snapshot do pré-fetch
                    # somente se a chamada falhar (latência de rede crítica).
                    exec_px = self._mark_price() or _prefetch_snapshot_px
                    if exec_px and exec_px > 0:
                        self._execute_clock_orders(
                            exits        = _prefetch_exits,
                            orders       = _prefetch_orders,
                            exec_px      = exec_px,
                            candle_ts_ms = _prefetch_candle_ts_ms,
                        )
                        last_processed_closed_ts = _prefetch_candle_ts_ms
                        _clock_executed    = True
                        _clock_executed_at = now
                        _rest_validated    = False
                        self._refresh_cache()
                    else:
                        log.error(
                            "  ❌ [CLOCK-EXEC] Mark price indisponível em T=0 — "
                            "abortando execução clock; REST fallback (P5) assumirá o ciclo"
                        )
                        # Reseta pré-fetch para que o REST fallback processe normalmente
                        _prefetch_done   = False
                        _prefetch_exits  = []
                        _prefetch_orders = []

                # ── PRIORIDADE 4: VALIDAÇÃO REST (T + REST_VALIDATE_DELAY) ────
                # Aguarda REST_VALIDATE_DELAY s após T=0 e busca o candle via REST
                # somente para atualizar o cache do novo candle em formação
                # (_forming_open / _forming_high / _forming_low).
                # IMPORTANTE: strategy.next() NÃO é chamado aqui — o candle já
                # foi processado no pré-fetch (P2). Apenas sincroniza o estado
                # do trailing stop e exibe o candle oficial no log.
                if (
                    _clock_executed
                    and not _rest_validated
                    and (now - _clock_executed_at) >= REST_VALIDATE_DELAY
                ):
                    val_candles = self._candle_single()
                    if val_candles and len(val_candles) >= 2:
                        val_cur = val_candles[0]   # novo candle em formação
                        try:
                            _vts = datetime.fromtimestamp(
                                int(val_cur[0]) / 1000, tz=timezone.utc)
                            self._forming_high = float(val_cur[2])
                            self._forming_low  = float(val_cur[3])
                            self._forming_open = float(val_cur[1])
                            self._forming_ts   = _vts
                            _rest_validated    = True
                            log.info(
                                f"  ✅ [REST-VALIDATE] Novo candle em formação: "
                                f"O={val_cur[1]} H={val_cur[2]} L={val_cur[3]} "
                                f"| ts={_vts}"
                            )
                        except (ValueError, IndexError) as _ve:
                            log.warning(
                                f"  ⚠️ [REST-VALIDATE] Extração de candle falhou: {_ve}"
                            )
                    # REST ainda não atualizou? Continuará tentando na próxima iteração.

                # ── PRIORIDADE 5: REST FALLBACK ───────────────────────────────
                # Ativado somente quando o pré-fetch falhou (_prefetch_done=False)
                # e o clock-exec não aconteceu. Processa o candle pela rota REST
                # clássica (detecta timestamp novo em prev[0]), garantindo que o
                # bot nunca perca um fechamento por falha do clock-sync.
                if not _prefetch_done and not _clock_executed:
                    fb_candles = self._candle_single()
                    if fb_candles is None or len(fb_candles) < 2:
                        time.sleep(CONSTANT_SLEEP)
                        continue

                    fb_cur  = fb_candles[0]
                    fb_prev = fb_candles[1]

                    # Atualiza cache do candle em formação
                    if len(fb_cur) >= 5:
                        try:
                            _fts = datetime.fromtimestamp(
                                int(fb_cur[0]) / 1000, tz=timezone.utc)
                            self._forming_high = float(fb_cur[2])
                            self._forming_low  = float(fb_cur[3])
                            self._forming_open = float(fb_cur[1])
                            self._forming_ts   = _fts
                        except (ValueError, IndexError) as _fe:
                            log.warning(f"  ⚠️ [REST-FALLBACK] Candle em formação: {_fe}")

                    if len(fb_prev) < 5:
                        time.sleep(CONSTANT_SLEEP)
                        continue

                    try:
                        fb_prev_ts_raw = int(fb_prev[0])
                    except (ValueError, IndexError) as _te:
                        log.warning(f"  ⚠️ [REST-FALLBACK] Timestamp inválido: {_te}")
                        time.sleep(CONSTANT_SLEEP)
                        continue

                    if (last_processed_closed_ts is None
                            or fb_prev_ts_raw > last_processed_closed_ts):
                        try:
                            fb_prev_dt   = datetime.fromtimestamp(
                                fb_prev_ts_raw / 1000, tz=timezone.utc)
                            fb_closed = {
                                'open':      float(fb_prev[1]),
                                'high':      float(fb_prev[2]),
                                'low':       float(fb_prev[3]),
                                'close':     float(fb_prev[4]),
                                'timestamp': fb_prev_dt,
                                'index':     self.strategy._bar + 1,
                            }
                        except (ValueError, IndexError) as _ee:
                            log.warning(f"  ⚠️ [REST-FALLBACK] Extração de candle: {_ee}")
                            time.sleep(CONSTANT_SLEEP)
                            continue

                        log.info(
                            f"  🛟 [REST-FALLBACK] Candle fechado [{fb_prev_ts_raw}]: "
                            f"O={fb_closed['open']:.2f} H={fb_closed['high']:.2f} "
                            f"L={fb_closed['low']:.2f} C={fb_closed['close']:.2f} "
                            f"@ {fb_prev_dt}"
                        )
                        new_ts = self._process_closed_candle(
                            fb_closed, fb_prev_ts_raw, last_processed_closed_ts)
                        if new_ts is not None:
                            last_processed_closed_ts = new_ts
                            log.debug(
                                f"  ✔ [REST-FALLBACK] Candle {fb_prev_ts_raw} processado")
                        else:
                            log.warning(
                                f"  ⚠️ [REST-FALLBACK] Processamento do candle "
                                f"{fb_prev_ts_raw} falhou")
                        self._refresh_cache()

                # ── Reset: ciclo clock-sync completo ──────────────────────────
                # Libera o estado somente quando AMBAS as fases terminaram:
                # execução clock (P3) + validação REST (P4).
                if _clock_executed and _rest_validated:
                    _prefetch_done         = False
                    _prefetch_exits        = []
                    _prefetch_orders       = []
                    _prefetch_snapshot_px  = 0.0
                    _prefetch_candle_ts_ms = 0
                    _clock_executed        = False
                    _clock_executed_at     = 0.0
                    _rest_validated        = False
                    _clock_cycle_start     = 0.0
                    log.debug("  🔄 [CLOCK-SYNC] Ciclo completo — estado resetado")

                time.sleep(CONSTANT_SLEEP)

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
            <thead>  <tr><th>Hora</th><th>Ação</th><th>Preço</th><th>Qty ETH</th><th>Motivo</th> </tr> </thead>
            <tbody id="lv-trades">  <tr><td colspan="5" style="text-align:center;color:var(--muted);padding:20px">Aguardando...</td></tr> </tbody>
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
            <thead>  <tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th>
                       <th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th><th>Modo</th> </tr> </thead>
            <tbody id="hist-tbl">  <tr><td colspan="11" style="text-align:center;color:var(--muted);padding:20px">Carregando...</td></tr> </tbody>
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
              <thead>  <tr><th>#</th><th>Entrada</th><th>Saída</th><th>Dir</th><th>Qty</th><th>P. Entrada</th><th>P. Saída</th><th>PnL USDT</th><th>PnL %</th><th>Motivo</th> </tr> </thead>
              <tbody id="bt-tbl"></tbody>
                </table>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:20px">
        <div class="card-head"><span class="card-title">HISTÓRICO DE BACKTESTS</span></div>
        <div class="tbl-wrap">
              <table>
            <thead>  <tr><th>Data</th><th>Símbolo</th><th>TF</th><th>Candles</th><th>PnL</th><th>Win Rate</th><th>Trades</th><th>PF</th><th>Drawdown</th><th>Sharpe</th> </tr> </thead>
            <tbody id="bt-hist-tbl">  <tr><td colspan="10" style="text-align:center;color:var(--muted);padding:20px">Sem histórico</td></tr> </tbody>
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
    if (d.bal  != null) document.getElementById('lv-bal').textContent
