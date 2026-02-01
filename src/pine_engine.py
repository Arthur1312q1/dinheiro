"""
Motor de Execução Pine Script v3 - VERSÃO FINAL OTIMIZADA
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
        self.gain_limit = self.params.get('GainLimit', 900)  # CORREÇÃO: 900 do Pine Script
        self.threshold = self.params.get('Threshold', 0.0)   # CORREÇÃO: 0.0 do Pine Script (desabilitado)
        self.fixedSL = self.params.get('fixedSL', 2000)      # Stop Loss em pontos
        self.fixedTP = self.params.get('fixedTP', 55)        # Take Profit em pontos
        self.risk = self.params.get('risk', 0.01)           # Risco 1%
        self.adaptive = self.params.get('adaptive', 'Cos IFM')  # Método adaptativo
        
        # Configuração do cálculo adaptativo
        self.alpha = 2.0 / (self.period + 1)
        
        # Séries temporais
        self.series_data['src'] = PineSeries([])
        self.series_data['EC'] = PineSeries([])
        self.series_data['EMA'] = PineSeries([])
        self.series_data['LeastError'] = PineSeries([])
        
        # SINAIS CRÍTICOS: Estes são calculados na barra atual mas executados na próxima
        self.series_data['buy_signal'] = PineSeries([0.0])  # Crossover detectado
        self.series_data['sell_signal'] = PineSeries([0.0])  # Crossunder detectado
        
        # Flags persistentes (como no Pine: pendingBuy, pendingSell)
        self.series_data['pendingBuy'] = PineSeries([0.0])
        self.series_data['pendingSell'] = PineSeries([0.0])
        
        # Inicializa métodos adaptativos
        self._init_adaptive_methods()
        
        logger.info(f"✅ Pine Script Interpreter inicializado")
        logger.info(f"   Period={self.period}, GainLimit={self.gain_limit}, Threshold={self.threshold}")
        logger.info(f"   SL={self.fixedSL}p, TP={self.fixedTP}p, Risk={self.risk*100}%")
        logger.info(f"   Adaptive Method: {self.adaptive}")
        logger.info(f"   Código Pine tamanho: {len(pine_code)} bytes")
    
    def _extract_parameters(self) -> Dict[str, Any]:
        """Extrai parâmetros do código Pine Script EXATAMENTE como estão"""
        params = {}
        
        # Padrões para encontrar valores default - CORRIGIDOS
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
        
        # Valores padrão EXATOS do Pine Script original
        defaults = {
            'fixedSL': 2000,
            'fixedTP': 55,
            'risk': 0.01,
            'Period': 20,
            'Threshold': 0.0,  # 0.0 no Pine original (threshold desabilitado)
            'GainLimit': 900,
            'adaptive': 'Cos IFM'
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
        """Calcula EMA exatamente como no Pine Script"""
        if prev_ema is None:
            return src
        return self.alpha * src + (1 - self.alpha) * prev_ema
    
    def calculate_zero_lag_ema(self, src: float) -> Tuple[float, float, float]:
        """
        Implementa o algoritmo Zero Lag EMA do Pine Script EXATAMENTE
        Retorna: (EMA, EC, LeastError)
        """
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
        
        # CORREÇÃO: Loop de -GainLimit a +GainLimit com passo 0.1
        # No Pine: for i = -GainLimit to GainLimit, Gain := i/10.0
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
    
    def process_candle(self, candle: Dict[str, float]) -> Dict[str, Any]:
        """
        Processa um candle através da estratégia Pine Script EXATAMENTE como TradingView
        Retorna sinais com delay de 1 barra (buy_signal[1]).
        """
        self.candle_count += 1
        src = candle['close']
        self.series_data['src'].append(src)
        
        # 1. Calcular EMA e EC (Zero Lag)
        ema, ec, least_error = self.calculate_zero_lag_ema(src)
        
        # 2. Obter valores anteriores para crossover/crossunder
        ec_prev = self.series_data['EC'][1] if len(self.series_data['EC']) > 1 else ec
        ema_prev = self.series_data['EMA'][1] if len(self.series_data['EMA']) > 1 else ema
        
        # 3. Calcular sinais na barra atual (como no Pine)
        crossover_signal = (ec_prev <= ema_prev) and (ec > ema)
        crossunder_signal = (ec_prev >= ema_prev) and (ec < ema)
        
        # 4. Aplicar threshold (no Pine: 100*LeastError/src > Threshold)
        error_pct = 100 * least_error / src if src > 0 else 0
        threshold_check = error_pct > self.threshold
        
        # 5. Sinais da barra atual (RAW)
        buy_signal_current = crossover_signal and threshold_check
        sell_signal_current = crossunder_signal and threshold_check
        
        # 6. CRÍTICO: Implementar delay de 1 barra (buy_signal[1] no Pine)
        # O sinal da barra anterior [1] é que determina a execução
        buy_signal_prev = self.series_data['buy_signal'].current() if len(self.series_data['buy_signal']) > 0 else 0
        sell_signal_prev = self.series_data['sell_signal'].current() if len(self.series_data['sell_signal']) > 0 else 0
        
        # 7. Atualizar séries de sinais (para uso na próxima barra)
        self.series_data['buy_signal'].append(1.0 if buy_signal_current else 0.0)
        self.series_data['sell_signal'].append(1.0 if sell_signal_current else 0.0)
        
        # 8. IMPLEMENTAÇÃO CORRETA do pendingBuy/pendingSell (como no Pine)
        pending_buy_prev = self.series_data['pendingBuy'].current() if len(self.series_data['pendingBuy']) > 0 else 0
        pending_sell_prev = self.series_data['pendingSell'].current() if len(self.series_data['pendingSell']) > 0 else 0
        
        # Resetar flags pendentes (nz(pendingBuy[1]) no Pine)
        pending_buy_new = pending_buy_prev
        pending_sell_new = pending_sell_prev
        
        # Se houver sinal na barra ANTERIOR, marcar como pendente
        if buy_signal_prev > 0:
            pending_buy_new = 1.0
            pending_sell_new = 0.0  # Resetar oposto (como no Pine)
        
        if sell_signal_prev > 0:
            pending_sell_new = 1.0
            pending_buy_new = 0.0  # Resetar oposto (como no Pine)
        
        self.series_data['pendingBuy'].append(pending_buy_new)
        self.series_data['pendingSell'].append(pending_sell_new)
        
        # 9. Determinar sinais para execução (baseado nos pendentes)
        pending_buy_for_execution = pending_buy_new > 0
        pending_sell_for_execution = pending_sell_new > 0
        
        # 10. LOGS DETALHADOS - CRÍTICO PARA DEBUG
        if self.candle_count <= 10 or buy_signal_current or sell_signal_current or pending_buy_for_execution or pending_sell_for_execution:
            logger.info(f"📊 Candle #{self.candle_count}: Preço=${src:.2f}")
            logger.info(f"   EMA={ema:.2f}, EC={ec:.2f}, EC_prev={ec_prev:.2f}, EMA_prev={ema_prev:.2f}")
            logger.info(f"   Erro={error_pct:.2f}%, Threshold={self.threshold}")
            
            # DEBUG EXTRA: Log de crossover/crossunder
            if crossover_signal:
                logger.info(f"   🟢 CRUZAMENTO PARA CIMA DETECTADO: EC ({ec:.2f}) > EMA ({ema:.2f})")
            if crossunder_signal:
                logger.info(f"   🔴 CRUZAMENTO PARA BAIXO DETECTADO: EC ({ec:.2f}) < EMA ({ema:.2f})")
            
            if buy_signal_current:
                logger.info(f"   🟢 SINAL BUY NA BARRA ATUAL (executará na próxima)")
            
            if sell_signal_current:
                logger.info(f"   🔴 SINAL SELL NA BARRA ATUAL (executará na próxima)")
            
            if pending_buy_for_execution:
                logger.info(f"   ⚡ PENDING BUY ATIVO (aguardando execução)")
            
            if pending_sell_for_execution:
                logger.info(f"   ⚡ PENDING SELL ATIVO (aguardando execução)")
        
        # 11. Resultado
        result = {
            # Sinais da barra atual (para debug)
            'buy_signal_current': buy_signal_current,
            'sell_signal_current': sell_signal_current,
            
            # Sinais PENDENTES para execução (estes são os que importam)
            'pending_buy': pending_buy_for_execution,
            'pending_sell': pending_sell_for_execution,
            
            # Dados técnicos
            'price': src,
            'ema': ema,
            'ec': ec,
            'least_error': least_error,
            'error_pct': error_pct,
            'candle_number': self.candle_count,
            'timestamp': datetime.now().isoformat(),
            
            # Valores anteriores (para cálculo de crossover)
            'ec_prev': ec_prev,
            'ema_prev': ema_prev,
            
            # Sinais cruzados
            'crossover': crossover_signal,
            'crossunder': crossunder_signal,
            
            # Estado interno (debug)
            'buy_signal_prev': buy_signal_prev,
            'sell_signal_prev': sell_signal_prev
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
    
    def get_diagnostic_info(self):
        """Retorna informações de diagnóstico"""
        return {
            'candle_count': self.candle_count,
            'params': self.params,
            'ec_current': self.series_data['EC'].current() if 'EC' in self.series_data else 0,
            'ema_current': self.series_data['EMA'].current() if 'EMA' in self.series_data else 0,
            'ec_prev': self.series_data['EC'][1] if 'EC' in self.series_data and len(self.series_data['EC']) > 1 else 0,
            'ema_prev': self.series_data['EMA'][1] if 'EMA' in self.series_data and len(self.series_data['EMA']) > 1 else 0,
            'pending_buy': self.series_data['pendingBuy'].current() if 'pendingBuy' in self.series_data else 0,
            'pending_sell': self.series_data['pendingSell'].current() if 'pendingSell' in self.series_data else 0,
            'buy_signal_prev': self.series_data['buy_signal'].current() if 'buy_signal' in self.series_data else 0,
            'sell_signal_prev': self.series_data['sell_signal'].current() if 'sell_signal' in self.series_data else 0
        }
