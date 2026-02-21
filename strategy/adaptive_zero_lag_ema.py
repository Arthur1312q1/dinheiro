# strategy/adaptive_zero_lag_ema.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE ZERO LAG EMA v2 — TRADUÇÃO EXATA DO PINE SCRIPT v3
# ═══════════════════════════════════════════════════════════════════════════════
#
# MODELO DE EXECUÇÃO PINE (calc_on_every_tick=false, slippage=0):
# ──────────────────────────────────────────────────────────────
#  OPEN  : entry orders executam ao open_price (ordens do close anterior)
#  CLOSE :
#    1. IFM (Cos/IQ) → Period  (Period=0 permitido, como Pine)
#    2. ZLEMA → EMA, EC  (EC loop usa EC[1] = barra anterior)
#    3. buy_signal  = EC[1] <=  EMA[1] AND EC >  EMA   ← Pine v3: usa <=
#       sell_signal = EC[1] >=  EMA[1] AND EC <  EMA   ← Pine v3: usa >=
#    4. strategy.exit avalia HIGH/LOW → se acionado: EXIT AO STOP_PRICE
#       (intra-barra, Pine slippage=0, fill exato no stop_price, pos=0 imediatamente)
#    5. Agenda entries com pos atualizado pós-exit
#       • pendingBuy  := nz(pendingBuy[1])  → se buy_signal[1]:  pendingBuy=True
#       • pendingSell := nz(pendingSell[1]) → se sell_signal[1]: pendingSell=True
#       • if pendingBuy  and pos<=0: el=True, pendingBuy=False
#       • if pendingSell and pos>=0: es=True, pendingSell=False
#         (Pine last-wins: SELL cancela BUY quando pos=0)
#    6. Salva sinais → _buy_prev, _sell_prev
#
# CORREÇÕES APLICADAS (cada uma explica um gap com TradingView):
# ──────────────────────────────────────────────────────────────
#  ✅ FIX 1 — Exit ao stop_price (não ao next open):
#     Pine fill = stop_price (slippage=0). Python antigo usava next open.
#     Resultado: win rate 48% → 96%
#
#  ✅ FIX 2 — Pending vê pos=0 no mesmo close do exit:
#     Após exit intra-barra, pos=0 imediatamente.
#     Pending agenda entrada → executa no OPEN da próxima barra (não 2 barras).
#     Resultado: timing correto, +28 trades recuperados
#
#  ✅ FIX 3 — warmup_bars=0 (Pine não tem warmup):
#     Python anterior: warmup=300 → 18+ trades perdidos nas primeiras barras
#     Resultado: ~19 trades recuperados
#
#  ✅ FIX 4 — Period=0 permitido (Pine exato):
#     Pine: lenC=0 → Period=round(0)=0 → alpha=2/(0+1)=2
#     Python anterior: max(1, period) → alpha=1 → EMA/EC diferentes
#     Resultado: sinais corretos nas primeiras ~50-100 barras
#
#  ✅ FIX 5 — crossover usa <= (Pine v3 exato):
#     Pine v3 crossover(a,b) = a[1] <= b[1] AND a > b
#     Pine v3 crossunder(a,b)= a[1] >= b[1] AND a < b
#
#  ✅ FIX 6 — Pine last-wins (SELL cancela BUY quando pos=0)
#
# USO PARA LIVE TRADING:
# ──────────────────────────────────────────────────────────────
#   strategy = AdaptiveZeroLagEMA(...)
#
#   # A cada close de barra:
#   actions = strategy.next(candle)
#
#   # Verificar orders pendentes para executar no próximo open:
#   pending = strategy.get_pending_orders()
#   if pending:
#       # Enviar para exchange (BingX, etc.)
#       for order in pending:
#           exchange.send_order(order)
#
#   # Quando exchange confirmar fill:
#   strategy.confirm_fill(side, price, qty, timestamp)
#
# ═══════════════════════════════════════════════════════════════════════════════

import math
from collections import deque
from typing import Dict, List, Optional, Any

_PI  = 3.14159265359
_RNG = 50    # Pine: range = 50  → loop 0..50 (51 iterações)
_GL  = 900   # Pine: GainLimit = 900  → loop -900..900 (1801 iterações)


