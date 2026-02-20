# strategy/adaptive_zero_lag_ema.py
#
# TRADUÇÃO EXATA DO PINE SCRIPT v3 — LINHA POR LINHA
# Sem herança, sem complexidade, sem abstrações.
# Cada variável aqui corresponde a uma variável do Pine.
#
# Pine original (estrutura de execução):
# ─────────────────────────────────────────────────────────────────────
#   [CLOSE de cada barra, Pine executa:]
#   1. Calcula src, IFM, Period
#   2. Calcula EMA, EC (ZLEMA)
#   3. buy_signal = crossover(EC,EMA) and threshold
#   4. sell_signal = crossunder(EC,EMA) and threshold
#   5. pendingBuy  := nz(pendingBuy[1])   ← herda da barra anterior
#   6. pendingSell := nz(pendingSell[1])
#   7. if buy_signal[1]:  pendingBuy  := true
#   8. if sell_signal[1]: pendingSell := true
#   9. if pendingBuy  and pos<=0: strategy.entry("BUY");  pendingBuy=false
#  10. if pendingSell and pos>=0: strategy.entry("SELL"); pendingSell=false
#
#   [OPEN da barra seguinte, Pine executa as ordens agendadas]
#   → entry orders têm prioridade: cancelam exits contrários
#   → trailing stop executa se não houver entry contrário
#
# REGRAS FUNDAMENTAIS DO PINE (pyramiding=1, calc_on_every_tick=false):
# ─────────────────────────────────────────────────────────────────────
# R1: strategy.entry() contrário fecha posição atual e abre nova, tudo no open
# R2: trailing stop detectado no close → executa no OPEN (ao preço do OPEN)
# R3: crossover(a,b) = a[1] <= b[1] AND a > b  (Pine v3 usa <=)
# R4: crossunder(a,b)= a[1] >= b[1] AND a < b  (Pine v3 usa >=)
# R5: pending persiste de barra a barra até ser consumido (pos satisfeita)
# R6: strategy.exit declarado na mesma barra do entry → trailing começa na entrada
# R7: entry cancela exit agendado na mesma barra (entry vence)
# R8: balance = initial_capital + netprofit (atualizado a cada trade)

import math
from collections import deque
from typing import Dict, List, Optional

_PI       = 3.14159265359
_RANGE    = 50          # Pine: range = 50
_GL       = 900         # Pine: GainLimit = 900


