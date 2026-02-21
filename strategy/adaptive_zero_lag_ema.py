# strategy/adaptive_zero_lag_ema.py
#
# TRADUÇÃO EXATA DO PINE SCRIPT v3 — MODELO DE EXECUÇÃO CORRETO
#
# BUG RAIZ IDENTIFICADO (e corrigido aqui):
# ──────────────────────────────────────────────────────────────
# ERRADO (versões anteriores):
#   trail/SL detectado no close de bar N → exit AGENDADO → executa no OPEN de N+1
#   → fill ao open_price → às vezes ABAIXO do entry (loss quando Pine ganha)
#   → pending vê pos=0 apenas no close de N+1 → 1 barra extra de delay
#
# CORRETO (Pine com calc_on_every_tick=false, slippage=0):
#   "strategy.exit() is triggered when low/high reaches trail_price.
#    The exit is assumed to happen AT the trail_price (slippage=0)."
#   → fill AO STOP_PRICE (não ao next open)
#   → pos=0 INTRA-BARRA no close de N → pending vê pos=0 imediatamente
#   → nova entrada agendada no MESMO close → executa no OPEN de N+1
#
# Por isso Pine tem:
#   • Win rate 80% (fill ao stop sempre positivo quando trail ativa)
#   • +28 trades (timing de entrada 1 barra mais cedo após exits)
#
# ──────────────────────────────────────────────────────────────
# Sequência CORRETA por barra:
#   OPEN:  executa entry orders (agendadas no close anterior)
#   CLOSE: 1. calcula IFM → Period
#           2. calcula ZLEMA → EMA, EC
#           3. buy_signal, sell_signal (crossover com <=)
#           4. verifica trail/SL usando H/L → se ativado: EXECUTA exit ao stop_price
#           5. agenda entries com pos atualizado (pós-exit se houve)
#           6. salva sinais para próxima barra

import math
from collections import deque
from typing import Dict, List, Optional

