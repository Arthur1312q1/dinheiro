# main.py
# ═══════════════════════════════════════════════════════════════════════════════
# AZLEMA — Adaptive Zero Lag EMA | Backtest + Live Trading
# OKX ETH-USDT-SWAP Futures 1x | 95% do saldo por operação
# ═══════════════════════════════════════════════════════════════════════════════
#
# VARIÁVEIS DE AMBIENTE (configurar no Render):
#   OKX_API_KEY        → chave da API OKX
#   OKX_SECRET_KEY     → chave secreta OKX
#   OKX_PASSPHRASE     → passphrase OKX
#   MODE               → "backtest" (padrão) | "live"
#   SYMBOL             → "ETH-USDT" (padrão)
#   TIMEFRAME          → "30m" (padrão)
#   BACKTEST_CANDLES   → 4500 (padrão)
#   WARMUP_CANDLES     → 1000 (padrão)
#   INITIAL_CAPITAL    → 1000.0 (padrão, usado só em backtest)
#   PORT               → 5000 (padrão)
#
# ESTRATÉGIA: NÃO MODIFICADA — strategy/adaptive_zero_lag_ema.py intacto
# O live trader apenas:
#   1. Faz warmup da estratégia com histórico (sem executar ordens)
#   2. A cada close de barra, chama strategy.next(candle)
#   3. Se strategy.get_pending_orders() → executa na OKX
#   4. strategy monitora trail/SL internamente → quando retorna exit → fecha na OKX
# ═══════════════════════════════════════════════════════════════════════════════

import os
import hmac
import hashlib
import base64
import json
import time
import threading
import traceback
import logging
import requests
import argparse
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from flask import Flask, jsonify

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import DataCollector
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter


# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('azlema')


# ─── Helpers env ──────────────────────────────────────────────────────────────
def env(k, d=None):     return os.environ.get(k, d)
def env_int(k, d=0):
    v = os.environ.get(k)
    return int(v) if v else d
def env_float(k, d=0.0):
    v = os.environ.get(k)
    return float(v) if v else d

def normalize_symbol(s: str) -> str:
    s = s.strip().upper().replace('/', '-').replace('_', '-').replace(' ', '-')
    if '-' not in s and s.endswith('USDT'):
        s = s[:-4] + '-USDT'
    return s


# ─── Config — TUDO HARDCODED AQUI (só chaves OKX ficam no Render) ────────────
MODE             = "live"        # "backtest" | "live"
SYMBOL           = "ETH-USDT"
TIMEFRAME        = "30m"
BACKTEST_CANDLES = 4500          # 93.75 dias de trading (igual ao TradingView)
WARMUP_CANDLES   = 1000          # 20.8 dias extras para IFM/ZLEMA convergir
TOTAL_CANDLES    = BACKTEST_CANDLES + WARMUP_CANDLES   # = 5500

# OKX credentials — ÚNICAS coisas que vêm do Render (Environment Variables)
OKX_API_KEY     = env("OKX_API_KEY",     "")
OKX_SECRET_KEY  = env("OKX_SECRET_KEY",  "")
OKX_PASSPHRASE  = env("OKX_PASSPHRASE",  "")

# Estratégia — config fixa, não alterar (afeta resultados do backtest)
STRATEGY_CONFIG = {
    "adaptive_method": "Cos IFM",
    "threshold":       0.0,
    "fixed_sl_points": 2000,
    "fixed_tp_points": 55,
    "trail_offset":    15,
    "risk_percent":    0.01,   # irrelevante no live (usa 95% do saldo real da OKX)
    "tick_size":       0.01,
    "initial_capital": 1000.0, # irrelevante no live (sizing usa saldo real da OKX)
    "max_lots":        100,
    "default_period":  20,
    "warmup_bars":     WARMUP_CANDLES,
}


