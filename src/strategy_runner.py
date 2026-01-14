"""
Executor principal da estratégia Pine Script
Integra o interpretador com o cliente OKX
"""
import os
import logging
import time
from typing import Dict, List, Optional

from pine_engine import PineScriptInterpreter
from okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunner:
    """Executa a estratégia Pine Script com dados em tempo real"""
    
    def __init__(self, okx_client: OKXClient):
        self.okx_client = okx_client
        self.interpreter = None
        self.is_running = False
        self.last_processed_timestamp = 0  # Para evitar reprocessamento
        
        # Carregar código Pine Script do arquivo
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado com Pine Script")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self) -> Optional[str]:
        """Carrega o código Pine Script do arquivo"""
        try:
            # Tenta vários caminhos possíveis
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "./strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for script_path in possible_paths:
                if os.path.exists(script_path):
                    with open(script_path, 'r', encoding='utf-8') as f:
                        logger.info(f"✅ Carregado Pine Script de: {script_path}")
                        return f.read()
            
            logger.error("❌ Arquivo Pine Script não encontrado em nenhum local")
            return None
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None
    
    def start(self):
        """Inicia a execução da estratégia"""
        if not self.interpreter:
            logger.error("❌ Interpreter não inicializado")
            return False
        
        self.is_running = True
        self.last_processed_timestamp = 0  # Reset ao iniciar
        logger.info("🚀 Strategy Runner iniciado")
        return True
    
    def stop(self):
        """Para a execução da estratégia"""
        self.is_running = False
        logger.info("⏹️ Strategy Runner parado")
    
    def run_strategy_on_candles(self, candles: List[Dict]) -> Dict:
        """
        Executa a estratégia em uma lista de candles
        Apenas processa candles NOVOS para evitar sinais históricos
        """
        if not self.interpreter or not candles:
            return {"signal": "HOLD", "strength": 0}
        
        # Encontrar o candle mais recente
        latest_candle = candles[-1] if candles else None
        
        if not latest_candle:
            return {"signal": "HOLD", "strength": 0}
        
        # Verificar se este candle já foi processado
        if latest_candle['timestamp'] <= self.last_processed_timestamp:
            return {"signal": "HOLD", "strength": 0}
        
        # Apenas processar o ÚLTIMO candle (evitar processar histórico)
        logger.info(f"📊 Processando candle mais recente: Timestamp {latest_candle['timestamp']}")
        
        # Processar apenas o último candle
        result = self.interpreter.process_candle(latest_candle)
        self.last_processed_timestamp = latest_candle['timestamp']
        
        # Se houver sinal de trade, executar na OKX
        if result['signal'] in ['BUY', 'SELL'] and result['strength'] > 0:
            logger.info(f"📢 SINAL DETECTADO: {result['signal']} no preço ${result['price']:.2f}")
            
            # Calcular tamanho da posição
            position_size = self.okx_client.calculate_position_size()
            
            if position_size >= 0.01:  # Mínimo da OKX
                logger.info(f"📈 Executando {result['signal']} com {position_size:.4f} ETH")
                
                # Executar ordem na OKX
                success = self.okx_client.place_order(
                    side=result['signal'],
                    quantity=position_size
                )
                
                if success:
                    logger.info(f"✅ Ordem {result['signal']} executada na OKX")
                else:
                    logger.error(f"❌ Falha na ordem {result['signal']}")
            else:
                logger.warning(f"⚠️  Posição muito pequena ({position_size:.4f} ETH). Mínimo: 0.01 ETH")
        
        return result
    
    def get_strategy_status(self) -> Dict:
        """Retorna o status atual da estratégia"""
        if not self.interpreter:
            return {"status": "not_initialized"}
        
        return {
            "status": "running" if self.is_running else "stopped",
            "candle_count": self.interpreter.candle_count,
            "parameters": self.interpreter.params,
            "pending_buy": self.interpreter.series_data['pendingBuy'].current() > 0,
            "pending_sell": self.interpreter.series_data['pendingSell'].current() > 0,
            "last_processed_timestamp": self.last_processed_timestamp
        }