_PI  = 3.14159265359
_RNG = 50    # Pine: range = 50
_GL  = 900   # Pine: GainLimit = 900


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
        self.method   = adaptive_method
        self.threshold= threshold
        self.sl       = fixed_sl_points   # ticks
        self.tp       = fixed_tp_points   # ticks (trail_points = ativação)
        self.toff     = trail_offset       # ticks (trail_offset  = distância)
        self.risk     = risk_percent
        self.tick     = tick_size
        self.ic       = initial_capital
        self.maxlots  = float(max_lots)
        self.fp       = force_period
        self.warmup   = warmup_bars

        # ── Cosine IFM ─────────────────────────────────────────────
        self._src7  = deque([0.0]*8, maxlen=8)
        self._v1p   = 0.0
        self._s2 = self._s3 = 0.0
        self._dC    = deque([0.0]*(_RNG+1), maxlen=_RNG+1)
        self._instC = self._lenC = 0.0

        # ── I-Q IFM ────────────────────────────────────────────────
        self._src7q = deque([0.0]*8, maxlen=8)
        self._Pbuf  = deque([0.0]*5, maxlen=5)
        self._ipbuf = deque([0.0]*4, maxlen=4)
        self._qbuf  = deque([0.0]*3, maxlen=3)
        self._re = self._im = 0.0
        self._dIQ   = deque([0.0]*(_RNG+1), maxlen=_RNG+1)
        self._instIQ= self._lenIQ = 0.0

        # ── ZLEMA ───────────────────────────────────────────────────
        self.Period = 20
        self._EMA = self._EC = 0.0
        self.LeastError = 0.0

        # ── Sinais ─────────────────────────────────────────────────
        self._buy_prev  = False   # buy_signal[1]
        self._sell_prev = False   # sell_signal[1]

        # ── Pending flags (Pine: persistem entre barras) ─────────────
        self._pBuy  = False   # pendingBuy
        self._pSell = False   # pendingSell

        # ── Entry orders agendados para OPEN ───────────────────────
        self._el = False   # entry long
        self._es = False   # entry short

        # ── Posição ─────────────────────────────────────────────────
        self.position_size  = 0.0
        self.position_price = 0.0
        self.net_profit     = 0.0
        self.balance        = initial_capital

        # ── Trailing tracking ───────────────────────────────────────
        self._highest       = 0.0
        self._lowest        = float('inf')
        self._trail_active  = False
        self._monitored     = False   # True quando posição tem exit monitorado

        self._bar = 0

    # ═══════════════════════════════════════════════════════════════
    # COSINE IFM — exato Pine v3
    # ═══════════════════════════════════════════════════════════════
    def _cosine_ifm(self, src: float) -> None:
        self._src7.append(src)
        v1   = src - self._src7[0]
        v1_1 = self._v1p; self._v1p = v1
        self._s2 = 0.2*(v1_1+v1)**2 + 0.8*self._s2
        self._s3 = 0.2*(v1_1-v1)**2 + 0.8*self._s3
        v2 = 0.0
        if self._s2 != 0.0:
            r = self._s3/self._s2
            if r >= 0.0: v2 = math.sqrt(r)
        dC = 2.0*math.atan(v2) if self._s3 != 0.0 else 0.0
        self._dC.append(dC)
        dl = list(self._dC); v4 = 0.0; inst = 0.0
        for i in range(_RNG+1):
            j = -(i+1)
            if abs(j) <= len(dl):
                v4 += dl[j]
                if v4 > 2*_PI and inst == 0.0:
                    inst = float(i-1)
        if inst == 0.0: inst = self._instC
        self._instC = inst
        self._lenC  = 0.25*inst + 0.75*self._lenC

    # ═══════════════════════════════════════════════════════════════
    # I-Q IFM — exato Pine v3
    # ═══════════════════════════════════════════════════════════════
    def _iq_ifm(self, src: float) -> None:
        im, qm = 0.635, 0.338
        self._src7q.append(src)
        P = src - self._src7q[0]
        self._Pbuf.append(P)
        pl = list(self._Pbuf)
        ib = list(self._ipbuf); qb = list(self._qbuf)
        inph = 1.25*(pl[0] - im*pl[2]) + im*ib[0]
        quad = pl[2] - qm*pl[4] + qm*qb[0]
        inph_1 = ib[2]; quad_1 = qb[1]
        self._ipbuf.append(inph); self._qbuf.append(quad)
        re = 0.2*(inph*inph_1 + quad*quad_1) + 0.8*self._re
        im2= 0.2*(inph*quad_1 - inph_1*quad) + 0.8*self._im
        self._re, self._im = re, im2
        dIQ = math.atan(im2/re) if re != 0.0 else 0.0
        self._dIQ.append(dIQ)
        dl = list(self._dIQ); V = 0.0; inst = 0.0
        for i in range(_RNG+1):
            j = -(i+1)
            if abs(j) <= len(dl):
                V += dl[j]
                if V > 2*_PI and inst == 0.0:
                    inst = float(i)
        if inst == 0.0: inst = self._instIQ
        self._instIQ = inst
        self._lenIQ  = 0.25*inst + 0.75*self._lenIQ

    # ═══════════════════════════════════════════════════════════════
    # ZLEMA — exato Pine v3
    # EC no loop usa EC[1] (self._EC = barra anterior)
    # ═══════════════════════════════════════════════════════════════
    def _zlema(self, src: float, period: int):
        alpha   = 2.0 / (period + 1)
        ema_prv = self._EMA
        ec_prv  = self._EC
        ema = alpha*src + (1-alpha)*ema_prv
        le = 1_000_000.0; bg = 0.0
        for i in range(-_GL, _GL+1):
            g    = i/10.0
            ec_c = alpha*(ema + g*(src - ec_prv)) + (1-alpha)*ec_prv
            e    = abs(src - ec_c)
            if e < le: le = e; bg = g
        ec = alpha*(ema + bg*(src - ec_prv)) + (1-alpha)*ec_prv
        self._EMA = ema; self._EC = ec
        self.LeastError = le
        return ema_prv, ec_prv, ema, ec   # prev, prev, curr, curr

    # ═══════════════════════════════════════════════════════════════
    # OPEN: executa entries agendados
    # ═══════════════════════════════════════════════════════════════
    def _exec_open(self, open_p: float, ts) -> List[Dict]:
        acts = []
        el, es = self._el, self._es

        # Entry contrário → reversão (cancela exit monitorado implicitamente via _open_*)
        if el and self.position_size < 0:
            acts.append(self._close(open_p, ts, "REVERSAL"))
            a = self._open_long(open_p, ts); acts.append(a) if a else None
            self._el = False

        elif es and self.position_size > 0:
            acts.append(self._close(open_p, ts, "REVERSAL"))
            a = self._open_short(open_p, ts); acts.append(a) if a else None
            self._es = False

        # Entry flat (pos=0)
        if el and self.position_size == 0.0:
            a = self._open_long(open_p, ts); acts.append(a) if a else None
            self._el = False

        if es and self.position_size == 0.0:
            a = self._open_short(open_p, ts); acts.append(a) if a else None
            self._es = False

        return acts

    # ═══════════════════════════════════════════════════════════════
    # CLOSE: verifica trail/SL e executa INTRA-BARRA ao stop_price
    #
    # Pine (calc_on_every_tick=false, slippage=0):
    # "The exit is assumed to happen at the trail_price / stop_price"
    # Fill = stop_price, NÃO o open da próxima barra
    # Pos=0 IMEDIATAMENTE → pending vê pos=0 no mesmo close
    # ═══════════════════════════════════════════════════════════════
    def _check_trail_and_exit(self, h: float, l: float, ts) -> Optional[Dict]:
        if self.position_size == 0.0 or not self._monitored:
            return None

        if self.position_size > 0:   # ─── LONG ─────────────────
            self._highest = max(self._highest, h)
            pt = (self._highest - self.position_price) / self.tick
            if pt >= self.tp:
                self._trail_active = True
            if self._trail_active:
                stop = self._highest - self.toff * self.tick
                rsn  = "TRAIL"
            else:
                stop = self.position_price - self.sl * self.tick
                rsn  = "SL"
            if l <= stop:
                return self._exit_at_price(stop, "long", rsn, ts)

        elif self.position_size < 0:  # ─── SHORT ────────────────
            self._lowest = min(self._lowest, l)
            pt = (self.position_price - self._lowest) / self.tick
            if pt >= self.tp:
                self._trail_active = True
            if self._trail_active:
                stop = self._lowest + self.toff * self.tick
                rsn  = "TRAIL"
            else:
                stop = self.position_price + self.sl * self.tick
                rsn  = "SL"
            if h >= stop:
                return self._exit_at_price(stop, "short", rsn, ts)

        return None

    # ═══════════════════════════════════════════════════════════════
    # CLOSE: agenda entries — exato Pine v3
    # ═══════════════════════════════════════════════════════════════
    def _sched_entries(self, in_warmup: bool) -> None:
        # Propaga sinais da barra anterior para pending
        if self._buy_prev:  self._pBuy  = True
        if self._sell_prev: self._pSell = True

        if in_warmup:
            if self._pBuy  and self.position_size <= 0.0: self._pBuy  = False
            if self._pSell and self.position_size >= 0.0: self._pSell = False
            return

        # Pine if #1: pendingBuy and pos<=0
        if self._pBuy and self.position_size <= 0.0:
            self._el   = True
            self._pBuy = False

        # Pine if #2: pendingSell and pos>=0 (INDEPENDENTE do #1)
        if self._pSell and self.position_size >= 0.0:
            self._es    = True
            self._pSell = False
            # Pine last-wins: SELL cancela BUY quando pos=0
            if self._el and self.position_size == 0.0:
                self._el = False

    # ═══════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════
    def _lots(self) -> float:
        bal = self.ic + self.net_profit
        sl_usd = self.sl * self.tick
        if sl_usd <= 0.0 or bal <= 0.0: return 0.0
        return min((self.risk * bal) / sl_usd, self.maxlots)

    def _open_long(self, price: float, ts) -> Optional[Dict]:
        qty = self._lots()
        if qty <= 0.0: return None
        self.position_size  =  qty
        self.position_price =  price
        self._highest       =  price
        self._lowest        =  float('inf')
        self._trail_active  =  False
        self._monitored     =  True
        self.balance        =  self.ic + self.net_profit
        return {"action":"BUY","price":price,"qty":qty,
                "balance":self.balance,"timestamp":ts,
                "comment":"ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _open_short(self, price: float, ts) -> Optional[Dict]:
        qty = self._lots()
        if qty <= 0.0: return None
        self.position_size  = -qty
        self.position_price =  price
        self._lowest        =  price
        self._highest       =  float('inf')
        self._trail_active  =  False
        self._monitored     =  True
        self.balance        =  self.ic + self.net_profit
        return {"action":"SELL","price":price,"qty":qty,
                "balance":self.balance,"timestamp":ts,
                "comment":"ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _close(self, price: float, ts, reason: str = "REVERSAL") -> Dict:
        """Fecha posição ao preço especificado (reversão = open_p)."""
        if self.position_size > 0:
            qty = self.position_size
            pnl = (price - self.position_price) * qty
            side = "EXIT_LONG"
        else:
            qty = abs(self.position_size)
            pnl = (self.position_price - price) * qty
            side = "EXIT_SHORT"
        self.net_profit += pnl
        self.balance     = self.ic + self.net_profit
        self._reset_pos()
        lbl = "LONG" if side == "EXIT_LONG" else "SHORT"
        return {"action":side,"price":price,"qty":qty,"pnl":pnl,
                "balance":self.balance,"timestamp":ts,"exit_reason":reason,
                "comment":f"EXIT-{lbl}_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _exit_at_price(self, stop_price: float, side: str, reason: str, ts) -> Dict:
        """
        Executa exit AO STOP_PRICE (Pine slippage=0).
        Intra-barra: pos=0 imediatamente no close.
        """
        if side == "long" and self.position_size > 0:
            qty = self.position_size
            pnl = (stop_price - self.position_price) * qty
            act = "EXIT_LONG"
        elif side == "short" and self.position_size < 0:
            qty = abs(self.position_size)
            pnl = (self.position_price - stop_price) * qty
            act = "EXIT_SHORT"
        else:
            return None
        self.net_profit += pnl
        self.balance     = self.ic + self.net_profit
        self._reset_pos()
        lbl = "LONG" if act == "EXIT_LONG" else "SHORT"
        return {"action":act,"price":stop_price,"qty":qty,"pnl":pnl,
                "balance":self.balance,"timestamp":ts,"exit_reason":reason,
                "comment":f"EXIT-{lbl}_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _reset_pos(self):
        self.position_size  = 0.0
        self.position_price = 0.0
        self._highest       = 0.0
        self._lowest        = float('inf')
        self._trail_active  = False
        self._monitored     = False

    # ═══════════════════════════════════════════════════════════════
    # MAIN
    # ═══════════════════════════════════════════════════════════════
    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle. Retorna lista de ações (trades).

        MODELO PINE (calc_on_every_tick=false, slippage=0):
        ┌─────────────────────────────────────────────────────────────┐
        │ OPEN:  entry orders executam ao open_price                  │
        │ CLOSE: 1. IFM → Period                                      │
        │         2. ZLEMA → EMA, EC                                  │
        │         3. buy/sell_signal (crossover com <=)               │
        │         4. trail/SL → se ativado: EXIT ao stop_price        │
        │            (intra-barra, pos=0 imediatamente)               │
        │         5. agenda entries (vê pos atualizado pós-exit)      │
        │         6. salva sinais                                      │
        └─────────────────────────────────────────────────────────────┘
        """
        self._bar += 1
        wu = self._bar <= self.warmup

        op = candle['open']; h = candle['high']
        l  = candle['low'];  src = candle['close']
        ts = candle.get('timestamp', self._bar)
        idx= candle.get('index',     self._bar)

        acts: List[Dict] = []

        # ── OPEN: executa entries ──────────────────────────────────
        if not wu:
            acts.extend(self._exec_open(op, ts))
        else:
            self._el = self._es = False

        # ── CLOSE: IFM ────────────────────────────────────────────
        if self.fp is not None:
            self.Period = max(1, self.fp)
        else:
            if self.method in ("Cos IFM", "Average"):
                self._cosine_ifm(src)
            if self.method in ("I-Q IFM", "Average"):
                self._iq_ifm(src)
            if   self.method == "Cos IFM":  self.Period = max(1, int(round(self._lenC)))
            elif self.method == "I-Q IFM":  self.Period = max(1, int(round(self._lenIQ)))
            elif self.method == "Average":  self.Period = max(1, int(round((self._lenC+self._lenIQ)/2)))

        # ── CLOSE: ZLEMA ──────────────────────────────────────────
        ema_p, ec_p, ema, ec = self._zlema(src, self.Period)

        # ── CLOSE: Sinais (Pine v3: crossover usa <=) ─────────────
        buy_sig  = (ec_p <= ema_p) and (ec > ema)
        sell_sig = (ec_p >= ema_p) and (ec < ema)
        if self.threshold > 0 and src != 0:
            err = 100.0 * self.LeastError / src
            buy_sig  = buy_sig  and (err > self.threshold)
            sell_sig = sell_sig and (err > self.threshold)

        # ── CLOSE: Trail/SL → EXIT INTRA-BARRA AO STOP_PRICE ─────
        # Pine slippage=0: fill exato no stop_price, pos=0 imediatamente
        if not wu:
            exit_act = self._check_trail_and_exit(h, l, ts)
            if exit_act:
                acts.append(exit_act)

        # ── CLOSE: Agenda entries (vê pos atualizado após exit) ───
        self._sched_entries(wu)

        # ── Salva sinais ──────────────────────────────────────────
        self._buy_prev  = buy_sig
        self._sell_prev = sell_sig

        # ── Log periódico ─────────────────────────────────────────
        if idx % 200 == 0:
            print(
                f"{'[WU]' if wu else '    '}[{idx:5d}] "
                f"P={self.Period:3d} EC={ec:.4f} EMA={ema:.4f} "
                f"xo={int(buy_sig)} xu={int(sell_sig)} "
                f"pos={self.position_size:+.4f} "
                f"trail={'ON' if self._trail_active else 'off'} "
                f"el={self._el} es={self._es} "
                f"pB={self._pBuy} pS={self._pSell} "
                f"bal={self.balance:.2f}"
            )

        return acts