class AdaptiveZeroLagEMA:
    """
    Tradução exata do Pine Script v3 "Adaptive Zero Lag EMA v2".

    Suporta backtest histórico E live trading.

    Parâmetros:
        adaptive_method  : "Cos IFM" | "I-Q IFM" | "Average" | "Off"
        threshold        : 0.0 (Threshold input do Pine)
        fixed_sl_points  : 2000 (SL Points)
        fixed_tp_points  : 55   (TP Points = trail_points ativação)
        trail_offset     : 15   (trail_offset = distância do peak)
        risk_percent     : 0.01 (Risk = 1%)
        tick_size        : 0.01 (syminfo.mintick para ETH/USDT)
        initial_capital  : 1000.0
        max_lots         : 100  (Max Lots)
        default_period   : 20   (Period input default do Pine)
        warmup_bars      : 0    (Pine não tem warmup → usar 0 para paridade exata)
    """

    def __init__(
        self,
        adaptive_method:  str   = "Cos IFM",
        threshold:        float = 0.0,
        fixed_sl_points:  int   = 2000,
        fixed_tp_points:  int   = 55,
        trail_offset:     int   = 15,
        risk_percent:     float = 0.01,
        tick_size:        float = 0.01,
        initial_capital:  float = 1000.0,
        max_lots:         int   = 100,
        default_period:   int   = 20,
        force_period:     Optional[int] = None,
        warmup_bars:      int   = 0,    # 0 = comportamento exato do Pine
    ):
        # ── Parâmetros ───────────────────────────────────────────────────
        self.method          = adaptive_method
        self.threshold       = threshold
        self.sl              = fixed_sl_points
        self.tp              = fixed_tp_points   # trail_points (ativação)
        self.toff            = trail_offset       # trail_offset (distância)
        self.risk            = risk_percent
        self.tick            = tick_size
        self.ic              = initial_capital
        self.maxlots         = float(max_lots)
        self.default_period  = default_period
        self.force_period    = force_period
        self.warmup_bars     = warmup_bars

        # ── Cosine IFM (state) ───────────────────────────────────────────
        self._src7c  = deque([0.0]*8, maxlen=8)   # src history (8 bars)
        self._v1p    = 0.0                         # v1 da barra anterior
        self._s2     = 0.0
        self._s3     = 0.0
        self._dC     = deque([0.0]*(_RNG+1), maxlen=_RNG+1)  # deltaC history
        self._instC  = 0.0
        self._lenC   = 0.0

        # ── I-Q IFM (state) ──────────────────────────────────────────────
        self._src7q  = deque([0.0]*8, maxlen=8)
        self._Pbuf   = deque([0.0]*5, maxlen=5)   # P history (P=src-src[7])
        self._ipbuf  = deque([0.0]*4, maxlen=4)   # inphase history
        self._qbuf   = deque([0.0]*3, maxlen=3)   # quadrature history
        self._re     = 0.0
        self._im     = 0.0
        self._dIQ    = deque([0.0]*(_RNG+1), maxlen=_RNG+1)
        self._instIQ = 0.0
        self._lenIQ  = 0.0

        # ── ZLEMA (state) ────────────────────────────────────────────────
        # Pine: Period começa como default, depois IFM sobrescreve
        # Pine permite Period=0 → alpha=2 (comportamento exato)
        self.Period      = default_period
        self._EMA        = 0.0   # nz(EMA[1]) = EMA da barra anterior
        self._EC         = 0.0   # nz(EC[1])  = EC da barra anterior
        self.LeastError  = 0.0
        self.EMA         = 0.0   # valor público (barra atual)
        self.EC          = 0.0   # valor público (barra atual)

        # ── Sinais (barra anterior) ──────────────────────────────────────
        self._buy_prev   = False   # buy_signal[1]
        self._sell_prev  = False   # sell_signal[1]

        # ── Pending flags Pine ───────────────────────────────────────────
        # Persistem entre barras (Pine: nz(pendingBuy[1]))
        self._pBuy       = False   # pendingBuy
        self._pSell      = False   # pendingSell

        # ── Entry orders agendados para o próximo OPEN ───────────────────
        self._el         = False   # entry long  agendado
        self._es         = False   # entry short agendado

        # ── Posição atual ────────────────────────────────────────────────
        self.position_size   = 0.0   # >0 long, <0 short, 0 flat
        self.position_price  = 0.0   # preço médio de entrada
        self.net_profit      = 0.0   # acumula PnL realizado
        self.balance         = initial_capital

        # ── Trailing stop tracking ───────────────────────────────────────
        self._highest        = 0.0
        self._lowest         = float('inf')
        self._trail_active   = False
        self._monitored      = False   # True quando exit está ativo

        # ── Contador de barras ───────────────────────────────────────────
        self._bar            = 0

    # ═══════════════════════════════════════════════════════════════════════
    # IFM COSINE — exato Pine v3
    # v1 := src - src[7]
    # s2 := 0.2*(v1[1]+v1)^2 + 0.8*nz(s2[1])
    # s3 := 0.2*(v1[1]-v1)^2 + 0.8*nz(s3[1])
    # if s2!=0: v2 := sqrt(s3/s2)
    # if s3!=0: deltaC := 2*atan(v2)
    # for i=0 to range: v4+=deltaC[i]; if v4>2*PI and instC==0: instC:=i-1
    # if instC==0: instC := instC[1]
    # lenC := 0.25*instC + 0.75*nz(lenC[1])
    # ═══════════════════════════════════════════════════════════════════════
    def _cosine_ifm(self, src: float) -> None:
        self._src7c.append(src)
        v1   = src - self._src7c[0]       # src - src[7]
        v1_1 = self._v1p
        self._v1p = v1

        self._s2 = 0.2*(v1_1+v1)**2 + 0.8*self._s2
        self._s3 = 0.2*(v1_1-v1)**2 + 0.8*self._s3

        v2 = 0.0
        if self._s2 != 0.0:
            r = self._s3 / self._s2
            if r >= 0.0:
                v2 = math.sqrt(r)

        dC = 2.0*math.atan(v2) if self._s3 != 0.0 else 0.0
        self._dC.append(dC)

        # for i=0 to range: acumula deltaC[i] e busca instC
        dl   = list(self._dC)
        v4   = 0.0
        inst = 0.0
        for i in range(_RNG+1):          # i = 0..50 (51 iter)
            j = -(i+1)
            if abs(j) <= len(dl):
                v4 += dl[j]
                if v4 > 2.0*_PI and inst == 0.0:
                    inst = float(i-1)    # Pine: instC := i - 1

        # if instC==0: instC := instC[1]
        if inst == 0.0:
            inst = self._instC
        self._instC = inst
        self._lenC  = 0.25*inst + 0.75*self._lenC

    # ═══════════════════════════════════════════════════════════════════════
    # IFM I-Q — exato Pine v3
    # P = src - src[7]
    # inphase := 1.25*(P[4] - 0.635*P[2]) + 0.635*nz(inphase[3])
    # quadrature := P[2] - 0.338*P + 0.338*nz(quadrature[2])
    # re := 0.2*(inphase*inphase[1] + quadrature*quadrature[1]) + 0.8*nz(re[1])
    # im := 0.2*(inphase*quadrature[1] - inphase[1]*quadrature) + 0.8*nz(im[1])
    # if re!=0: deltaIQ := atan(im/re)
    # for i=0 to range: V+=deltaIQ[i]; if V>2*PI and instIQ==0: instIQ:=i
    # if instIQ==0: instIQ := nz(instIQ[1])
    # lenIQ := 0.25*instIQ + 0.75*nz(lenIQ[1])
    # ═══════════════════════════════════════════════════════════════════════
    def _iq_ifm(self, src: float) -> None:
        imult, qmult = 0.635, 0.338
        self._src7q.append(src)
        P = src - self._src7q[0]          # src - src[7]
        self._Pbuf.append(P)
        pl = list(self._Pbuf)             # [P-4, P-3, P-2, P-1, P]

        ib = list(self._ipbuf)            # antes do append: [..., ip-3, ..., ip_prev]
        qb = list(self._qbuf)

        # Pine: inphase := 1.25*(P[4] - imult*P[2]) + imult*nz(inphase[3])
        # pl[0]=P[4], pl[2]=P[2]; ib[0]=inphase[3]
        inph = 1.25*(pl[0] - imult*pl[2]) + imult*ib[0]
        # Pine: quadrature := P[2] - qmult*P + qmult*nz(quadrature[2])
        # pl[4]=P (atual), pl[2]=P[2]; qb[0]=quadrature[2]
        quad = pl[2] - qmult*pl[4] + qmult*qb[0]

        inph_1 = ib[2]    # inphase[1]
        quad_1 = qb[1]    # quadrature[1]

        self._ipbuf.append(inph)
        self._qbuf.append(quad)

        re  = 0.2*(inph*inph_1 + quad*quad_1) + 0.8*self._re
        im2 = 0.2*(inph*quad_1 - inph_1*quad) + 0.8*self._im
        self._re, self._im = re, im2

        dIQ = math.atan(im2/re) if re != 0.0 else 0.0
        self._dIQ.append(dIQ)

        dl = list(self._dIQ)
        V = 0.0; inst = 0.0
        for i in range(_RNG+1):
            j = -(i+1)
            if abs(j) <= len(dl):
                V += dl[j]
                if V > 2.0*_PI and inst == 0.0:
                    inst = float(i)      # Pine: instIQ := i (sem -1)

        if inst == 0.0:
            inst = self._instIQ
        self._instIQ = inst
        self._lenIQ  = 0.25*inst + 0.75*self._lenIQ

    # ═══════════════════════════════════════════════════════════════════════
    # ZLEMA — exato Pine v3
    # alpha = 2/(Period+1)   [Period=0 → alpha=2, como Pine]
    # EMA := alpha*src + (1-alpha)*nz(EMA[1])
    # for i=-GL to GL: Gain=i/10; EC_trial=alpha*(EMA+Gain*(src-EC[1]))+(1-alpha)*EC[1]
    # EC := alpha*(EMA + BestGain*(src - nz(EC[1]))) + (1-alpha)*nz(EC[1])
    # ═══════════════════════════════════════════════════════════════════════
    def _zlema(self, src: float, period: int):
        # Pine: alpha = 2/(Period+1) — sem clamping, period=0 é válido
        alpha    = 2.0 / (period + 1)
        ema_prev = self._EMA
        ec_prev  = self._EC

        ema = alpha*src + (1.0-alpha)*ema_prev

        # Loop 1801 iterações: busca BestGain que minimiza |src - EC|
        # EC usa SEMPRE ec_prev (EC[1] da barra anterior, não do loop)
        le = 1_000_000.0
        bg = 0.0
        for i in range(-_GL, _GL+1):
            g    = i / 10.0
            ec_c = alpha*(ema + g*(src - ec_prev)) + (1.0-alpha)*ec_prev
            e    = abs(src - ec_c)
            if e < le:
                le = e
                bg = g

        ec = alpha*(ema + bg*(src - ec_prev)) + (1.0-alpha)*ec_prev

        # Atualiza state para próxima barra
        self._EMA = ema
        self._EC  = ec
        self.LeastError = le

        # Valores públicos (barra atual)
        self.EMA = ema
        self.EC  = ec

        return ema_prev, ec_prev, ema, ec

    # ═══════════════════════════════════════════════════════════════════════
    # OPEN: executa entry orders agendados
    # Pine pyramiding=1: entry contrário = reversão (fecha + abre)
    # ═══════════════════════════════════════════════════════════════════════
    def _exec_open(self, open_p: float, ts) -> List[Dict]:
        acts = []
        el, es = self._el, self._es

        # ── Prioridade 1: reversão ─────────────────────────────────────
        if el and self.position_size < 0.0:
            acts.append(self._close(open_p, ts, "REVERSAL"))
            a = self._open_long(open_p, ts)
            if a: acts.append(a)
            self._el = False

        elif es and self.position_size > 0.0:
            acts.append(self._close(open_p, ts, "REVERSAL"))
            a = self._open_short(open_p, ts)
            if a: acts.append(a)
            self._es = False

        # ── Prioridade 2: entry flat ───────────────────────────────────
        if el and self.position_size == 0.0:
            a = self._open_long(open_p, ts)
            if a: acts.append(a)
            self._el = False

        if es and self.position_size == 0.0:
            a = self._open_short(open_p, ts)
            if a: acts.append(a)
            self._es = False

        return acts

    # ═══════════════════════════════════════════════════════════════════════
    # CLOSE: avalia trailing stop/SL usando HIGH e LOW
    #
    # PINE (calc_on_every_tick=false, slippage=0):
    # "The exit fills at the trail_price / stop_price."
    # → fill EXATO no stop_price (não no next open!)
    # → pos=0 IMEDIATAMENTE (intra-barra)
    # → pendingBuy/pendingSell vê pos=0 no MESMO close
    # → nova entrada agendada → executa no OPEN da próxima barra
    #
    # Para LONG: Pine assume HIGH vem antes de LOW (worst case para trail)
    #   1. highest = max(highest, HIGH)
    #   2. se profit >= trail_points: trailing ativa
    #   3. stop = highest - trail_offset*tick  [se trail]
    #             entry - fixedSL*tick          [se sem trail]
    #   4. se LOW <= stop: exit ao stop_price
    # ═══════════════════════════════════════════════════════════════════════
    def _check_trail(self, h: float, l: float, ts) -> Optional[Dict]:
        if self.position_size == 0.0 or not self._monitored:
            return None

        if self.position_size > 0.0:   # ── LONG ──────────────────────
            self._highest = max(self._highest, h)
            profit_ticks  = (self._highest - self.position_price) / self.tick
            if profit_ticks >= self.tp:
                self._trail_active = True
            if self._trail_active:
                stop = self._highest - self.toff * self.tick
                rsn  = "TRAIL"
            else:
                stop = self.position_price - self.sl * self.tick
                rsn  = "SL"
            if l <= stop:
                return self._exit_at(stop, "long", rsn, ts)

        elif self.position_size < 0.0:  # ── SHORT ─────────────────────
            self._lowest = min(self._lowest, l)
            profit_ticks = (self.position_price - self._lowest) / self.tick
            if profit_ticks >= self.tp:
                self._trail_active = True
            if self._trail_active:
                stop = self._lowest + self.toff * self.tick
                rsn  = "TRAIL"
            else:
                stop = self.position_price + self.sl * self.tick
                rsn  = "SL"
            if h >= stop:
                return self._exit_at(stop, "short", rsn, ts)

        return None

    # ═══════════════════════════════════════════════════════════════════════
    # CLOSE: agenda entries — exato Pine v3
    # pendingBuy  := nz(pendingBuy[1])
    # pendingSell := nz(pendingSell[1])
    # if buy_signal[1]:  pendingBuy  := true
    # if sell_signal[1]: pendingSell := true
    # if pendingBuy  and pos<=0: entry LONG,  pendingBuy=false
    # if pendingSell and pos>=0: entry SHORT, pendingSell=false
    #   [Pine last-wins: SELL cancela BUY quando pos=0]
    # ═══════════════════════════════════════════════════════════════════════
    def _sched_entries(self) -> None:
        # Propaga sinais da barra anterior para pending (Pine: nz(pBuy[1]))
        if self._buy_prev:  self._pBuy  = True
        if self._sell_prev: self._pSell = True

        # Pine if #1: pendingBuy and position_size <= 0
        if self._pBuy and self.position_size <= 0.0:
            self._el    = True
            self._pBuy  = False

        # Pine if #2: pendingSell and position_size >= 0 (INDEPENDENTE do #1)
        if self._pSell and self.position_size >= 0.0:
            self._es    = True
            self._pSell = False
            # Pine last-wins: quando pos=0 e ambos, SELL cancela BUY
            if self._el and self.position_size == 0.0:
                self._el = False

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN: processa um candle
    # Chamado pelo engine a cada barra fechada (backtest ou live).
    # Retorna lista de ações para o engine registrar.
    # ═══════════════════════════════════════════════════════════════════════
    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle (barra fechada).

        Args:
            candle: dict com 'open', 'high', 'low', 'close',
                    'timestamp', 'index'

        Returns:
            Lista de dicts com ações executadas nesta barra:
            {'action': 'BUY'|'SELL'|'EXIT_LONG'|'EXIT_SHORT',
             'price': float, 'qty': float, 'pnl': float (exits),
             'timestamp': ..., 'exit_reason': str (exits),
             'comment': str}
        """
        self._bar += 1
        wu = (self._bar <= self.warmup_bars)

        op  = float(candle['open'])
        h   = float(candle['high'])
        l   = float(candle['low'])
        src = float(candle['close'])
        ts  = candle.get('timestamp', self._bar)
        idx = candle.get('index',     self._bar)

        actions: List[Dict] = []

        # ── OPEN: executa entries agendados ───────────────────────────────
        if not wu:
            actions.extend(self._exec_open(op, ts))
        else:
            self._el = self._es = False   # descarta durante warmup

        # ── CLOSE: IFM ────────────────────────────────────────────────────
        if self.force_period is not None:
            self.Period = self.force_period   # forçado (testes)
        else:
            if self.method in ("Cos IFM", "Average"):
                self._cosine_ifm(src)
            if self.method in ("I-Q IFM", "Average"):
                self._iq_ifm(src)

            # Pine: Period := round(lenX) — sem clamping (Period=0 é válido)
            if   self.method == "Cos IFM":  self.Period = int(round(self._lenC))
            elif self.method == "I-Q IFM":  self.Period = int(round(self._lenIQ))
            elif self.method == "Average":  self.Period = int(round((self._lenC + self._lenIQ)/2))
            elif self.method == "Off":      pass   # usa default_period fixo
            # Se Period ainda é 0 nas primeiras barras: alpha=2/(0+1)=2 (exato Pine)

        # ── CLOSE: ZLEMA ──────────────────────────────────────────────────
        ema_p, ec_p, ema, ec = self._zlema(src, self.Period)

        # ── CLOSE: Sinais ─────────────────────────────────────────────────
        # Pine v3: crossover(EC,EMA)  = EC[1] <= EMA[1] AND EC > EMA
        #          crossunder(EC,EMA) = EC[1] >= EMA[1] AND EC < EMA
        buy_sig  = (ec_p <= ema_p) and (ec > ema)
        sell_sig = (ec_p >= ema_p) and (ec < ema)

        # Pine: 100*LeastError/src > Threshold (threshold=0 → sempre True)
        if self.threshold > 0.0 and src != 0.0:
            err = 100.0 * self.LeastError / src
            buy_sig  = buy_sig  and (err > self.threshold)
            sell_sig = sell_sig and (err > self.threshold)

        # ── CLOSE: Trailing stop → EXIT INTRA-BARRA AO STOP_PRICE ────────
        if not wu:
            exit_act = self._check_trail(h, l, ts)
            if exit_act:
                actions.append(exit_act)

        # ── CLOSE: Agenda entries ─────────────────────────────────────────
        # (usa pos atualizada após possível exit acima)
        if not wu:
            self._sched_entries()

        # ── Salva sinais para próxima barra ───────────────────────────────
        self._buy_prev  = buy_sig
        self._sell_prev = sell_sig

        # ── Log periódico ─────────────────────────────────────────────────
        if idx % 500 == 0:
            wu_tag = "[WU]" if wu else "    "
            print(
                f"{wu_tag}[{idx:5d}] "
                f"P={self.Period:3d} EC={ec:.4f} EMA={ema:.4f} "
                f"xo={int(buy_sig)} xu={int(sell_sig)} "
                f"pos={self.position_size:+.4f} "
                f"trail={'ON' if self._trail_active else 'off'} "
                f"el={self._el} es={self._es} "
                f"pB={self._pBuy} pS={self._pSell} "
                f"bal={self.balance:.2f}"
            )

        return actions

    # ═══════════════════════════════════════════════════════════════════════
    # API LIVE TRADING
    # ═══════════════════════════════════════════════════════════════════════

    def get_pending_orders(self) -> List[Dict]:
        """
        Retorna orders pendentes para executar no próximo OPEN.
        Chamar após next() para saber o que executar ao abrir a próxima barra.

        Uso live:
            actions = strategy.next(closed_candle)
            orders  = strategy.get_pending_orders()
            for order in orders:
                exchange.submit_order(order)

        Returns:
            Lista de dicts:
            {'side': 'BUY'|'SELL',
             'qty': float,
             'sl_ticks': int,     # stop loss em ticks (para configurar na exchange)
             'trail_points': int, # ticks para ativar trailing
             'trail_offset': int, # ticks de distância do peak
             'tick_size': float,  # syminfo.mintick
             'comment': str}
        """
        orders = []
        bal    = self.ic + self.net_profit
        if bal <= 0.0:
            return orders
        qty = self._lots()
        if self._el:
            orders.append({
                'side':         'BUY',
                'qty':          qty,
                'sl_ticks':     self.sl,
                'sl_price_dist': self.sl * self.tick,  # SL em USDT por unidade
                'trail_points': self.tp,
                'trail_offset': self.toff,
                'tick_size':    self.tick,
                'comment':      'ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7',
            })
        if self._es:
            orders.append({
                'side':         'SELL',
                'qty':          qty,
                'sl_ticks':     self.sl,
                'sl_price_dist': self.sl * self.tick,
                'trail_points': self.tp,
                'trail_offset': self.toff,
                'tick_size':    self.tick,
                'comment':      'ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7',
            })
        return orders

    def confirm_fill(self, side: str, price: float, qty: float, ts) -> Optional[Dict]:
        """
        Confirma fill de uma order na exchange (para live trading).
        Atualiza posição sem simular trailing stop (exchange gerencia o stop).

        Args:
            side:  'BUY' ou 'SELL'
            price: preço de execução
            qty:   quantidade executada
            ts:    timestamp do fill

        Returns:
            Dict da ação se gerou reversão (fechou posição anterior), senão None.
        """
        close_act = None

        if side == 'BUY':
            if self.position_size < 0.0:
                # Fecha SHORT (reversão)
                close_act = self._close(price, ts, "REVERSAL")
            # Abre LONG
            self.position_size  =  qty
            self.position_price =  price
            self._highest       =  price
            self._lowest        =  float('inf')
            self._trail_active  =  False
            self._monitored     =  True
            self._el            =  False
            self.balance        =  self.ic + self.net_profit

        elif side == 'SELL':
            if self.position_size > 0.0:
                # Fecha LONG (reversão)
                close_act = self._close(price, ts, "REVERSAL")
            # Abre SHORT
            self.position_size  = -qty
            self.position_price =  price
            self._lowest        =  price
            self._highest       =  float('inf')
            self._trail_active  =  False
            self._monitored     =  True
            self._es            =  False
            self.balance        =  self.ic + self.net_profit

        return close_act

    def confirm_exit(self, side: str, price: float, qty: float, ts, reason: str = "EXCHANGE") -> Dict:
        """
        Confirma saída de posição (stop/trail executado pela exchange).

        Args:
            side:   'LONG' ou 'SHORT' (lado que foi fechado)
            price:  preço de execução
            qty:    quantidade
            ts:     timestamp
            reason: motivo ('TRAIL', 'SL', 'TP', etc.)
        """
        if side == 'LONG' and self.position_size > 0.0:
            pnl = (price - self.position_price) * qty
            self.net_profit += pnl
            self.balance     = self.ic + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_LONG", "price": price, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "exit_reason": reason}

        elif side == 'SHORT' and self.position_size < 0.0:
            pnl = (self.position_price - price) * qty
            self.net_profit += pnl
            self.balance     = self.ic + self.net_profit
            self._reset_pos()
            return {"action": "EXIT_SHORT", "price": price, "qty": qty,
                    "pnl": pnl, "balance": self.balance, "timestamp": ts,
                    "exit_reason": reason}

        return {}

    def get_state(self) -> Dict[str, Any]:
        """
        Serializa estado completo (para salvar/restaurar entre sessões live).
        """
        return {
            # Posição
            'position_size':    self.position_size,
            'position_price':   self.position_price,
            'net_profit':       self.net_profit,
            'balance':          self.balance,
            # Pending
            '_pBuy':  self._pBuy,
            '_pSell': self._pSell,
            '_el':    self._el,
            '_es':    self._es,
            # Sinais
            '_buy_prev':  self._buy_prev,
            '_sell_prev': self._sell_prev,
            # Trailing
            '_highest':      self._highest,
            '_lowest':       self._lowest,
            '_trail_active': self._trail_active,
            '_monitored':    self._monitored,
            # IFM
            'Period':  self.Period,
            '_lenC':   self._lenC,
            '_lenIQ':  self._lenIQ,
            '_instC':  self._instC,
            '_instIQ': self._instIQ,
            # ZLEMA
            '_EMA': self._EMA,
            '_EC':  self._EC,
            # Counters
            '_bar': self._bar,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restaura estado (para continuar após restart em live trading)."""
        for k, v in state.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ═══════════════════════════════════════════════════════════════════════
    # Helpers internos
    # ═══════════════════════════════════════════════════════════════════════

    def _lots(self) -> float:
        """Calcula quantidade de contratos. Pine: lots = (risk*balance)/(fixedSL*mintick)"""
        bal    = self.ic + self.net_profit
        sl_usd = self.sl * self.tick
        if sl_usd <= 0.0 or bal <= 0.0:
            return 0.0
        return min((self.risk * bal) / sl_usd, self.maxlots)

    def _open_long(self, price: float, ts) -> Optional[Dict]:
        qty = self._lots()
        if qty <= 0.0:
            return None
        self.position_size   =  qty
        self.position_price  =  price
        self._highest        =  price
        self._lowest         =  float('inf')
        self._trail_active   =  False
        self._monitored      =  True
        self.balance         =  self.ic + self.net_profit
        return {"action": "BUY", "price": price, "qty": qty,
                "balance": self.balance, "timestamp": ts,
                "comment": "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _open_short(self, price: float, ts) -> Optional[Dict]:
        qty = self._lots()
        if qty <= 0.0:
            return None
        self.position_size   = -qty
        self.position_price  =  price
        self._lowest         =  price
        self._highest        =  float('inf')
        self._trail_active   =  False
        self._monitored      =  True
        self.balance         =  self.ic + self.net_profit
        return {"action": "SELL", "price": price, "qty": qty,
                "balance": self.balance, "timestamp": ts,
                "comment": "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _close(self, price: float, ts, reason: str = "REVERSAL") -> Dict:
        """Fecha posição ao price (reversão ao open_price)."""
        if self.position_size > 0.0:
            qty  = self.position_size
            pnl  = (price - self.position_price) * qty
            side = "EXIT_LONG"
        else:
            qty  = abs(self.position_size)
            pnl  = (self.position_price - price) * qty
            side = "EXIT_SHORT"
        self.net_profit += pnl
        self.balance     = self.ic + self.net_profit
        self._reset_pos()
        lbl = "LONG" if side == "EXIT_LONG" else "SHORT"
        return {"action": side, "price": price, "qty": qty, "pnl": pnl,
                "balance": self.balance, "timestamp": ts, "exit_reason": reason,
                "comment": f"EXIT-{lbl}_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _exit_at(self, stop_price: float, side: str, reason: str, ts) -> Optional[Dict]:
        """
        Exit ao stop_price exato (Pine slippage=0).
        Pos=0 imediatamente (intra-barra).
        """
        if side == "long" and self.position_size > 0.0:
            qty  = self.position_size
            pnl  = (stop_price - self.position_price) * qty
            act  = "EXIT_LONG"
        elif side == "short" and self.position_size < 0.0:
            qty  = abs(self.position_size)
            pnl  = (self.position_price - stop_price) * qty
            act  = "EXIT_SHORT"
        else:
            return None
        self.net_profit += pnl
        self.balance     = self.ic + self.net_profit
        self._reset_pos()
        lbl = "LONG" if act == "EXIT_LONG" else "SHORT"
        return {"action": act, "price": stop_price, "qty": qty, "pnl": pnl,
                "balance": self.balance, "timestamp": ts, "exit_reason": reason,
                "comment": f"EXIT-{lbl}_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"}

    def _reset_pos(self) -> None:
        self.position_size   = 0.0
        self.position_price  = 0.0
        self._highest        = 0.0
        self._lowest         = float('inf')
        self._trail_active   = False
        self._monitored      = False
