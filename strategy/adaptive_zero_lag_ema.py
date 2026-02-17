# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# TRADUÇÃO FIEL DO PINE SCRIPT v3 → PYTHON
#
# PROBLEMA RAIZ CORRIGIDO:
#   O TradingView processa TODOS os candles históricos para aquecer os
#   indicadores (EMA, EC, lenC etc). Se iniciamos com EMA=EC=0, ocorre
#   um crossover FALSO na primeira barra porque EC converge para o preço
#   rapidamente (BestGain corrige) mas EMA começa em alpha*price (~3% do price).
#
#   SOLUÇÃO: a estratégia recebe warmup_bars como parâmetro.
#   Ela processa os indicadores em TODOS os candles, mas só abre posições
#   após warmup_bars barras (equivalente ao histórico do TradingView).
#
# FLUXO PINE (calc_on_every_tick=false, process_orders_on_close=false default):
#   Barra N:   buy_signal calculado no CLOSE
#   Barra N+1: buy_signal[1]=True → pendingBuy=True
#              if pendingBuy and pos<=0: strategy.entry chamado
#              → entrada executa no OPEN da barra N+2
#   Barra N+2: posição aberta, strategy.exit monitora
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
    fixed_sl_points: int = 2000         # fixedSL (em ticks)
    fixed_tp_points: int = 55           # trail_points (em ticks)
    trail_offset: int = 15              # trail_offset (em ticks)
    risk_percent: float = 0.01
    tick_size: float = 0.01             # syminfo.mintick
    initial_capital: float = 1000.0
    max_lots: int = 100
    force_period: Optional[int] = None
    warmup_bars: int = 100              # barras para aquecer indicadores sem operar

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

    # ---------- PERÍODO ----------
    Period: int = 20

    # ---------- FLAGS PINE-STYLE ----------
    pending_buy: bool = False
    pending_sell: bool = False
    buy_signal_prev: bool = False
    sell_signal_prev: bool = False
    entry_scheduled_long: bool = False
    entry_scheduled_short: bool = False

    # ---------- POSIÇÃO ----------
    position_size: float = 0.0
    position_avg_price: float = 0.0
    net_profit: float = 0.0

    # ---------- TRAILING STOP ----------
    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_active: bool = False
    exit_active: bool = False

    # ---------- CONTADOR DE BARRAS ----------
    _bar_count: int = 0

    # ---------- BUFFERS AUXILIARES ----------
    # IMPORTANTE: src_buf é SEPARADO para IFM e não compartilhado
    # No Pine, src[7] é o mesmo para ambos os IFMs pois é calculado uma vez
    # Como usamos "Cos IFM" por padrão (apenas _calc_cosine_ifm), isso é OK
    _src_buf_iq: deque = field(default_factory=lambda: deque(maxlen=8))
    _P_buf: deque = field(default_factory=lambda: deque(maxlen=5))
    _src_buf_cos: deque = field(default_factory=lambda: deque(maxlen=8))

    balance: float = field(init=False)

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
            self._src_buf_iq.append(0.0)
            self._src_buf_cos.append(0.0)
        for _ in range(5):
            self._P_buf.append(0.0)

    # ═══════════════════════════════════════════════════════════════════════
    # I-Q IFM — fiel ao Pine (usa atan, não atan2)
    # ═══════════════════════════════════════════════════════════════════════
    def _calc_iq_ifm(self, src: float):
        imult = 0.635
        qmult = 0.338

        self._src_buf_iq.append(src)
        P = src - self._src_buf_iq[0]  # src - src[7]
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

        re = 0.2 * (inphase * inphase_1 + quadrature * quadrature_1) + 0.8 * self.re_prev
        im = 0.2 * (inphase * quadrature_1 - inphase_1 * quadrature) + 0.8 * self.im_prev
        self.re_prev = re
        self.im_prev = im

        deltaIQ = math.atan(im / re) if re != 0.0 else 0.0  # Pine: atan (não atan2)
        self.deltaIQ_buffer.append(deltaIQ)

        d_list = list(self.deltaIQ_buffer)
        V = 0.0
        instIQ = 0.0
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(d_list):
                V += d_list[idx]
                if V > 2 * PI and instIQ == 0.0:
                    instIQ = float(i)

        if instIQ == 0.0:
            instIQ = self.instIQ
        self.instIQ = instIQ
        self.lenIQ = 0.25 * instIQ + 0.75 * self.lenIQ

    # ═══════════════════════════════════════════════════════════════════════
    # COSINE IFM — fiel ao Pine
    # ═══════════════════════════════════════════════════════════════════════
    def _calc_cosine_ifm(self, src: float):
        self._src_buf_cos.append(src)
        v1 = src - self._src_buf_cos[0]  # src - src[7]
        v1_1 = self.v1_prev
        self.v1_prev = v1

        self.s2 = 0.2 * (v1_1 + v1) ** 2 + 0.8 * self.s2
        self.s3 = 0.2 * (v1_1 - v1) ** 2 + 0.8 * self.s3

        v2 = 0.0
        if self.s2 != 0.0:
            ratio = self.s3 / self.s2
            if ratio >= 0.0:
                v2 = math.sqrt(ratio)

        deltaC = 2 * math.atan(v2) if self.s3 != 0.0 else 0.0
        self.deltaC_buffer.append(deltaC)

        d_list = list(self.deltaC_buffer)
        v4 = 0.0
        instC = 0.0
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(d_list):
                v4 += d_list[idx]
                if v4 > 2 * PI and instC == 0.0:
                    instC = float(i - 1)  # Pine: i - 1

        if instC == 0.0:
            instC = self.instC  # instC[1]
        self.instC = instC
        self.lenC = 0.25 * instC + 0.75 * self.lenC

    # ═══════════════════════════════════════════════════════════════════════
    # ZERO-LAG EMA — retorna (ema_prev, ec_prev, ema_new, ec_new)
    # ═══════════════════════════════════════════════════════════════════════
    def _calc_zero_lag_ema(self, src: float, period: int):
        alpha = 2.0 / (period + 1)
        ema_prev = self.EMA   # nz(EMA[1])
        ec_prev  = self.EC    # nz(EC[1])

        ema_new = alpha * src + (1 - alpha) * ema_prev

        least_error = 1_000_000.0
        best_gain = 0.0
        for i in range(-GAIN_LIMIT, GAIN_LIMIT + 1):
            gain = i / 10.0
            ec_cand = alpha * (ema_new + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_cand)
            if error < least_error:
                least_error = error
                best_gain = gain

        ec_new = alpha * (ema_new + best_gain * (src - ec_prev)) + (1 - alpha) * ec_prev

        self.EMA = ema_new
        self.EC  = ec_new
        self.LeastError = least_error
        self.BestGain   = best_gain

        return ema_prev, ec_prev, ema_new, ec_new

    # ═══════════════════════════════════════════════════════════════════════
    # TRAILING STOP — replica strategy.exit do Pine
    # loss=fixedSL ticks, trail_points=fixedTP ticks, trail_offset=15 ticks
    # ═══════════════════════════════════════════════════════════════════════
    def _check_exit(self, candle: Dict) -> Optional[Dict]:
        if self.position_size == 0 or not self.exit_active:
            return None

        high  = candle['high']
        low   = candle['low']
        close = candle['close']
        ts    = candle.get('timestamp')

        if self.position_size > 0:
            # LONG
            self.highest_price = max(self.highest_price, high)
            profit_ticks = (self.highest_price - self.position_avg_price) / self.tick_size
            if profit_ticks >= self.fixed_tp_points:
                self.trailing_active = True

            stop = (self.highest_price - self.trail_offset * self.tick_size
                    if self.trailing_active
                    else self.position_avg_price - self.fixed_sl_points * self.tick_size)

            if low <= stop:
                exit_price = min(close, stop)
                qty = self.position_size
                pnl = (exit_price - self.position_avg_price) * qty
                self.net_profit += pnl
                self.balance = self.initial_capital + self.net_profit
                self.position_size = 0.0
                self.position_avg_price = 0.0
                self.highest_price = 0.0
                self.trailing_active = False
                self.exit_active = False
                return {
                    "action": "EXIT_LONG", "price": exit_price, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
                }

        elif self.position_size < 0:
            # SHORT
            self.lowest_price = min(self.lowest_price, low)
            profit_ticks = (self.position_avg_price - self.lowest_price) / self.tick_size
            if profit_ticks >= self.fixed_tp_points:
                self.trailing_active = True

            stop = (self.lowest_price + self.trail_offset * self.tick_size
                    if self.trailing_active
                    else self.position_avg_price + self.fixed_sl_points * self.tick_size)

            if high >= stop:
                exit_price = max(close, stop)
                qty = abs(self.position_size)
                pnl = (self.position_avg_price - exit_price) * qty
                self.net_profit += pnl
                self.balance = self.initial_capital + self.net_profit
                self.position_size = 0.0
                self.position_avg_price = 0.0
                self.lowest_price = float('inf')
                self.trailing_active = False
                self.exit_active = False
                return {
                    "action": "EXIT_SHORT", "price": exit_price, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
                }

        return None

    # ═══════════════════════════════════════════════════════════════════════
    # LOTS — Pine: balance = initial_capital + netprofit
    #              lots = (risk * balance) / (fixedSL * mintick)
    #              lots > maxQty ? maxQty : lots
    # ═══════════════════════════════════════════════════════════════════════
    def _calc_lots(self) -> float:
        balance = self.initial_capital + self.net_profit
        risk_amount    = self.risk_percent * balance
        stop_loss_usdt = self.fixed_sl_points * self.tick_size
        if stop_loss_usdt <= 0:
            return 0.0
        lots = risk_amount / stop_loss_usdt
        return min(lots, float(self.max_lots))

    # ═══════════════════════════════════════════════════════════════════════
    # MÉTODO PRINCIPAL
    # ═══════════════════════════════════════════════════════════════════════
    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle. Durante warmup_bars barras iniciais, calcula
        indicadores mas NÃO abre posições (equivalente ao histórico do TradingView).
        """
        open_p = candle['open']
        idx    = candle.get('index', 0)
        ts     = candle.get('timestamp')
        actions = []

        # Incrementa contador de barras processadas pela estratégia
        self._bar_count += 1
        in_warmup = self._bar_count <= self.warmup_bars

        # ══════════════════════════════════════════════════════════════════
        # PINE: pendingBuy := nz(pendingBuy[1])
        #       if buy_signal[1]: pendingBuy := true
        # ══════════════════════════════════════════════════════════════════
        if self.buy_signal_prev:
            self.pending_buy = True
        if self.sell_signal_prev:
            self.pending_sell = True

        # ══════════════════════════════════════════════════════════════════
        # Executa entradas agendadas no OPEN (só fora do warmup)
        # ══════════════════════════════════════════════════════════════════
        if not in_warmup:
            balance = self.initial_capital + self.net_profit

            if self.entry_scheduled_long and balance > 0:
                if self.position_size <= 0:
                    # Fecha short se houver
                    if self.position_size < 0:
                        qty = abs(self.position_size)
                        pnl = (self.position_avg_price - open_p) * qty
                        self.net_profit += pnl
                        self.balance = self.initial_capital + self.net_profit
                        actions.append({
                            "action": "EXIT_SHORT", "price": open_p, "qty": qty,
                            "pnl": pnl, "balance": self.balance, "timestamp": ts,
                            "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
                        })
                        self.position_size = 0.0
                        self.exit_active = False
                        self.trailing_active = False

                    lots = self._calc_lots()
                    if lots > 0:
                        self.position_size = lots
                        self.position_avg_price = open_p
                        self.highest_price = open_p
                        self.lowest_price = 0.0
                        self.trailing_active = False
                        self.exit_active = True
                        self.balance = self.initial_capital + self.net_profit
                        actions.append({
                            "action": "BUY", "qty": lots, "price": open_p,
                            "comment": "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                            "balance": self.balance, "timestamp": ts
                        })
                        print(f"✅ LONG [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
                self.entry_scheduled_long = False

            if self.entry_scheduled_short and balance > 0:
                if self.position_size >= 0:
                    # Fecha long se houver
                    if self.position_size > 0:
                        qty = self.position_size
                        pnl = (open_p - self.position_avg_price) * qty
                        self.net_profit += pnl
                        self.balance = self.initial_capital + self.net_profit
                        actions.append({
                            "action": "EXIT_LONG", "price": open_p, "qty": qty,
                            "pnl": pnl, "balance": self.balance, "timestamp": ts,
                            "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
                        })
                        self.position_size = 0.0
                        self.exit_active = False
                        self.trailing_active = False

                    lots = self._calc_lots()
                    if lots > 0:
                        self.position_size = -lots
                        self.position_avg_price = open_p
                        self.lowest_price = open_p
                        self.highest_price = float('inf')
                        self.trailing_active = False
                        self.exit_active = True
                        self.balance = self.initial_capital + self.net_profit
                        actions.append({
                            "action": "SELL", "qty": lots, "price": open_p,
                            "comment": "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                            "balance": self.balance, "timestamp": ts
                        })
                        print(f"✅ SHORT [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
                self.entry_scheduled_short = False

        else:
            # No warmup: descarta entradas agendadas (não foram abertas ainda)
            self.entry_scheduled_long = False
            self.entry_scheduled_short = False

        # ══════════════════════════════════════════════════════════════════
        # Calcula indicadores com CLOSE (sempre, inclusive no warmup)
        # ══════════════════════════════════════════════════════════════════
        src = candle['close']

        if self.force_period is None:
            if self.adaptive_method in ("I-Q IFM", "Average"):
                self._calc_iq_ifm(src)
            if self.adaptive_method in ("Cos IFM", "Average"):
                self._calc_cosine_ifm(src)

            if self.adaptive_method == "Cos IFM":
                self.Period = max(1, int(round(self.lenC)))
            elif self.adaptive_method == "I-Q IFM":
                self.Period = max(1, int(round(self.lenIQ)))
            elif self.adaptive_method == "Average":
                self.Period = max(1, int(round((self.lenC + self.lenIQ) / 2)))
            else:
                self.Period = 20
        else:
            self.Period = max(1, self.force_period)

        ema_prev, ec_prev, ema_new, ec_new = self._calc_zero_lag_ema(src, self.Period)

        # crossover(EC, EMA): EC[1] <= EMA[1] AND EC > EMA
        crossover  = (ec_prev <= ema_prev) and (ec_new > ema_new)
        crossunder = (ec_prev >= ema_prev) and (ec_new < ema_new)
        error_pct  = 100.0 * self.LeastError / src if src != 0 else 0.0
        buy_signal  = crossover  and (error_pct > self.threshold)
        sell_signal = crossunder and (error_pct > self.threshold)

        # ══════════════════════════════════════════════════════════════════
        # Trailing stop / SL (só fora do warmup, mas posição só existe fora)
        # ══════════════════════════════════════════════════════════════════
        exit_action = self._check_exit(candle)
        if exit_action:
            actions.append(exit_action)

        # ══════════════════════════════════════════════════════════════════
        # Agenda entrada para próximo candle
        # Pine: if pendingBuy and pos<=0: strategy.entry (→ entra no próximo open)
        # Durante warmup: processa pending mas descarta (entry_scheduled vai ser
        # limpo no início do próximo next())
        # ══════════════════════════════════════════════════════════════════
        if self.pending_buy and self.position_size <= 0:
            self.entry_scheduled_long = True
            self.entry_scheduled_short = False
            self.pending_buy = False
            if not in_warmup:
                print(f"🚀 Long agendado → barra {idx+1}")

        if self.pending_sell and self.position_size >= 0:
            self.entry_scheduled_short = True
            self.entry_scheduled_long = False
            self.pending_sell = False
            if not in_warmup:
                print(f"🚀 Short agendado → barra {idx+1}")

        # Salva sinais para próxima barra
        self.buy_signal_prev  = buy_signal
        self.sell_signal_prev = sell_signal

        if idx % 100 == 0:
            warmup_str = " [WARMUP]" if in_warmup else ""
            print(
                f"📊 [{idx}]{warmup_str} P={self.Period} "
                f"EC={ec_new:.2f} EMA={ema_new:.2f} diff={ec_new-ema_new:.4f} "
                f"xo={crossover} xu={crossunder} err%={error_pct:.4f} "
                f"pos={self.position_size:.4f} bal={self.balance:.2f}"
            )

        return actions
