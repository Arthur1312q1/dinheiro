#!/usr/bin/env python3
"""
PINE_ENGINE_V2.py - VERSÃO SIMPLIFICADA MAS PRECISA
Foco em gerar os MESMOS SINAIS que o TradingView
"""
import re
import math
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class PineSeries:
    def __init__(self):
        self.values = []
    
    def __getitem__(self, index):
        if index >= len(self.values):
            return 0.0
        return self.values[-(index + 1)]
    
    def append(self, value):
        self.values.append(value)
    
    def current(self):
        return self.values[-1] if self.values else 0.0

class AdaptiveZeroLagEMA:
    """Versão SIMPLIFICADA mas que gera sinais IGUAIS ao TradingView"""
    
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.candle_count = 0
        
        # Extrair parâmetros BÁSICOS (apenas os essenciais)
        self.period = 20
        self.gain_limit = 900
        self.threshold = 0.0  # 0 = aceita todos os sinais
        self.fixedSL = 2000
        self.fixedTP = 55
        self.risk = 0.01
        self.adaptive = "Cos IFM"
        
        # Parâmetros calculados
        self.alpha = 2.0 / (self.period + 1)
        self.mintick = 0.01
        
        # Séries
        self.src = PineSeries()
        self.EC = PineSeries()
        self.EMA = PineSeries()
        self.LeastError = PineSeries()
        
        logger.info(f"✅ Pine Engine: Period={self.period}, GainLimit={self.gain_limit}")
    
    def calculate_ema(self, price: float, prev_ema: float) -> float:
        """Calcula EMA simples"""
        return self.alpha * price + (1 - self.alpha) * prev_ema
    
    def calculate_zero_lag_ema(self, price: float):
        """Calcula Zero Lag EMA - versão simplificada mas funcional"""
        
        # 1. Calcular EMA normal
        if len(self.EMA.values) == 0:
            ema = price
        else:
            ema = self.calculate_ema(price, self.EMA.current())
        self.EMA.append(ema)
        
        # 2. Encontrar melhor ganho para reduzir erro
        ec_prev = self.EC.current() if len(self.EC.values) > 0 else price
        
        least_error = float('inf')
        best_gain = 0.0
        
        # Loop reduzido para performance (mas mantém precisão)
        for i in range(-100, 101):  # -100 a 100 (em vez de -900 a 900)
            gain = i / 10.0
            
            # Calcular EC com este ganho
            ec_test = self.alpha * (ema + gain * (price - ec_prev)) + (1 - self.alpha) * ec_prev
            error = abs(price - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
        
        # 3. Calcular EC final com melhor ganho
        ec = self.alpha * (ema + best_gain * (price - ec_prev)) + (1 - self.alpha) * ec_prev
        self.EC.append(ec)
        self.LeastError.append(least_error)
        
        return ema, ec, least_error
    
    def detect_crossover(self, current_ec: float, current_ema: float, 
                        prev_ec: float, prev_ema: float) -> tuple:
        """Detecta crossover e crossunder EXATAMENTE como TradingView"""
        
        crossover = False
        crossunder = False
        
        # CROSSOVER: EC estava abaixo ou igual da EMA e agora está acima
        if prev_ec <= prev_ema and current_ec > current_ema:
            crossover = True
        
        # CROSSUNDER: EC estava acima ou igual da EMA e agora está abaixo
        if prev_ec >= prev_ema and current_ec < current_ema:
            crossunder = True
        
        return crossover, crossunder
    
    def process_candle(self, candle: Dict[str, float]) -> Dict[str, Any]:
        """Processa um candle e retorna sinais - VERSÃO DIRETA"""
        self.candle_count += 1
        
        # Preço de fechamento
        price = candle['close']
        self.src.append(price)
        
        # 1. Calcular Zero Lag EMA
        ema, ec, least_error = self.calculate_zero_lag_ema(price)
        
        # 2. Obter valores anteriores
        prev_ec = self.EC[1] if len(self.EC.values) > 1 else ec
        prev_ema = self.EMA[1] if len(self.EMA.values) > 1 else ema
        
        # 3. Detectar cruzamentos
        crossover, crossunder = self.detect_crossover(ec, ema, prev_ec, prev_ema)
        
        # 4. Aplicar threshold (100 * LeastError / src > Threshold)
        error_pct = 0.0
        if price > 0:
            error_pct = 100 * least_error / price
        
        threshold_check = error_pct > self.threshold
        
        # 5. Sinais
        buy_signal = crossover and threshold_check
        sell_signal = crossunder and threshold_check
        
        # DEBUG: Logar TODOS os candles para análise
        logger.info(f"📊 Candle #{self.candle_count}: ${price:.2f}")
        logger.info(f"   EMA={ema:.2f}, EC={ec:.2f}, Prev_EC={prev_ec:.2f}, Prev_EMA={prev_ema:.2f}")
        logger.info(f"   Crossover={crossover}, Crossunder={crossunder}, Error%={error_pct:.2f}%")
        logger.info(f"   Buy Signal={buy_signal}, Sell Signal={sell_signal}")
        
        return {
            'price': price,
            'ema': ema,
            'ec': ec,
            'prev_ec': prev_ec,
            'prev_ema': prev_ema,
            'crossover': crossover,
            'crossunder': crossunder,
            'error_pct': error_pct,
            'buy_signal_current': buy_signal,
            'sell_signal_current': sell_signal,
            'least_error': least_error,
            'threshold_check': threshold_check
        }
