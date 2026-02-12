# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# TRADUÇÃO CIRÚRGICA – ADAPTIVE ZERO LAG EMA v2 (PINE SCRIPT v3 → PYTHON)
# ═══════════════════════════════════════════════════════════════════════════
# Engenharia reversa completa. Cada linha reflete EXATAMENTE o comportamento
# do código Pine original, incluindo:
# - Reinicialização de variáveis temporárias a cada barra
# - Acesso histórico via buffers circulares
# - Tratamento de valores nulos com nz()
# - Detecção de ciclo por soma acumulada
# - Otimização de ganho com EC[1] congelado
# - Flags persistentes com delay de 1 barra
# - Trailing stop com ativação por lucro
# ═══════════════════════════════════════════════════════════════════════════

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

PI = 3.14159265359
RANGE = 50          # range fixo (hardcoded no Pine)
GAIN_LIMIT = 900    # -900 a +900, passo 0.1


@dataclass
class AdaptiveZeroLagEMA:
    """
    Gêmeo digital da estratégia AZLEMA.
    Uso IDÊNTICO ao Pine Script v3.
    """

    # ---------- PARÂMETROS (INPUTS DO PINE) ----------
    adaptive_method: str = "Cos IFM"   # "Off", "Cos IFM", "I-Q IFM", "Average"
    threshold: float = 0.0
    fixed_sl_points: int = 2000
    fixed_tp_points: int = 55
    trail_offset: int = 15
    risk_percent: float = 0.01
    tick_size: float = 0.01           # ETH/USDT = 0.01, BTC/USDT = 0.1
    initial_capital: float = 1000.0
    max_lots: int = 100

    # ---------- ESTADO PERSISTENTE (ENTRE BARRAS) ----------
    # I-Q IFM - buffers para acesso histórico
    inphase_buffer: deque = field(default_factory=lambda: deque(maxlen=4))
    quadrature_buffer: deque = field(default_factory=lambda: deque(maxlen=3))
    re: float = 0.0
    im: float = 0.0
    re_prev: float = 0.0      # re[1] da barra anterior
    im_prev: float = 0.0      # im[1] da barra anterior
    deltaIQ_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instIQ: float = 0.0
    lenIQ: float = 0.0

    # Cosine IFM
    v1_prev: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    deltaC_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instC: float = 0.0
    lenC: float = 0.0

    # Zero-Lag EMA
    EMA: float = 0.0
    EMA_prev: float = 0.0
    EC: float = 0.0
    EC_prev: float = 0.0
    LeastError: float = 0.0
    BestGain: float = 0.0
    alpha: float = 0.0

    # Sinais e flags (anti-repaint)
    buy_signal_prev: bool = False
    sell_signal_prev: bool = False
    pending_buy: bool = False
    pending_sell: bool = False

    # Período adaptativo
    Period: int = 20

    # Gerenciamento de posição (simulação manual)
    position_size: float = 0.0   # positivo = long, negativo = short
    entry_price: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_activated: bool = False
    stop_price: float = 0.0

    # Capital e risco
    balance: float = field(init=False)

    # Buffers auxiliares (src[7], P[4], P[2])
    _src_buffer: deque = field(default_factory=lambda: deque(maxlen=8))
    _P_buffer: deque = field(default_factory=lambda: deque(maxlen=5))

    # ---------- WEBHOOK COMMENTS (PRESERVADOS) ----------
    enter_long_comment: str = "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
    exit_long_comment: str = "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
    enter_short_comment: str = "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
    exit_short_comment: str = "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"

    def __post_init__(self):
        """Inicializa buffers e estado."""
        self.balance = self.initial_capital

        # Preenche buffers com zeros
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

        # Estado inicial das variáveis persistentes
        self.instIQ = 0.0
        self.instC = 0.0
        self.lenIQ = 0.0
        self.lenC = 0.0
        self.EMA = 0.0
        self.EC = 0.0
        self.EC_prev = 0.0
        self.EMA_prev = 0.0
        self.re = 0.0
        self.im = 0.0
        self.re_prev = 0.0
        self.im_prev = 0.0
        self.v1_prev = 0.0
        self.s2 = 0.0
        self.s3 = 0.0

    # ------------------------------------------------------------------------
    # NÚCLEO 1: I-Q IFM (In-Phase/Quadrature Instantaneous Frequency Measurement)
    # ------------------------------------------------------------------------
    def _update_iq_ifm(self, src: float):
        """
        Implementação IDÊNTICA ao bloco I-Q IFM do Pine.
        Variáveis temporárias são reinicializadas a cada chamada.
        """
        # --- Reinicialização (como no Pine) ---
        imult = 0.635
        qmult = 0.338
        inphase = 0.0
        quadrature = 0.0
        re = 0.0
        im = 0.0
        deltaIQ = 0.0
        instIQ = 0.0
        V = 0.0

        # P = src - src[7]
        self._src_buffer.append(src)
        src_7 = self._src_buffer[0]
        P = src - src_7

        # Atualiza buffer de P
        self._P_buffer.append(P)
        P_4 = self._P_buffer[0] if len(self._P_buffer) > 4 else 0.0  # P[4]
        P_2 = self._P_buffer[2] if len(self._P_buffer) > 2 else 0.0  # P[2]

        # Acessa valores históricos (com nz implícito = 0 se não existir)
        inphase_3 = self.inphase_buffer[0] if self.inphase_buffer else 0.0
        quadrature_2 = self.quadrature_buffer[0] if self.quadrature_buffer else 0.0
        inphase_1 = self.inphase_buffer[-2] if len(self.inphase_buffer) > 1 else 0.0
        quadrature_1 = self.quadrature_buffer[-2] if len(self.quadrature_buffer) > 1 else 0.0
        inphase_0 = self.inphase_buffer[-1] if self.inphase_buffer else 0.0
        quadrature_0 = self.quadrature_buffer[-1] if self.quadrature_buffer else 0.0

        # --- Cálculo do inphase ---
        # inphase := 1.25*(P[4] - imult*P[2]) + imult*nz(inphase[3])
        inphase = 1.25 * (P_4 - imult * P_2) + imult * inphase_3

        # --- Cálculo do quadrature ---
        # quadrature := P[2] - qmult*P + qmult*nz(quadrature[2])
        quadrature = P_2 - qmult * P + qmult * quadrature_2

        # Atualiza buffers de inphase e quadrature
        self.inphase_buffer.append(inphase)
        self.quadrature_buffer.append(quadrature)

        # --- Cálculo de re e im ---
        # re := 0.2*(inphase*inphase[1] + quadrature*quadrature[1]) + 0.8*nz(re[1])
        # im := 0.2*(inphase*quadrature[1] - inphase[1]*quadrature) + 0.8*nz(im[1])
        re = 0.2 * (inphase * inphase_1 + quadrature * quadrature_1) + 0.8 * self.re_prev
        im = 0.2 * (inphase * quadrature_1 - inphase_1 * quadrature) + 0.8 * self.im_prev

        self.re_prev = re
        self.im_prev = im

        # --- Cálculo de deltaIQ ---
        if re != 0.0:
            deltaIQ = math.atan2(im, re)
        else:
            deltaIQ = 0.0 if im == 0.0 else math.copysign(PI / 2, im)

        self.deltaIQ_buffer.append(deltaIQ)

        # --- Detecção do período (instIQ) ---
        # Equivalente a: for i=0 to range: V := V + deltaIQ[i]
        delta_list = list(self.deltaIQ_buffer)
        for i in range(RANGE + 1):
            idx = -(i + 1)  # i=0 -> -1 (atual), i=1 -> -2, etc.
            if abs(idx) <= len(delta_list):
                V += delta_list[idx]
                if V > 2 * PI and instIQ == 0.0:
                    instIQ = float(i)

        # if (instIQ == 0.0): instIQ := nz(instIQ[1])
        if instIQ == 0.0:
            instIQ = self.instIQ

        self.instIQ = instIQ

        # --- Suavização: lenIQ := 0.25*instIQ + 0.75*nz(lenIQ[1]) ---
        lenIQ = 0.25 * instIQ + 0.75 * self.lenIQ
        self.lenIQ = lenIQ

        return deltaIQ, lenIQ

    # ------------------------------------------------------------------------
    # NÚCLEO 2: COSINE IFM
    # ------------------------------------------------------------------------
    def _update_cosine_ifm(self, src: float):
        """
        Implementação IDÊNTICA ao bloco Cosine IFM do Pine.
        """
        # --- Reinicialização ---
        s2 = 0.0
        s3 = 0.0
        deltaC = 0.0
        instC = 0.0
        v1 = 0.0
        v2 = 0.0
        v4 = 0.0

        # v1 := src - src[7]
        self._src_buffer.append(src)
        src_7 = self._src_buffer[0]
        v1 = src - src_7

        # v1[1] é o v1 da barra anterior
        v1_1 = self.v1_prev
        self.v1_prev = v1

        # s2 := 0.2*(v1[1] + v1)*(v1[1] + v1) + 0.8*nz(s2[1])
        # s3 := 0.2*(v1[1] - v1)*(v1[1] - v1) + 0.8*nz(s3[1])
        s2 = 0.2 * (v1_1 + v1) * (v1_1 + v1) + 0.8 * self.s2
        s3 = 0.2 * (v1_1 - v1) * (v1_1 - v1) + 0.8 * self.s3

        self.s2 = s2
        self.s3 = s3

        # if (s2 != 0): v2 := sqrt(s3/s2)
        if s2 != 0.0 and s3 / s2 >= 0.0:
            v2 = math.sqrt(s3 / s2)

        # if (s3 != 0): deltaC := 2*atan(v2)
        if s3 != 0.0:
            deltaC = 2 * math.atan(v2)

        self.deltaC_buffer.append(deltaC)

        # --- Detecção do período (instC) ---
        delta_list = list(self.deltaC_buffer)
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(delta_list):
                v4 += delta_list[idx]
                if v4 > 2 * PI and instC == 0.0:
                    instC = float(i - 1)   # Pine usa i-1

        if instC == 0.0:
            instC = self.instC

        self.instC = instC

        # --- Suavização: lenC := 0.25*instC + 0.75*nz(lenC[1]) ---
        lenC = 0.25 * instC + 0.75 * self.lenC
        self.lenC = lenC

        return deltaC, lenC

    # ------------------------------------------------------------------------
    # NÚCLEO 3: ZERO LAG EMA COM OTIMIZAÇÃO DE GANHO
    # ------------------------------------------------------------------------
    def _update_zero_lag_ema(self, src: float, period: int):
        """
        Otimização de ganho com EC[1] congelado.
        """
        # alpha = 2/(Period + 1)
        alpha = 2.0 / (period + 1)
        self.alpha = alpha

        # EMA := alpha*src + (1-alpha)*nz(EMA[1])
        self.EMA = alpha * src + (1 - alpha) * self.EMA_prev
        self.EMA_prev = self.EMA

        # --- CONGELA EC[1] ANTES DO LOOP ---
        ec_prev = self.EC_prev

        least_error = 1e12
        best_gain = 0.0

        # for i = -GainLimit to GainLimit
        for i in range(-GAIN_LIMIT, GAIN_LIMIT + 1):
            gain = i / 10.0
            # EC := alpha*(EMA + Gain*(src - nz(EC[1]))) + (1 - alpha)*nz(EC[1])
            ec_candidate = alpha * (self.EMA + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_candidate)
            if error < least_error:
                least_error = error
                best_gain = gain

        # --- RECÁLCULO DO EC COM MELHOR GANHO ---
        self.EC = alpha * (self.EMA + best_gain * (src - ec_prev)) + (1 - alpha) * ec_prev
        self.EC_prev = self.EC

        self.LeastError = least_error
        self.BestGain = best_gain

    # ------------------------------------------------------------------------
    # NÚCLEO 4: GERENCIAMENTO DE POSIÇÃO E TRAILING STOP
    # ------------------------------------------------------------------------
    def _update_position_trailing(self, candle: Dict) -> Optional[Dict]:
        """Simulação manual do trailing stop com ativação por lucro."""
        if self.position_size == 0:
            self.trailing_activated = False
            return None

        high = candle['high']
        low = candle['low']
        close = candle['close']
        timestamp = candle.get('timestamp', None)

        # --- POSIÇÃO LONG ---
        if self.position_size > 0:
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
                pnl = (exit_price - self.entry_price) * self.position_size
                self.balance += pnl

                self.position_size = 0
                self.entry_price = 0.0
                self.highest_price = 0.0
                self.trailing_activated = False

                return {
                    "action": "EXIT_LONG",
                    "price": exit_price,
                    "qty": abs(self.position_size),
                    "pnl": pnl,
                    "balance": self.balance,
                    "timestamp": timestamp,
                    "comment": self.exit_long_comment
                }

        # --- POSIÇÃO SHORT ---
        elif self.position_size < 0:
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
                pnl = (self.entry_price - exit_price) * abs(self.position_size)
                self.balance += pnl

                self.position_size = 0
                self.entry_price = 0.0
                self.lowest_price = float('inf')
                self.trailing_activated = False

                return {
                    "action": "EXIT_SHORT",
                    "price": exit_price,
                    "qty": abs(self.position_size),
                    "pnl": pnl,
                    "balance": self.balance,
                    "timestamp": timestamp,
                    "comment": self.exit_short_comment
                }

        return None

    # ------------------------------------------------------------------------
    # NÚCLEO 5: CÁLCULO DE LOTE (POSITION SIZING)
    # ------------------------------------------------------------------------
    def _calculate_lots(self) -> float:
        """Fórmula: (risk * balance) / (fixedSL * tick_size)"""
        risk_amount = self.risk_percent * self.balance
        stop_loss_usdt = self.fixed_sl_points * self.tick_size
        if stop_loss_usdt <= 0:
            return 0.0
        lots = risk_amount / stop_loss_usdt
        return min(lots, float(self.max_lots))

    # ------------------------------------------------------------------------
    # MÉTODO PRINCIPAL: next(candle) – EXECUÇÃO POR BARRA
    # ------------------------------------------------------------------------
    def next(self, candle: Dict) -> Dict:
        """
        Processa um candle e retorna a ação.
        Ordem de execução IDÊNTICA ao Pine Script.
        """
        src = candle['close']

        # ====================================================================
        # 1. CÁLCULO DOS INDICADORES ADAPTATIVOS
        # ====================================================================
        deltaIQ, lenIQ = 0.0, self.lenIQ
        if self.adaptive_method in ["I-Q IFM", "Average"]:
            deltaIQ, lenIQ = self._update_iq_ifm(src)

        deltaC, lenC = 0.0, self.lenC
        if self.adaptive_method in ["Cos IFM", "Average"]:
            deltaC, lenC = self._update_cosine_ifm(src)

        # ====================================================================
        # 2. DEFINIÇÃO DO PERÍODO ADAPTATIVO
        # ====================================================================
        if self.adaptive_method == "Cos IFM":
            self.Period = int(round(lenC))
        elif self.adaptive_method == "I-Q IFM":
            self.Period = int(round(lenIQ))
        elif self.adaptive_method == "Average":
            self.Period = int(round((lenC + lenIQ) / 2))
        else:  # "Off"
            self.Period = 20

        self.Period = max(1, self.Period)

        # ====================================================================
        # 3. ZERO LAG EMA E OTIMIZAÇÃO DE GANHO
        # ====================================================================
        self._update_zero_lag_ema(src, self.Period)

        # ====================================================================
        # 4. GERAÇÃO DE SINAIS BRUTOS (BARRA ATUAL)
        # ====================================================================
        crossover = (self.EC_prev <= self.EMA_prev) and (self.EC > self.EMA)
        crossunder = (self.EC_prev >= self.EMA_prev) and (self.EC < self.EMA)

        error_percent = 100.0 * self.LeastError / src if src != 0 else 0.0

        buy_signal = crossover and (error_percent > self.threshold)
        sell_signal = crossunder and (error_percent > self.threshold)

        # ====================================================================
        # 5. ATIVAÇÃO DE FLAGS COM SINAIS PASSADOS (DELAY DE 1 BARRA)
        # ====================================================================
        if self.buy_signal_prev:
            self.pending_buy = True
        if self.sell_signal_prev:
            self.pending_sell = True

        # Armazena sinais atuais para a PRÓXIMA barra
        self.buy_signal_prev = buy_signal
        self.sell_signal_prev = sell_signal

        # ====================================================================
        # 6. VERIFICAÇÃO DE SAÍDAS (TRAILING STOP)
        # ====================================================================
        exit_action = self._update_position_trailing(candle)
        if exit_action:
            return exit_action

        # ====================================================================
        # 7. EXECUÇÃO DE ENTRADAS
        # ====================================================================
        action = "NONE"
        qty = 0.0
        price = 0.0
        comment = ""

        if self.balance > 0:
            lots = self._calculate_lots()

            # --- ENTRADA LONG ---
            if self.pending_buy and self.position_size <= 0:
                # Fecha posição oposta (se houver) – pyramiding=1
                if self.position_size < 0:
                    self.position_size = 0

                self.position_size = lots
                self.entry_price = src
                self.highest_price = src
                self.lowest_price = 0.0
                self.trailing_activated = False
                self.stop_price = self.entry_price - (self.fixed_sl_points * self.tick_size)

                action = "BUY"
                qty = lots
                price = src
                comment = self.enter_long_comment

                # RESET IMEDIATO DA FLAG (atomicidade)
                self.pending_buy = False

            # --- ENTRADA SHORT ---
            elif self.pending_sell and self.position_size >= 0:
                if self.position_size > 0:
                    self.position_size = 0

                self.position_size = -lots
                self.entry_price = src
                self.lowest_price = src
                self.highest_price = float('inf')
                self.trailing_activated = False
                self.stop_price = self.entry_price + (self.fixed_sl_points * self.tick_size)

                action = "SELL"
                qty = lots
                price = src
                comment = self.enter_short_comment

                self.pending_sell = False

        # ====================================================================
        # 8. ATUALIZAÇÃO DOS VALORES "PREV" PARA PRÓXIMA BARRA
        # ====================================================================
        self.EMA_prev = self.EMA
        self.EC_prev = self.EC

        return {
            "action": action,
            "qty": qty,
            "price": price,
            "comment": comment,
            "balance": self.balance,
            "timestamp": candle.get('timestamp'),
            "indicators": {
                "Period": self.Period,
                "EC": self.EC,
                "EMA": self.EMA,
                "LeastError": self.LeastError,
                "BestGain": self.BestGain
            }
        }
