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

class PineScriptInterpreter:
    """Interpreta e executa código Pine Script v3"""
    
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.symbol_table = {}
        self.series_data = {}
        self.candle_count = 0
        
        # Constantes do Pine Script
        self.symbol_table['PI'] = 3.14159265359
        self.symbol_table['true'] = True
        self.symbol_table['false'] = False
        
        # Configurações da estratégia (extraídas do código)
        self.params = self._extract_parameters()
        
        # Estado da execução
        self.period = self.params.get('Period', 20)
        self.gain_limit = self.params.get('GainLimit', 8)
        self.threshold = self.params.get('Threshold', 0.05)
        
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
        
        logger.info(f"✅ Pine Script Interpreter inicializado: Period={self.period}, "
                   f"Threshold={self.threshold}, GainLimit={self.gain_limit}")
    
    def _extract_parameters(self) -> Dict[str, Any]:
        """Extrai parâmetros do código Pine Script"""
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
        if ec_prev == 0 and len(self.series_data['EC'].values) == 1:
            ec_prev = src
        
        # Buscar melhor ganho
        alpha = 2.0 / (self.period + 1)
        best_gain = 0.0
        least_error = float('inf')
        
        # Testar ganhos de -GainLimit a +GainLimit (passo 0.1)
        for i in range(-self.gain_limit, self.gain_limit + 1):
            gain = i / 10.0
            
            # Calcular EC com este ganho
            ec_test = alpha * (ema + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
        
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
        ec_prev = self.series_data['EC'][1] if len(self.series_data['EC'].values) > 1 else ec
        ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA'].values) > 1 else ema
        
        # Crossover (EC cruza EMA para cima) - igual a crossover(EC, EMA) no Pine
        crossover_signal = (ec_prev <= ema_prev) and (ec > ema)
        
        # Crossunder (EC cruza EMA para baixo) - igual a crossunder(EC, EMA) no Pine
        crossunder_signal = (ec_prev >= ema_prev) and (ec < ema)
        
        # Aplicar threshold (100*LeastError/src > Threshold)
        error_pct = 100 * least_error / src if src > 0 else 0
        threshold_check = error_pct > self.threshold
        
        # Sinais RAW - estes serão usados na PRÓXIMA barra no StrategyRunner
        buy_signal_raw = crossover_signal and threshold_check
        sell_signal_raw = crossunder_signal and threshold_check
        
        # 3. Resultado (SOMENTE sinais RAW, sem execução)
        result = {
            'signal': 'HOLD',  # Sempre HOLD - execução é no StrategyRunner
            'strength': 0,
            'buy_signal_raw': buy_signal_raw,    # Para usar na PRÓXIMA barra
            'sell_signal_raw': sell_signal_raw,  # Para usar na PRÓXIMA barra
            'price': src,
            'ema': ema,
            'ec': ec,
            'least_error': least_error,
            'error_pct': error_pct,
            'candle_number': self.candle_count,
            'timestamp': datetime.now().isoformat()
        }
        
        # Log detalhado apenas quando há sinal RAW
        if buy_signal_raw or sell_signal_raw:
            logger.info(f"📊 Candle {self.candle_count}: Preço=${src:.2f}")
            logger.info(f"   EMA={ema:.2f}, EC={ec:.2f}, Erro={error_pct:.2f}%")
            logger.info(f"   Sinal RAW: {'BUY' if buy_signal_raw else 'SELL' if sell_signal_raw else 'NONE'}")
        
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
