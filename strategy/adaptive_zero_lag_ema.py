# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# TRADUÇÃO FIEL DO PINE SCRIPT v3 → PYTHON
#
# CORREÇÃO FINAL (esta versão):
#
#   BUG: Quando exit_scheduled=True (trailing stop ou SL ativado no close),
#   a posição ainda está aberta naquele close (o exit só executa no próximo open).
#   Porém, o Python estava avaliando "if pending and position_size <= 0"
#   (para SHORT = posição < 0 satisfaz <= 0), setando entry_scheduled contrário
#   na MESMA barra do exit, causando reversão indevida no próximo open.
#
#   COMPORTAMENTO CORRETO DO PINE:
#     Barra X:   trailing tocado → exit agendado para open de X+1
#     Barra X+1: EXIT executa no open → position_size = 0
#                close: pending_buy e pos=0 → entry_scheduled_long
#     Barra X+2: LONG entra no open
#
#   COMPORTAMENTO BUGADO DO PYTHON (anterior):
#     Barra X:   trailing tocado → exit_scheduled=True
#                pending_buy e pos<=0 (SHORT) → entry_scheduled_long=True ← ERRADO!
#     Barra X+1: entry_scheduled_long E pos<0 → reversão ao open, CANCELA o exit
#                SHORT fecha ao open (PERDA vs lucro do trailing!)
#
#   CORREÇÃO: No close da barra, quando avaliamos entry_scheduled,
#   consideramos exit_scheduled=True como "posição ainda aberta efetivamente".
#   Só agenda entry SE NÃO houver exit_scheduled pendente.
#   Exceção: entry na MESMA direção (não é reversão).
#
#   IMPACTO: Elimina reversões prematuras que cancelavam trailing stops lucrativos.
#   O TradingView confirma: trailing fecha PRIMEIRO, depois entry na barra seguinte.
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

    inphase_buffer: deque = field(default_factory=lambda: deque(maxlen=4))
    quadrature_buffer: deque = field(default_factory=lambda: deque(maxlen=3))
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
    EC: float = 0.0
    LeastError: float = 0.0
    BestGain: float = 0.0
    Period: int = 20

    pending_buy: bool = False
    pending_sell: bool = False
    buy_signal_prev: bool = False
    sell_signal_prev: bool = False
    entry_scheduled_long: bool = False
    entry_scheduled_short: bool = False

    exit_scheduled: bool = False
    exit_scheduled_side: str = ""
    exit_scheduled_reason: str = ""

    position_size: float = 0.0
    position_avg_price: float = 0.0
    net_profit: float = 0.0

    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_active: bool = False
    exit_active: bool = False
    _stop_price: float = 0.0
    _bar_count: int = 0

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

    # ─── IFM ───────────────────────────────────────────────────────────────
    def _calc_iq_ifm(self, src: float):
        imult, qmult = 0.635, 0.338
        self._src_buf_iq.append(src)
        P = src - self._src_buf_iq[0]
        self._P_buf.append(P)
        P_list = list(self._P_buf)
        P_4 = P_list[0];  P_2 = P_list[2] if len(P_list) >= 3 else 0.0
        ib = list(self.inphase_buffer);  qb = list(self.quadrature_buffer)
        inphase_3 = ib[0] if len(ib) >= 4 else 0.0
        inphase_1 = ib[-2] if len(ib) >= 2 else 0.0
        quad_2 = qb[0] if len(qb) >= 3 else 0.0
        quad_1 = qb[-2] if len(qb) >= 2 else 0.0
        inph = 1.25*(P_4 - imult*P_2) + imult*inphase_3
        quad = P_2 - qmult*P + qmult*quad_2
        self.inphase_buffer.append(inph); self.quadrature_buffer.append(quad)
        re = 0.2*(inph*inphase_1 + quad*quad_1) + 0.8*self.re_prev
        im = 0.2*(inph*quad_1 - inphase_1*quad) + 0.8*self.im_prev
        self.re_prev = re; self.im_prev = im
        dIQ = math.atan(im/re) if re != 0.0 else 0.0
        self.deltaIQ_buffer.append(dIQ)
        dl = list(self.deltaIQ_buffer); V = 0.0; inst = 0.0
        for i in range(RANGE+1):
            idx = -(i+1)
            if abs(idx) <= len(dl):
                V += dl[idx]
                if V > 2*PI and inst == 0.0: inst = float(i)
        if inst == 0.0: inst = self.instIQ
        self.instIQ = inst
        self.lenIQ = 0.25*inst + 0.75*self.lenIQ

    def _calc_cosine_ifm(self, src: float):
        self._src_buf_cos.append(src)
        v1 = src - self._src_buf_cos[0]; v1_1 = self.v1_prev; self.v1_prev = v1
        self.s2 = 0.2*(v1_1+v1)**2 + 0.8*self.s2
        self.s3 = 0.2*(v1_1-v1)**2 + 0.8*self.s3
        v2 = 0.0
        if self.s2 != 0.0:
            r = self.s3/self.s2
            if r >= 0.0: v2 = math.sqrt(r)
        dC = 2*math.atan(v2) if self.s3 != 0.0 else 0.0
        self.deltaC_buffer.append(dC)
        dl = list(self.deltaC_buffer); v4 = 0.0; inst = 0.0
        for i in range(RANGE+1):
            idx = -(i+1)
            if abs(idx) <= len(dl):
                v4 += dl[idx]
                if v4 > 2*PI and inst == 0.0: inst = float(i-1)
        if inst == 0.0: inst = self.instC
        self.instC = inst
        self.lenC = 0.25*inst + 0.75*self.lenC

    def _calc_zero_lag_ema(self, src: float, period: int):
        alpha = 2.0/(period+1)
        ep = self.EMA; ecp = self.EC
        ema = alpha*src + (1-alpha)*ep
        le = 1_000_000.0; bg = 0.0
        for i in range(-GAIN_LIMIT, GAIN_LIMIT+1):
            g = i/10.0
            ec_c = alpha*(ema + g*(src - ecp)) + (1-alpha)*ecp
            e = abs(src - ec_c)
            if e < le: le = e; bg = g
        ec = alpha*(ema + bg*(src - ecp)) + (1-alpha)*ecp
        self.EMA = ema; self.EC = ec; self.LeastError = le; self.BestGain = bg
        return ep, ecp, ema, ec

    # ─── TRAILING STOP ────────────────────────────────────────────────────
    def _check_stop_touched(self, candle: Dict):
        if self.position_size == 0 or not self.exit_active or self.exit_scheduled:
            return False
        h = candle['high']; l = candle['low']
        if self.position_size > 0:
            self.highest_price = max(self.highest_price, h)
            pt = (self.highest_price - self.position_avg_price) / self.tick_size
            if pt >= self.fixed_tp_points: self.trailing_active = True
            stop = (self.highest_price - self.trail_offset*self.tick_size
                    if self.trailing_active
                    else self.position_avg_price - self.fixed_sl_points*self.tick_size)
            self._stop_price = stop
            if l <= stop:
                self.exit_scheduled = True; self.exit_scheduled_side = "long"
                self.exit_scheduled_reason = "TRAIL" if self.trailing_active else "SL"
                return True
        elif self.position_size < 0:
            self.lowest_price = min(self.lowest_price, l)
            pt = (self.position_avg_price - self.lowest_price) / self.tick_size
            if pt >= self.fixed_tp_points: self.trailing_active = True
            stop = (self.lowest_price + self.trail_offset*self.tick_size
                    if self.trailing_active
                    else self.position_avg_price + self.fixed_sl_points*self.tick_size)
            self._stop_price = stop
            if h >= stop:
                self.exit_scheduled = True; self.exit_scheduled_side = "short"
                self.exit_scheduled_reason = "TRAIL" if self.trailing_active else "SL"
                return True
        return False

    def _execute_scheduled_exit(self, open_p: float, ts) -> Optional[Dict]:
        """Exit por stop/trailing. Executa ao open (sem reversão competindo)."""
        if not self.exit_scheduled: return None
        reason = self.exit_scheduled_reason
        if self.exit_scheduled_side == "long":
            ep = min(self._stop_price, open_p)
            qty = self.position_size
            pnl = (ep - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_long()
            return {"action":"EXIT_LONG","price":ep,"qty":qty,"pnl":pnl,
                    "balance":self.balance,"timestamp":ts,"exit_reason":reason,
                    "comment":"EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}
        elif self.exit_scheduled_side == "short":
            ep = max(self._stop_price, open_p)
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - ep) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_short()
            return {"action":"EXIT_SHORT","price":ep,"qty":qty,"pnl":pnl,
                    "balance":self.balance,"timestamp":ts,"exit_reason":reason,
                    "comment":"EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}
        return None

    def _close_for_reversal(self, open_p: float, ts) -> Optional[Dict]:
        """
        Fecha posição por REVERSÃO ao open_price.
        Entry contrário cancela exit → usa open_price, não stop_price.
        """
        if self.position_size == 0: return None
        self.exit_scheduled = False; self.exit_scheduled_side = ""
        if self.position_size > 0:
            qty = self.position_size
            pnl = (open_p - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_long()
            return {"action":"EXIT_LONG","price":open_p,"qty":qty,"pnl":pnl,
                    "balance":self.balance,"timestamp":ts,"exit_reason":"REVERSAL",
                    "comment":"EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}
        else:
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - open_p) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_short()
            return {"action":"EXIT_SHORT","price":open_p,"qty":qty,"pnl":pnl,
                    "balance":self.balance,"timestamp":ts,"exit_reason":"REVERSAL",
                    "comment":"EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _reset_long(self):
        self.position_size=0.0; self.position_avg_price=0.0
        self.highest_price=0.0; self.trailing_active=False
        self.exit_active=False; self.exit_scheduled=False; self.exit_scheduled_side=""

    def _reset_short(self):
        self.position_size=0.0; self.position_avg_price=0.0
        self.lowest_price=float('inf'); self.trailing_active=False
        self.exit_active=False; self.exit_scheduled=False; self.exit_scheduled_side=""

    def _calc_lots(self) -> float:
        bal = self.initial_capital + self.net_profit
        sl_usdt = self.fixed_sl_points * self.tick_size
        if sl_usdt <= 0: return 0.0
        return min((self.risk_percent * bal) / sl_usdt, float(self.max_lots))

    # ─── MAIN ─────────────────────────────────────────────────────────────
    def next(self, candle: Dict) -> List[Dict]:
        open_p = candle['open']
        idx    = candle.get('index', 0)
        ts     = candle.get('timestamp')
        actions = []

        self._bar_count += 1
        in_warmup = self._bar_count <= self.warmup_bars

        # Atualiza pending com sinal da barra anterior
        if self.buy_signal_prev:  self.pending_buy  = True
        if self.sell_signal_prev: self.pending_sell = True

        if not in_warmup:
            bal = self.initial_capital + self.net_profit

            # ── OPEN: Ordem de execução fiel ao Pine ─────────────────────
            #
            # 1. REVERSÃO (entry contrário QUANDO NÃO há exit_scheduled)
            #    Pine: entry cancela exit → se entry e exit no mesmo open,
            #    usa open_price. MAS isso só se aplica quando ambos foram
            #    agendados NA MESMA barra. Se exit_scheduled veio de barra
            #    anterior, ele executa PRIMEIRO.
            #
            # 2. EXIT por trailing/SL (se agendado em barra anterior)
            #
            # 3. ENTRY novo (após position_size = 0)

            has_contra_long  = self.entry_scheduled_long  and self.position_size < 0
            has_contra_short = self.entry_scheduled_short and self.position_size > 0

            # Reversão SÓ cancela exit se exit foi agendado NA MESMA barra
            # que o entry (já tratado pela flag exit_scheduled).
            # Como o exit_scheduled e entry_scheduled são de barras DIFERENTES
            # (exit na barra X, entry na barra X+1 APÓS exit executar),
            # o exit SEMPRE executa primeiro aqui.
            #
            # Exceção: entry contrário agendado sem exit_scheduled → reversão normal.

            if has_contra_long and not self.exit_scheduled and bal > 0:
                rev = self._close_for_reversal(open_p, ts)
                if rev: actions.append(rev)

            if has_contra_short and not self.exit_scheduled and bal > 0:
                rev = self._close_for_reversal(open_p, ts)
                if rev: actions.append(rev)

            # Exit por trailing/SL (executa ANTES de novos entries)
            if self.exit_scheduled and self.position_size != 0:
                exit_act = self._execute_scheduled_exit(open_p, ts)
                if exit_act: actions.append(exit_act)

            # Entries (após position_size zerado pelo exit acima)
            if self.entry_scheduled_long and bal > 0 and self.position_size <= 0:
                lots = self._calc_lots()
                if lots > 0:
                    self.position_size = lots
                    self.position_avg_price = open_p
                    self.highest_price = open_p; self.lowest_price = 0.0
                    self.trailing_active = False; self.exit_active = True
                    self.exit_scheduled = False
                    self._stop_price = open_p - self.fixed_sl_points*self.tick_size
                    self.balance = self.initial_capital + self.net_profit
                    actions.append({"action":"BUY","qty":lots,"price":open_p,
                        "comment":"ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                        "balance":self.balance,"timestamp":ts})
                    print(f"✅ LONG  [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
                self.entry_scheduled_long = False

            if self.entry_scheduled_short and bal > 0 and self.position_size >= 0:
                lots = self._calc_lots()
                if lots > 0:
                    self.position_size = -lots
                    self.position_avg_price = open_p
                    self.lowest_price = open_p; self.highest_price = float('inf')
                    self.trailing_active = False; self.exit_active = True
                    self.exit_scheduled = False
                    self._stop_price = open_p + self.fixed_sl_points*self.tick_size
                    self.balance = self.initial_capital + self.net_profit
                    actions.append({"action":"SELL","qty":lots,"price":open_p,
                        "comment":"ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                        "balance":self.balance,"timestamp":ts})
                    print(f"✅ SHORT [{idx}] @ {open_p:.2f} qty={lots:.4f} bal={self.balance:.2f}")
                self.entry_scheduled_short = False

        else:
            self.entry_scheduled_long = False
            self.entry_scheduled_short = False
            self.exit_scheduled = False

        # ── CLOSE: indicadores ────────────────────────────────────────────
        src = candle['close']
        if self.force_period is None:
            if self.adaptive_method in ("I-Q IFM","Average"): self._calc_iq_ifm(src)
            if self.adaptive_method in ("Cos IFM","Average"):  self._calc_cosine_ifm(src)
            if   self.adaptive_method == "Cos IFM":  self.Period = max(1, int(round(self.lenC)))
            elif self.adaptive_method == "I-Q IFM":  self.Period = max(1, int(round(self.lenIQ)))
            elif self.adaptive_method == "Average":  self.Period = max(1, int(round((self.lenC+self.lenIQ)/2)))
            else:                                     self.Period = 20
        else:
            self.Period = max(1, self.force_period)

        ep, ecp, ema, ec = self._calc_zero_lag_ema(src, self.Period)
        xo = (ecp <= ep) and (ec > ema)
        xu = (ecp >= ep) and (ec < ema)
        err = 100.0*self.LeastError/src if src != 0 else 0.0
        buy_signal  = xo and (err > self.threshold)
        sell_signal = xu and (err > self.threshold)

        # ── CLOSE: verifica trailing/SL ───────────────────────────────────
        self._check_stop_touched(candle)

        # ── CLOSE: agenda entries para próximo open ───────────────────────
        #
        # ✅ CORREÇÃO PRINCIPAL:
        # Só agenda entry SE NÃO houver exit_scheduled ativo.
        # Quando exit_scheduled=True, a posição é tratada como "ainda aberta".
        # O entry contrário será agendado NA BARRA SEGUINTE, após o exit executar.
        # Isso replica exatamente o comportamento do Pine Script.
        #
        # Pine: if pendingBuy and pos<=0 → entry agendado
        # Mas "pos" no Pine ainda não reflete o exit pendente:
        # O exit só muda pos no OPEN da próxima barra.
        # No close atual, pos ainda é o valor antes do exit.
        # Portanto, se pos<0 (SHORT) e exit_scheduled:
        # Pine: pos<0 ≤ 0 → entry seria chamado... MAS o Pine gerencia
        # a ordem: exit primeiro, depois o pending na próxima barra.
        # A diferença está em QUANDO o pending se torna True:
        # buy_signal_prev → pending_buy é setado no início de X+1,
        # APÓS o exit ter executado no open de X+1.
        # Mas pending_buy setado por buy_signal_prev que veio de BEFORE o exit...
        #
        # A solução correta: bloquear entry_scheduled quando exit_scheduled=True
        # pois a posição ainda não fechou.
        if not self.exit_scheduled:
            # Posição não está prestes a fechar → comportamento normal
            if self.pending_buy and self.position_size <= 0:
                self.entry_scheduled_long  = True
                self.entry_scheduled_short = False
                self.pending_buy = False
                if not in_warmup: print(f"🚀 Long agendado → [{idx+1}]")

            if self.pending_sell and self.position_size >= 0:
                self.entry_scheduled_short = True
                self.entry_scheduled_long  = False
                self.pending_sell = False
                if not in_warmup: print(f"🚀 Short agendado → [{idx+1}]")
        else:
            # Posição vai fechar no próximo open → não agenda entry contrário agora.
            # O pending será avaliado na PRÓXIMA barra, após o exit executar.
            # (pending_buy/sell persistem para próxima barra, não são zerados)
            # Só agenda na MESMA direção (improvável mas possível)
            pass

        self.buy_signal_prev  = buy_signal
        self.sell_signal_prev = sell_signal

        if idx % 100 == 0:
            wstr = " [WU]" if in_warmup else ""
            print(f"📊 [{idx}]{wstr} P={self.Period} EC={ec:.2f} EMA={ema:.2f} "
                  f"diff={ec-ema:.4f} pos={self.position_size:.4f} "
                  f"bal={self.balance:.2f} trail={'ON' if self.trailing_active else 'off'} "
                  f"exitS={self.exit_scheduled}")

        return actions
