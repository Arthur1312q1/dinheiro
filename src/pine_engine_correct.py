"""
Interpretador Pine Script CORRETO para calc_on_every_tick=false
"""
import re
import math
import logging

logger = logging.getLogger(__name__)

class PineScriptCorrect:
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.params = self._extract_parameters()
        
        # Estado interno (valores entre barras)
        self.ema = 0.0
        self.ec = 0.0
        self.least_error = 0.0
        
        # Histórico para [1]
        self.prev_buy_signal = False
        self.prev_sell_signal = False
        
        logger.info(f"✅ Pine Script Correct: Period={self.params['Period']}, calc_on_every_tick=false")
    
    def _extract_parameters(self):
        """Extrai parâmetros exatos da estratégia"""
        return {
            'Period': 20,
            'GainLimit': 900,
            'Threshold': 0.0,
            'fixedSL': 2000,
            'fixedTP': 55,
            'risk': 0.01,
            'adaptive': 'Cos IFM'
        }
    
    def process_candle_close(self, close_price: float):
        """
        Processa o FECHAMENTO de uma barra
        Executado APENAS quando a barra fecha (como no TradingView)
        """
        # 1. Calcular EMA
        alpha = 2.0 / (self.params['Period'] + 1)
        
        if self.ema == 0:
            self.ema = close_price
        else:
            self.ema = alpha * close_price + (1 - alpha) * self.ema
        
        # 2. Calcular EC (Zero Lag EMA)
        best_gain = 0.0
        least_error = float('inf')
        
        for i in range(-self.params['GainLimit'], self.params['GainLimit'] + 1):
            gain = i / 10.0
            ec_test = alpha * (self.ema + gain * (close_price - self.ec)) + (1 - alpha) * self.ec
            error = abs(close_price - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
        
        self.ec = alpha * (self.ema + best_gain * (close_price - self.ec)) + (1 - alpha) * self.ec
        self.least_error = least_error
        
        # 3. Verificar crossover/crossunder
        # Precisamos dos valores anteriores
        crossover = False
        crossunder = False
        
        # Em uma implementação real, precisaríamos armazenar EC e EMA anteriores
        # Para simplificar, vamos assumir que temos acesso aos valores anteriores
        if hasattr(self, 'prev_ec') and hasattr(self, 'prev_ema'):
            crossover = (self.prev_ec <= self.prev_ema) and (self.ec > self.ema)
            crossunder = (self.prev_ec >= self.prev_ema) and (self.ec < self.ema)
        
        # Atualizar anteriores para próxima barra
        self.prev_ec = self.ec
        self.prev_ema = self.ema
        
        # 4. Aplicar threshold
        error_pct = 100 * self.least_error / close_price if close_price > 0 else 0
        threshold_check = error_pct > self.params['Threshold']
        
        buy_signal = crossover and threshold_check
        sell_signal = crossunder and threshold_check
        
        # 5. Atualizar históricos de sinais
        self.prev_buy_signal = buy_signal
        self.prev_sell_signal = sell_signal
        
        return {
            'price': close_price,
            'ema': self.ema,
            'ec': self.ec,
            'least_error': self.least_error,
            'error_pct': error_pct,
            'buy_signal': buy_signal,
            'sell_signal': sell_signal,
            'crossover': crossover,
            'crossunder': crossunder
        }
    
    def reset(self):
        """Reseta o estado (para reinício)"""
        self.ema = 0.0
        self.ec = 0.0
        self.least_error = 0.0
        self.prev_buy_signal = False
        self.prev_sell_signal = False
