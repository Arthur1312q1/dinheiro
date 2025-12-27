import numpy as np
import pandas as pd
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class AdaptiveZeroLagEMA:
    def __init__(self):
        self.period = 20
        self.gain_limit = 900
        self.threshold = 0
        self.alpha = 2 / (self.period + 1)
        
        # Estado interno
        self.ema = None
        self.ec = None
        self.prev_ec = None
        self.prev_ema = None
        
    def calculate_ema(self, price: float, prev_ema: Optional[float]) -> float:
        """Calcula EMA padrão"""
        if prev_ema is None:
            return price
        return self.alpha * price + (1 - self.alpha) * prev_ema
    
    def calculate_zero_lag_ema(self, price: float, prev_ema: Optional[float], 
                               prev_ec: Optional[float], best_gain: float) -> float:
        """Calcula EMA Zero Lag"""
        if prev_ema is None or prev_ec is None:
            return price
        
        # EMA padrão
        ema = self.calculate_ema(price, prev_ema)
        
        # EMA Zero Lag
        ec = self.alpha * (ema + best_gain * (price - prev_ec)) + (1 - self.alpha) * prev_ec
        return ema, ec
    
    def find_best_gain(self, prices: List[float]) -> float:
        """Encontra o melhor ganho para Zero Lag EMA"""
        if len(prices) < 2:
            return 0
        
        # Simular para encontrar melhor ganho
        best_gain = 0
        least_error = float('inf')
        
        for i in range(-self.gain_limit, self.gain_limit + 1):
            gain = i / 10
            error_sum = 0
            
            # Simulação com histórico limitado
            ema = prices[0]
            ec = prices[0]
            
            for price in prices[1:]:
                ema = self.calculate_ema(price, ema)
                ec = self.alpha * (ema + gain * (price - ec)) + (1 - self.alpha) * ec
                error = abs(price - ec)
                error_sum += error
            
            avg_error = error_sum / len(prices)
            
            if avg_error < least_error:
                least_error = avg_error
                best_gain = gain
        
        return best_gain
    
    def calculate_signals(self, candles: List[Dict]) -> Dict:
        """Calcula sinais de compra/venda baseado na estratégia"""
        if len(candles) < 30:  # Precisa de dados suficientes
            return {"signal": "HOLD", "strength": 0}
        
        closes = [c["close"] for c in candles]
        
        # Encontrar melhor ganho
        best_gain = self.find_best_gain(closes[-50:])  # Usar últimas 50 velas
        
        # Calcular EMA e EC para o último candle
        last_close = closes[-1]
        
        if self.ema is None or self.ec is None:
            self.ema = last_close
            self.ec = last_close
            self.prev_ema = last_close
            self.prev_ec = last_close
        
        # Calcular EMA atual
        ema_current = self.calculate_ema(last_close, self.ema)
        
        # Calcular EC atual
        ec_current = self.alpha * (ema_current + best_gain * (last_close - self.ec)) + (1 - self.alpha) * self.ec
        
        # Verificar cruzamento
        buy_signal = False
        sell_signal = False
        
        if self.prev_ec is not None and self.prev_ema is not None:
            # Cruzamento para cima (EC cruza EMA)
            if self.prev_ec <= self.prev_ema and ec_current > ema_current:
                buy_signal = True
            
            # Cruzamento para baixo (EC cruza EMA)
            if self.prev_ec >= self.prev_ema and ec_current < ema_current:
                sell_signal = True
        
        # Atualizar valores anteriores
        self.prev_ec = ec_current
        self.prev_ema = ema_current
        self.ec = ec_current
        self.ema = ema_current
        
        # Calcular força do sinal
        if buy_signal or sell_signal:
            error_pct = abs(last_close - ec_current) / last_close * 100
            strength = 1 if error_pct > self.threshold else 0
            
            if buy_signal and strength > 0:
                return {"signal": "BUY", "strength": strength, "price": last_close}
            elif sell_signal and strength > 0:
                return {"signal": "SELL", "strength": strength, "price": last_close}
        
        return {"signal": "HOLD", "strength": 0, "price": last_close}
