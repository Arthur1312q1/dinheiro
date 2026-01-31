"""
Motor de Execução Pine Script v3
Interpreta e executa estratégias Pine Script diretamente em Python
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
        if len(self.values) > 500:  # Mantém histórico limitado
            self.values.pop(0)
    
    def current(self) -> float:
        return self.values[-1] if self.values else 0.0
    
    def __len__(self):
        return len(self.values)

class PineScriptInterpreter:
    """Interpreta e executa código Pine Script v3"""
    
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.symbol_table = {}
        self.series_data = {}
        
        # Contador de candles processados
        self.candle_count = 0
        
        # Configurações da estratégia (extraídas do código)
        self.params = self._extract_parameters()
        
        # Estado da execução
        self.period = self.params.get('Period', 20)
        self.gain_limit = self.params.get('GainLimit', 900)  # CORREÇÃO: 900 do Pine Script
        self.threshold = self.params.get('Threshold', 0.0)   # CORREÇÃO: 0.0 do Pine Script
        self.fixedSL = self.params.get('fixedSL', 2000)      # NOVO: extrair SL
        self.fixedTP = self.params.get('fixedTP', 55)        # NOVO: extrair TP
        self.risk = self.params.get('risk', 0.01)           # NOVO: extrair risk
        
        # Séries temporais
        self.series_data['src'] = PineSeries([])
        self.series_data['EC'] = PineSeries([])
        self.series_data['EMA'] = PineSeries([])
        self.series_data['LeastError'] = PineSeries([])
        self.series_data['pendingBuy'] = PineSeries([0.0])
        self.series_data['pendingSell'] = PineSeries([0.0])
        self.series_data['buy_signal'] = PineSeries([0.0])
        self.series_data['sell_signal'] = PineSeries([0.0])
        
        # Inicializa métodos adaptativos
        self._init_adaptive_methods()
        
        logger.info(f"✅ Pine Script Interpreter inicializado")
        logger.info(f"   Period={self.period}, Threshold={self.threshold}, GainLimit={self.gain_limit}")
        logger.info(f"   fixedSL={self.fixedSL}, fixedTP={self.fixedTP}, risk={self.risk}")
        logger.info(f"   Código Pine tamanho: {len(pine_code)} bytes")
    
    def _extract_parameters(self) -> Dict[str, Any]:
        """Extrai parâmetros do código Pine Script CORRETAMENTE"""
        params = {}
        
        # Padrões para encontrar valores default
        patterns = {
            'Period': r'Period.*defval\s*=\s*(\d+)',
            'GainLimit': r'GainLimit.*defval\s*=\s*(\d+)',
            'Threshold': r'Threshold.*defval=([\d.]+)',
            'fixedSL': r'fixedSL.*defval=(\d+)',
            'fixedTP': r'fixedTP.*defval=(\d+)',
            'risk': r'risk.*defval=([\d.]+)',
            'adaptive': r'adaptive.*defval="([^"]+)"'
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, self.pine_code, re.IGNORECASE)
            if match:
                value = match.group(1)
                if key in ['Period', 'GainLimit', 'fixedSL', 'fixedTP']:
                    params[key] = int(value)
                elif key in ['Threshold', 'risk']:
                    params[key] = float(value)
                else:
                    params[key] = value
        
        # Valores padrão se não encontrar
        defaults = {
            'fixedSL': 2000,
            'fixedTP': 55,
            'risk': 0.01,
            'Period': 20,
            'Threshold': 0.0,  # CORREÇÃO: 0.0 do Pine Script original
            'GainLimit': 900   # CORREÇÃO: 900 do Pine Script original
        }
        
        for key, default in defaults.items():
            if key not in params:
                params[key] = default
        
        return params
    
    def _init_adaptive_methods(self):
        """Inicializa variáveis para métodos adaptativos"""
        self.series_data['lenIQ'] = PineSeries([0.0])
        self.series_data['lenC'] = PineSeries([0.0])
        self.series_data['re'] = PineSeries([0.0])
        self.series_data['im'] = PineSeries([0.0])
        self.series_data['s2'] = PineSeries([0.0])
        self.series_data['s3'] = PineSeries([0.0])
    
    def calculate_ema(self, src: float, prev_ema: Optional[float]) -> float:
        """Calcula EMA padrão como no Pine Script"""
        alpha = 2.0 / (self.period + 1)
        if prev_ema is None:
            return src
        return alpha * src + (1 - alpha) * prev_ema
    
    def calculate_zero_lag_ema(self, src: float) -> Tuple[float, float, float]:
        """
        Implementa o algoritmo Zero Lag EMA do Pine Script
        Retorna: (EMA, EC, LeastError)
        """
        # Calcular EMA
        ema_prev = self.series_data['EMA'].current()
        ema = self.calculate_ema(src, ema_prev)
        self.series_data['EMA'].append(ema)
        
        # Inicializar EC se necessário
        ec_prev = self.series_data['EC'].current()
        if ec_prev == 0 and len(self.series_data['EC']) == 1:
            ec_prev = src
        
        # Buscar melhor ganho
        alpha = 2.0 / (self.period + 1)
        best_gain = 0.0
        least_error = float('inf')
        
        # CORREÇÃO: Testar ganhos de -GainLimit a +GainLimit (passo 0.1)
        # Como no Pine Script: for i = -GainLimit to GainLimit
        # Gain := i/10.0
        gain_step = 0.1
        start_gain = -self.gain_limit * gain_step
        end_gain = self.gain_limit * gain_step
        
        i = start_gain
        while i <= end_gain:
            gain = i
            
            # Calcular EC com este ganho
            ec_test = alpha * (ema + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
            
            i += gain_step
        
        # Calcular EC final com melhor ganho
        ec = alpha * (ema + best_gain * (src - ec_prev)) + (1 - alpha) * ec_prev
        self.series_data['EC'].append(ec)
        self.series_data['LeastError'].append(least_error)
        
        return ema, ec, least_error
    
    def process_candle(self, candle: Dict[str, float]) -> Dict[str, Any]:
        """
        Processa um candle através da estratégia Pine Script
        Retorna sinais RAW (não executa trades).
        A execução é controlada pelo StrategyRunner.
        candle: {'open': x, 'high': x, 'low': x, 'close': x, 'volume': x}
        """
        self.candle_count += 1
        src = candle['close']
        self.series_data['src'].append(src)
        
        # 1. Calcular EMA e EC (Zero Lag)
        ema, ec, least_error = self.calculate_zero_lag_ema(src)
        
        # 2. Verificar sinais RAW
        # Precisamos de valores anteriores para crossover/crossunder
        ec_prev = self.series_data['EC'][1] if len(self.series_data['EC']) > 1 else ec
        ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA']) > 1 else ema
        
        # Crossover (EC cruza EMA para cima) - igual a crossover(EC, EMA) no Pine
        crossover_signal = (ec_prev <= ema_prev) and (ec > ema)
        
        # Crossunder (EC cruza EMA para baixo) - igual a crossunder(EC, EMA) no Pine
        crossunder_signal = (ec_prev >= ema_prev) and (ec < ema)
        
        # CORREÇÃO: Aplicar threshold (100*LeastError/src > Threshold)
        # No Pine Script original: 100*LeastError/src > Threshold
        error_pct = 100 * least_error / src if src > 0 else 0
        threshold_check = error_pct > self.threshold
        
        # Sinais RAW - estes serão usados na PRÓXIMA barra no StrategyRunner
        buy_signal_raw = crossover_signal and threshold_check
        sell_signal_raw = crossunder_signal and threshold_check
        
        # Log detalhado para primeiros candles ou quando há sinal
        if self.candle_count <= 10 or buy_signal_raw or sell_signal_raw:
            logger.info(f"📊 Candle #{self.candle_count}: Preço=${src:.2f}")
            logger.info(f"   EMA={ema:.2f}, EC={ec:.2f}, Erro={error_pct:.2f}%")
            logger.info(f"   EC anterior={ec_prev:.2f}, EMA anterior={ema_prev:.2f}")
            logger.info(f"   Crossover: {crossover_signal}, Crossunder: {crossunder_signal}")
            logger.info(f"   Threshold check ({error_pct:.2f}% > {self.threshold}): {threshold_check}")
            
            if buy_signal_raw:
                logger.info(f"   🟢🟢🟢 SINAL BUY RAW DETECTADO! 🟢🟢🟢")
            elif sell_signal_raw:
                logger.info(f"   🔴🔴🔴 SINAL SELL RAW DETECTADO! 🔴🔴🔴")
        
        # 3. Resultado
        result = {
            'signal': 'HOLD',
            'strength': 0,
            'buy_signal_raw': buy_signal_raw,
            'sell_signal_raw': sell_signal_raw,
            'price': src,
            'ema': ema,
            'ec': ec,
            'least_error': least_error,
            'error_pct': error_pct,
            'candle_number': self.candle_count,
            'timestamp': datetime.now().isoformat(),
            'crossover': crossover_signal,
            'crossunder': crossunder_signal,
            'threshold_check': threshold_check
        }
        
        return result
    
    def reset(self):
        """Reseta o estado do interpretador"""
        for series in self.series_data.values():
            series.values.clear()
        
        # Reinicializa com valores padrão
        self.series_data['pendingBuy'] = PineSeries([0.0])
        self.series_data['pendingSell'] = PineSeries([0.0])
        self.series_data['buy_signal'] = PineSeries([0.0])
        self.series_data['sell_signal'] = PineSeries([0.0])
        
        self.candle_count = 0
        
        logger.info("🔄 Pine Script Interpreter resetado")
