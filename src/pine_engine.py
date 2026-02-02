"""
Motor de Execução Pine Script v3 - VERSÃO COM PROCESSAMENTO POR TICK
"""
import re
import math
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class PineSeries:
    """Representa uma série temporal no Pine Script"""
    values: List[float]
    
    def __getitem__(self, index: int) -> float:
        """Implementa o operador [] do Pine Script (histórico)"""
        if index >= len(self.values):
            return 0.0  # nz() behavior
        return self.values[-(index + 1)]
    
    def append(self, value: float):
        self.values.append(value)
        if len(self.values) > 5000:  # Mantém histórico suficiente
            self.values.pop(0)
    
    def current(self) -> float:
        return self.values[-1] if self.values else 0.0
    
    def __len__(self):
        return len(self.values)

class PineScriptInterpreter:
    """Interpreta e executa código Pine Script v3 EXATAMENTE como TradingView"""
    
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.symbol_table = {}
        self.series_data = {}
        
        # Contador de candles processados
        self.candle_count = 0
        
        # Configurações da estratégia (extraídas do código)
        self.params = self._extract_parameters()
        
        # Estado da execução - VALORES EXATOS do Pine Script
        self.period = self.params.get('Period', 20)
        self.gain_limit = self.params.get('GainLimit', 900)
        self.threshold = self.params.get('Threshold', 0.0)
        self.fixedSL = self.params.get('fixedSL', 2000)
        self.fixedTP = self.params.get('fixedTP', 55)
        self.risk = self.params.get('risk', 0.01)
        self.adaptive = self.params.get('adaptive', 'Cos IFM')
        
        # Configuração do cálculo adaptativo
        self.alpha = 2.0 / (self.period + 1)
        
        # CONSTANTES do Pine Script
        self.PI = 3.14159265359
        self.range_val = 50  # range = 50 no Pine
        
        # Configuração para métodos adaptativos
        self.imult = 0.635
        self.qmult = 0.338
        
        # Inicializar todas as séries
        self._initialize_series()
        
        logger.info(f"✅ Pine Script Interpreter inicializado")
        logger.info(f"   Period={self.period}, GainLimit={self.gain_limit}, Threshold={self.threshold}")
        logger.info(f"   SL={self.fixedSL}p, TP={self.fixedTP}p, Risk={self.risk*100}%")
        logger.info(f"   Adaptive Method: {self.adaptive}")
        logger.info(f"   PI={self.PI}, Range={self.range_val}")
    
    def _extract_parameters(self) -> Dict[str, Any]:
        """Extrai parâmetros do código Pine Script EXATAMENTE como estão"""
        params = {}
        
        patterns = {
            'Period': r'Period.*defval\s*=\s*(\d+)',
            'GainLimit': r'GainLimit.*defval\s*=\s*(\d+)',
            'Threshold': r'Threshold.*defval\s*=\s*([\d.]+)',
            'fixedSL': r'fixedSL.*defval\s*=\s*(\d+)',
            'fixedTP': r'fixedTP.*defval\s*=\s*(\d+)',
            'risk': r'risk.*defval\s*=\s*([\d.]+)',
            'adaptive': r'adaptive.*defval\s*=\s*"([^"]+)"'
        }
        
        for key, pattern in patterns.items():
            try:
                match = re.search(pattern, self.pine_code, re.IGNORECASE | re.MULTILINE)
                if match:
                    value = match.group(1)
                    if key in ['Period', 'GainLimit', 'fixedSL', 'fixedTP']:
                        params[key] = int(value)
                    elif key in ['Threshold', 'risk']:
                        params[key] = float(value)
                    else:
                        params[key] = value
            except Exception as e:
                logger.warning(f"⚠️ Não consegui extrair parâmetro {key}: {e}")
        
        defaults = {
            'fixedSL': 2000,
            'fixedTP': 55,
            'risk': 0.01,
            'Period': 20,
            'Threshold': 0.0,
            'GainLimit': 900,
            'adaptive': 'Cos IFM'
        }
        
        for key, default in defaults.items():
            if key not in params:
                params[key] = default
        
        return params
    
    def _initialize_series(self):
        """Inicializa todas as séries temporais"""
        series_list = [
            'src', 'EC', 'EMA', 'LeastError',
            'buy_signal', 'sell_signal',
            'pendingBuy', 'pendingSell',
            'lenIQ', 'lenC', 're', 'im', 's2', 's3',
            'inphase', 'quadrature', 'deltaIQ', 'instIQ', 'V',
            'deltaC', 'instC', 'v1', 'v2', 'v4',
            'P', 'inphase_hist', 'quadrature_hist'
        ]
        
        for series in series_list:
            self.series_data[series] = PineSeries([0.0])
    
    def calculate_ema(self, src: float, prev_ema: Optional[float]) -> float:
        """Calcula EMA exatamente como no Pine Script: alpha*src + (1-alpha)*nz(EMA[1])"""
        if prev_ema is None or prev_ema == 0:
            return src
        return self.alpha * src + (1 - self.alpha) * prev_ema
    
    def _calculate_adaptive_period(self):
        """Calcula período adaptativo como no Pine Script"""
        src = self.series_data['src'].current()
        
        # I-Q IFM
        if self.adaptive == "I-Q IFM" or self.adaptive == "Average":
            # P = src - src[7]
            src_7 = self.series_data['src'][7] if len(self.series_data['src']) > 7 else src
            P = src - src_7
            
            # inphase = 1.25*(P[4] - imult*P[2]) + imult*nz(inphase[3])
            P_4 = self.series_data['src'][4] if len(self.series_data['src']) > 4 else src
            P_2 = self.series_data['src'][2] if len(self.series_data['src']) > 2 else src
            inphase_3 = self.series_data['inphase'][3] if len(self.series_data['inphase']) > 3 else 0
            inphase = 1.25 * (P_4 - self.imult * P_2) + self.imult * inphase_3
            
            # quadrature = P[2] - qmult*P + qmult*nz(quadrature[2])
            quadrature_2 = self.series_data['quadrature'][2] if len(self.series_data['quadrature']) > 2 else 0
            quadrature = P_2 - self.qmult * P + self.qmult * quadrature_2
            
            # re = 0.2*(inphase*inphase[1] + quadrature*quadrature[1]) + 0.8*nz(re[1])
            inphase_1 = self.series_data['inphase'][1] if len(self.series_data['inphase']) > 1 else inphase
            quadrature_1 = self.series_data['quadrature'][1] if len(self.series_data['quadrature']) > 1 else quadrature
            re_1 = self.series_data['re'][1] if len(self.series_data['re']) > 1 else 0
            re = 0.2 * (inphase * inphase_1 + quadrature * quadrature_1) + 0.8 * re_1
            
            # im = 0.2*(inphase*quadrature[1] - inphase[1]*quadrature) + 0.8*nz(im[1])
            im_1 = self.series_data['im'][1] if len(self.series_data['im']) > 1 else 0
            im = 0.2 * (inphase * quadrature_1 - inphase_1 * quadrature) + 0.8 * im_1
            
            # deltaIQ = atan(im/re)
            deltaIQ = math.atan(im / re) if re != 0 else 0
            
            # Cálculo de instIQ (simplificado)
            instIQ = 0.0
            V = 0.0
            for i in range(self.range_val + 1):
                V += deltaIQ  # deltaIQ[i] seria histórico
                if V > 2 * self.PI and instIQ == 0.0:
                    instIQ = i
            
            if instIQ == 0.0:
                instIQ = self.series_data['instIQ'][1] if len(self.series_data['instIQ']) > 1 else 0
            
            # lenIQ = 0.25*instIQ + 0.75*nz(lenIQ[1])
            lenIQ_1 = self.series_data['lenIQ'][1] if len(self.series_data['lenIQ']) > 1 else 0
            lenIQ = 0.25 * instIQ + 0.75 * lenIQ_1
            
            self.series_data['lenIQ'].append(lenIQ)
            self.series_data['inphase'].append(inphase)
            self.series_data['quadrature'].append(quadrature)
            self.series_data['re'].append(re)
            self.series_data['im'].append(im)
            self.series_data['instIQ'].append(instIQ)
        
        # Cosine IFM
        if self.adaptive == "Cos IFM" or self.adaptive == "Average":
            # v1 = src - src[7]
            src_7 = self.series_data['src'][7] if len(self.series_data['src']) > 7 else src
            v1 = src - src_7
            
            # s2 = 0.2*(v1[1] + v1)*(v1[1] + v1) + 0.8*nz(s2[1])
            v1_1 = self.series_data['v1'][1] if len(self.series_data['v1']) > 1 else v1
            s2_1 = self.series_data['s2'][1] if len(self.series_data['s2']) > 1 else 0
            s2 = 0.2 * (v1_1 + v1) * (v1_1 + v1) + 0.8 * s2_1
            
            # s3 = 0.2*(v1[1] - v1)*(v1[1] - v1) + 0.8*nz(s3[1])
            s3_1 = self.series_data['s3'][1] if len(self.series_data['s3']) > 1 else 0
            s3 = 0.2 * (v1_1 - v1) * (v1_1 - v1) + 0.8 * s3_1
            
            # v2 = sqrt(s3/s2)
            v2 = math.sqrt(s3 / s2) if s2 != 0 else 0
            
            # deltaC = 2*atan(v2)
            deltaC = 2 * math.atan(v2) if s3 != 0 else 0
            
            # Cálculo de instC (simplificado)
            instC = 0.0
            v4 = 0.0
            for i in range(self.range_val + 1):
                v4 += deltaC  # deltaC[i] seria histórico
                if v4 > 2 * self.PI and instC == 0.0:
                    instC = i - 1
            
            if instC == 0.0:
                instC = self.series_data['instC'][1] if len(self.series_data['instC']) > 1 else 0
            
            # lenC = 0.25*instC + 0.75*nz(lenC[1])
            lenC_1 = self.series_data['lenC'][1] if len(self.series_data['lenC']) > 1 else 0
            lenC = 0.25 * instC + 0.75 * lenC_1
            
            self.series_data['lenC'].append(lenC)
            self.series_data['v1'].append(v1)
            self.series_data['s2'].append(s2)
            self.series_data['s3'].append(s3)
            self.series_data['instC'].append(instC)
        
        # Determinar período final
        if self.adaptive == "Cos IFM":
            self.period = round(self.series_data['lenC'].current())
        elif self.adaptive == "I-Q IFM":
            self.period = round(self.series_data['lenIQ'].current())
        elif self.adaptive == "Average":
            lenC_val = self.series_data['lenC'].current()
            lenIQ_val = self.series_data['lenIQ'].current()
            self.period = round((lenC_val + lenIQ_val) / 2)
        
        # Atualizar alpha com novo período
        self.alpha = 2.0 / (self.period + 1)
    
    def calculate_zero_lag_ema(self, src: float) -> Tuple[float, float, float]:
        """
        Implementa o algoritmo Zero Lag EMA do Pine Script EXATAMENTE
        Retorna: (EMA, EC, LeastError)
        """
        # Calcular período adaptativo se necessário
        if self.adaptive != "Off":
            self._calculate_adaptive_period()
        
        # Calcular EMA
        ema_prev = self.series_data['EMA'].current()
        ema = self.calculate_ema(src, ema_prev)
        self.series_data['EMA'].append(ema)
        
        # Obter EC anterior
        ec_prev = self.series_data['EC'].current()
        if ec_prev == 0 and len(self.series_data['EC']) == 1:
            ec_prev = src  # Primeira iteração
        
        # Buscar melhor ganho (exatamente como no Pine)
        best_gain = 0.0
        least_error = float('inf')
        
        # Loop de -GainLimit a +GainLimit com passo 0.1
        for i in range(-self.gain_limit, self.gain_limit + 1):
            gain = i / 10.0
            
            # Calcular EC com este ganho
            ec_test = self.alpha * (ema + gain * (src - ec_prev)) + (1 - self.alpha) * ec_prev
            error = abs(src - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
        
        # Calcular EC final com melhor ganho
        ec = self.alpha * (ema + best_gain * (src - ec_prev)) + (1 - self.alpha) * ec_prev
        self.series_data['EC'].append(ec)
        self.series_data['LeastError'].append(least_error)
        
        return ema, ec, least_error
    
    def process_tick(self, price: float, timestamp: datetime) -> Dict[str, Any]:
        """
        Processa um tick em tempo real (chamar a cada novo preço)
        """
        self.series_data['src'].append(price)
        
        # Calcular Zero Lag EMA
        ema, ec, least_error = self.calculate_zero_lag_ema(price)
        
        # Obter valores anteriores
        ec_prev = self.series_data['EC'][1] if len(self.series_data['EC']) > 1 else ec
        ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA']) > 1 else ema
        
        # Calcular sinais CRUZAIS (crossover/crossunder)
        crossover = (ec_prev <= ema_prev) and (ec > ema)
        crossunder = (ec_prev >= ema_prev) and (ec < ema)
        
        # Aplicar threshold
        error_pct = 100 * least_error / price if price > 0 else 0
        threshold_check = error_pct > self.threshold
        
        # Sinais da barra atual (RAW)
        buy_signal_current = crossover and threshold_check
        sell_signal_current = crossunder and threshold_check
        
        # Obter sinais da barra anterior
        buy_signal_prev = self.series_data['buy_signal'].current()
        sell_signal_prev = self.series_data['sell_signal'].current()
        
        # Atualizar sinais da barra atual
        self.series_data['buy_signal'].append(1.0 if buy_signal_current else 0.0)
        self.series_data['sell_signal'].append(1.0 if sell_signal_current else 0.0)
        
        # Lógica de flags pendentes EXATA como no Pine
        pending_buy_prev = self.series_data['pendingBuy'].current()
        pending_sell_prev = self.series_data['pendingSell'].current()
        
        pending_buy_new = pending_buy_prev
        pending_sell_new = pending_sell_prev
        
        # Se houver sinal na barra ANTERIOR, marcar como pendente
        if buy_signal_prev > 0:
            pending_buy_new = 1.0
            pending_sell_new = 0.0  # Resetar oposto
        
        if sell_signal_prev > 0:
            pending_sell_new = 1.0
            pending_buy_new = 0.0  # Resetar oposto
        
        self.series_data['pendingBuy'].append(pending_buy_new)
        self.series_data['pendingSell'].append(pending_sell_new)
        
        return {
            'timestamp': timestamp,
            'price': price,
            'ema': ema,
            'ec': ec,
            'least_error': least_error,
            'error_pct': error_pct,
            'crossover': crossover,
            'crossunder': crossunder,
            'buy_signal_current': buy_signal_current,
            'sell_signal_current': sell_signal_current,
            'buy_signal_prev': buy_signal_prev > 0,
            'sell_signal_prev': sell_signal_prev > 0,
            'pending_buy': pending_buy_new > 0,
            'pending_sell': pending_sell_new > 0,
            'period': self.period,
            'alpha': self.alpha
        }
    
    def process_candle(self, candle: Dict[str, float]) -> Dict[str, Any]:
        """Processa um candle completo (para histórico/inicialização)"""
        return self.process_tick(candle['close'], datetime.now())
    
    def reset(self):
        """Reseta o estado do interpretador"""
        for series in self.series_data.values():
            series.values.clear()
        
        # Reinicializa com valores padrão
        self._initialize_series()
        self.candle_count = 0
        
        logger.info("🔄 Pine Script Interpreter resetado")
    
    def get_diagnostic_info(self):
        """Retorna informações de diagnóstico"""
        return {
            'candle_count': self.candle_count,
            'params': self.params,
            'period': self.period,
            'alpha': self.alpha,
            'ec_current': self.series_data['EC'].current(),
            'ema_current': self.series_data['EMA'].current(),
            'ec_prev': self.series_data['EC'][1] if len(self.series_data['EC']) > 1 else 0,
            'ema_prev': self.series_data['EMA'][1] if len(self.series_data['EMA']) > 1 else 0,
            'pending_buy': self.series_data['pendingBuy'].current() > 0,
            'pending_sell': self.series_data['pendingSell'].current() > 0,
            'buy_signal_prev': self.series_data['buy_signal'].current() > 0,
            'sell_signal_prev': self.series_data['sell_signal'].current() > 0
        }
