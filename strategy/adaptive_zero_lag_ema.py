# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# ADAPTIVE ZERO LAG EMA v2 – FIXES APLICADOS:
# ✅ FIX 1: qty capturado antes de zerar position_size no exit
# ✅ FIX 2: Lógica pending simplificada (sinal → scheduled → entrada, 2 candles)
# ✅ FIX 3: lowest_price inicializado corretamente para LONG
# ✅ FIX 4: EMA_prev / EC_prev atualizados ANTES de calcular crossover
# ✅ FIX 5: Logs de diagnóstico melhorados
# ═══════════════════════════════════════════════════════════════════════════

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PI = 3.14159265359
RANGE = 50
GAIN_LIMIT = 900


@dataclass
class AdaptiveZeroLagEMA:
    # ---------- PARÂMETROS ----------
    adaptive_method: str = "Cos IFM"
    threshold: float = 0.0
    fixed_sl_points: int = 2000
    fixed_tp_points: int = 55
    trail_offset: int = 15
    risk_percent: float = 0.01
    tick_size: float = 0.01
    initial_capital: float = 1000.0
    max_lots: int = 100
    force_period: Optional[int] = None

    # ---------- ESTADO PERSISTENTE ----------
    inphase_buffer: deque = field(default_factory=lambda: deque(maxlen=4))
    quadrature_buffer: deque = field(default_factory=lambda: deque(maxlen=3))
    re: float = 0.0
    im: float = 0.0
    re_prev: float = 0.0
    im_prev: float = 0.0
    deltaIQ_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instIQ: float = 0.0
    lenIQ: float = 0.0

    v1_prev: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    deltaC_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instC: float = 0.0
    lenC: float = 0.0

    EMA: float = 0.0
    EMA_prev: float = 0.0
    EC: float = 0.0
    EC_prev: float = 0.0
    LeastError: float = 0.0
    BestGain: float = 0.0
    alpha: float = 0.0

    # ✅ FIX 2: Apenas um nível de delay (sinal → scheduled → entrada)
    entry_scheduled_long: bool = False
    entry_scheduled_short: bool = False

    Period: int = 20

    # Gerenciamento de posição
    position_size: float = 0.0
    entry_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = float('inf')  # ✅ FIX 3: inf para SHORT tracking
    trailing_activated: bool = False
    stop_price: float = 0.0

    balance: float = field(init=False)

    # Buffers auxiliares
    _src_buffer: deque = field(default_factory=lambda: deque(maxlen=8))
    _P_buffer: deque = field(default_factory=lambda: deque(maxlen=5))

    # Comentários das ordens
    enter_long_comment: str = "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
    exit_long_comment: str = "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
    enter_short_comment: str = "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
    exit_short_comment: str = "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"

    def __post_init__(self):
        self.balance = self.initial_capital
        for _ in range(4):
            self.inphase_buffer.append(0.0)
        for _ in range(3):
            self.quadrature_buffer.append(0.0)
        for _ in range(RANGE + 1):
            self.deltaIQ_buffer.append(0.0)
            self.deltaC_buffer.append(0.0)
        for _ in range(8):
            self._src_buffer.append(0.0)
        for _ in range(5):
            self._P_buffer.append(0.0)

    # ---------- Métodos IFM (inalterados) ----------
    def _update_iq_ifm(self, src: float):
        imult = 0.635
        qmult = 0.338
        inphase = 0.0
        quadrature = 0.0
        re = 0.0
        im = 0.0
        deltaIQ = 0.0
        instIQ = 0.0
        V = 0.0

        self._src_buffer.append(src)
        src_7 = self._src_buffer[0]
        P = src - src_7

        self._P_buffer.append(P)
        P_4 = self._P_buffer[0] if len(self._P_buffer) > 4 else 0.0
        P_2 = self._P_buffer[2] if len(self._P_buffer) > 2 else 0.0

        inphase_3 = self.inphase_buffer[0] if self.inphase_buffer else 0.0
        quadrature_2 = self.quadrature_buffer[0] if self.quadrature_buffer else 0.0
        inphase_1 = self.inphase_buffer[-2] if len(self.inphase_buffer) > 1 else 0.0
        quadrature_1 = self.quadrature_buffer[-2] if len(self.quadrature_buffer) > 1 else 0.0

        inphase = 1.25 * (P_4 - imult * P_2) + imult * inphase_3
        quadrature = P_2 - qmult * P + qmult * quadrature_2

        self.inphase_buffer.append(inphase)
        self.quadrature_buffer.append(quadrature)

        re = 0.2 * (inphase * inphase_1 + quadrature * quadrature_1) + 0.8 * self.re_prev
        im = 0.2 * (inphase * quadrature_1 - inphase_1 * quadrature) + 0.8 * self.im_prev

        self.re_prev = re
        self.im_prev = im

        if re != 0.0:
            deltaIQ = math.atan2(im, re)
        else:
            deltaIQ = 0.0 if im == 0.0 else math.copysign(PI / 2, im)

        self.deltaIQ_buffer.append(deltaIQ)

        delta_list = list(self.deltaIQ_buffer)
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(delta_list):
                V += delta_list[idx]
                if V > 2 * PI and instIQ == 0.0:
                    instIQ = float(i)

        if instIQ == 0.0:
            instIQ = self.instIQ
        self.instIQ = instIQ

        self.lenIQ = 0.25 * instIQ + 0.75 * self.lenIQ

    def _update_cosine_ifm(self, src: float):
        s2 = 0.0
        s3 = 0.0
        deltaC = 0.0
        instC = 0.0
        v1 = 0.0
        v2 = 0.0
        v4 = 0.0

        self._src_buffer.append(src)
        src_7 = self._src_buffer[0]
        v1 = src - src_7

        v1_1 = self.v1_prev
        self.v1_prev = v1

        s2 = 0.2 * (v1_1 + v1) * (v1_1 + v1) + 0.8 * self.s2
        s3 = 0.2 * (v1_1 - v1) * (v1_1 - v1) + 0.8 * self.s3

        self.s2 = s2
        self.s3 = s3

        if s2 != 0.0 and s3 / s2 >= 0.0:
            v2 = math.sqrt(s3 / s2)

        if s3 != 0.0:
            deltaC = 2 * math.atan(v2)

        self.deltaC_buffer.append(deltaC)

        delta_list = list(self.deltaC_buffer)
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(delta_list):
                v4 += delta_list[idx]
                if v4 > 2 * PI and instC == 0.0:
                    instC = float(i - 1)

        if instC == 0.0:
            instC = self.instC
        self.instC = instC

        self.lenC = 0.25 * instC + 0.75 * self.lenC

    def _update_zero_lag_ema(self, src: float, period: int):
        alpha = 2.0 / (period + 1)
        self.alpha = alpha

        # ✅ FIX 4: Salvar prev ANTES de calcular novo valor
        ema_prev = self.EMA
        ec_prev = self.EC

        self.EMA = alpha * src + (1 - alpha) * ema_prev
        self.EMA_prev = ema_prev  # valor anterior real

        least_error = 1e12
        best_gain = 0.0

        for i in range(-GAIN_LIMIT, GAIN_LIMIT + 1):
            gain = i / 10.0
            ec_candidate = alpha * (self.EMA + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_candidate)
            if error < least_error:
                least_error = error
                best_gain = gain

        self.EC = alpha * (self.EMA + best_gain * (src - ec_prev)) + (1 - alpha) * ec_prev
        self.EC_prev = ec_prev  # ✅ valor anterior real (não o novo)
        self.LeastError = least_error
        self.BestGain = best_gain

    def _update_position_trailing(self, candle: Dict) -> Optional[Dict]:
        if self.position_size == 0:
            self.trailing_activated = False
            return None

        high = candle['high']
        low = candle['low']
        close = candle['close']
        timestamp = candle.get('timestamp', None)

        if self.position_size > 0:
            # LONG
            self.highest_price = max(self.highest_price, high)
            if not self.trailing_activated:
                profit_points = (self.highest_price - self.entry_price) / self.tick_size
                if profit_points >= self.fixed_tp_points:
                    self.trailing_activated = True
            if self.trailing_activated:
                self.stop_price = self.highest_price - (self.trail_offset * self.tick_size)
            else:
                self.stop_price = self.entry_price - (self.fixed_sl_points * self.tick_size)

            if low <= self.stop_price:
                exit_price = min(close, self.stop_price)
                # ✅ FIX 1: capturar qty ANTES de zerar position_size
                qty = abs(self.position_size)
                pnl = (exit_price - self.entry_price) * qty
                self.balance += pnl
                self.position_size = 0
                self.entry_price = 0.0
                self.highest_price = 0.0
                self.lowest_price = float('inf')
                self.trailing_activated = False
                return {
                    "action": "EXIT_LONG",
                    "price": exit_price,
                    "qty": qty,  # ✅ qty correto
                    "pnl": pnl,
                    "balance": self.balance,
                    "timestamp": timestamp,
                    "comment": self.exit_long_comment
                }

        elif self.position_size < 0:
            # SHORT
            self.lowest_price = min(self.lowest_price, low)
            if not self.trailing_activated:
                profit_points = (self.entry_price - self.lowest_price) / self.tick_size
                if profit_points >= self.fixed_tp_points:
                    self.trailing_activated = True
            if self.trailing_activated:
                self.stop_price = self.lowest_price + (self.trail_offset * self.tick_size)
            else:
                self.stop_price = self.entry_price + (self.fixed_sl_points * self.tick_size)

            if high >= self.stop_price:
                exit_price = max(close, self.stop_price)
                # ✅ FIX 1: capturar qty ANTES de zerar position_size
                qty = abs(self.position_size)
                pnl = (self.entry_price - exit_price) * qty
                self.balance += pnl
                self.position_size = 0
                self.entry_price = 0.0
                self.lowest_price = float('inf')
                self.highest_price = 0.0
                self.trailing_activated = False
                return {
                    "action": "EXIT_SHORT",
                    "price": exit_price,
                    "qty": qty,  # ✅ qty correto
                    "pnl": pnl,
                    "balance": self.balance,
                    "timestamp": timestamp,
                    "comment": self.exit_short_comment
                }
        return None

    def _calculate_lots(self) -> float:
        risk_amount = self.risk_percent * self.balance
        stop_loss_usdt = self.fixed_sl_points * self.tick_size
        if stop_loss_usdt <= 0:
            return 0.0
        lots = risk_amount / stop_loss_usdt
        return min(lots, float(self.max_lots))

    # ---------- MÉTODO PRINCIPAL ----------
    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle e retorna lista de ações (entradas/saídas).
        Fluxo por candle:
          1. Executar entradas agendadas no open
          2. Calcular indicadores no close
          3. Verificar trailing stop/SL/TP
          4. Agendar entrada para próximo candle se houver sinal
        """
        open_price = candle['open']
        high = candle['high']
        low = candle['low']
        close = candle['close']
        bar_index = candle.get('index', 0)
        timestamp = candle.get('timestamp')

        actions = []

        # ====================================================================
        # 1. EXECUTAR ENTRADAS AGENDADAS (abertura do candle)
        # ====================================================================
        if self.entry_scheduled_long and self.position_size <= 0:
            # Fechar short se houver
            if self.position_size < 0:
                qty = abs(self.position_size)
                pnl = (self.entry_price - open_price) * qty
                self.balance += pnl
                actions.append({
                    "action": "EXIT_SHORT",
                    "price": open_price,
                    "qty": qty,
                    "pnl": pnl,
                    "balance": self.balance,
                    "timestamp": timestamp,
                    "comment": self.exit_short_comment
                })
                self.position_size = 0

            lots = self._calculate_lots()
            if lots > 0:
                self.position_size = lots
                self.entry_price = open_price
                self.highest_price = open_price
                self.lowest_price = float('inf')
                self.trailing_activated = False
                self.stop_price = self.entry_price - (self.fixed_sl_points * self.tick_size)
                actions.append({
                    "action": "BUY",
                    "qty": lots,
                    "price": open_price,
                    "comment": self.enter_long_comment,
                    "balance": self.balance,
                    "timestamp": timestamp
                })
                print(f"✅ ENTRADA LONG na barra {bar_index} @ {open_price:.2f} | lots={lots:.4f}")
            self.entry_scheduled_long = False

        if self.entry_scheduled_short and self.position_size >= 0:
            # Fechar long se houver
            if self.position_size > 0:
                qty = self.position_size
                pnl = (open_price - self.entry_price) * qty
                self.balance += pnl
                actions.append({
                    "action": "EXIT_LONG",
                    "price": open_price,
                    "qty": qty,
                    "pnl": pnl,
                    "balance": self.balance,
                    "timestamp": timestamp,
                    "comment": self.exit_long_comment
                })
                self.position_size = 0

            lots = self._calculate_lots()
            if lots > 0:
                self.position_size = -lots
                self.entry_price = open_price
                self.lowest_price = open_price
                self.highest_price = float('inf')
                self.trailing_activated = False
                self.stop_price = self.entry_price + (self.fixed_sl_points * self.tick_size)
                actions.append({
                    "action": "SELL",
                    "qty": lots,
                    "price": open_price,
                    "comment": self.enter_short_comment,
                    "balance": self.balance,
                    "timestamp": timestamp
                })
                print(f"✅ ENTRADA SHORT na barra {bar_index} @ {open_price:.2f} | lots={lots:.4f}")
            self.entry_scheduled_short = False

        # ====================================================================
        # 2. CALCULAR INDICADORES (usando fechamento)
        # ====================================================================
        src = close

        if self.force_period is None:
            if self.adaptive_method in ["I-Q IFM", "Average"]:
                self._update_iq_ifm(src)
            if self.adaptive_method in ["Cos IFM", "Average"]:
                self._update_cosine_ifm(src)

            if self.adaptive_method == "Cos IFM":
                self.Period = int(round(self.lenC))
            elif self.adaptive_method == "I-Q IFM":
                self.Period = int(round(self.lenIQ))
            elif self.adaptive_method == "Average":
                self.Period = int(round((self.lenC + self.lenIQ) / 2))
            else:
                self.Period = 20
        else:
            self.Period = self.force_period

        self.Period = max(1, self.Period)

        # ✅ FIX 4: _update_zero_lag_ema agora salva corretamente EMA_prev/EC_prev
        self._update_zero_lag_ema(src, self.Period)

        # Sinais de crossover usando valores prev salvos antes do update
        crossover = (self.EC_prev <= self.EMA_prev) and (self.EC > self.EMA)
        crossunder = (self.EC_prev >= self.EMA_prev) and (self.EC < self.EMA)
        error_percent = 100.0 * self.LeastError / src if src != 0 else 0.0
        buy_signal = crossover and (error_percent > self.threshold)
        sell_signal = crossunder and (error_percent > self.threshold)

        # ====================================================================
        # 3. TRAILING STOP / SL (saídas intra-candle)
        # ====================================================================
        exit_action = self._update_position_trailing(candle)
        if exit_action:
            actions.append(exit_action)

        # ====================================================================
        # 4. AGENDAR ENTRADAS PARA O PRÓXIMO CANDLE
        # ✅ FIX 2: Lógica simplificada – sinal direto vira scheduled
        # ====================================================================
        if buy_signal and self.position_size <= 0:
            self.entry_scheduled_long = True
            self.entry_scheduled_short = False
            print(f"🚀 Long agendado para barra {bar_index + 1} (sinal na barra {bar_index})")

        if sell_signal and self.position_size >= 0:
            self.entry_scheduled_short = True
            self.entry_scheduled_long = False
            print(f"🚀 Short agendado para barra {bar_index + 1} (sinal na barra {bar_index})")

        # ====================================================================
        # 5. LOGS DE DIAGNÓSTICO
        # ====================================================================
        if bar_index % 50 == 0:
            diff = self.EC - self.EMA
            print(
                f"📊 Barra {bar_index}: Period={self.Period}, "
                f"EC={self.EC:.2f}, EMA={self.EMA:.2f}, diff={diff:.4f}, "
                f"crossover={crossover}, crossunder={crossunder}, "
                f"error%={error_percent:.4f}, pos={self.position_size:.4f}, bal={self.balance:.2f}"
            )

        return actions