# ═══════════════════════════════════════════════════════════════════════════════
# OKX API CLIENT (embutido no main.py conforme solicitado)
# ═══════════════════════════════════════════════════════════════════════════════
class OKX:
    """
    Cliente OKX minimalista para futures ETH-USDT-SWAP.
    Todas as chamadas são REST autenticadas via HMAC-SHA256.
    """
    BASE = "https://www.okx.com"
    INST = "ETH-USDT-SWAP"   # Perpetual futures ETH/USDT

    def __init__(self, api_key: str, secret: str, passphrase: str):
        self.api_key    = api_key
        self.secret     = secret
        self.passphrase = passphrase

    # ── Autenticação OKX ──────────────────────────────────────────────────────
    def _sign(self, ts: str, method: str, path: str, body: str = "") -> str:
        msg = ts + method.upper() + path + body
        return base64.b64encode(
            hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        return {
            "OK-ACCESS-KEY":        self.api_key,
            "OK-ACCESS-SIGN":       self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type":         "application/json",
        }

    def _get(self, path: str, params: dict = None) -> dict:
        qs   = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
        full = path + qs
        resp = requests.get(self.BASE + full,
                            headers=self._headers("GET", full),
                            timeout=10)
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        b    = json.dumps(body)
        resp = requests.post(self.BASE + path,
                             headers=self._headers("POST", path, b),
                             data=b, timeout=10)
        return resp.json()

    # ── Conta / Posição ───────────────────────────────────────────────────────
    def get_usdt_balance(self) -> float:
        """Retorna saldo USDT disponível na conta Futures/Trading."""
        r = self._get("/api/v5/account/balance", {"ccy": "USDT"})
        try:
            for item in r["data"][0]["details"]:
                if item["ccy"] == "USDT":
                    return float(item["availBal"])
        except Exception:
            pass
        log.error(f"Erro ao buscar saldo: {r}")
        return 0.0

    def get_position(self) -> Optional[Dict]:
        """
        Retorna posição aberta em ETH-USDT-SWAP, ou None se flat.
        Retorno: {'side': 'long'|'short', 'size': float, 'avg_px': float}
        """
        r = self._get("/api/v5/account/positions", {"instType": "SWAP", "instId": self.INST})
        try:
            for pos in r.get("data", []):
                sz = float(pos.get("pos", 0))
                if sz != 0:
                    return {
                        "side":   pos["posSide"],    # "long" | "short"
                        "size":   abs(sz),
                        "avg_px": float(pos.get("avgPx", 0)),
                    }
        except Exception:
            pass
        return None

    def get_mark_price(self) -> float:
        """Retorna mark price atual do contrato."""
        r = self._get("/api/v5/public/mark-price",
                      {"instType": "SWAP", "instId": self.INST})
        try:
            return float(r["data"][0]["markPx"])
        except Exception:
            pass
        # Fallback: ticker
        r2 = self._get("/api/v5/market/ticker", {"instId": self.INST})
        try:
            return float(r2["data"][0]["last"])
        except Exception:
            return 0.0

    def get_contract_size(self) -> float:
        """
        Tamanho do contrato ETH-USDT-SWAP em ETH.
        OKX: 1 contrato = 0.01 ETH (ctVal=0.01)
        """
        r = self._get("/api/v5/public/instruments",
                      {"instType": "SWAP", "instId": self.INST})
        try:
            return float(r["data"][0]["ctVal"])
        except Exception:
            return 0.01  # default OKX ETH-USDT-SWAP

    def set_leverage(self, lever: int = 1, mode: str = "cross") -> bool:
        """Define alavancagem para o contrato."""
        r = self._post("/api/v5/account/set-leverage", {
            "instId":  self.INST,
            "lever":   str(lever),
            "mgnMode": mode,
        })
        ok = r.get("code") == "0"
        if not ok:
            log.error(f"set_leverage erro: {r}")
        return ok

    def set_position_mode(self) -> bool:
        """Define modo de posição como long/short separados."""
        r = self._post("/api/v5/account/set-position-mode",
                       {"posMode": "long_short_mode"})
        ok = r.get("code") == "0"
        if not ok:
            # já pode estar configurado
            log.debug(f"set_position_mode: {r.get('msg')}")
        return True

    # ── Ordens ────────────────────────────────────────────────────────────────
    def _contracts_from_eth(self, eth_qty: float) -> int:
        """Converte quantidade ETH → número inteiro de contratos OKX."""
        ct_val = self.get_contract_size()   # 0.01 ETH por contrato
        return max(1, int(eth_qty / ct_val))

    def open_long(self, eth_qty: float) -> Dict:
        """
        Abre posição LONG com market order.
        eth_qty: quantidade em ETH (ex: 0.5)
        """
        sz = self._contracts_from_eth(eth_qty)
        body = {
            "instId":  self.INST,
            "tdMode":  "cross",
            "side":    "buy",
            "posSide": "long",
            "ordType": "market",
            "sz":      str(sz),
        }
        log.info(f"  → OPEN LONG: {sz} contratos ({eth_qty:.4f} ETH)")
        r = self._post("/api/v5/trade/order", body)
        self._log_order_result(r, "OPEN_LONG")
        return r

    def open_short(self, eth_qty: float) -> Dict:
        """Abre posição SHORT com market order."""
        sz = self._contracts_from_eth(eth_qty)
        body = {
            "instId":  self.INST,
            "tdMode":  "cross",
            "side":    "sell",
            "posSide": "short",
            "ordType": "market",
            "sz":      str(sz),
        }
        log.info(f"  → OPEN SHORT: {sz} contratos ({eth_qty:.4f} ETH)")
        r = self._post("/api/v5/trade/order", body)
        self._log_order_result(r, "OPEN_SHORT")
        return r

    def close_long(self, eth_qty: float) -> Dict:
        """Fecha posição LONG com market order."""
        sz = self._contracts_from_eth(eth_qty)
        body = {
            "instId":  self.INST,
            "tdMode":  "cross",
            "side":    "sell",
            "posSide": "long",
            "ordType": "market",
            "sz":      str(sz),
        }
        log.info(f"  → CLOSE LONG: {sz} contratos ({eth_qty:.4f} ETH)")
        r = self._post("/api/v5/trade/order", body)
        self._log_order_result(r, "CLOSE_LONG")
        return r

    def close_short(self, eth_qty: float) -> Dict:
        """Fecha posição SHORT com market order."""
        sz = self._contracts_from_eth(eth_qty)
        body = {
            "instId":  self.INST,
            "tdMode":  "cross",
            "side":    "buy",
            "posSide": "short",
            "ordType": "market",
            "sz":      str(sz),
        }
        log.info(f"  → CLOSE SHORT: {sz} contratos ({eth_qty:.4f} ETH)")
        r = self._post("/api/v5/trade/order", body)
        self._log_order_result(r, "CLOSE_SHORT")
        return r

    def close_all(self) -> None:
        """Fecha todas as posições abertas (emergência)."""
        pos = self.get_position()
        if pos is None:
            return
        if pos["side"] == "long":
            self.close_long(pos["size"] * self.get_contract_size())
        else:
            self.close_short(pos["size"] * self.get_contract_size())

    def _log_order_result(self, r: dict, tag: str) -> None:
        if r.get("code") == "0":
            data = r.get("data", [{}])[0]
            log.info(f"  ✅ {tag} OK | ordId={data.get('ordId')} sCode={data.get('sCode')}")
        else:
            log.error(f"  ❌ {tag} ERRO: {r}")


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TRADER
# ═══════════════════════════════════════════════════════════════════════════════
class LiveTrader:
    """
    Executa a estratégia AZLEMA em tempo real na OKX.

    Fluxo por barra (30 min):
      1. Aguarda o close da barra atual
      2. Busca o candle fechado
      3. strategy.next(candle) → retorna ações (exits intra-barra)
      4. Executa exits retornados pela estratégia (trail/SL detectados)
      5. strategy.get_pending_orders() → ordens para o próximo open
      6. Executa ordens imediatamente (somos o "próximo open")
      7. strategy.confirm_fill() → atualiza estado interno da estratégia

    Sizing: 95% do saldo USDT disponível na OKX / mark_price = ETH qty
    Alavancagem: 1x cross margin (configurado no startup)
    """

    BALANCE_PCT = 0.95   # 95% do saldo por operação

    def __init__(self, okx: OKX, strategy: AdaptiveZeroLagEMA):
        self.okx      = okx
        self.strategy = strategy
        self._running = False
        self._trade_log: List[Dict] = []   # log de operações reais

    # ── Setup inicial ─────────────────────────────────────────────────────────
    def setup(self) -> bool:
        """Configura alavancagem 1x e modo de posição na OKX."""
        log.info("⚙️  Configurando OKX...")

        # Modo long/short separados (necessário para entrar de qualquer lado)
        self.okx.set_position_mode()

        # Alavancagem 1x
        ok = self.okx.set_leverage(1, "cross")
        if ok:
            log.info("  ✅ Alavancagem 1x configurada")
        else:
            log.warning("  ⚠️  Falha ao configurar alavancagem (pode já estar certa)")

        # Verificar credenciais
        bal = self.okx.get_usdt_balance()
        if bal <= 0:
            log.error("  ❌ Saldo USDT = 0 ou credenciais inválidas")
            return False

        log.info(f"  ✅ Saldo USDT disponível: {bal:.4f}")
        return True

    # ── Warmup ────────────────────────────────────────────────────────────────
    def warmup(self, df: pd.DataFrame) -> None:
        """
        Processa candles históricos SEM executar ordens reais.
        Apenas aquece o estado interno da estratégia (IFM, ZLEMA, sinais).
        """
        log.info(f"🔄 Warmup: {len(df)} candles históricos...")

        for _, row in df.iterrows():
            candle = {
                'open':      float(row['open']),
                'high':      float(row['high']),
                'low':       float(row['low']),
                'close':     float(row['close']),
                'timestamp': row.get('timestamp', 0),
                'index':     int(row.get('index', 0)),
            }
            # A estratégia tem warmup_bars internamente — NÃO executa ordens
            self.strategy.next(candle)

        log.info(f"  ✅ Warmup concluído | Period={self.strategy.Period} | "
                 f"EC={self.strategy.EC:.4f} | EMA={self.strategy.EMA:.4f}")

        # Verificar se posição real bate com estado da estratégia
        self._sync_position()

    # ── Sync de posição ───────────────────────────────────────────────────────
    def _sync_position(self) -> None:
        """
        Sincroniza estado da estratégia com posição real da OKX.
        Importante ao fazer restart (evita abrir posição duplicada).
        """
        real_pos = self.okx.get_position()
        strat_pos = self.strategy.position_size

        log.info(f"📍 Posição OKX: {real_pos} | Estratégia: {strat_pos:.4f}")

        if real_pos is None and abs(strat_pos) > 0:
            # Estratégia acha que tem posição mas OKX está flat
            # → resetar estado da estratégia
            log.warning("  ⚠️  Estratégia com posição mas OKX flat → resetando estado")
            self.strategy._reset_pos()

        elif real_pos is not None and strat_pos == 0:
            # OKX tem posição mas estratégia não sabe
            # → atualizar estado da estratégia
            mark = self.okx.get_mark_price()
            ct   = self.okx.get_contract_size()
            qty  = real_pos["size"] * ct
            side = 'BUY' if real_pos["side"] == "long" else 'SELL'
            px   = real_pos["avg_px"]
            log.warning(f"  ⚠️  OKX tem posição {side} {qty:.4f} ETH @ {px} → sincronizando")
            self.strategy.confirm_fill(side, px, qty, datetime.utcnow())

    # ── Sizing ────────────────────────────────────────────────────────────────
    def _calc_qty(self) -> float:
        """
        Calcula quantidade ETH = 95% do saldo USDT / mark_price.
        Ex: saldo=1000 USDT, price=3000 → qty=0.3167 ETH
        """
        balance    = self.okx.get_usdt_balance()
        mark_price = self.okx.get_mark_price()

        if balance <= 0 or mark_price <= 0:
            log.error(f"  ❌ Saldo={balance} ou price={mark_price} inválidos")
            return 0.0

        qty = (balance * self.BALANCE_PCT) / mark_price
        log.info(f"  💰 Saldo: {balance:.4f} USDT | Preço: {mark_price:.2f} | "
                 f"Qty: {qty:.4f} ETH (95%)")
        return qty

    # ── Processamento de candle ───────────────────────────────────────────────
    def process_candle(self, candle: Dict) -> None:
        """
        Processa um candle fechado:
        1. Roda strategy.next() → detecta exits (trail/SL)
        2. Executa exits detectados na OKX
        3. Executa entries pendentes na OKX
        4. Atualiza estado da estratégia com confirm_fill/exit
        """
        ts  = candle.get('timestamp', datetime.utcnow())
        log.info(f"\n{'─'*55}")
        log.info(f"📊 Candle {ts} | O={candle['open']:.2f} H={candle['high']:.2f} "
                 f"L={candle['low']:.2f} C={candle['close']:.2f}")

        # ── Roda a estratégia ──────────────────────────────────────────────
        actions = self.strategy.next(candle)

        log.info(f"  Strategy: pos={self.strategy.position_size:+.4f} | "
                 f"Period={self.strategy.Period} | EC={self.strategy.EC:.4f} "
                 f"EMA={self.strategy.EMA:.4f} | "
                 f"el={self.strategy._el} es={self.strategy._es} | "
                 f"trail={'ON' if self.strategy._trail_active else 'off'}")

        # ── Processa exits retornados pela estratégia ──────────────────────
        for act in actions:
            action_type = act.get('action', '')

            if action_type == 'EXIT_LONG':
                log.info(f"  🔴 TRAIL/SL detectado: EXIT LONG @ {act['price']:.2f} "
                         f"(razão: {act.get('exit_reason')}) | PnL estimado: {act.get('pnl',0):.4f}")
                # Executa close na OKX (strategy já atualizou o estado internamente)
                real_pos = self.okx.get_position()
                if real_pos and real_pos['side'] == 'long':
                    ct  = self.okx.get_contract_size()
                    qty = real_pos['size'] * ct
                    r   = self.okx.close_long(qty)
                    fill_px = self._get_last_fill_price(r) or act['price']
                    # Nota: strategy já executou o exit internamente via next()
                    # confirm_exit atualiza PnL com preço real
                    self.strategy.confirm_exit('LONG', fill_px, qty, ts, act.get('exit_reason','TRAIL'))
                    self._log_trade("EXIT_LONG", fill_px, qty, act.get('exit_reason'))
                else:
                    log.warning("  ⚠️  EXIT_LONG mas OKX não tem posição long")

            elif action_type == 'EXIT_SHORT':
                log.info(f"  🔴 TRAIL/SL detectado: EXIT SHORT @ {act['price']:.2f} "
                         f"(razão: {act.get('exit_reason')}) | PnL estimado: {act.get('pnl',0):.4f}")
                real_pos = self.okx.get_position()
                if real_pos and real_pos['side'] == 'short':
                    ct  = self.okx.get_contract_size()
                    qty = real_pos['size'] * ct
                    r   = self.okx.close_short(qty)
                    fill_px = self._get_last_fill_price(r) or act['price']
                    self.strategy.confirm_exit('SHORT', fill_px, qty, ts, act.get('exit_reason','TRAIL'))
                    self._log_trade("EXIT_SHORT", fill_px, qty, act.get('exit_reason'))
                else:
                    log.warning("  ⚠️  EXIT_SHORT mas OKX não tem posição short")

        # ── Processa entries pendentes ─────────────────────────────────────
        pending = self.strategy.get_pending_orders()

        for order in pending:
            side = order['side']
            qty  = self._calc_qty()

            if qty <= 0:
                log.warning("  ⚠️  Qty=0, ignorando ordem")
                continue

            log.info(f"  🟢 ENTRY {side}: {qty:.4f} ETH")

            if side == 'BUY':
                # Fecha SHORT se houver (reversão)
                real_pos = self.okx.get_position()
                if real_pos and real_pos['side'] == 'short':
                    ct = self.okx.get_contract_size()
                    self.okx.close_short(real_pos['size'] * ct)
                    log.info("  ↩️  Reversão: fechou SHORT antes de abrir LONG")

                r       = self.okx.open_long(qty)
                fill_px = self._get_last_fill_price(r) or self.okx.get_mark_price()
                self.strategy.confirm_fill('BUY', fill_px, qty, ts)
                self._log_trade("ENTER_LONG", fill_px, qty)

            elif side == 'SELL':
                # Fecha LONG se houver (reversão)
                real_pos = self.okx.get_position()
                if real_pos and real_pos['side'] == 'long':
                    ct = self.okx.get_contract_size()
                    self.okx.close_long(real_pos['size'] * ct)
                    log.info("  ↩️  Reversão: fechou LONG antes de abrir SHORT")

                r       = self.okx.open_short(qty)
                fill_px = self._get_last_fill_price(r) or self.okx.get_mark_price()
                self.strategy.confirm_fill('SELL', fill_px, qty, ts)
                self._log_trade("ENTER_SHORT", fill_px, qty)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _get_last_fill_price(self, order_response: dict) -> Optional[float]:
        """Extrai preço de fill de uma resposta de ordem OKX."""
        try:
            ord_id = order_response["data"][0]["ordId"]
            # Aguarda fill (market order — normalmente < 1s)
            time.sleep(1)
            r = self.okx._get("/api/v5/trade/order",
                               {"instId": self.okx.INST, "ordId": ord_id})
            return float(r["data"][0]["avgPx"])
        except Exception:
            return None

    def _log_trade(self, action: str, price: float, qty: float, reason: str = "") -> None:
        """Salva operação no log interno."""
        entry = {
            "time":   datetime.utcnow().isoformat(),
            "action": action,
            "price":  price,
            "qty":    qty,
            "reason": reason,
            "balance_strategy": self.strategy.balance,
        }
        self._trade_log.append(entry)
        log.info(f"  📝 LOG: {action} | {qty:.4f} ETH @ {price:.2f}")

    # ── Loop principal ────────────────────────────────────────────────────────
    def _wait_for_candle_close(self, timeframe_min: int = 30) -> None:
        """
        Aguarda o próximo close de barra.
        Para 30min: espera até :00 ou :30 + 3s de segurança.
        """
        now     = datetime.utcnow()
        minutes = now.minute
        secs    = now.second
        tf      = timeframe_min

        # Quanto falta para o próximo múltiplo de tf minutos
        next_min = ((minutes // tf) + 1) * tf
        if next_min >= 60:
            next_min -= 60
            wait = (60 - minutes - 1) * 60 + (60 - secs) + next_min * 60 + 3
        else:
            wait = (next_min - minutes) * 60 - secs + 3

        log.info(f"⏰ Aguardando próximo close em {wait:.0f}s "
                 f"(~{datetime.utcnow().strftime('%H:%M')} UTC)...")
        time.sleep(max(1, wait))

    def _fetch_latest_candle(self) -> Optional[Dict]:
        """
        Busca o candle mais recente fechado da OKX.
        Retorna o segundo candle da lista (o primeiro é o atual, ainda aberto).
        """
        TF_MAP = {
            '1m':'1m','5m':'5m','15m':'15m','30m':'30m',
            '1h':'1H','4h':'4H','1d':'1D',
        }
        bar = TF_MAP.get(TIMEFRAME, '30m')
        r   = requests.get(
            "https://www.okx.com/api/v5/market/candles",
            params={"instId": "ETH-USDT-SWAP", "bar": bar, "limit": "2"},
            timeout=10
        ).json()

        try:
            # data[0] = barra atual (aberta), data[1] = última fechada
            c = r["data"][1]
            return {
                'open':      float(c[1]),
                'high':      float(c[2]),
                'low':       float(c[3]),
                'close':     float(c[4]),
                'timestamp': datetime.fromtimestamp(int(c[0])/1000, tz=timezone.utc),
                'index':     self.strategy._bar + 1,
            }
        except Exception as e:
            log.error(f"Erro ao buscar último candle: {e}")
            return None

    def run_live(self, df_warmup: pd.DataFrame) -> None:
        """
        Loop principal do live trading.
        1. Setup OKX
        2. Warmup com histórico
        3. Loop: aguarda close → processa → repete
        """
        log.info("╔══════════════════════════════════════════════╗")
        log.info("║   AZLEMA LIVE TRADING — OKX ETH-USDT-SWAP   ║")
        log.info("║   Futures 1x | 95% do saldo | Sem leverage  ║")
        log.info("╚══════════════════════════════════════════════╝")

        if not self.setup():
            log.error("❌ Setup falhou. Verifique credenciais OKX.")
            return

        # Warmup histórico (processa df_warmup sem executar ordens)
        self.warmup(df_warmup)

        tf_min = int(TIMEFRAME.replace('m','').replace('h','')) * (60 if 'h' in TIMEFRAME else 1)

        self._running = True
        log.info(f"\n🚀 Live trading iniciado | {SYMBOL} {TIMEFRAME}")

        while self._running:
            try:
                # Aguarda próximo close de barra
                self._wait_for_candle_close(tf_min)

                # Busca candle fechado
                candle = self._fetch_latest_candle()
                if candle is None:
                    log.warning("⚠️  Candle não obtido, tentando na próxima barra")
                    continue

                # Processa
                self.process_candle(candle)

            except KeyboardInterrupt:
                log.info("\n🛑 Interrompido pelo usuário")
                self._running = False

            except Exception as e:
                log.error(f"❌ Erro no loop: {e}")
                log.error(traceback.format_exc())
                log.info("   Aguardando 60s antes de tentar novamente...")
                time.sleep(60)

        log.info("🔴 Live trading encerrado")

    def stop(self) -> None:
        self._running = False

    @property
    def trade_log(self) -> List[Dict]:
        return self._trade_log


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST
# ═══════════════════════════════════════════════════════════════════════════════
def run_full_backtest():
    log.info(f"\n{'═'*55}")
    log.info(f"  AZLEMA Backtest — OKX {SYMBOL} {TIMEFRAME}")
    log.info(f"  Warmup: {WARMUP_CANDLES} | Trading: {BACKTEST_CANDLES}")
    log.info(f"{'═'*55}\n")

    collector = DataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=TOTAL_CANDLES)
    df        = collector.fetch_ohlcv()

    if df.empty:
        raise ValueError("Nenhum candle obtido")

    df = df.reset_index(drop=True)
    df['index'] = df.index

    actual_warmup = min(WARMUP_CANDLES, len(df) - 1)
    cfg = {**STRATEGY_CONFIG, "warmup_bars": actual_warmup}

    strategy = AdaptiveZeroLagEMA(**cfg)
    engine   = BacktestEngine(strategy, df)
    results  = engine.run()

    log.info(f"📊 Trades: {results['total_trades']} | WR: {results['win_rate']:.1f}% | "
             f"PnL: {results['total_pnl_usdt']:.2f} USDT | "
             f"Balance: ${results['final_balance']:.2f}")

    df_report = df.iloc[actual_warmup:].reset_index(drop=True)
    return BacktestReporter(results, df_report)


# ═══════════════════════════════════════════════════════════════════════════════
# FLASK — Status e controle
# ═══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)

# Objeto global do trader (populado se MODE=live)
_trader: Optional[LiveTrader] = None


@app.route('/')
@app.route('/backtest')
def backtest_web():
    if MODE == 'live':
        return jsonify({
            "mode":    "live",
            "symbol":  SYMBOL,
            "status":  "running" if _trader and _trader._running else "stopped",
            "trades":  _trader.trade_log if _trader else [],
            "strategy": {
                "period":   _trader.strategy.Period if _trader else None,
                "position": _trader.strategy.position_size if _trader else None,
                "balance":  _trader.strategy.balance if _trader else None,
            }
        })
    try:
        reporter = run_full_backtest()
        return reporter.generate_html()
    except Exception as e:
        tb = traceback.format_exc()
        return jsonify({"error": str(e), "traceback": tb.split('\n')}), 500


@app.route('/status')
def status():
    """Status do live trader."""
    if _trader is None:
        return jsonify({"mode": MODE, "status": "not_started"})

    real_pos = None
    try:
        if MODE == 'live':
            real_pos = _trader.okx.get_position()
    except Exception:
        pass

    return jsonify({
        "mode":          MODE,
        "status":        "running" if _trader._running else "stopped",
        "symbol":        SYMBOL,
        "timeframe":     TIMEFRAME,
        "position_real": real_pos,
        "position_strat": _trader.strategy.position_size,
        "strategy_bal":  _trader.strategy.balance,
        "period":        _trader.strategy.period if hasattr(_trader.strategy, 'period') else _trader.strategy.Period,
        "trade_count":   len(_trader.trade_log),
        "last_trades":   _trader.trade_log[-5:],
    })


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/health')
def health():
    return jsonify({
        "status":    "healthy",
        "mode":      MODE,
        "symbol":    SYMBOL,
        "timeframe": TIMEFRAME,
    }), 200


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════════════════════════════════════════
def start_live_in_background():
    """Inicia o live trader em thread separada (Flask continua servindo /status)."""
    global _trader

    log.info("📥 Baixando dados históricos para warmup...")
    collector = DataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=TOTAL_CANDLES)
    df        = collector.fetch_ohlcv()

    if df.empty:
        log.error("❌ Sem dados históricos. Live trading não iniciado.")
        return

    df = df.reset_index(drop=True)
    df['index'] = df.index

    # Cria estratégia e trader
    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
    okx      = OKX(OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE)
    _trader  = LiveTrader(okx, strategy)

    # Inicia loop live em thread separada
    t = threading.Thread(target=_trader.run_live, args=(df,), daemon=True)
    t.start()
    log.info("✅ Live trader iniciado em background thread")


def run_local_backtest():
    reporter    = run_full_backtest()
    report_path = reporter.save_html('azlema_backtest_report.html')
    log.info(f"✅ Relatório: {report_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['backtest', 'server', 'live'],
                        default=MODE)
    args = parser.parse_args()
    mode = args.mode

    if mode == 'backtest':
        run_local_backtest()

    elif mode in ('server', 'live'):
        port = int(os.environ.get("PORT", 5000))  # PORT vem do Render automaticamente

        if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
            log.error("❌ OKX_API_KEY, OKX_SECRET_KEY e OKX_PASSPHRASE são obrigatórios!")
            exit(1)

        log.info("🔑 Credenciais OKX OK")
        start_live_in_background()

        log.info(f"🌐 Flask na porta {port} | /status para acompanhar")
        app.run(host='0.0.0.0', port=port, debug=False)
