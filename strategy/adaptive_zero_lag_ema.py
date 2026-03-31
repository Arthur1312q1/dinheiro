# strategy/adaptive_zero_lag_ema.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# ADAPTIVE ZERO LAG EMA v2 — TRADUÇÃO EXATA DO PINE SCRIPT v3
# ═══════════════════════════════════════════════════════════════════════════════
#
# CORREÇÕES APLICADAS:
# ────────────────────────────────────────────────────────────────────────────
#  FIX-2 (State Desync / Inversão de Sinais):
#    • Substituído `_bar % 2` por `_live_bar_count`, contador dedicado que
#      começa em 0 após o warmup → primeira barra live SEMPRE gera BUY.
#    • `_live_bar_count` é resetado no warmup (via main.py) garantindo
#      determinismo independente do horário de início.
#
#  FIX-3 (Falha no Fechamento Intrabar / High-Low Parity):
#    • `update_trailing_live` aceita `is_entry_candle=True` e
#      `current_price` (ticker/last price).
#    • Quando `is_entry_candle=True`:
#        - _highest/_lowest NÃO são atualizados pelo H/L histórico do candle.
#        - O stop é verificado contra `current_price` (preço real no momento
#          do poll), eliminando saídas falsas por preços anteriores ao fill.
#    • Início de `_highest`/`_lowest` a partir do preço de fill real
#      (já garantido por `_open_long`/`_open_short`).
#
#  FIX-12 (Mark Price Instantâneo no Trailing Stop — CORREÇÃO 1):
#    • `update_trailing_live` agora calcula `eff_high` e `eff_low` injetando
#      `current_price` (mark price em tempo real) nas extremas do candle.
#    • O gatilho de saída verifica AMBOS: eff_low/eff_high do candle E o
#      current_price diretamente, garantindo que o stop seja respeitado
#      mesmo quando o preço cruza o nível entre dois polls de candle REST.
#    • Elimina dessincronização entre o mark price real e as extremas do candle
#      que causava atrasos na execução de SL/Trail no modo live.
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
        # Stop prices calculados (acessíveis externamente para live trading)
        self.long_stop       = 0.0    # stop price ativo para posição LONG
        self.short_stop      = 0.0    # stop price ativo para posição SHORT

        # ── Contador de barras ───────────────────────────────────────────
        self._bar            = 0

        # ── FIX-2: Contador dedicado de barras live ───────────────────────
        # Separado de `_bar` para que o sinal alternado (BUY/SELL) seja
        # determinístico e independente do número de barras de warmup.
        # Sempre resetado para 0 antes do início live (via main.py warmup).
        # Regra: _live_bar_count ímpar → BUY, par (exceto 0) → SELL.
        self._live_bar_count = 0

    # ═══════════════════════════════════════════════════════════════════════
    # IFM COSINE — exato Pine v3
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

        dl   = list(self._dC)
        v4   = 0.0
        inst = 0.0
        for i in range(_RNG+1):
            j = -(i+1)
            if abs(j) <= len(dl):
                v4 += dl[j]
                if v4 > 2.0*_PI and inst == 0.0:
                    inst = float(i-1)

        if inst == 0.0:
            inst = self._instC
        self._instC = inst
        self._lenC  = 0.25*inst + 0.75*self._lenC

    # ═══════════════════════════════════════════════════════════════════════
    # IFM I-Q — exato Pine v3
    # ═══════════════════════════════════════════════════════════════════════
    def _iq_ifm(self, src: float) -> None:
        imult, qmult = 0.635, 0.338
        self._src7q.append(src)
        P = src - self._src7q[0]
        self._Pbuf.append(P)
        pl = list(self._Pbuf)

        ib = list(self._ipbuf)
        qb = list(self._qbuf)

        inph = 1.25*(pl[0] - imult*pl[2]) + imult*ib[0]
        quad = pl[2] - qmult*pl[4] + qmult*qb[0]

        inph_1 = ib[2]
        quad_1 = qb[1]

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
                    inst = float(i)

        if inst == 0.0:
            inst = self._instIQ
        self._instIQ = inst
        self._lenIQ  = 0.25*inst + 0.75*self._lenIQ

    # ═══════════════════════════════════════════════════════════════════════
    # ZLEMA — exato Pine v3
    # ═══════════════════════════════════════════════════════════════════════
    def _zlema(self, src: float, period: int):
        alpha    = 2.0 / (period + 1)
        ema_prev = self._EMA
        ec_prev  = self._EC

        ema = alpha*src + (1.0-alpha)*ema_prev

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

        self._EMA = ema
        self._EC  = ec
        self.LeastError = le

        self.EMA = ema
        self.EC  = ec

        return ema_prev, ec_prev, ema, ec

    # ═══════════════════════════════════════════════════════════════════════
    # OPEN: executa entry orders agendados
    # ═══════════════════════════════════════════════════════════════════════
    def _exec_open(self, open_p: float, ts) -> List[Dict]:
        acts = []
        el, es = self._el, self._es

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
    # ═══════════════════════════════════════════════════════════════════════
    def _check_trail(self, h: float, l: float, ts) -> Optional[Dict]:
        if self.position_size == 0.0 or not self._monitored:
            return None

        if self.position_size > 0.0:
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
            self.long_stop = stop
            if l <= stop:
                return self._exit_at(stop, "long", rsn, ts)

        elif self.position_size < 0.0:
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
            self.short_stop = stop
            if h >= stop:
                return self._exit_at(stop, "short", rsn, ts)

        return None

    # ═══════════════════════════════════════════════════════════════════════
    # CLOSE: agenda entries — exato Pine v3
    # ═══════════════════════════════════════════════════════════════════════
    def _sched_entries(self) -> None:
        if self._buy_prev:  self._pBuy  = True
        if self._sell_prev: self._pSell = True

        if self._pBuy and self.position_size <= 0.0:
            self._el    = True
            self._pBuy  = False

        if self._pSell and self.position_size >= 0.0:
            self._es    = True
            self._pSell = False
            if self._el and self.position_size == 0.0:
                self._el = False

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN: processa um candle
    # ═══════════════════════════════════════════════════════════════════════
    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle (barra fechada).

        Returns:
            Lista de dicts com ações executadas nesta barra.
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
            self._el = self._es = False

        # ── CLOSE: IFM ────────────────────────────────────────────────────
        if self.force_period is not None:
            self.Period = self.force_period
        else:
            if self.method in ("Cos IFM", "Average"):
                self._cosine_ifm(src)
            if self.method in ("I-Q IFM", "Average"):
                self._iq_ifm(src)

            if   self.method == "Cos IFM":  self.Period = int(round(self._lenC))
            elif self.method == "I-Q IFM":  self.Period = int(round(self._lenIQ))
            elif self.method == "Average":  self.Period = int(round((self._lenC + self._lenIQ)/2))

        # ── CLOSE: ZLEMA ──────────────────────────────────────────────────
        ema_p, ec_p, ema, ec = self._zlema(src, self.Period)

        # ── CLOSE: Sinais reais de crossover ─────────────────────────────
        buy_sig  = (ec_p <= ema_p) and (ec > ema)
        sell_sig = (ec_p >= ema_p) and (ec < ema)

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
        if not wu:
            self._sched_entries()

        # ── Salva sinais para próxima barra ───────────────────────────────
        if wu:
            # Durante warmup: usa sinais reais do crossover
            self._buy_prev  = buy_sig
            self._sell_prev = sell_sig
        else:
            # ── FIX-2: Usa _live_bar_count em vez de _bar % 2 ─────────────
            # _live_bar_count começa em 0 e é incrementado aqui.
            # Sempre resetado para 0 no warmup (main.py) → 1ª barra live = BUY.
            self._live_bar_count += 1
            if self._live_bar_count % 2 == 1:   # ímpar → BUY
                self._buy_prev  = True
                self._sell_prev = False
            else:                                # par   → SELL
                self._buy_prev  = False
                self._sell_prev = True

        # ── Log periódico ─────────────────────────────────────────────────
        if idx % 500 == 0:
            wu_tag = "[WU]" if wu else "    "
            print(
                f"{wu_tag}[{idx:5d}] "
                f"P={self.Period:3d} EC={ec:.4f} EMA={ema:.4f} "
                f"xo={int(self._buy_prev)} xu={int(self._sell_prev)} "
                f"pos={self.position_size:+.4f} "
                f"trail={'ON' if self._trail_active else 'off'} "
                f"el={self._el} es={self._es} "
                f"pB={self._pBuy} pS={self._pSell} "
                f"liveBar={self._live_bar_count} "
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
        """
        orders = []
        bal    = self.ic + self.net_profit
        if bal <= 0.0:
            return orders
        qty = self._lots()
        if self._el:
            orders.append({
                'side':          'BUY',
                'qty':           qty,
                'sl_ticks':      self.sl,
                'sl_price_dist': self.sl * self.tick,
                'trail_points':  self.tp,
                'trail_offset':  self.toff,
                'tick_size':     self.tick,
                'comment':       'ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7',
            })
        if self._es:
            orders.append({
                'side':          'SELL',
                'qty':           qty,
                'sl_ticks':      self.sl,
                'sl_price_dist': self.sl * self.tick,
                'trail_points':  self.tp,
                'trail_offset':  self.toff,
                'tick_size':     self.tick,
                'comment':       'ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7',
            })
        return orders

    def confirm_fill(self, side: str, price: float, qty: float, ts) -> Optional[Dict]:
        """
        Confirma fill de uma order na exchange (para live trading).
        """
        close_act = None

        if side == 'BUY':
            if self.position_size < 0.0:
                close_act = self._close(price, ts, "REVERSAL")
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
                close_act = self._close(price, ts, "REVERSAL")
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
        """Confirma saída de posição (stop/trail executado pela exchange)."""
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
        """Serializa estado completo (para salvar/restaurar entre sessões live)."""
        return {
            'position_size':    self.position_size,
            'position_price':   self.position_price,
            'net_profit':       self.net_profit,
            'balance':          self.balance,
            '_pBuy':            self._pBuy,
            '_pSell':           self._pSell,
            '_el':              self._el,
            '_es':              self._es,
            '_buy_prev':        self._buy_prev,
            '_sell_prev':       self._sell_prev,
            '_highest':         self._highest,
            '_lowest':          self._lowest,
            '_trail_active':    self._trail_active,
            '_monitored':       self._monitored,
            'Period':           self.Period,
            '_lenC':            self._lenC,
            '_lenIQ':           self._lenIQ,
            '_instC':           self._instC,
            '_instIQ':          self._instIQ,
            '_EMA':             self._EMA,
            '_EC':              self._EC,
            '_bar':             self._bar,
            '_live_bar_count':  self._live_bar_count,   # FIX-2: salva contador live
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
        # FIX-3: _highest parte exatamente do preço de fill real
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
        # FIX-3: _lowest parte exatamente do preço de fill real
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
        Pos=0 IMEDIATAMENTE (intra-barra).
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

    # ═══════════════════════════════════════════════════════════════════════
    # FIX-12: update_trailing_live — Mark Price Instantâneo (CORREÇÃO 1)
    # ═══════════════════════════════════════════════════════════════════════
    def update_trailing_live(
        self,
        high:             float,
        low:              float,
        ts,
        is_entry_candle:  bool  = False,
        current_price:    float = 0.0,
    ) -> Optional[Dict]:
        """
        Versão LIVE do trailing stop — atualiza _highest/_lowest e verifica saída.

        NÃO avança _bar, NÃO recalcula IFM/ZLEMA.
        Chamar a cada ~2 s com posição aberta, ~15 s sem posição.

        Args:
            high, low       : H/L do candle em formação (polling contínuo).
            ts              : timestamp do poll.
            is_entry_candle : True apenas no primeiro poll APÓS confirmação
                              de uma nova entrada (FIX-3 / FIX-4).
                              Quando True, usa `current_price` (ticker/last)
                              em vez do H/L histórico do candle para verificar
                              o stop — elimina saídas falsas por preços
                              ocorridos ANTES do fill da ordem.
            current_price   : mark price em tempo real (ticker/last).
                              FIX-12: injetado em eff_high/eff_low para
                              garantir que o stop seja verificado contra o
                              preço real, não apenas as extremas do candle REST
                              que possuem atraso de atualização.

        Returns:
            Dict de saída se stop foi atingido, None caso contrário.
        """
        if self.position_size == 0.0 or not self._monitored:
            return None

        # ── FIX-12: Injetar o Mark Price instantâneo nas extremas do candle ──
        # eff_high = máximo entre o high do candle REST e o preço atual de mercado
        # eff_low  = mínimo entre o low do candle REST e o preço atual de mercado
        # Isso garante que, mesmo que o candle REST ainda não tenha atualizado
        # suas extremas, o stop seja verificado com o preço real mais recente.
        eff_high = max(high, current_price) if current_price and current_price > 0 else high
        eff_low  = min(low,  current_price) if current_price and current_price > 0 else low
        eff_curr = current_price if current_price and current_price > 0 else None

        if is_entry_candle:
            # ── FIX-3: candle de entrada — usa ticker, não H/L histórico ──
            # _highest/_lowest já foram inicializados ao preço de fill exato
            # por _open_long/_open_short. Nenhuma atualização do pico aqui
            # para não contaminar com movimento anterior ao nosso fill.
            if not eff_curr:
                # Sem ticker disponível: não verifica (seguro — próximo poll fará)
                return None

            if self.position_size > 0.0:
                # Trail ainda não ativado no candle de entrada (profit=0)
                stop = self.position_price - self.sl * self.tick
                self.long_stop = stop
                if eff_curr <= stop:
                    return self._exit_at(stop, "long", "SL", ts)

            elif self.position_size < 0.0:
                stop = self.position_price + self.sl * self.tick
                self.short_stop = stop
                if eff_curr >= stop:
                    return self._exit_at(stop, "short", "SL", ts)

            return None

        # ── Poll normal (não-entry): usa eff_high/eff_low (candle + mark price) ──
        if self.position_size > 0.0:
            self._highest = max(self._highest, eff_high)
            profit_ticks  = (self._highest - self.position_price) / self.tick
            if profit_ticks >= self.tp:
                self._trail_active = True

            if self._trail_active:
                stop = self._highest - self.toff * self.tick
                rsn  = "TRAIL"
            else:
                stop = self.position_price - self.sl * self.tick
                rsn  = "SL"

            self.long_stop = stop

            # FIX-12: gatilho duplo — verifica eff_low (candle+mark) E eff_curr
            trigger = (eff_low <= stop)
            if eff_curr:
                trigger = trigger or (eff_curr <= stop)
            if trigger:
                return self._exit_at(stop, "long", rsn, ts)

        else:  # position_size < 0
            self._lowest  = min(self._lowest, eff_low)
            profit_ticks  = (self.position_price - self._lowest) / self.tick
            if profit_ticks >= self.tp:
                self._trail_active = True

            if self._trail_active:
                stop = self._lowest + self.toff * self.tick
                rsn  = "TRAIL"
            else:
                stop = self.position_price + self.sl * self.tick
                rsn  = "SL"

            self.short_stop = stop

            # FIX-12: gatilho duplo — verifica eff_high (candle+mark) E eff_curr
            trigger = (eff_high >= stop)
            if eff_curr:
                trigger = trigger or (eff_curr >= stop)
            if trigger:
                return self._exit_at(stop, "short", rsn, ts)

        return None
