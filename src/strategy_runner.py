"""
Executor principal da estratégia Pine Script
Integra o interpretador com o cliente OKX
"""
import os
import logging
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
        """Carrega o código Pine Script do arquivo."""
        try:
            # Tenta vários caminhos possíveis
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "./strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "Adaptive_Zero_Lag_EMA_v2.pine"
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
        """Inicia a execução da estratégia."""
        if not self.interpreter:
            logger.error("❌ Interpreter não inicializado")
            return False
        
        self.is_running = True
        self.last_processed_timestamp = 0
        logger.info("🚀 Strategy Runner iniciado")
        return True
    
    def stop(self):
        """Para a execução da estratégia."""
        self.is_running = False
        logger.info("⏹️ Strategy Runner parado")
    
    def warm_up_strategy(self, historical_candles: List[Dict]):
        """
        Processa candles históricos APENAS para inicializar os cálculos da estratégia
        (EMAs, EC, séries temporais). NÃO gera ou executa trades durante este processo.
        """
        if not self.interpreter:
            return
        
        logger.info(f"🔥 Aquecendo a estratégia com {len(historical_candles)} candles históricos...")
        
        # Reseta o interpretador para estado limpo antes do warm-up
        self.interpreter.reset()
        
        # Processa cada candle histórico, mas IGNORA qualquer sinal de trade
        for candle in historical_candles:
            # Processa o candle no interpretador
            result = self.interpreter.process_candle(candle)
            
            # Atualiza o último timestamp processado
            self.last_processed_timestamp = candle['timestamp']
            
            # Log apenas para primeiros e últimos candles do warm-up
            if len(self.interpreter.series_data['src'].values) == 1:
                logger.info(f"   Primeiro candle histórico: ${candle['close']:.2f}")
            elif len(self.interpreter.series_data['src'].values) == len(historical_candles):
                logger.info(f"   Último candle histórico: ${candle['close']:.2f}")
        
        logger.info(f"✅ Estratégia aquecida. Estado inicial carregado com {self.interpreter.candle_count} candles.")
        logger.info(f"   Último timestamp processado: {self.last_processed_timestamp}")
    
    def run_strategy_on_new_candles(self, new_candles: List[Dict]) -> Dict:
        """
        Processa APENAS candles NOVOS (com timestamp > last_processed_timestamp).
        Esta é a fase de execução em tempo real que pode gerar trades.
        """
        if not self.interpreter or not new_candles or not self.is_running:
            return {"signal": "HOLD", "strength": 0}
        
        last_signal = {"signal": "HOLD", "strength": 0}
        
        for candle in new_candles:
            # Verifica se é realmente um candle novo
            if candle['timestamp'] <= self.last_processed_timestamp:
                continue
            
            # Processa o candle através do interpretador Pine Script
            result = self.interpreter.process_candle(candle)
            last_signal = result
            self.last_processed_timestamp = candle['timestamp']
            
            # Se houver sinal de trade, executar na OKX
            if result['signal'] in ['BUY', 'SELL'] and result['strength'] > 0:
                logger.info(f"📢 SINAL DE {result['signal']} detectado no preço ${result['price']:.2f}")
                
                # Calcular tamanho da posição
                position_size = self.okx_client.calculate_position_size()
                
                if position_size > 0:
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
                    logger.warning(f"⚠️  Posição muito pequena ou cálculo falhou ({position_size:.6f} ETH)")
        
        return last_signal
    
    def get_strategy_status(self) -> Dict:
        """Retorna o status atual da estratégia."""
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
