"""
MOTOR PINE SCRIPT 100% IDÊNTICO AO TRADINGVIEW
Implementa EXATAMENTE o algoritmo Adaptive Zero Lag EMA v2
"""
import re
import math
import logging
from typing import List, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)

class PineSeries:
    """Implementa comportamento IDÊNTICO ao Pine Script"""
    def __init__(self):
        self.values = []
    
    def __getitem__(self, index):
        """Implementa nz() behavior - retorna 0 se não existir"""
        if index >= len(self.values):
            return 0.0
        return self.values[-(index + 1)]
    
    def append(self, value):
        self.values.append(value)
        # Mantém apenas histórico necessário
        if len(self.values) > 10000:
            self.values.pop(0)
    
    def current(self):
        return self.values[-1] if self.values else 0.0
    
    def __len__(self):
        return len(self.values)

class AdaptiveZeroLagEMA:
    """Implementa EXATAMENTE a estratégia do TradingView"""
    
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.candle_count = 0
        
        # Extrai parâmetros EXATOS
        self.params = self._extract_exact_params()
        
        # Configurações EXATAS do TradingView
        self.period = 20  # Inicial
        self.alpha = 2.0 / (self.period + 1)
        
        # MÉTODO ADAPTATIVO
        self.adaptive = self.params.get('adaptive', 'Cos IFM')
        
        # LIMITES
        self.gain_limit = self.params.get('GainLimit', 900)
        self.threshold = self.params.get('Threshold', 0.0)
        
        # RISK MANAGEMENT
        self.fixedSL = self.params.get('fixedSL', 2000)
        self.fixedTP = self.params.get('fixedTP', 55)
        self.trail_offset = 15  # FIXO no código Pine
        self.risk = self.params.get('risk', 0.01)
        
        # SÉRIES TEMPORAIS (como TradingView)
        self.init_series()
        
        # ESTADO DOS SINAIS
        self.buy_signal = PineSeries()
        self.sell_signal = PineSeries()
        self.pending_buy = PineSeries()
        self.pending_sell = PineSeries()
        
        logger.info(f"✅ AdaptiveZeroLagEMA inicializado")
        logger.info(f"   Método: {self.adaptive}, Period: {self.period}")
        logger.info(f"   GainLimit: {self.gain_limit}, Threshold: {self.threshold}")
        logger.info(f"   SL: {self.fixedSL}p, TP: {self.fixedTP}p, Trail: {self.trail_offset}p")
    
    def _extract_exact_params(self) -> Dict[str, Any]:
        """Extrai parâmetros IDÊNTICOS ao Pine Script"""
        params = {}
        
        # Padrões EXATOS do seu código
        patterns = {
            'Period': r'Period.*defval\s*=\s*(\d+)',
            'adaptive': r'adaptive.*defval\s*=\s*"([^"]+)"',
            'GainLimit': r'GainLimit.*defval\s*=\s*(\d+)',
            'Threshold': r'Threshold.*defval\s*=\s*([\d.]+)',
            'fixedSL': r'fixedSL.*defval\s*=\s*(\d+)',
            'fixedTP': r'fixedTP.*defval\s*=\s*(\d+)',
            'risk': r'risk.*defval\s*=\s*([\d.]+)'
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, self.pine_code, re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1)
                if key in ['Period', 'GainLimit', 'fixedSL', 'fixedTP']:
                    params[key] = int(value)
                elif key in ['Threshold', 'risk']:
                    params[key] = float(value)
                else:
                    params[key] = value
        
        # Valores padrão EXATOS do seu código
        defaults = {
            'Period': 20,
            'adaptive': 'Cos IFM',
            'GainLimit': 900,
            'Threshold': 0.0,
            'fixedSL': 2000,
            'fixedTP': 55,
            'risk': 0.01
        }
        
        for key, default in defaults.items():
            if key not in params:
                params[key] = default
        
        return params
    
    def init_series(self):
        """Inicializa TODAS as séries como no TradingView"""
        self.src = PineSeries()
        self.EC = PineSeries()
        self.EMA = PineSeries()
        self.LeastError = PineSeries()
        
        # Variáveis adaptativas
        self.lenC = PineSeries()
        self.lenIQ = PineSeries()
        self.s2 = PineSeries()
        self.s3 = PineSeries()
        self.re = PineSeries()
        self.im = PineSeries()
        
        # Para Cosine IFM
        self.v1 = PineSeries()
        self.v2 = PineSeries()
        self.v4 = PineSeries()
        self.deltaC = PineSeries()
        self.instC = PineSeries()
        
        # Para I-Q IFM
        self.inphase = PineSeries()
        self.quadrature = PineSeries()
        self.deltaIQ = PineSeries()
        self.instIQ = PineSeries()
    
    def calculate_cosine_ifm(self):
        """Implementa EXATAMENTE o Cosine IFM do Pine Script"""
        if len(self.src.values) < 8:
            return 20  # Período padrão até ter dados suficientes
        
        # v1 := src - src[7]
        v1_val = self.src.current() - self.src[7]
        self.v1.append(v1_val)
        
        # s2 := 0.2*(v1[1] + v1)*(v1[1] + v1) + 0.8*nz(s2[1])
        s2_val = 0.2 * (self.v1[1] + v1_val) * (self.v1[1] + v1_val) + 0.8 * self.s2[1]
        self.s2.append(s2_val)
        
        # s3 := 0.2*(v1[1] - v1)*(v1[1] - v1) + 0.8*nz(s3[1])
        s3_val = 0.2 * (self.v1[1] - v1_val) * (self.v1[1] - v1_val) + 0.8 * self.s3[1]
        self.s3.append(s3_val)
        
        # if (s2 != 0): v2 := sqrt(s3/s2)
        v2_val = 0.0
        if s2_val != 0:
            v2_val = math.sqrt(s3_val / s2_val)
        self.v2.append(v2_val)
        
        # if (s3 != 0): deltaC := 2*atan(v2)
        deltaC_val = 0.0
        if s3_val != 0:
            deltaC_val = 2 * math.atan(v2_val)
        self.deltaC.append(deltaC_val)
        
        # Encontrar instC
        instC_val = 0.0
        v4_val = 0.0
        
        # for i = 0 to range (range=50 no seu código)
        for i in range(0, 51):  # range=50, inclusive
            deltaC_i = self.deltaC[i] if i < len(self.deltaC.values) else 0.0
            v4_val += deltaC_i
            if v4_val > 2 * math.pi and instC_val == 0.0:
                instC_val = i - 1
        
        if instC_val == 0.0:
            instC_val = self.instC[1] if len(self.instC) > 1 else 20
        
        self.instC.append(instC_val)
        
        # lenC := 0.25*instC + 0.75*nz(lenC[1])
        lenC_val = 0.25 * instC_val + 0.75 * self.lenC[1]
        self.lenC.append(lenC_val)
        
        return round(lenC_val)
    
    def calculate_IQ_ifm(self):
        """Implementa EXATAMENTE o I-Q IFM do Pine Script"""
        if len(self.src.values) < 8:
            return 20
        
        # Constants
        imult = 0.635
        qmult = 0.338
        
        # P = src - src[7]
        P = self.src.current() - self.src[7]
        
        # inphase := 1.25*(P[4] - imult*P[2]) + imult*nz(inphase[3])
        inphase_val = 1.25 * (self.src[4] - imult * self.src[2]) + imult * self.inphase[3]
        self.inphase.append(inphase_val)
        
        # quadrature := P[2] - qmult*P + qmult*nz(quadrature[2])
        quadrature_val = self.src[2] - qmult * P + qmult * self.quadrature[2]
        self.quadrature.append(quadrature_val)
        
        # re := 0.2*(inphase*inphase[1] + quadrature*quadrature[1]) + 0.8*nz(re[1])
        re_val = 0.2 * (inphase_val * self.inphase[1] + quadrature_val * self.quadrature[1]) + 0.8 * self.re[1]
        self.re.append(re_val)
        
        # im := 0.2*(inphase*quadrature[1] - inphase[1]*quadrature) + 0.8*nz(im[1])
        im_val = 0.2 * (inphase_val * self.quadrature[1] - self.inphase[1] * quadrature_val) + 0.8 * self.im[1]
        self.im.append(im_val)
        
        # deltaIQ
        deltaIQ_val = 0.0
        if re_val != 0.0:
            deltaIQ_val = math.atan(im_val / re_val)
        self.deltaIQ.append(deltaIQ_val)
        
        # Encontrar instIQ
        instIQ_val = 0.0
        V_val = 0.0
        
        for i in range(0, 51):
            deltaIQ_i = self.deltaIQ[i] if i < len(self.deltaIQ.values) else 0.0
            V_val += deltaIQ_i
            if V_val > 2 * math.pi and instIQ_val == 0.0:
                instIQ_val = i
        
        if instIQ_val == 0.0:
            instIQ_val = self.instIQ[1] if len(self.instIQ) > 1 else 20
        
        self.instIQ.append(instIQ_val)
        
        # lenIQ := 0.25*instIQ + 0.75*nz(lenIQ[1])
        lenIQ_val = 0.25 * instIQ_val + 0.75 * self.lenIQ[1]
        self.lenIQ.append(lenIQ_val)
        
        return round(lenIQ_val)
    
    def calculate_adaptive_period(self):
        """Calcula período adaptativo EXATO como TradingView"""
        if self.adaptive == "Off":
            return self.params['Period']
        
        elif self.adaptive == "Cos IFM":
            period = self.calculate_cosine_ifm()
            self.period = period
            self.alpha = 2.0 / (self.period + 1)
            return period
        
        elif self.adaptive == "I-Q IFM":
            period = self.calculate_IQ_ifm()
            self.period = period
            self.alpha = 2.0 / (self.period + 1)
            return period
        
        elif self.adaptive == "Average":
            period_cos = self.calculate_cosine_ifm()
            period_iq = self.calculate_IQ_ifm()
            period = round((period_cos + period_iq) / 2)
            self.period = period
            self.alpha = 2.0 / (self.period + 1)
            return period
    
    def calculate_zero_lag_ema(self, src_price: float):
        """Implementa EXATAMENTE o Zero Lag EMA do Pine Script"""
        
        # 1. Calcular EMA
        ema_prev = self.EMA.current() if len(self.EMA) > 0 else src_price
        ema = self.alpha * src_price + (1 - self.alpha) * ema_prev
        self.EMA.append(ema)
        
        # 2. Calcular EC com melhor ganho
        ec_prev = self.EC.current() if len(self.EC) > 0 else src_price
        
        # LOOP IDÊNTICO ao Pine: for i = -GainLimit to GainLimit
        least_error = 1000000.0  # Como no Pine: LeastError = 1000000.0
        best_gain = 0.0
        
        for i in range(-self.gain_limit, self.gain_limit + 1):
            gain = i / 10.0  # Gain := i/10.0
            
            # EC := alpha*(EMA + Gain*(src - nz(EC[1]))) + (1 - alpha)*nz(EC[1])
            ec_test = self.alpha * (ema + gain * (src_price - ec_prev)) + (1 - self.alpha) * ec_prev
            
            error = abs(src_price - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
        
        # Calcular EC final com melhor ganho
        ec = self.alpha * (ema + best_gain * (src_price - ec_prev)) + (1 - self.alpha) * ec_prev
        self.EC.append(ec)
        self.LeastError.append(least_error)
        
        return ema, ec, least_error
    
    def calculate_signals(self, ema: float, ec: float, least_error: float, src_price: float):
        """Calcula sinais EXATAMENTE como TradingView"""
        
        # Valores anteriores para crossover/crossunder
        ec_prev = self.EC[1] if len(self.EC) > 1 else ec
        ema_prev = self.EMA[1] if len(self.EMA) > 1 else ema
        
        # Crossover/Crossunder
        crossover_signal = (ec_prev <= ema_prev) and (ec > ema)
        crossunder_signal = (ec_prev >= ema_prev) and (ec < ema)
        
        # Threshold (100*LeastError/src > Threshold)
        error_pct = 100 * least_error / src_price if src_price > 0 else 0
        threshold_check = error_pct > self.threshold
        
        # Sinais da barra atual (RAW)
        buy_signal_current = crossover_signal and threshold_check
        sell_signal_current = crossunder_signal and threshold_check
        
        # Série de sinais
        self.buy_signal.append(1.0 if buy_signal_current else 0.0)
        self.sell_signal.append(1.0 if sell_signal_current else 0.0)
        
        # FLAGS PERSISTENTES - EXATO como Pine: pendingBuy := nz(pendingBuy[1])
        pending_buy = self.pending_buy[1] if len(self.pending_buy) > 1 else 0.0
        pending_sell = self.pending_sell[1] if len(self.pending_sell) > 1 else 0.0
        
        # Se houver sinal na barra ANTERIOR, marcar como pendente
        buy_signal_prev = self.buy_signal[1] if len(self.buy_signal) > 1 else 0.0
        sell_signal_prev = self.sell_signal[1] if len(self.sell_signal) > 1 else 0.0
        
        if buy_signal_prev > 0:
            pending_buy = 1.0
            pending_sell = 0.0  # Reset oposto (como no Pine)
        
        if sell_signal_prev > 0:
            pending_sell = 1.0
            pending_buy = 0.0  # Reset oposto (como no Pine)
        
        self.pending_buy.append(pending_buy)
        self.pending_sell.append(pending_sell)
        
        return {
            'buy_signal_current': buy_signal_current,
            'sell_signal_current': sell_signal_current,
            'pending_buy': pending_buy > 0,
            'pending_sell': pending_sell > 0,
            'ema': ema,
            'ec': ec,
            'least_error': least_error,
            'error_pct': error_pct,
            'period': self.period,
            'crossover': crossover_signal,
            'crossunder': crossunder_signal,
            'ec_prev': ec_prev,
            'ema_prev': ema_prev
        }
    
    def process_candle(self, candle: Dict[str, float]) -> Dict[str, Any]:
        """Processa um candle IDÊNTICO ao TradingView"""
        self.candle_count += 1
        
        # Fonte do preço (src)
        src_price = candle['close']
        self.src.append(src_price)
        
        # 1. Calcular período adaptativo
        period = self.calculate_adaptive_period()
        
        # 2. Calcular Zero Lag EMA
        ema, ec, least_error = self.calculate_zero_lag_ema(src_price)
        
        # 3. Calcular sinais
        signals = self.calculate_signals(ema, ec, least_error, src_price)
        
        # LOG DETALHADO
        if self.candle_count <= 10 or signals['pending_buy'] or signals['pending_sell']:
            logger.info(f"📊 Candle #{self.candle_count}:")
            logger.info(f"   Preço: ${src_price:.2f}, Período: {period}")
            logger.info(f"   EMA: {ema:.2f}, EC: {ec:.2f}")
            logger.info(f"   EC_prev: {signals['ec_prev']:.2f}, EMA_prev: {signals['ema_prev']:.2f}")
            logger.info(f"   Erro: {signals['error_pct']:.2f}%")
            
            if signals['buy_signal_current']:
                logger.info(f"   🟢 BUY_SIGNAL na barra atual (executa na próxima)")
            
            if signals['sell_signal_current']:
                logger.info(f"   🔴 SELL_SIGNAL na barra atual (executa na próxima)")
            
            if signals['pending_buy']:
                logger.info(f"   ⚡ PENDING_BUY ativo (será executado)")
            
            if signals['pending_sell']:
                logger.info(f"   ⚡ PENDING_SELL ativo (será executado)")
        
        return signals
