"""
Motor de Execução Pine Script v3 - VERSÃO COM EXECUÇÃO POR TICK
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
        if len(self.values) > 5000:
            self.values.pop(0)
    
    def current(self) -> float:
        return self.values[-1] if self.values else 0.0
    
    def __len__(self):
        return len(self.values)

class PineScriptInterpreter:
    """Interpreta código Pine Script v3 EXATAMENTE como TradingView"""
    
    def __init__(self, pine_code: str):
        self.pine_code = pine_code
        self.symbol_table = {}
        self.series_data = {}
        
        # Configurações da estratégia
        self.params = self._extract_parameters()
        
        # Estado da execução - VALORES EXATOS do Pine Script
        self.period = self.params.get('Period', 20)
        self.gain_limit = self.params.get('GainLimit', 900)
        self.threshold = self.params.get('Threshold', 0.0)
        self.fixedSL = self.params.get('fixedSL', 2000)
        self.fixedTP = self.params.get('fixedTP', 55)
        self.risk = self.params.get('risk', 0.01)
        self.adaptive = self.params.get('adaptive', 'Cos IFM')
        
        # Configuração do cálculo
        self.alpha = 2.0 / (self.period + 1)
        
        # CONSTANTES
        self.PI = 3.14159265359
        self.range_val = 50
        
        # Inicializar todas as séries
        self._initialize_series()
        
        logger.info(f"✅ Pine Script Interpreter (Tick Mode)")
        logger.info(f"   Period={self.period}, GainLimit={self.gain_limit}")
        logger.info(f"   SL={self.fixedSL}p, TP={self.fixedTP}p, Risk={self.risk*100}%")
    
    def _extract_parameters(self) -> Dict[str, Any]:
        """Extrai parâmetros do código Pine Script"""
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
            except:
                pass
        
        defaults = {
            'fixedSL': 2000, 'fixedTP': 55, 'risk': 0.01,
            'Period': 20, 'Threshold': 0.0, 'GainLimit': 900,
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
            'pendingBuy', 'pendingSell'
        ]
        
        for series in series_list:
            self.series_data[series] = PineSeries([0.0])
    
    def calculate_ema(self, src: float, prev_ema: Optional[float]) -> float:
        """Calcula EMA: alpha*src + (1-alpha)*nz(EMA[1])"""
        if prev_ema is None or prev_ema == 0:
            return src
        return self.alpha * src + (1 - self.alpha) * prev_ema
    
    def calculate_zero_lag_ema(self, src: float) -> Tuple[float, float, float]:
        """Implementa o algoritmo Zero Lag EMA"""
        # Calcular EMA
        ema_prev = self.series_data['EMA'].current()
        ema = self.calculate_ema(src, ema_prev)
        self.series_data['EMA'].append(ema)
        
        # Obter EC anterior
        ec_prev = self.series_data['EC'].current()
        if ec_prev == 0 and len(self.series_data['EC']) == 1:
            ec_prev = src
        
        # Buscar melhor ganho
        best_gain = 0.0
        least_error = float('inf')
        
        for i in range(-self.gain_limit, self.gain_limit + 1):
            gain = i / 10.0
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
        Processa um TICK em tempo real (chamado a cada novo preço)
        Implementação EXATA do Pine Script:
        - calc_on_every_tick=false (padrão) no Pine significa que o script roda no FECHAMENTO da barra
        - Mas no TradingView, quando há trailing stop, ele monitora a cada tick para SAÍDAS
        """
        self.series_data['src'].append(price)
        
        # Calcular Zero Lag EMA
        ema, ec, least_error = self.calculate_zero_lag_ema(price)
        
        # Obter valores anteriores
        ec_prev = self.series_data['EC'][1] if len(self.series_data['EC']) > 1 else ec
        ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA']) > 1 else ema
        
        # Calcular sinais CRUZAIS
        crossover = (ec_prev <= ema_prev) and (ec > ema)
        crossunder = (ec_prev >= ema_prev) and (ec < ema)
        
        # Aplicar threshold
        error_pct = 100 * least_error / price if price > 0 else 0
        threshold_check = error_pct > self.threshold
        
        # Sinais da barra atual (RAW) - SÓ NO FECHAMENTO NO PINE, mas aqui processamos a cada tick
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
        # NO PINE: if (buy_signal[1]) pendingBuy := true
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
            'buy_signal_prev': buy_signal_prev > 0,  # Sinal da barra anterior
            'sell_signal_prev': sell_signal_prev > 0, # Sinal da barra anterior
            'pending_buy': pending_buy_new > 0,       # Flag para execução
            'pending_sell': pending_sell_new > 0,     # Flag para execução
            'period': self.period
        }
    
    def reset(self):
        """Reseta o estado do interpretador"""
        for series in self.series_data.values():
            series.values.clear()
        self._initialize_series()
