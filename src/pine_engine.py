import re
import math
import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class PineSeries:
    values: List[float]
    
    def __getitem__(self, index: int) -> float:
        if index >= len(self.values):
            return 0.0
        return self.values[-(index + 1)]
    
    def append(self, value: float):
        self.values.append(value)
        if len(self.values) > 500:
            self.values.pop(0)
    
    def current(self) -> float:
        return self.values[-1] if self.values else 0.0

class PineScriptInterpreter:
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.symbol_table = {}
        self.series_data = {}
        self.candle_count = 0
        
        self.symbol_table['PI'] = 3.14159265359
        self.symbol_table['true'] = True
        self.symbol_table['false'] = False
        
        self.params = self._extract_parameters()
        self.period = self.params.get('Period', 20)
        self.gain_limit = self.params.get('GainLimit', 8)
        self.threshold = self.params.get('Threshold', 0.05)
        
        self.series_data['src'] = PineSeries([])
        self.series_data['EC'] = PineSeries([])
        self.series_data['EMA'] = PineSeries([])
        self.series_data['LeastError'] = PineSeries([])
        self.series_data['pendingBuy'] = PineSeries([0.0])
        self.series_data['pendingSell'] = PineSeries([0.0])
        self.series_data['buy_signal'] = PineSeries([0.0])
        self.series_data['sell_signal'] = PineSeries([0.0])
        
        self._init_adaptive_methods()
        
        logger.info(f"✅ Pine Script Interpreter inicializado")
    
    def _extract_parameters(self) -> Dict[str, Any]:
        params = {}
        patterns = {
            'Period': r'Period.*defval\s*=\s*(\d+)',
            'GainLimit': r'GainLimit.*defval\s*=\s*(\d+)',
            'Threshold': r'Threshold.*defval=([\d.]+)',
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, self.pine_code, re.IGNORECASE)
            if match:
                value = match.group(1)
                if key in ['Period', 'GainLimit']:
                    params[key] = int(value)
                elif key in ['Threshold']:
                    params[key] = float(value)
        
        return params
    
    def _init_adaptive_methods(self):
        self.series_data['lenIQ'] = PineSeries([0.0])
        self.series_data['lenC'] = PineSeries([0.0])
        self.series_data['re'] = PineSeries([0.0])
        self.series_data['im'] = PineSeries([0.0])
        self.series_data['s2'] = PineSeries([0.0])
        self.series_data['s3'] = PineSeries([0.0])
    
    def calculate_ema(self, src: float, prev_ema: Optional[float]) -> float:
        alpha = 2.0 / (self.period + 1)
        if prev_ema is None:
            return src
        return alpha * src + (1 - alpha) * prev_ema
    
    def calculate_zero_lag_ema(self, src: float) -> Tuple[float, float, float]:
        ema_prev = self.series_data['EMA'].current()
        ema = self.calculate_ema(src, ema_prev)
        self.series_data['EMA'].append(ema)
        
        ec_prev = self.series_data['EC'].current()
        if ec_prev == 0 and len(self.series_data['EC'].values) == 1:
            ec_prev = src
        
        alpha = 2.0 / (self.period + 1)
        best_gain = 0.0
        least_error = float('inf')
        
        for i in range(-self.gain_limit, self.gain_limit + 1):
            gain = i / 10.0
            ec_test = alpha * (ema + gain * (src - ec_prev)) + (1 - alpha) * ec_prev
            error = abs(src - ec_test)
            
            if error < least_error:
                least_error = error
                best_gain = gain
        
        ec = alpha * (ema + best_gain * (src - ec_prev)) + (1 - alpha) * ec_prev
        self.series_data['EC'].append(ec)
        self.series_data['LeastError'].append(least_error)
        
        return ema, ec, least_error
    
    def process_candle(self, candle: Dict[str, float], is_warming_up: bool = False) -> Dict[str, Any]:
        """Processa um candle. Se is_warming_up=True, ignora sinais de trade"""
        self.candle_count += 1
        src = candle['close']
        self.series_data['src'].append(src)
        
        ema, ec, least_error = self.calculate_zero_lag_ema(src)
        
        ec_prev = self.series_data['EC'][1] if len(self.series_data['EC'].values) > 1 else ec
        ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA'].values) > 1 else ema
        
        crossover_signal = (ec_prev <= ema_prev) and (ec > ema)
        crossunder_signal = (ec_prev >= ema_prev) and (ec < ema)
        
        error_pct = 100 * least_error / src if src > 0 else 0
        threshold_check = error_pct > self.threshold
        
        buy_signal_raw = crossover_signal and threshold_check
        sell_signal_raw = crossunder_signal and threshold_check
        
        self.series_data['buy_signal'].append(1.0 if buy_signal_raw else 0.0)
        self.series_data['sell_signal'].append(1.0 if sell_signal_raw else 0.0)
        
        buy_signal_prev = self.series_data['buy_signal'][1] > 0
        sell_signal_prev = self.series_data['sell_signal'][1] > 0
        
        pending_buy = self.series_data['pendingBuy'].current() > 0
        pending_sell = self.series_data['pendingSell'].current() > 0
        
        if buy_signal_prev:
            pending_buy = True
        if sell_signal_prev:
            pending_sell = True
        
        # LÓGICA CRÍTICA: Se estiver aquecendo, FORÇA HOLD
        if is_warming_up:
            action = "HOLD"
            action_strength = 0
        else:
            action = "HOLD"
            action_strength = 0
            position_size = 0
            
            if pending_buy and position_size <= 0:
                action = "BUY"
                action_strength = 1
                pending_buy = False
                if not is_warming_up:
                    logger.info(f"🚀 EXECUTANDO BUY (candle {self.candle_count})")
            
            elif pending_sell and position_size >= 0:
                action = "SELL"
                action_strength = 1
                pending_sell = False
                if not is_warming_up:
                    logger.info(f"🚀 EXECUTANDO SELL (candle {self.candle_count})")
        
        self.series_data['pendingBuy'].append(1.0 if pending_buy else 0.0)
        self.series_data['pendingSell'].append(1.0 if pending_sell else 0.0)
        
        result = {
            'signal': action,
            'strength': action_strength,
            'price': src,
            'ema': ema,
            'ec': ec,
            'least_error': least_error,
            'error_pct': error_pct,
            'candle_number': self.candle_count,
            'is_warming_up': is_warming_up
        }
        
        if action != "HOLD" and not is_warming_up:
            logger.info(f"📊 Candle {self.candle_count}: Preço=${src:.2f}, Sinal={action}")
        
        return result
    
    def reset(self):
        for series in self.series_data.values():
            series.values.clear()
        
        self.series_data['pendingBuy'] = PineSeries([0.0])
        self.series_data['pendingSell'] = PineSeries([0.0])
        self.series_data['buy_signal'] = PineSeries([0.0])
        self.series_data['sell_signal'] = PineSeries([0.0])
        self.candle_count = 0
        
        logger.info("🔄 Pine Script Interpreter resetado")