class AdaptiveZeroLagEMA:

    def __init__(
        self,
        adaptive_method: str   = "Cos IFM",
        threshold:       float = 0.0,
        fixed_sl_points: int   = 2000,
        fixed_tp_points: int   = 55,
        trail_offset:    int   = 15,
        risk_percent:    float = 0.01,
        tick_size:       float = 0.01,
        initial_capital: float = 1000.0,
        max_lots:        int   = 100,
        force_period:    Optional[int] = None,
        warmup_bars:     int   = 300,
    ):
        # ── config ───────────────────────────────────────────────────────
        self.adaptive_method = adaptive_method
        self.threshold       = threshold
        self.fixed_sl        = fixed_sl_points
        self.fixed_tp        = fixed_tp_points
        self.trail_off       = trail_offset
        self.risk            = risk_percent
        self.tick            = tick_size
        self.initial_capital = initial_capital
        self.max_lots        = float(max_lots)
        self.force_period    = force_period
        self.warmup_bars     = warmup_bars

        # ── Cosine IFM ───────────────────────────────────────────────────
        self._src7  = deque([0.0]*8, maxlen=8)   # src - src[7]
        self._v1p   = 0.0
        self._s2    = 0.0
        self._s3    = 0.0
        self._dC    = deque([0.0]*(_RANGE+1), maxlen=_RANGE+1)
        self._instC = 0.0
        self._lenC  = 0.0

        # ── I-Q IFM ──────────────────────────────────────────────────────
        self._src7q = deque([0.0]*8, maxlen=8)
        self._Pbuf  = deque([0.0]*5, maxlen=5)
        self._ipbuf = deque([0.0]*4, maxlen=4)   # inphase history
        self._qbuf  = deque([0.0]*3, maxlen=3)   # quadrature history
        self._re    = 0.0
        self._im    = 0.0
        self._dIQ   = deque([0.0]*(_RANGE+1), maxlen=_RANGE+1)
        self._instIQ= 0.0
        self._lenIQ = 0.0

        # ── ZLEMA ────────────────────────────────────────────────────────
        self.Period = 20
        self._EMA   = 0.0   # EMA[1] (barra anterior)
        self._EC    = 0.0   # EC[1]  (barra anterior)
        self.LeastError = 0.0

        # ── Sinais da barra anterior (Pine: buy_signal[1]) ───────────────
        self._buy_prev  = False
        self._sell_prev = False

        # ── Pending flags Pine (persistem entre barras) ───────────────────
        self._pBuy  = False   # pendingBuy
        self._pSell = False   # pendingSell

        # ── Orders agendadas para o próximo OPEN ──────────────────────────
        # (zerados após execução)
        self._el = False   # entry long  agendado
        self._es = False   # entry short agendado
        self._xe = False   # exit  agendado
        self._xe_side = "" # "long" ou "short"
        self._xe_stop = 0.0

        # ── Posição ───────────────────────────────────────────────────────
        self.position_size  = 0.0    # >0 long, <0 short, 0 flat
        self.position_price = 0.0    # preço médio de entrada
        self.net_profit     = 0.0
        self.balance        = initial_capital

        # ── Trailing ─────────────────────────────────────────────────────
        self._highest = 0.0
        self._lowest  = float('inf')
        self._trailing_active = False
        self._exit_monitored  = False  # True quando strategy.exit está ativo

        self._bar = 0   # contador de barras

    # ═════════════════════════════════════════════════════════════════════
    # IFM Cosine — Pine v3 exato
    # ═════════════════════════════════════════════════════════════════════
    def _cosine_ifm(self, src: float) -> float:
        self._src7.append(src)
        v1   = src - self._src7[0]          # src - src[7]
        v1_1 = self._v1p;  self._v1p = v1

        self._s2 = 0.2*(v1_1+v1)**2 + 0.8*self._s2
        self._s3 = 0.2*(v1_1-v1)**2 + 0.8*self._s3

        v2 = 0.0
        if self._s2 != 0.0:
            r = self._s3/self._s2
            if r >= 0.0: v2 = math.sqrt(r)

        dC = 2.0*math.atan(v2) if self._s3 != 0.0 else 0.0
        self._dC.append(dC)

        dl   = list(self._dC)
        v4   = 0.0
        inst = 0.0
        for i in range(_RANGE+1):           # i = 0..50 (51 iter)
            j = -(i+1)
            if abs(j) <= len(dl):
                v4 += dl[j]
                if v4 > 2*_PI and inst == 0.0:
                    inst = float(i-1)

        if inst == 0.0: inst = self._instC
        self._instC = inst
        self._lenC  = 0.25*inst + 0.75*self._lenC
        return self._lenC

    # ═════════════════════════════════════════════════════════════════════
    # IFM I-Q — Pine v3 exato
    # ═════════════════════════════════════════════════════════════════════
    def _iq_ifm(self, src: float) -> float:
        imult, qmult = 0.635, 0.338
        self._src7q.append(src)
        P = src - self._src7q[0]            # src - src[7]
        self._Pbuf.append(P)
        pl = list(self._Pbuf)               # [P-4, P-3, P-2, P-1, P]

        ib = list(self._ipbuf)              # [ip-3, ip-2, ip-1, ip_last] before append
        qb = list(self._qbuf)

        inph = 1.25*(pl[0] - imult*pl[2]) + imult*ib[0]
        quad = pl[2] - qmult*pl[4] + qmult*qb[0]

        inph_1 = ib[2]
        quad_1 = qb[1]

        self._ipbuf.append(inph)
        self._qbuf.append(quad)

        re = 0.2*(inph*inph_1 + quad*quad_1) + 0.8*self._re
        im = 0.2*(inph*quad_1 - inph_1*quad) + 0.8*self._im
        self._re, self._im = re, im

        dIQ = math.atan(im/re) if re != 0.0 else 0.0
        self._dIQ.append(dIQ)

        dl = list(self._dIQ)
        V = 0.0; inst = 0.0
        for i in range(_RANGE+1):
            j = -(i+1)
            if abs(j) <= len(dl):
                V += dl[j]
                if V > 2*_PI and inst == 0.0:
                    inst = float(i)

        if inst == 0.0: inst = self._instIQ
        self._instIQ = inst
        self._lenIQ  = 0.25*inst + 0.75*self._lenIQ
        return self._lenIQ

    # ═════════════════════════════════════════════════════════════════════
    # ZLEMA — Pine v3 exato
    # EC usa EC[1] (self._EC = barra anterior) para TODAS as iterações do loop
    # ═════════════════════════════════════════════════════════════════════
    def _zlema(self, src: float, period: int):
        alpha   = 2.0 / (period + 1)
        ema_prv = self._EMA
        ec_prv  = self._EC

        ema = alpha*src + (1-alpha)*ema_prv

        le = 1_000_000.0; bg = 0.0
        for i in range(-_GL, _GL+1):       # -900..900 (1801 iter)
            g    = i/10.0
            ec_c = alpha*(ema + g*(src - ec_prv)) + (1-alpha)*ec_prv
            e    = abs(src - ec_c)
            if e < le: le = e; bg = g

        ec = alpha*(ema + bg*(src - ec_prv)) + (1-alpha)*ec_prv
        self._EMA = ema
        self._EC  = ec
        self.LeastError = le

        return ema_prv, ec_prv, ema, ec   # retorna ANTERIOR e ATUAL

    # ═════════════════════════════════════════════════════════════════════
    # OPEN: executa ordens agendadas
    #
    # ORDEM NO PINE (pyramiding=1):
    # 1. Se entry contrário agendado → entry VENCE (cancela _xe, reverte)
    # 2. Se exit agendado sem entry contrário → exit executa
    # 3. Se entry mesmo lado com pos=0 → entry executa
    # ═════════════════════════════════════════════════════════════════════
    def _execute_open(self, open_p: float, ts) -> List[Dict]:
        acts = []
        el, es = self._el, self._es
        
        bal = self.initial_capital + self.net_profit
        if bal <= 0:
            self._el = self._es = False
            return acts

        # ── Entry contrário → reversão (cancela exit, fecha e reabre) ────
        if el and self.position_size < 0:
            # fecha SHORT
            acts.append(self._close_pos(open_p, ts, "REVERSAL"))
            # abre LONG
            a = self._open_long(open_p, ts)
            if a: acts.append(a)
            self._el = False

        elif es and self.position_size > 0:
            # fecha LONG
            acts.append(self._close_pos(open_p, ts, "REVERSAL"))
            # abre SHORT
            a = self._open_short(open_p, ts)
            if a: acts.append(a)
            self._es = False

        # ── Exit agendado (sem entry contrário) ───────────────────────────
        elif self._xe and self.position_size != 0.0:
            a = self._do_exit(open_p, ts)
            if a: acts.append(a)

        # ── Entry flat (pos=0) ────────────────────────────────────────────
        if el and self.position_size == 0.0:
            a = self._open_long(open_p, ts)
            if a: acts.append(a)
            self._el = False

        if es and self.position_size == 0.0:
            a = self._open_short(open_p, ts)
            if a: acts.append(a)
            self._es = False

        return acts

    # ═════════════════════════════════════════════════════════════════════
    # CLOSE: verifica trailing stop
    # Pine: verifica HIGH/LOW do candle
    # Se ativado: exit agendado para próximo OPEN (exec_price = open)
    # ═════════════════════════════════════════════════════════════════════
    def _check_trail(self, h: float, l: float):
        if self.position_size == 0.0 or not self._exit_monitored or self._xe:
            return

        if self.position_size > 0:   # LONG
            self._highest = max(self._highest, h)
            pt = (self._highest - self.position_price) / self.tick
            if pt >= self.fixed_tp:
                self._trailing_active = True
            stop = (self._highest - self.trail_off*self.tick
                    if self._trailing_active
                    else self.position_price - self.fixed_sl*self.tick)
            if l <= stop:
                self._xe      = True
                self._xe_side = "long"
                self._xe_stop = stop
                self._xe_rsn  = "TRAIL" if self._trailing_active else "SL"

        elif self.position_size < 0:  # SHORT
            self._lowest = min(self._lowest, l)
            pt = (self.position_price - self._lowest) / self.tick
            if pt >= self.fixed_tp:
                self._trailing_active = True
            stop = (self._lowest + self.trail_off*self.tick
                    if self._trailing_active
                    else self.position_price + self.fixed_sl*self.tick)
            if h >= stop:
                self._xe      = True
                self._xe_side = "short"
                self._xe_stop = stop
                self._xe_rsn  = "TRAIL" if self._trailing_active else "SL"

    # ═════════════════════════════════════════════════════════════════════
    # CLOSE: agenda entries (EXATAMENTE como Pine)
    # ═════════════════════════════════════════════════════════════════════
    def _schedule_entries(self, in_warmup: bool, idx: int):
        # Pine: propaga sinais da barra anterior para pending
        if self._buy_prev:  self._pBuy  = True
        if self._sell_prev: self._pSell = True

        if in_warmup:
            # Pine não tem warmup, mas iniciamos indicadores.
            # Durante warmup, consumimos pending sem executar.
            if self._pBuy  and self.position_size <= 0.0: self._pBuy  = False
            if self._pSell and self.position_size >= 0.0: self._pSell = False
            return

        # ── Pine if #1: pendingBuy and pos<=0 ─────────────────────────
        if self._pBuy and self.position_size <= 0.0:
            self._el   = True
            self._pBuy = False

        # ── Pine if #2: pendingSell and pos>=0 (INDEPENDENTE do #1) ───
        # Se pos=0 e ambos: SELL sobrescreve BUY (Pine "last wins")
        if self._pSell and self.position_size >= 0.0:
            self._es    = True
            self._pSell = False
            # Pine last-wins: se BUY também foi agendado e pos=0, SELL cancela BUY
            if self._el and self.position_size == 0.0:
                self._el = False

    # ═════════════════════════════════════════════════════════════════════
    # Helpers de posição
    # ═════════════════════════════════════════════════════════════════════
    def _calc_lots(self) -> float:
        bal    = self.initial_capital + self.net_profit
        sl_usd = self.fixed_sl * self.tick
        if sl_usd <= 0.0 or bal <= 0.0: return 0.0
        return min((self.risk * bal) / sl_usd, self.max_lots)

    def _open_long(self, price: float, ts) -> Optional[Dict]:
        lots = self._calc_lots()
        if lots <= 0.0: return None
        self.position_size  =  lots
        self.position_price =  price
        self._highest       =  price
        self._lowest        =  float('inf')
        self._trailing_active = False
        self._exit_monitored  = True
        self._xe              = False   # cancela exit pendente anterior
        self.balance = self.initial_capital + self.net_profit
        return {"action": "BUY", "price": price, "qty": lots,
                "balance": self.balance, "timestamp": ts,
                "comment": "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _open_short(self, price: float, ts) -> Optional[Dict]:
        lots = self._calc_lots()
        if lots <= 0.0: return None
        self.position_size  = -lots
        self.position_price =  price
        self._lowest        =  price
        self._highest       =  float('inf')
        self._trailing_active = False
        self._exit_monitored  = True
        self._xe              = False
        self.balance = self.initial_capital + self.net_profit
        return {"action": "SELL", "price": price, "qty": lots,
                "balance": self.balance, "timestamp": ts,
                "comment": "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _close_pos(self, price: float, ts, reason: str = "REVERSAL") -> Dict:
        """Fecha posição ao preço de open (reversão ou exit)."""
        # Cancela exit pendente — entry vence
        self._xe = False

        if self.position_size > 0:
            qty = self.position_size
            pnl = (price - self.position_price) * qty
            side = "EXIT_LONG"
        else:
            qty = abs(self.position_size)
            pnl = (self.position_price - price) * qty
            side = "EXIT_SHORT"

        self.net_profit += pnl
        self.balance     = self.initial_capital + self.net_profit
        self._reset_pos()
        return {"action": side, "price": price, "qty": qty, "pnl": pnl,
                "balance": self.balance, "timestamp": ts, "exit_reason": reason,
                "comment": f"EXIT-{'LONG' if side=='EXIT_LONG' else 'SHORT'}_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _do_exit(self, open_p: float, ts) -> Optional[Dict]:
        """
        Executa exit por trailing/SL.
        Pine calc_on_every_tick=false, slippage=0:
          exec_price = open_p (sempre o open da próxima barra)
        """
        side = self._xe_side
        rsn  = self._xe_rsn

        if side == "long" and self.position_size > 0:
            qty = self.position_size
            pnl = (open_p - self.position_price) * qty
            self.net_profit += pnl
            self.balance     = self.initial_capital + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_LONG", "price": open_p, "qty": qty,
                    "pnl": pnl, "balance": self.balance,
                    "timestamp": ts, "exit_reason": rsn,
                    "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

        elif side == "short" and self.position_size < 0:
            qty = abs(self.position_size)
            pnl = (self.position_price - open_p) * qty
            self.net_profit += pnl
            self.balance     = self.initial_capital + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_SHORT", "price": open_p, "qty": qty,
                    "pnl": pnl, "balance": self.balance,
                    "timestamp": ts, "exit_reason": rsn,
                    "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}
        return None

    def _reset_pos(self):
        self.position_size    = 0.0
        self.position_price   = 0.0
        self._highest         = 0.0
        self._lowest          = float('inf')
        self._trailing_active = False
        self._exit_monitored  = False
        self._xe              = False
        self._xe_side         = ""

    # ═════════════════════════════════════════════════════════════════════
    # MAIN — chamado pelo engine para cada candle
    # ═════════════════════════════════════════════════════════════════════
    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle. Retorna lista de ações (trades).

        SEQUÊNCIA FIEL AO PINE:
        ┌─────────────────────────────────────────────────────────────┐
        │ OPEN:  executa ordens agendadas no close anterior           │
        │ CLOSE: calcula indicadores → sinais → trailing → agenda     │
        └─────────────────────────────────────────────────────────────┘
        """
        self._bar += 1
        in_warmup = (self._bar <= self.warmup_bars)

        open_p = candle['open']
        h      = candle['high']
        l      = candle['low']
        src    = candle['close']
        ts     = candle.get('timestamp', self._bar)
        idx    = candle.get('index',     self._bar)

        actions: List[Dict] = []

        # ── OPEN ──────────────────────────────────────────────────────────
        if not in_warmup:
            actions.extend(self._execute_open(open_p, ts))
        else:
            # Warmup: cancela ordens sem executar
            self._el = self._es = False

        # ── CLOSE: IFM ────────────────────────────────────────────────────
        if self.force_period is not None:
            self.Period = max(1, self.force_period)
        else:
            if self.adaptive_method in ("Cos IFM", "Average"):
                lenC = self._cosine_ifm(src)
            if self.adaptive_method in ("I-Q IFM", "Average"):
                lenIQ = self._iq_ifm(src)

            if   self.adaptive_method == "Cos IFM":
                self.Period = max(1, int(round(self._lenC)))
            elif self.adaptive_method == "I-Q IFM":
                self.Period = max(1, int(round(self._lenIQ)))
            elif self.adaptive_method == "Average":
                self.Period = max(1, int(round((self._lenC + self._lenIQ)/2)))

        # ── CLOSE: ZLEMA ──────────────────────────────────────────────────
        ema_prv, ec_prv, ema, ec = self._zlema(src, self.Period)

        # ── CLOSE: Sinais ─────────────────────────────────────────────────
        # Pine v3: crossover(EC,EMA)  = EC[1] <= EMA[1] AND EC > EMA
        #          crossunder(EC,EMA) = EC[1] >= EMA[1] AND EC < EMA
        buy_sig  = (ec_prv <= ema_prv) and (ec > ema)
        sell_sig = (ec_prv >= ema_prv) and (ec < ema)

        # Filtro threshold: 100*LeastError/src > Threshold (0 → sempre passa)
        if self.threshold > 0:
            err = 100.0 * self.LeastError / src if src != 0 else 0.0
            buy_sig  = buy_sig  and (err > self.threshold)
            sell_sig = sell_sig and (err > self.threshold)

        # ── CLOSE: Trailing stop (strategy.exit verifica HIGH/LOW) ────────
        # Pine: monitora a cada barra enquanto posição aberta
        # Detectado no close → exit agendado → executa no OPEN seguinte
        self._check_trail(h, l)

        # ── CLOSE: Agenda entries ─────────────────────────────────────────
        # (usa sinais da barra ANTERIOR: buy_signal[1] = self._buy_prev)
        self._schedule_entries(in_warmup, idx)

        # ── Salva sinais para próxima barra ───────────────────────────────
        self._buy_prev  = buy_sig
        self._sell_prev = sell_sig

        # ── Log periódico ─────────────────────────────────────────────────
        if idx % 200 == 0:
            wu = "[WU]" if in_warmup else "    "
            print(
                f"{wu}[{idx:5d}] P={self.Period:3d} "
                f"EC={ec:.4f} EMA={ema:.4f} "
                f"xo={buy_sig} xu={sell_sig} "
                f"pos={self.position_size:+.4f} "
                f"trail={'ON' if self._trailing_active else 'off'} "
                f"xe={self._xe} el={self._el} es={self._es} "
                f"pB={self._pBuy} pS={self._pSell} "
                f"bal={self.balance:.2f}"
            )

        return actions
