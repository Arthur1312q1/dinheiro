# strategy/adaptive_zero_lag_ema.py
# ═══════════════════════════════════════════════════════════════════════════
# TRADUÇÃO FIEL DO PINE SCRIPT v3 → PYTHON
#
# CORREÇÕES DESTA VERSÃO (vs versão anterior):
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ BUG #1 - DELAY EXTRA DE 1 BARRA (CRÍTICO)                              │
# │                                                                         │
# │ Pine v3:                                                                │
# │   Barra N: buy_signal calculado, pending_buy setado por buy_signal[1]  │
# │   Barra N: if pending_buy and pos<=0 → entry.entry executa no OPEN     │
# │            da mesma barra onde pending foi avaliado                     │
# │            (Pine avalia tudo no fechamento, executa no open seguinte)  │
# │   Resultado: sinal em N-1 → pending em N → entry no open de N+1        │
# │                                                                         │
# │ Python anterior:                                                        │
# │   buy_signal_prev → pending_buy=True → entry_scheduled_long=True       │
# │   → entry no open seguinte                                              │
# │   Resultado: sinal em N-1 → pending em N → scheduled em N →            │
# │              entry no open de N+1 ✓ MESMO que Pine ✓                   │
# │                                                                         │
# │ AGUARDA: o problema real era o crossover/crossunder                     │
# └─────────────────────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ BUG #2 - CROSSOVER/CROSSUNDER COM <= AO INVÉS DE <                     │
# │                                                                         │
# │ Pine crossover(EC, EMA):                                                │
# │   EC[1] < EMA[1]  AND  EC > EMA  (strict less than)                    │
# │                                                                         │
# │ Python anterior:                                                        │
# │   xo = (ecp <= ep) and (ec > ema)  ← <= permite EC[1]==EMA[1]         │
# │                                                                         │
# │ Correção:                                                               │
# │   xo = (ecp < ep)  and (ec > ema)  ← strict <                         │
# │   xu = (ecp > ep)  and (ec < ema)  ← strict >                         │
# └─────────────────────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ BUG #3 - REVERSÃO: Pine strategy.entry() SEMPRE fecha posição contrária│
# │                                                                         │
# │ No Pine, strategy.entry() com pyramiding=1 fecha posição contrária     │
# │ automaticamente. A reversão usa o MESMO preço de entrada.              │
# │ Não há lógica separada de "fechar primeiro" — é atômico.               │
# │                                                                         │
# │ Python: implementação complexa com exit_scheduled bloqueando entries    │
# │ Simplificação: quando há entry contrário agendado sem exit_scheduled,  │
# │ fecha e entra atomicamente no open.                                     │
# └─────────────────────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ BUG #4 - PENDING SELL NÃO LIMPO APÓS REVERSÃO                          │
# │                                                                         │
# │ Quando pending_buy causa entry long enquanto há pending_sell ativo,     │
# │ pending_sell deve ser limpo (e vice-versa).                             │
# │ Pine: pyramiding=1, novos sinais sobrescrevem pendentes.               │
# └─────────────────────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ BUG #5 - TRAILING STOP: STOP PRICE vs OPEN PRICE                       │
# │                                                                         │
# │ Pine strategy.exit com trail:                                           │
# │   - O exit executa NO PREÇO DO STOP, não no open                       │
# │   - Se open < stop (gap down em long), usa open (slippage=0)           │
# │   - Se open > stop (gap up em long), usa stop (sem melhora)            │
# │                                                                         │
# │ Para SL fixo: mesmo comportamento                                       │
# │ slippage=0 no Pine → preço exato do stop ou open se gap                │
# └─────────────────────────────────────────────────────────────────────────┘
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │ BUG #6 - TRAILING: VERIFICAÇÃO NO HIGH/LOW DO CANDLE (intra-barra)     │
# │                                                                         │
# │ Pine verifica se trailing/SL foi tocado usando high/low do candle.     │
# │ Se high >= stop (short) ou low <= stop (long) → executa.               │
# │ O preço de execução é o stop price (ou open se gap).                   │
# │                                                                         │
# │ Mas o TradingView executa no OPEN da PRÓXIMA barra se calc_on_every_   │
# │ tick=false, pois orders são processadas no open.                        │
# │ Com calc_on_every_tick=false: stop detectado no close → executa no     │
# │ open da próxima barra ao stop_price (ou open se gap).                  │
# └─────────────────────────────────────────────────────────────────────────┘
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

    # ── I-Q IFM buffers ───────────────────────────────────────────────────
    inphase_buffer: deque = field(default_factory=lambda: deque(maxlen=4))
    quadrature_buffer: deque = field(default_factory=lambda: deque(maxlen=3))
    re_prev: float = 0.0
    im_prev: float = 0.0
    deltaIQ_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instIQ: float = 0.0
    lenIQ: float = 0.0

    # ── Cosine IFM buffers ────────────────────────────────────────────────
    v1_prev: float = 0.0
    s2: float = 0.0
    s3: float = 0.0
    deltaC_buffer: deque = field(default_factory=lambda: deque(maxlen=RANGE + 1))
    instC: float = 0.0
    lenC: float = 0.0

    # ── ZLEMA state ────────────────────────────────────────────────────────
    EMA: float = 0.0
    EC: float = 0.0
    LeastError: float = 0.0
    BestGain: float = 0.0
    Period: int = 20

    # ── Signal state ───────────────────────────────────────────────────────
    # Pine: pendingBuy/pendingSell persistem entre barras
    # buy_signal[1] no pine = buy_signal da barra anterior
    pending_buy: bool = False
    pending_sell: bool = False
    buy_signal_prev: bool = False
    sell_signal_prev: bool = False

    # entry_scheduled: agendado no close para executar no próximo open
    # (equivale ao Pine "if pending and pos<=0" avaliado no close)
    entry_scheduled_long: bool = False
    entry_scheduled_short: bool = False

    # exit_scheduled: trailing/SL tocado no candle → executa no próximo open
    exit_scheduled: bool = False
    exit_scheduled_side: str = ""
    exit_scheduled_reason: str = ""

    # ── Position state ────────────────────────────────────────────────────
    position_size: float = 0.0
    position_avg_price: float = 0.0
    net_profit: float = 0.0

    # ── Trailing state ────────────────────────────────────────────────────
    highest_price: float = 0.0
    lowest_price: float = 0.0
    trailing_active: bool = False
    exit_active: bool = False
    _stop_price: float = 0.0
    _bar_count: int = 0

    # ── Raw price buffers ─────────────────────────────────────────────────
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
    # IFM METHODS
    # ═══════════════════════════════════════════════════════════════════════

    def _calc_iq_ifm(self, src: float):
        """
        I-Q IFM idêntico ao Pine v3.
        
        Pine:
            P = src - src[7]
            inphase := 1.25*(P[4] - imult*P[2]) + imult*nz(inphase[3])
            quadrature := P[2] - qmult*P + qmult*nz(quadrature[2])
            re := 0.2*(inphase*inphase[1] + quadrature*quadrature[1]) + 0.8*nz(re[1])
            im := 0.2*(inphase*quadrature[1] - inphase[1]*quadrature) + 0.8*nz(im[1])
        """
        imult, qmult = 0.635, 0.338

        # src[7] = elemento mais antigo do buffer de 8 elementos
        self._src_buf_iq.append(src)
        P = src - self._src_buf_iq[0]  # src - src[7]

        self._P_buf.append(P)
        P_list = list(self._P_buf)
        # Após append: P_list = [P-4, P-3, P-2, P-1, P]
        P_4 = P_list[0]   # P[4] = 4 barras atrás
        P_2 = P_list[2]   # P[2] = 2 barras atrás
        P_0 = P_list[4]   # P[0] = P atual

        # inphase[1], inphase[3]
        ib = list(self.inphase_buffer)
        inphase_3 = ib[0]   # 3 barras atrás (buffer maxlen=4: [t-3,t-2,t-1,t-atual])
        inphase_1 = ib[2]   # 1 barra atrás (índice -2 antes do append atual)

        # quadrature[2], quadrature[1]
        qb = list(self.quadrature_buffer)
        quad_2 = qb[0]   # 2 barras atrás
        quad_1 = qb[1]   # 1 barra atrás

        inph = 1.25 * (P_4 - imult * P_2) + imult * inphase_3
        quad = P_2 - qmult * P_0 + qmult * quad_2

        self.inphase_buffer.append(inph)
        self.quadrature_buffer.append(quad)

        re = 0.2 * (inph * inphase_1 + quad * quad_1) + 0.8 * self.re_prev
        im = 0.2 * (inph * quad_1 - inphase_1 * quad) + 0.8 * self.im_prev
        self.re_prev = re
        self.im_prev = im

        dIQ = math.atan(im / re) if re != 0.0 else 0.0
        self.deltaIQ_buffer.append(dIQ)

        dl = list(self.deltaIQ_buffer)
        V = 0.0
        inst = 0.0
        # Pine: for i=0 to range; V += deltaIQ[i]; if V>2*PI and inst==0 → inst=i
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(dl):
                V += dl[idx]
                if V > 2 * PI and inst == 0.0:
                    inst = float(i)

        if inst == 0.0:
            inst = self.instIQ
        self.instIQ = inst
        self.lenIQ = 0.25 * inst + 0.75 * self.lenIQ

    def _calc_cosine_ifm(self, src: float):
        """
        Cosine IFM idêntico ao Pine v3.
        
        Pine:
            v1 := src - src[7]
            s2 := 0.2*(v1[1] + v1)*(v1[1] + v1) + 0.8*nz(s2[1])
            s3 := 0.2*(v1[1] - v1)*(v1[1] - v1) + 0.8*nz(s3[1])
            if (s2 != 0) v2 := sqrt(s3/s2)
            if (s3 != 0) deltaC := 2*atan(v2)
        """
        self._src_buf_cos.append(src)
        v1 = src - self._src_buf_cos[0]   # src - src[7]
        v1_1 = self.v1_prev               # v1[1] = v1 da barra anterior
        self.v1_prev = v1

        self.s2 = 0.2 * (v1_1 + v1) ** 2 + 0.8 * self.s2
        self.s3 = 0.2 * (v1_1 - v1) ** 2 + 0.8 * self.s3

        v2 = 0.0
        if self.s2 != 0.0:
            r = self.s3 / self.s2
            if r >= 0.0:
                v2 = math.sqrt(r)

        dC = 2 * math.atan(v2) if self.s3 != 0.0 else 0.0
        self.deltaC_buffer.append(dC)

        dl = list(self.deltaC_buffer)
        v4 = 0.0
        inst = 0.0
        # Pine: for i=0 to range; v4 += deltaC[i]; if v4>2*PI and inst==0 → inst=i-1
        for i in range(RANGE + 1):
            idx = -(i + 1)
            if abs(idx) <= len(dl):
                v4 += dl[idx]
                if v4 > 2 * PI and inst == 0.0:
                    inst = float(i - 1)

        if inst == 0.0:
            inst = self.instC
        self.instC = inst
        self.lenC = 0.25 * inst + 0.75 * self.lenC

    # ═══════════════════════════════════════════════════════════════════════
    # ZERO-LAG EMA
    # ═══════════════════════════════════════════════════════════════════════

    def _calc_zero_lag_ema(self, src: float, period: int):
        """
        Fiel ao Pine v3.
        
        Pine:
            alpha = 2/(Period + 1)
            EMA := alpha*src + (1-alpha)*nz(EMA[1])
            for i = -GainLimit to GainLimit
                Gain := i/10
                EC := alpha*(EMA + Gain*(src - nz(EC[1]))) + (1-alpha)*nz(EC[1])
                Error := src - EC
                if(abs(Error) < LeastError)
                    LeastError := abs(Error)
                    BestGain := Gain
            EC := alpha*(EMA + BestGain*(src - nz(EC[1]))) + (1-alpha)*nz(EC[1])
        
        CRÍTICO: O loop usa EC[1] = EC da barra anterior (não EC da iteração anterior).
        Cada iteração calcula EC_candidato usando o MESMO EC_prev.
        """
        alpha = 2.0 / (period + 1)

        ema_prev = self.EMA
        ec_prev  = self.EC

        # EMA := alpha*src + (1-alpha)*EMA[1]
        ema = alpha * src + (1 - alpha) * ema_prev

        # Loop: acha BestGain
        le = 1_000_000.0
        bg = 0.0
        for i in range(-GAIN_LIMIT, GAIN_LIMIT + 1):
            g = i / 10.0
            # EC_candidato usa EC[1] = ec_prev (MESMO para todas iterações)
            ec_c = alpha * (ema + g * (src - ec_prev)) + (1 - alpha) * ec_prev
            e = abs(src - ec_c)
            if e < le:
                le = e
                bg = g

        # EC final com BestGain
        ec = alpha * (ema + bg * (src - ec_prev)) + (1 - alpha) * ec_prev

        self.EMA = ema
        self.EC = ec
        self.LeastError = le
        self.BestGain = bg

        # Retorna valores ANTERIORES (para crossover) e ATUAIS
        return ema_prev, ec_prev, ema, ec

    # ═══════════════════════════════════════════════════════════════════════
    # TRAILING STOP / STOP LOSS
    # ═══════════════════════════════════════════════════════════════════════

    def _check_stop_touched(self, candle: Dict) -> bool:
        """
        Verifica se trailing stop ou SL foi tocado no candle atual.
        
        Pine strategy.exit com trail_points/trail_offset/loss:
        - trail_points: quantos ticks de lucro para ativar trailing
        - trail_offset: quantos ticks de recuo do máximo/mínimo para stop
        - loss: stop loss fixo em ticks a partir da entrada
        
        Com calc_on_every_tick=false: verificação é feita no close,
        execução no open da próxima barra.
        
        Precedência: se trailing ativo, usa trailing. Senão usa SL fixo.
        """
        if self.position_size == 0 or not self.exit_active or self.exit_scheduled:
            return False

        h = candle['high']
        l = candle['low']

        if self.position_size > 0:
            # LONG
            # Atualiza highest
            self.highest_price = max(self.highest_price, h)

            # Ativa trailing quando lucro >= trail_points ticks
            profit_ticks = (self.highest_price - self.position_avg_price) / self.tick_size
            if profit_ticks >= self.fixed_tp_points:
                self.trailing_active = True

            if self.trailing_active:
                stop = self.highest_price - self.trail_offset * self.tick_size
            else:
                stop = self.position_avg_price - self.fixed_sl_points * self.tick_size

            self._stop_price = stop

            if l <= stop:
                self.exit_scheduled = True
                self.exit_scheduled_side = "long"
                self.exit_scheduled_reason = "TRAIL" if self.trailing_active else "SL"
                return True

        elif self.position_size < 0:
            # SHORT
            # Atualiza lowest
            self.lowest_price = min(self.lowest_price, l)

            # Ativa trailing quando lucro >= trail_points ticks
            profit_ticks = (self.position_avg_price - self.lowest_price) / self.tick_size
            if profit_ticks >= self.fixed_tp_points:
                self.trailing_active = True

            if self.trailing_active:
                stop = self.lowest_price + self.trail_offset * self.tick_size
            else:
                stop = self.position_avg_price + self.fixed_sl_points * self.tick_size

            self._stop_price = stop

            if h >= stop:
                self.exit_scheduled = True
                self.exit_scheduled_side = "short"
                self.exit_scheduled_reason = "TRAIL" if self.trailing_active else "SL"
                return True

        return False

    def _execute_scheduled_exit(self, open_p: float, ts) -> Optional[Dict]:
        """
        Executa exit por trailing/SL no open da barra seguinte.
        
        Preço de execução:
        - LONG: min(stop_price, open) → se gap down, usa open (pior caso)
                Pine com slippage=0: executa no stop ou open se gap
        - SHORT: max(stop_price, open) → se gap up, usa open (pior caso)
        """
        if not self.exit_scheduled:
            return None

        reason = self.exit_scheduled_reason

        if self.exit_scheduled_side == "long":
            # Se gap down (open < stop), executa no open (slippage=0 → sem melhora)
            exec_price = min(self._stop_price, open_p)
            qty = self.position_size
            pnl = (exec_price - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_long()
            return {
                "action": "EXIT_LONG",
                "price": exec_price,
                "qty": qty,
                "pnl": pnl,
                "balance": self.balance,
                "timestamp": ts,
                "exit_reason": reason,
                "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }

        elif self.exit_scheduled_side == "short":
            # Se gap up (open > stop), executa no open (slippage=0 → sem melhora)
            exec_price = max(self._stop_price, open_p)
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - exec_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_short()
            return {
                "action": "EXIT_SHORT",
                "price": exec_price,
                "qty": qty,
                "pnl": pnl,
                "balance": self.balance,
                "timestamp": ts,
                "exit_reason": reason,
                "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }

        return None

    def _close_for_reversal(self, open_p: float, ts) -> Optional[Dict]:
        """
        Fecha posição existente para reversão (strategy.entry contrário).
        Pine: entry cancela exit_scheduled e usa open_price.
        """
        if self.position_size == 0:
            return None

        # Entry contrário cancela exit_scheduled (Pine: entry sobrescreve tudo)
        self.exit_scheduled = False
        self.exit_scheduled_side = ""

        if self.position_size > 0:
            qty = self.position_size
            pnl = (open_p - self.position_avg_price) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_long()
            return {
                "action": "EXIT_LONG",
                "price": open_p,
                "qty": qty,
                "pnl": pnl,
                "balance": self.balance,
                "timestamp": ts,
                "exit_reason": "REVERSAL",
                "comment": "EXIT-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }
        else:
            qty = abs(self.position_size)
            pnl = (self.position_avg_price - open_p) * qty
            self.net_profit += pnl
            self.balance = self.initial_capital + self.net_profit
            self._reset_short()
            return {
                "action": "EXIT_SHORT",
                "price": open_p,
                "qty": qty,
                "pnl": pnl,
                "balance": self.balance,
                "timestamp": ts,
                "exit_reason": "REVERSAL",
                "comment": "EXIT-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7"
            }

    def _reset_long(self):
        self.position_size = 0.0
        self.position_avg_price = 0.0
        self.highest_price = 0.0
        self.trailing_active = False
        self.exit_active = False
        self.exit_scheduled = False
        self.exit_scheduled_side = ""
        self.exit_scheduled_reason = ""

    def _reset_short(self):
        self.position_size = 0.0
        self.position_avg_price = 0.0
        self.lowest_price = float('inf')
        self.trailing_active = False
        self.exit_active = False
        self.exit_scheduled = False
        self.exit_scheduled_side = ""
        self.exit_scheduled_reason = ""

    def _calc_lots(self) -> float:
        """
        Pine: lots = (risk * balance) / (fixedSL * syminfo.mintick)
        Fiel à correção financeira do script.
        """
        bal = self.initial_capital + self.net_profit
        sl_usdt = self.fixed_sl_points * self.tick_size
        if sl_usdt <= 0:
            return 0.0
        lots = (self.risk_percent * bal) / sl_usdt
        return min(lots, float(self.max_lots))

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN LOOP - next(candle)
    # ═══════════════════════════════════════════════════════════════════════

    def next(self, candle: Dict) -> List[Dict]:
        """
        Processa um candle e retorna lista de ações (trades).
        
        Fluxo por barra (fiel ao Pine v3):
        
        ┌─── OPEN ────────────────────────────────────────────────────────┐
        │ 1. Se exit_scheduled (trailing/SL de barra anterior):           │
        │    → Executa exit ao stop_price (ou open se gap)                │
        │    → position_size = 0                                          │
        │                                                                 │
        │ 2. Se entry_scheduled (pending de barra anterior):              │
        │    → Se contrário à posição: REVERSÃO (fecha e reabre)         │
        │    → Se mesma direção ou flat: ENTRY                            │
        │    → Nos dois casos: usa open_price                             │
        │                                                                 │
        │ ORDEM CORRETA (Pine com pyramiding=1):                          │
        │    Exit por SL/trailing PRIMEIRO, depois entry contrário        │
        │    Mas entry contrário CANCELA exit_scheduled (reversão)        │
        │                                                                 │
        │ REGRA FINAL:                                                    │
        │    - Se exit_scheduled E entry contrário no mesmo open:         │
        │      → Entry contrário VENCE (cancela exit, fecha ao open)      │
        │    - Se exit_scheduled sem entry contrário:                     │
        │      → Exit executa normalmente                                 │
        └─────────────────────────────────────────────────────────────────┘
        
        ┌─── CLOSE ───────────────────────────────────────────────────────┐
        │ 3. Calcula indicadores (IFM, ZLEMA)                             │
        │ 4. Verifica trailing/SL no high/low → agenda exit               │
        │ 5. Avalia pending_buy/sell → agenda entry para próximo open     │
        │    REGRA: só agenda entry se NÃO há exit_scheduled              │
        │    (entry_scheduled na barra do exit = reversão = 1 barra extra)│
        └─────────────────────────────────────────────────────────────────┘
        """
        open_p = candle['open']
        idx    = candle.get('index', 0)
        ts     = candle.get('timestamp')
        actions = []

        self._bar_count += 1
        in_warmup = self._bar_count <= self.warmup_bars

        # ── STEP 1: Propaga sinais da barra anterior para pending ──────────
        # Pine: pendingBuy := nz(pendingBuy[1])
        #       if buy_signal[1]: pendingBuy := true
        # Equivalente: no início de cada barra, verifica se buy_signal[1]
        # (barra anterior) foi verdadeiro.
        if self.buy_signal_prev:
            self.pending_buy = True
        if self.sell_signal_prev:
            self.pending_sell = True

        if not in_warmup:
            bal = self.initial_capital + self.net_profit

            # ── STEP 2: OPEN - Executa ordens agendadas ────────────────────
            #
            # Lógica Pine strategy.entry() com pyramiding=1:
            # - Se entry contrário E exit_scheduled: entry CANCELA o exit
            # - Se entry contrário sem exit_scheduled: fecha posição (reversão)
            # - Exit por trailing/SL sem entry contrário: executa normalmente
            #
            # IMPLEMENTAÇÃO:
            # Caso A: entry_scheduled_long E posição SHORT (com ou sem exit_scheduled)
            #   → Reversão: fecha short ao open_price, abre long
            # Caso B: entry_scheduled_short E posição LONG (com ou sem exit_scheduled)
            #   → Reversão: fecha long ao open_price, abre short
            # Caso C: exit_scheduled sem entry contrário
            #   → Exit ao stop_price
            # Caso D: entry no flat (position=0 após exit ou sem posição)
            #   → Entry direto

            if bal > 0:
                entry_long  = self.entry_scheduled_long
                entry_short = self.entry_scheduled_short

                # Caso A: REVERSÃO para LONG (estava SHORT)
                if entry_long and self.position_size < 0:
                    rev = self._close_for_reversal(open_p, ts)
                    if rev:
                        actions.append(rev)

                # Caso B: REVERSÃO para SHORT (estava LONG)
                elif entry_short and self.position_size > 0:
                    rev = self._close_for_reversal(open_p, ts)
                    if rev:
                        actions.append(rev)

                # Caso C: exit por trailing/SL (sem entry contrário)
                elif self.exit_scheduled and self.position_size != 0:
                    exit_act = self._execute_scheduled_exit(open_p, ts)
                    if exit_act:
                        actions.append(exit_act)

                # Entry LONG (flat ou após reversão/exit)
                if entry_long and self.position_size <= 0:
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
                            "action": "BUY",
                            "qty": lots,
                            "price": open_p,
                            "comment": "ENTER-LONG_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                            "balance": self.balance,
                            "timestamp": ts
                        })
                        # Limpa pending contrário ao entrar (Pine: pyramiding=1)
                        self.pending_sell = False
                        if not in_warmup:
                            print(f"✅ LONG  [{idx}] @ {open_p:.2f} qty={lots:.4f} "
                                  f"bal={self.balance:.2f}")
                    self.entry_scheduled_long = False

                # Entry SHORT (flat ou após reversão/exit)
                elif entry_short and self.position_size >= 0:
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
                            "action": "SELL",
                            "qty": lots,
                            "price": open_p,
                            "comment": "ENTER-SHORT_BingX_ETH-USDT_trade_45M_9640193738b8e54a44f2e5c7",
                            "balance": self.balance,
                            "timestamp": ts
                        })
                        # Limpa pending contrário ao entrar
                        self.pending_buy = False
                        if not in_warmup:
                            print(f"✅ SHORT [{idx}] @ {open_p:.2f} qty={lots:.4f} "
                                  f"bal={self.balance:.2f}")
                    self.entry_scheduled_short = False

        else:
            # Warmup: limpa flags sem executar
            self.entry_scheduled_long  = False
            self.entry_scheduled_short = False
            # NÃO limpa exit_scheduled no warmup (não há posição aberta)

        # ── STEP 3: CLOSE - Calcula indicadores ───────────────────────────
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

        # ── STEP 4: Calcula sinais de crossover ───────────────────────────
        # Pine: crossover(EC, EMA) = EC[1] < EMA[1] AND EC > EMA
        # Pine: crossunder(EC, EMA) = EC[1] > EMA[1] AND EC < EMA
        # CORREÇÃO: strict < e > (não <= e >=)
        buy_signal  = (ec_prev < ema_prev) and (ec > ema)
        sell_signal = (ec_prev > ema_prev) and (ec < ema)

        # Aplica filtro de threshold
        err = 100.0 * self.LeastError / src if src != 0 else 0.0
        buy_signal  = buy_signal  and (err > self.threshold)
        sell_signal = sell_signal and (err > self.threshold)

        # ── STEP 5: Verifica trailing/SL no candle atual ──────────────────
        self._check_stop_touched(candle)

        # ── STEP 6: CLOSE - Agenda entries para próximo open ──────────────
        #
        # Pine v3 equivalente:
        #   if (pendingBuy and strategy.position_size <= 0)
        #       strategy.entry("BUY", ...)  ← agenda para próximo open
        #       pendingBuy := false
        #
        # REGRA CRÍTICA: Se exit_scheduled=True, a posição EFETIVA ainda
        # está aberta (o exit só executa no próximo open).
        # Portanto, NÃO agendamos entry contrário quando exit_scheduled=True.
        # O pending persiste e será avaliado NA PRÓXIMA barra, após o exit.
        #
        # Exceção: entry na MESMA direção da posição atual não faz sentido
        # (pyramiding=1), então também não agenda.

        if not in_warmup:
            if not self.exit_scheduled:
                # Posição não vai fechar → avalia pendentes normalmente
                if self.pending_buy and self.position_size <= 0:
                    self.entry_scheduled_long  = True
                    self.entry_scheduled_short = False
                    self.pending_buy = False
                    print(f"🚀 Long agendado → open[{idx + 1}]")

                elif self.pending_sell and self.position_size >= 0:
                    self.entry_scheduled_short = True
                    self.entry_scheduled_long  = False
                    self.pending_sell = False
                    print(f"🚀 Short agendado → open[{idx + 1}]")
            else:
                # exit_scheduled=True: posição ainda "aberta efetivamente"
                # Não agenda entry contrário agora.
                # pending_buy/sell persistem para próxima barra.
                # Na próxima barra: exit executa → posição=0 → pending avaliado
                pass
        else:
            # Warmup: não agenda entries
            if self.pending_buy and self.position_size <= 0:
                self.pending_buy = False
            if self.pending_sell and self.position_size >= 0:
                self.pending_sell = False

        # Salva sinais para próxima barra
        self.buy_signal_prev  = buy_signal
        self.sell_signal_prev = sell_signal

        # Debug periódico
        if idx % 100 == 0:
            wstr = " [WU]" if in_warmup else ""
            print(f"📊 [{idx}]{wstr} P={self.Period} EC={ec:.4f} EMA={ema:.4f} "
                  f"diff={ec - ema:.6f} xo={buy_signal} xu={sell_signal} "
                  f"pos={self.position_size:.4f} bal={self.balance:.2f} "
                  f"trail={'ON' if self.trailing_active else 'off'} "
                  f"exitS={self.exit_scheduled}")

        return actions
