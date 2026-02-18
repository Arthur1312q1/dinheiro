# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# TRADUÇÃO FIEL DO PINE SCRIPT v3 → PYTHON
#
# CORREÇÃO CRÍTICA (esta versão):
#
#   BUG: Quando exit_scheduled=True (SL ou trailing ativado) E ao mesmo tempo
#   há um entry contrário agendado (reversão), o Python fechava a posição
#   pelo stop_price usando min(stop, open).
#
#   COMPORTAMENTO DO PINE: "If both an entry and an exit are triggered on
#   the same bar, the entry order will always take precedence."
#   → O entry contrário CANCELA o exit existente.
#   → A posição fecha pelo OPEN da barra seguinte (não pelo stop_price).
#
#   IMPACTO:
#   1. Com SL tocado + reversão e open > SL mas < entrada:
#      Python: PnL = -(entry - SL) = -$20 (perda máxima)
#      Pine:   PnL = -(entry - open) = -$10 (perda menor, WIN?!)
#
#   2. Com trailing + reversão e open > trailing_stop:
#      Python: PnL = trailing_stop - entry (limitado)
#      Pine:   PnL = open - entry (captura o gap a favor!)
#
#   Resultado: Python tem muito mais perdas e wins menores → 48% win rate
#   Pine (TradingView) tem perdas menores e wins maiores → 80% win rate
#
# SOLUÇÃO: Se entry_contrário agendado, cancela exit_scheduled e fecha ao open.
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
    warmup_bars: int = 300

    # ---------- ESTADO IFM I-Q ----------
    inphase_buffer: deque = field(default_factory=lambda: deque(maxlen=4))
    quadrature_buffer: deque = field(default_factory=lambda: deque(maxlen=3))
    re_prev: float = 0.0
    im_prev: float = 0.0
    deltaIQ_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instIQ: float = 0.0
    lenIQ: float = 0.0

    # ---------- ESTADO IFM COSINE ----------
    v1_prev: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    deltaC_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instC: float = 0.0
    lenC: float = 0.0

    # ---------- ESTADO ZERO-LAG EMA ----------
    EMA: float = 0.0
    EC: float = 0.0
    LeastError: float = 0.0
    BestGain: float = 0.0
    Period: int = 20

    # ---------- FLAGS ----------
    pending_buy: bool = False
    pending_sell: bool = False
    buy_signal_prev: bool = False
    sell_signal_prev: bool = False
    entry_scheduled_long: bool = False
    entry_scheduled_short: bool = False

    # ---------- EXIT AGENDADO (executa no próximo open) ----------
    exit_scheduled: bool = False
    exit_scheduled_side: str = ""
    exit_scheduled_reason: str = ""

    # ---------- POSIÇÃO ----------
    position_size: float = 0.0
    position_avg_price: float = 0.0
    net_profit: float = 0.0

    # ---------- TRAILING ----------
    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_active: bool = False
    exit_active: bool = False
    _stop_price: float = 0.0

    _bar_count: int = 0

    # ---------- BUFFERS ----------
    _src_buf_iq: deque = field(default_factory=lambda: deque(maxlen=8))
    _P_buf: deque = field(default_factory=lambda: deque(maxlen=5))
    _src_buf_cos: deque = field(default_factory=lambda: deque(maxlen=8))

    balance: float = field(init=False)

    def __post_init__(self):
        self.balance = self.initial_capital
        for _ in range(4):   self.inphase_buffer.append(0.0)
        for _ in range(3):   self.quadrature_buffer.append(0.0)
        for _ in range(RANGE + 1):
            self.deltaIQ_buffer.append(0.0)
            self.deltaC_buffer.append(0.0)
        for _ in range(8):
            self._src_buf_iq.append(0.0)
            self._src_buf_cos.append(0.0)
        for _ in range(5):   self._P_buf.append(0.0)

    # ═══════════════════════════════════════════════════════════════════════
    # IFM — métodos inalterados
    # ═══════════════════════════════════════════════════════════════════════
    def _calc_iq_ifm(self, src: float):
        imult, qmult = 0.635, 0.338
        self._src_buf_iq.append(src)
        P = src - self._src_buf_iq[0]
        self._P_buf.append(P)
        P_list = list(self._P_buf)
        P_4 = P_list[0]
        P_2 = P_list[2] if len(P_list) >= 3 else 0.0
        ib = list(self.inphase_buffer)
        qb = list(self.quadrature_buffer)
        inphase_3    = ib[0]  if len(ib) >= 4 else 0.0
        inphase_1    = ib[-2] if len(ib) >= 2 else 0.0
        quadrature_2 = qb[0]  if len(qb) >= 3 else 0.0
        quadrature_1 = qb[-2] if len(qb) >= 2 else 0.0
        inphase    = 1.25 * (P_4 - imult * P_2) + imult * inphase_3
        quadrature = P_2 - qmult * P + qmult * quadrature_2
        self.inphase_buffer.append(inphase)
        self.quadrature_buffer.append(quadrature)
        re = 0.2*(inphase*inphase_1 + quadrature*quadrature_1) + 0.8*self.re_prev
        im = 0.2*(inphase*quadrature_1 - inphase_1*quadrature) + 0.8*self.im_prev
        self.re_prev = re; self.im_prev = im
        deltaIQ = math.atan(im / re) if re != 0.0 else 0.0
        self.deltaIQ_buffer.append(deltaIQ)
        d_list = list(self.deltaIQ_buffer)
        V = 0.0; instIQ = 0.0
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(d_list):
                V += d_list[idx]
                if V > 2 * PI and instIQ == 0.0: instIQ = float(i)
        if instIQ == 0.0: instIQ = self.instIQ
        self.instIQ = instIQ
        self.lenIQ = 0.25 * instIQ + 0.75 * self.lenIQ

    def _calc_cosine_ifm(self, src: float):
        self._src_buf_cos.append(src)
        v1 = src - self._src_buf_cos[0]
        v1_1 = self.v1_prev; self.v1_prev = v1
        self.s2 = 0.2 * (v1_1 + v1)**2 + 0.8 * self.s2
        self.s3 = 0.2 * (v1_1 - v1)**2 + 0.8 * self.s3
        v2 = 0.0
        if self.s2 != 0.0:
            ratio = self.s3 / self.s2
            if ratio >= 0.0: v2 = math.sqrt(ratio)
        deltaC = 2 * math.atan(v2) if self.s3 != 0.0 else 0.0
        self.deltaC_buffer.append(deltaC)
        d_list = list(self.deltaC_buffer)
        v4 = 0.0; instC = 0.0
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(d_list):
                v4 += d_list[idx]
                if v4 > 2 * PI and instC == 0.0: instC = float(i - 1)
        if instC == 0.0: instC = self.instC
        self.instC = instC
        self.lenC = 0.25 * instC + 0.75 * self.lenC

    def _calc_zero_lag_ema(self, src: float, period: int):
        alpha = 2.0 / (period + 1)
        ema_prev = self.EMA; ec_prev = self.EC
        ema_new = alpha * src + (1 - alpha) * ema_prev
        least_error = 1_000_000.0; best_gain = 0.0
        for i in range(-GAIN_LIMIT, GAIN_LIMIT + 1):
            gain = i / 10.0
            ec_cand = alpha * (ema_new + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_cand)
            if error < least_error: least_error = error; best_gain = gain
        ec_new = alpha * (ema_new + best_gain * (src - ec_prev)) + (1 - alpha) * ec_prev
        self.EMA = ema_new; self.EC = ec_new
        self.LeastError = least_error; self.BestGain = best_gain
        return ema_prev, ec_prev, ema_new, ec_new

    # ═══════════════════════════════════════════════════════════════════════
    # VERIFICA STOP — agenda exit para próximo open
    # ═══════════════════════════════════════════════════════════════════════
    def _check_stop_touched(self, candle: Dict):
        if self.position_size == 0 or not self.exit_active or self.exit_scheduled:
            return False
        high = candle['high']; low = candle['low']
        if self.position_size > 0:
            self.highest_price = max(self.highest_price, high)
            profit_ticks = (self.highest_price - self.position_avg_price) / self.tick_size
            if profit_ticks >= self.fixed_tp_points:
                self.trailing_active = True
            if self.trailing_active:
                stop = self.highest_price - self.trail_offset * self.tick_size
                reason = "TRAIL"
            else:
                stop = self.position_avg_price - self.fixed_sl_points * self.tick_size
                reason = "SL"
            self._stop_price = stop
            if low <= stop:
                self.exit_scheduled = True
                self.exit_scheduled_side = "long"
                self.exit_scheduled_reason = reason
                return True
        elif self.position_size < 0:
            self.lowest_price = min(self.lowest_price, low)
            profit_ticks = (self.position_avg_price - self.lowest_price) / self.tick_size
            if profit_ticks >= self.fixed_tp_points:
                self.trailing_active = True
            if self.trailing_active:
                stop = self.lowest_price + self.trail_offset * self.tick_size
                reason = "TRAIL"
            else:
                stop = self.position_avg_price + self.fixed_sl_points * self.tick_size
                reason = "SL"
            self._stop_price = stop
            if high >= stop:
                self.exit_scheduled = True
                self.exit_scheduled_side = "short"
                self.exit_scheduled_reason = reason
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════════
    # EXECUTA EXIT por stop/trailing no open da próxima barra
    # Só é chamado quando NÃO há entry contrário agendado.
    # ═══════════════════════════════════════════════════════════════════════
    def _execute_scheduled_exit(self, open_price: float, ts) -> Optional[Dict]:
        if not self.exit_scheduled:
            return None
        reason = self.exit_scheduled_reason
        if self.exit_scheduled_side == "long":
            exit_price = min(self._stop_price, open_price)
            qty = self.position_size
            pnl = (exit_price - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_long()
            return {
                "action": "EXIT_LONG", "price": exit_price, "qty": qty,
                "pnl": pnl, "balance": self.balance, "timestamp": ts,
                "exit_reason": reason,
                "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }
        elif self.exit_scheduled_side == "short":
            exit_price = max(self._stop_price, open_price)
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - exit_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_short()
            return {
                "action": "EXIT_SHORT", "price": exit_price, "qty": qty,
                "pnl": pnl, "balance": self.balance, "timestamp": ts,
                "exit_reason": reason,
                "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # FECHA POSIÇÃO ao open por REVERSÃO
    # ✅ CORREÇÃO: entry contrário cancela exit agendado → usa open, não stop_price
    # ═══════════════════════════════════════════════════════════════════════
    def _close_for_reversal(self, open_price: float, ts) -> Optional[Dict]:
        """
        Fecha posição ao open_price.
        CANCELA qualquer exit_scheduled (entry tem prioridade sobre exit no Pine).
        Resultado: preço de saída = open_price (não stop_price).
        Isso replica o comportamento: strategy.entry() cancela strategy.exit().
        """
        if self.position_size == 0:
            return None
        # Cancela exit pendente — entry tem prioridade
        self.exit_scheduled = False
        self.exit_scheduled_side = ""
        self.exit_scheduled_reason = ""

        if self.position_size > 0:
            qty = self.position_size
            pnl = (open_price - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_long()
            return {
                "action": "EXIT_LONG", "price": open_price, "qty": qty,
                "pnl": pnl, "balance": self.balance, "timestamp": ts,
                "exit_reason": "REVERSAL",
                "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }
        else:
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - open_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_short()
            return {
                "action": "EXIT_SHORT", "price": open_price, "qty": qty,
                "pnl": pnl, "balance": self.balance, "timestamp": ts,
                "exit_reason": "REVERSAL",
                "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }

    def _reset_long(self):
        self.position_size = 0.0; self.position_avg_price = 0.0
        self.highest_price = 0.0; self.trailing_active = False
        self.exit_active = False; self.exit_scheduled = False; self.exit_scheduled_side = ""

    def _reset_short(self):
        self.position_size = 0.0; self.position_avg_price = 0.0
        self.lowest_price = float('inf'); self.trailing_active = False
        self.exit_active = False; self.exit_scheduled = False; self.exit_scheduled_side = ""

    def _calc_lots(self) -> float:
        balance = self.initial_capital + self.net_profit
        stop_loss_usdt = self.fixed_sl_points * self.tick_size
        if stop_loss_usdt <= 0: return 0.0
        return min((self.risk_percent * balance) / stop_loss_usdt, float(self.max_lots))

    # ═══════════════════════════════════════════════════════════════════════
    # MÉTODO PRINCIPAL
    # ═══════════════════════════════════════════════════════════════════════
    def next(self, candle: Dict) -> List[Dict]:
        open_p = candle['open']
        idx    = candle.get('index', 0)
        ts     = candle.get('timestamp')
        actions = []

        self._bar_count += 1
        in_warmup = self._bar_count <= self.warmup_bars

        # Atualiza pending com sinal da barra anterior
        if self.buy_signal_prev:  self.pending_buy = True
        if self.sell_signal_prev: self.pending_sell = True

        if not in_warmup:
            balance = self.initial_capital + self.net_profit

            # ══════════════════════════════════════════════════════════════
            # OPEN: PRIORIDADE DAS ORDENS (replica Pine Script)
            #
            # Regra Pine: se entry contrário E exit agendados para o mesmo open,
            # o ENTRY tem prioridade. O exit é CANCELADO.
            # A posição fecha ao open_price (não ao stop_price).
            #
            # Isso impacta diretamente o PnL:
            # - Se open > stop_price: ganha mais (entry usa open melhor)
            # - Se open < stop_price (gap): ambos usariam o mesmo open
            # ══════════════════════════════════════════════════════════════

            # CASO 1: REVERSÃO — entry contrário cancela exit
            if self.entry_scheduled_short and self.position_size > 0 and balance > 0:
                # Long → Short: entry("SELL") cancela exit("B.Exit")
                rev = self._close_for_reversal(open_p, ts)
                if rev: actions.append(rev)

            if self.entry_scheduled_long and self.position_size < 0 and balance > 0:
                # Short → Long: entry("BUY") cancela exit("S.Exit")
                rev = self._close_for_reversal(open_p, ts)
                if rev: actions.append(rev)

            # CASO 2: EXIT puro (stop/trailing sem entry contrário)
            # Só executa se a posição ainda está aberta (não foi revertida acima)
            if self.exit_scheduled and self.position_size != 0:
                exit_act = self._execute_scheduled_exit(open_p, ts)
                if exit_act: actions.append(exit_act)

            # CASO 3: ENTRADAS
            if self.entry_scheduled_long and balance > 0 and self.position_size <= 0:
                lots = self._calc_lots()
                if lots > 0:
                    self.position_size = lots
                    self.position_avg_price = open_p
                    self.highest_price = open_p
                    self.lowest_price = 0.0
                    self.trailing_active = False
                    self.exit_active = True
                    self.exit_scheduled = False
                    self._stop_price = open_p - self.fixed_sl_points * self.tick_size
                    self.balance = self.initial_capital + self.net_profit
                    actions.append({
                        "action": "BUY", "qty": lots, "price": open_p,
                        "comment": "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                        "balance": self.balance, "timestamp": ts
                    })
                    print(f"✅ LONG  [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
                self.entry_scheduled_long = False

            if self.entry_scheduled_short and balance > 0 and self.position_size >= 0:
                lots = self._calc_lots()
                if lots > 0:
                    self.position_size = -lots
                    self.position_avg_price = open_p
                    self.lowest_price = open_p
                    self.highest_price = float('inf')
                    self.trailing_active = False
                    self.exit_active = True
                    self.exit_scheduled = False
                    self._stop_price = open_p + self.fixed_sl_points * self.tick_size
                    self.balance = self.initial_capital + self.net_profit
                    actions.append({
                        "action": "SELL", "qty": lots, "price": open_p,
                        "comment": "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                        "balance": self.balance, "timestamp": ts
                    })
                    print(f"✅ SHORT [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
                self.entry_scheduled_short = False

        else:
            # Warmup: descarta agendamentos
            self.entry_scheduled_long = False
            self.entry_scheduled_short = False
            self.exit_scheduled = False

        # ══════════════════════════════════════════════════════════════════
        # CLOSE: calcula indicadores
        # ══════════════════════════════════════════════════════════════════
        src = candle['close']

        if self.force_period is None:
            if self.adaptive_method in ("I-Q IFM", "Average"): self._calc_iq_ifm(src)
            if self.adaptive_method in ("Cos IFM", "Average"):  self._calc_cosine_ifm(src)
            if   self.adaptive_method == "Cos IFM":  self.Period = max(1, int(round(self.lenC)))
            elif self.adaptive_method == "I-Q IFM":  self.Period = max(1, int(round(self.lenIQ)))
            elif self.adaptive_method == "Average":  self.Period = max(1, int(round((self.lenC + self.lenIQ) / 2)))
            else:                                     self.Period = 20
        else:
            self.Period = max(1, self.force_period)

        ema_prev, ec_prev, ema_new, ec_new = self._calc_zero_lag_ema(src, self.Period)

        crossover  = (ec_prev <= ema_prev) and (ec_new > ema_new)
        crossunder = (ec_prev >= ema_prev) and (ec_new < ema_new)
        error_pct  = 100.0 * self.LeastError / src if src != 0 else 0.0
        buy_signal  = crossover  and (error_pct > self.threshold)
        sell_signal = crossunder and (error_pct > self.threshold)

        # ══════════════════════════════════════════════════════════════════
        # CLOSE: verifica stop → agenda exit para próximo open
        # (posição ainda aberta nesta barra, mesmo que exit_scheduled)
        # ══════════════════════════════════════════════════════════════════
        self._check_stop_touched(candle)

        # ══════════════════════════════════════════════════════════════════
        # CLOSE: agenda entries se pending
        # NOTA: position_size ainda > 0 mesmo que exit_scheduled=True
        # (o exit só executa no próximo open)
        # ══════════════════════════════════════════════════════════════════
        if self.pending_buy and self.position_size <= 0:
            self.entry_scheduled_long = True
            self.entry_scheduled_short = False
            self.pending_buy = False
            if not in_warmup: print(f"🚀 Long agendado → [{idx+1}]")

        if self.pending_sell and self.position_size >= 0:
            self.entry_scheduled_short = True
            self.entry_scheduled_long = False
            self.pending_sell = False
            if not in_warmup: print(f"🚀 Short agendado → [{idx+1}]")

        self.buy_signal_prev  = buy_signal
        self.sell_signal_prev = sell_signal

        if idx % 100 == 0:
            wstr = " [WU]" if in_warmup else ""
            print(f"📊 [{idx}]{wstr} P={self.Period} EC={ec_new:.2f} EMA={ema_new:.2f} "
                  f"diff={ec_new-ema_new:.4f} pos={self.position_size:.4f} "
                  f"bal={self.balance:.2f} trail={'ON' if self.trailing_active else 'off'}")

        return actions
