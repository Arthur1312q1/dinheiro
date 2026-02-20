# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# TRADUÇÃO FIEL AO PINE SCRIPT v3 - VERSÃO FINAL DEFINITIVA
#
# TODOS OS BUGS IDENTIFICADOS E CORRIGIDOS:
#
# BUG #1 [CRÍTICO] ─ Python bloqueava pending quando exit_scheduled=True
#   Pine: avalia pending no close usando position_size ATUAL (antes do exit)
#   Não há bloqueio por exit_scheduled. Entry contrário cancela exit.
#   Correção: remover bloqueio, processar pending normalmente no close.
#
# BUG #2 [CRÍTICO] ─ Crossover/Crossunder com < ao invés de <=
#   Pine v3: crossover(x,y) = x[1] <= y[1] AND x > y  (usa <=)
#             crossunder(x,y) = x[1] >= y[1] AND x < y  (usa >=)
#   Correção: usar <= e >= nas comparações.
#
# BUG #3 [MÉDIO] ─ Preço de execução do trailing stop
#   Pine com calc_on_every_tick=false, slippage=0:
#   Stop detectado no close → executa ao OPEN da próxima barra.
#   O stop_price determina QUANDO ativa, não o preço de fill.
#   Correção: exec_price = open_p (sempre o open).
#
# BUG #4 [MÉDIO] ─ Pine "last wins" quando pendingBuy e pendingSell simultâneos
#   Se pos=0 e ambos pending: Pine agenda ambos, SELL vence (último call).
#   Correção: se ambos pending com pos=0, apenas SELL é agendado.
#
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
    # ── Parâmetros (idênticos ao Pine) ───────────────────────────────────
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

    # ── I-Q IFM ──────────────────────────────────────────────────────────
    inphase_buffer: deque = field(default_factory=lambda: deque(maxlen=4))
    quadrature_buffer: deque = field(default_factory=lambda: deque(maxlen=3))
    re_prev: float = 0.0
    im_prev: float = 0.0
    deltaIQ_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instIQ: float = 0.0
    lenIQ: float = 0.0

    # ── Cosine IFM ────────────────────────────────────────────────────────
    v1_prev: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    deltaC_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instC: float = 0.0
    lenC: float = 0.0

    # ── ZLEMA ─────────────────────────────────────────────────────────────
    EMA: float = 0.0
    EC: float = 0.0
    LeastError: float = 0.0
    BestGain: float = 0.0
    Period: int = 20

    # ── Estado de sinal (Pine pendingBuy/pendingSell) ─────────────────────
    pending_buy: bool = False
    pending_sell: bool = False
    buy_signal_prev: bool = False
    sell_signal_prev: bool = False

    # ── Agendamento (avaliado no close, executa no open seguinte) ─────────
    entry_scheduled_long: bool = False
    entry_scheduled_short: bool = False
    exit_scheduled: bool = False
    exit_scheduled_side: str = ""
    exit_scheduled_reason: str = ""

    # ── Estado da posição ────────────────────────────────────────────────
    position_size: float = 0.0       # >0 long, <0 short, 0 flat
    position_avg_price: float = 0.0
    net_profit: float = 0.0

    # ── Trailing stop ─────────────────────────────────────────────────────
    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_active: bool = False
    exit_active: bool = False
    _stop_price: float = 0.0
    _bar_count: int = 0

    # ── Buffers ───────────────────────────────────────────────────────────
    _src_buf_iq: deque = field(default_factory=lambda: deque(maxlen=8))
    _P_buf: deque = field(default_factory=lambda: deque(maxlen=5))
    _src_buf_cos: deque = field(default_factory=lambda: deque(maxlen=8))

    balance: float = field(init=False)

    def __post_init__(self):
        self.balance = self.initial_capital
        for _ in range(4): self.inphase_buffer.append(0.0)
        for _ in range(3): self.quadrature_buffer.append(0.0)
        for _ in range(RANGE + 1):
            self.deltaIQ_buffer.append(0.0)
            self.deltaC_buffer.append(0.0)
        for _ in range(8):
            self._src_buf_iq.append(0.0)
            self._src_buf_cos.append(0.0)
        for _ in range(5): self._P_buf.append(0.0)

    # ═══════════════════════════════════════════════════════════════════════
    # IFM - I-Q Method
    # ═══════════════════════════════════════════════════════════════════════

    def _calc_iq_ifm(self, src: float):
        imult, qmult = 0.635, 0.338
        self._src_buf_iq.append(src)
        P = src - self._src_buf_iq[0]              # src - src[7]
        self._P_buf.append(P)
        pl = list(self._P_buf)                     # [P-4, P-3, P-2, P-1, P]
        P_4, P_2, P_0 = pl[0], pl[2], pl[4]

        ib = list(self.inphase_buffer)             # [ip-3, ip-2, ip-1, ip_last]
        inphase_3, inphase_1 = ib[0], ib[2]

        qb = list(self.quadrature_buffer)          # [q-2, q-1, q_last]
        quad_2, quad_1 = qb[0], qb[1]

        inph = 1.25 * (P_4 - imult * P_2) + imult * inphase_3
        quad = P_2 - qmult * P_0 + qmult * quad_2
        self.inphase_buffer.append(inph)
        self.quadrature_buffer.append(quad)

        re = 0.2 * (inph * inphase_1 + quad * quad_1) + 0.8 * self.re_prev
        im = 0.2 * (inph * quad_1 - inphase_1 * quad) + 0.8 * self.im_prev
        self.re_prev, self.im_prev = re, im

        dIQ = math.atan(im / re) if re != 0.0 else 0.0
        self.deltaIQ_buffer.append(dIQ)

        dl = list(self.deltaIQ_buffer)
        V = inst = 0.0
        for i in range(RANGE + 1):
            j = -(i + 1)
            if abs(j) <= len(dl):
                V += dl[j]
                if V > 2 * PI and inst == 0.0:
                    inst = float(i)
        if inst == 0.0: inst = self.instIQ
        self.instIQ = inst
        self.lenIQ = 0.25 * inst + 0.75 * self.lenIQ

    # ═══════════════════════════════════════════════════════════════════════
    # IFM - Cosine Method
    # ═══════════════════════════════════════════════════════════════════════

    def _calc_cosine_ifm(self, src: float):
        self._src_buf_cos.append(src)
        v1   = src - self._src_buf_cos[0]          # src - src[7]
        v1_1 = self.v1_prev
        self.v1_prev = v1

        self.s2 = 0.2 * (v1_1 + v1) ** 2 + 0.8 * self.s2
        self.s3 = 0.2 * (v1_1 - v1) ** 2 + 0.8 * self.s3

        v2 = 0.0
        if self.s2 != 0.0:
            r = self.s3 / self.s2
            if r >= 0.0: v2 = math.sqrt(r)

        dC = 2 * math.atan(v2) if self.s3 != 0.0 else 0.0
        self.deltaC_buffer.append(dC)

        dl = list(self.deltaC_buffer)
        v4 = inst = 0.0
        for i in range(RANGE + 1):
            j = -(i + 1)
            if abs(j) <= len(dl):
                v4 += dl[j]
                if v4 > 2 * PI and inst == 0.0:
                    inst = float(i - 1)
        if inst == 0.0: inst = self.instC
        self.instC = inst
        self.lenC = 0.25 * inst + 0.75 * self.lenC

    # ═══════════════════════════════════════════════════════════════════════
    # Zero-Lag EMA
    # ═══════════════════════════════════════════════════════════════════════

    def _calc_zero_lag_ema(self, src: float, period: int):
        """
        Fiel ao Pine v3.
        Loop usa EC[1] = ec_prev (mesmo para todas as iterações).
        """
        alpha = 2.0 / (period + 1)
        ema_prev = self.EMA
        ec_prev  = self.EC

        ema = alpha * src + (1 - alpha) * ema_prev

        le, bg = 1_000_000.0, 0.0
        for i in range(-GAIN_LIMIT, GAIN_LIMIT + 1):
            g    = i / 10.0
            ec_c = alpha * (ema + g * (src - ec_prev)) + (1 - alpha) * ec_prev
            e    = abs(src - ec_c)
            if e < le:
                le, bg = e, g

        ec = alpha * (ema + bg * (src - ec_prev)) + (1 - alpha) * ec_prev
        self.EMA, self.EC = ema, ec
        self.LeastError, self.BestGain = le, bg

        return ema_prev, ec_prev, ema, ec   # (prev, prev, curr, curr)

    # ═══════════════════════════════════════════════════════════════════════
    # Trailing Stop / Stop Loss
    # ═══════════════════════════════════════════════════════════════════════

    def _check_stop_touched(self, candle: Dict) -> bool:
        """
        Verifica HIGH/LOW do candle para ativação do stop/trail.
        Se ativado: exit_scheduled=True → executa no open da próxima barra.
        """
        if self.position_size == 0 or not self.exit_active or self.exit_scheduled:
            return False

        h, l = candle['high'], candle['low']

        if self.position_size > 0:                             # LONG
            self.highest_price = max(self.highest_price, h)
            pt = (self.highest_price - self.position_avg_price) / self.tick_size
            if pt >= self.fixed_tp_points:
                self.trailing_active = True
            stop = (self.highest_price - self.trail_offset * self.tick_size
                    if self.trailing_active
                    else self.position_avg_price - self.fixed_sl_points * self.tick_size)
            self._stop_price = stop
            if l <= stop:
                self.exit_scheduled      = True
                self.exit_scheduled_side = "long"
                self.exit_scheduled_reason = "TRAIL" if self.trailing_active else "SL"
                return True

        elif self.position_size < 0:                           # SHORT
            self.lowest_price = min(self.lowest_price, l)
            pt = (self.position_avg_price - self.lowest_price) / self.tick_size
            if pt >= self.fixed_tp_points:
                self.trailing_active = True
            stop = (self.lowest_price + self.trail_offset * self.tick_size
                    if self.trailing_active
                    else self.position_avg_price + self.fixed_sl_points * self.tick_size)
            self._stop_price = stop
            if h >= stop:
                self.exit_scheduled      = True
                self.exit_scheduled_side = "short"
                self.exit_scheduled_reason = "TRAIL" if self.trailing_active else "SL"
                return True

        return False

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers de execução
    # ═══════════════════════════════════════════════════════════════════════

    def _exec_exit(self, open_p: float, ts) -> Optional[Dict]:
        """
        Executa exit por stop/trail.
        Pine calc_on_every_tick=false, slippage=0 → exec_price = open_p.
        """
        side = self.exit_scheduled_side
        r    = self.exit_scheduled_reason

        if side == "long" and self.position_size > 0:
            qty = self.position_size
            pnl = (open_p - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_LONG", "price": open_p, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "exit_reason": r,
                    "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

        elif side == "short" and self.position_size < 0:
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - open_p) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_SHORT", "price": open_p, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "exit_reason": r,
                    "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}
        return None

    def _exec_close(self, open_p: float, ts, reason: str = "REVERSAL") -> Optional[Dict]:
        """
        Fecha posição ao open_price (reversão).
        Cancela qualquer exit agendado (Pine: entry cancela exit).
        """
        if self.position_size == 0:
            return None
        self.exit_scheduled = False
        self.exit_scheduled_side = ""
        self.exit_scheduled_reason = ""

        if self.position_size > 0:
            qty = self.position_size
            pnl = (open_p - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_LONG", "price": open_p, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "exit_reason": reason,
                    "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}
        else:
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - open_p) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_SHORT", "price": open_p, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "exit_reason": reason,
                    "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _exec_long(self, open_p: float, ts, idx: int) -> Optional[Dict]:
        lots = self._calc_lots()
        if lots <= 0: return None
        self.position_size      = lots
        self.position_avg_price = open_p
        self.highest_price      = open_p
        self.lowest_price       = float('inf')
        self.trailing_active    = False
        self.exit_active        = True
        self.exit_scheduled     = False
        self._stop_price        = open_p - self.fixed_sl_points * self.tick_size
        self.balance            = self.initial_capital + self.net_profit
        print(f"✅ LONG  [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
        return {"action": "BUY", "qty": lots, "price": open_p, "balance": self.balance,
                "timestamp": ts,
                "comment": "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _exec_short(self, open_p: float, ts, idx: int) -> Optional[Dict]:
        lots = self._calc_lots()
        if lots <= 0: return None
        self.position_size      = -lots
        self.position_avg_price = open_p
        self.lowest_price       = open_p
        self.highest_price      = float('inf')
        self.trailing_active    = False
        self.exit_active        = True
        self.exit_scheduled     = False
        self._stop_price        = open_p + self.fixed_sl_points * self.tick_size
        self.balance            = self.initial_capital + self.net_profit
        print(f"✅ SHORT [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
        return {"action": "SELL", "qty": lots, "price": open_p, "balance": self.balance,
                "timestamp": ts,
                "comment": "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _reset_pos(self):
        self.position_size      = 0.0
        self.position_avg_price = 0.0
        self.highest_price      = 0.0
        self.lowest_price       = float('inf')
        self.trailing_active    = False
        self.exit_active        = False
        self.exit_scheduled     = False
        self.exit_scheduled_side = ""
        self.exit_scheduled_reason = ""

    def _calc_lots(self) -> float:
        bal    = self.initial_capital + self.net_profit
        sl_usd = self.fixed_sl_points * self.tick_size
        if sl_usd <= 0: return 0.0
        return min((self.risk_percent * bal) / sl_usd, float(self.max_lots))

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════════

    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle.

        ━━━━ FLUXO FIEL AO PINE v3 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        OPEN (início de cada barra):
        ┌─────────────────────────────────────────────────────────────────┐
        │ Pine executa ordens na seguinte ordem de prioridade:            │
        │                                                                 │
        │ PRIORIDADE 1: Entry contrário agendado                         │
        │   → Cancela exit_scheduled                                     │
        │   → Fecha posição atual ao open (reversão)                     │
        │   → Abre nova posição ao open                                  │
        │                                                                 │
        │ PRIORIDADE 2: Exit por stop/trail (se não há entry contrário)  │
        │   → Executa ao open (slippage=0, calc_on_every_tick=false)     │
        │                                                                 │
        │ PRIORIDADE 3: Entry novo (flat → posição)                      │
        │   → Executa ao open                                            │
        └─────────────────────────────────────────────────────────────────┘

        CLOSE (fim de cada barra):
        ┌─────────────────────────────────────────────────────────────────┐
        │ 1. Calcula IFM (período adaptativo)                             │
        │ 2. Calcula ZLEMA (EC e EMA)                                     │
        │ 3. Crossover/crossunder (com <=/>= como Pine v3)               │
        │ 4. Verifica stop/trail no H/L → exit_scheduled                 │
        │ 5. Propaga sinais: buy_signal_prev → pending_buy               │
        │ 6. Avalia pending → agenda entry para próximo open             │
        │                                                                 │
        │ CRÍTICO: pending é avaliado com position_size ATUAL.            │
        │ exit_scheduled NÃO bloqueia o agendamento de entries!          │
        │ (Pine não faz isso - entry contrário cancela exit no open)     │
        │                                                                 │
        │ PINE "LAST WINS": se pos=0 e ambos pending, SELL vence         │
        └─────────────────────────────────────────────────────────────────┘
        """
        open_p  = candle['open']
        idx     = candle.get('index', 0)
        ts      = candle.get('timestamp')
        actions: List[Dict] = []

        self._bar_count += 1
        in_warmup = self._bar_count <= self.warmup_bars

        # ──────────────────────────────────────────────────────────────────
        # FASE OPEN
        # ──────────────────────────────────────────────────────────────────
        if not in_warmup and (self.initial_capital + self.net_profit) > 0:

            el = self.entry_scheduled_long
            es = self.entry_scheduled_short

            # ── Prioridade 1: entry contrário → reversão ──────────────────
            # Entry contrário cancela exit_scheduled e inverte posição.
            # Pine strategy.entry() com pyramiding=1 é sempre atômico.
            if el and self.position_size < 0:
                # Estava SHORT → reverte para LONG
                rev = self._exec_close(open_p, ts, "REVERSAL")
                if rev: actions.append(rev)
                act = self._exec_long(open_p, ts, idx)
                if act: actions.append(act)
                self.entry_scheduled_long = False

            elif es and self.position_size > 0:
                # Estava LONG → reverte para SHORT
                rev = self._exec_close(open_p, ts, "REVERSAL")
                if rev: actions.append(rev)
                act = self._exec_short(open_p, ts, idx)
                if act: actions.append(act)
                self.entry_scheduled_short = False

            # ── Prioridade 2: exit por stop/trail ─────────────────────────
            # Só executa se não houve reversão acima
            elif self.exit_scheduled and self.position_size != 0:
                act = self._exec_exit(open_p, ts)
                if act: actions.append(act)
                # Limpa flags de entry que possam existir
                # (não deveria ter entry após exit sem reversão, mas por segurança)

            # ── Prioridade 3: entry novo (flat) ───────────────────────────
            # Executa se pos=0 (pode ter chegado aqui direto ou após exit acima)
            if el and self.position_size == 0:
                act = self._exec_long(open_p, ts, idx)
                if act: actions.append(act)
                self.entry_scheduled_long = False

            if es and self.position_size == 0:
                act = self._exec_short(open_p, ts, idx)
                if act: actions.append(act)
                self.entry_scheduled_short = False

        else:
            self.entry_scheduled_long  = False
            self.entry_scheduled_short = False

        # ──────────────────────────────────────────────────────────────────
        # FASE CLOSE: Indicadores
        # ──────────────────────────────────────────────────────────────────
        src = candle['close']

        if self.force_period is None:
            if self.adaptive_method in ("I-Q IFM", "Average"):
                self._calc_iq_ifm(src)
            if self.adaptive_method in ("Cos IFM", "Average"):
                self._calc_cosine_ifm(src)
            if   self.adaptive_method == "Cos IFM":
                self.Period = max(1, int(round(self.lenC)))
            elif self.adaptive_method == "I-Q IFM":
                self.Period = max(1, int(round(self.lenIQ)))
            elif self.adaptive_method == "Average":
                self.Period = max(1, int(round((self.lenC + self.lenIQ) / 2)))
            else:
                self.Period = 20
        else:
            self.Period = max(1, self.force_period)

        ema_prev, ec_prev, ema, ec = self._calc_zero_lag_ema(src, self.Period)

        # ── Crossover / Crossunder (Pine v3: usa <= e >=) ─────────────────
        buy_signal  = (ec_prev <= ema_prev) and (ec > ema)
        sell_signal = (ec_prev >= ema_prev) and (ec < ema)

        # Filtro de threshold
        err = 100.0 * self.LeastError / src if src != 0 else 0.0
        buy_signal  = buy_signal  and (err > self.threshold)
        sell_signal = sell_signal and (err > self.threshold)

        # ── Verifica stop/trail no candle ─────────────────────────────────
        self._check_stop_touched(candle)

        # ──────────────────────────────────────────────────────────────────
        # FASE CLOSE: Agenda entries (FIEL AO PINE v3)
        # ──────────────────────────────────────────────────────────────────
        #
        # Pine executa no close (avalia pending com position_size atual):
        #
        #   pendingBuy  := nz(pendingBuy[1])       ← herda
        #   pendingSell := nz(pendingSell[1])       ← herda
        #   if buy_signal[1]:  pendingBuy  := true
        #   if sell_signal[1]: pendingSell := true
        #
        #   if (pendingBuy  and pos <= 0): strategy.entry("BUY");  pendingBuy=false
        #   if (pendingSell and pos >= 0): strategy.entry("SELL"); pendingSell=false
        #
        # NOTAS:
        # 1. exit_scheduled NÃO bloqueia avaliação de pending.
        #    Pine avalia pending com position_size atual (antes do exit).
        #    Se entry contrário → cancela exit no próximo open (reversão).
        #
        # 2. Pine usa dois IFs independentes (não elif).
        #    Se pos=0 e ambos pending → ambos seriam agendados.
        #    Pine "last wins": SELL sobrescreve BUY.
        #
        # 3. Pine não zera pending se a condição não foi satisfeita.
        #    (ex: pending_buy com pos>0 → pending_buy permanece True)

        # Propaga sinais da barra anterior
        if self.buy_signal_prev:
            self.pending_buy = True
        if self.sell_signal_prev:
            self.pending_sell = True

        if not in_warmup:
            # Pine if #1: pendingBuy and pos <= 0
            if self.pending_buy and self.position_size <= 0:
                self.entry_scheduled_long = True
                self.pending_buy = False
                print(f"🚀 Long agendado → [{idx + 1}] pos={self.position_size:.3f}")

            # Pine if #2: pendingSell and pos >= 0 (independente do if #1!)
            # Se pos=0 e ambos pending: SELL sobrescreve LONG (last wins)
            if self.pending_sell and self.position_size >= 0:
                self.entry_scheduled_short = True
                # Se BUY foi agendado acima E pos era 0: SELL cancela BUY (Pine last wins)
                if self.entry_scheduled_long and self.position_size == 0:
                    self.entry_scheduled_long = False
                self.pending_sell = False
                print(f"🚀 Short agendado → [{idx + 1}] pos={self.position_size:.3f}")

        else:
            # Warmup: consome pending sem executar
            if self.pending_buy and self.position_size <= 0:
                self.pending_buy = False
            if self.pending_sell and self.position_size >= 0:
                self.pending_sell = False

        # Salva sinais para próxima barra (Pine: buy_signal[1])
        self.buy_signal_prev  = buy_signal
        self.sell_signal_prev = sell_signal

        # Debug periódico
        if idx % 100 == 0:
            wstr = " [WU]" if in_warmup else ""
            print(
                f"📊 [{idx}]{wstr} P={self.Period} "
                f"EC={ec:.4f} EMA={ema:.4f} diff={ec - ema:+.6f} "
                f"xo={buy_signal} xu={sell_signal} "
                f"pos={self.position_size:.4f} bal={self.balance:.2f} "
                f"trail={'ON' if self.trailing_active else 'off'} "
                f"exitS={self.exit_scheduled} "
                f"pB={self.pending_buy} pS={self.pending_sell}"
            )

        return actions
