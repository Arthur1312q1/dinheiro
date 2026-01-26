import os
import logging
import time
import threading
from datetime import datetime, timedelta

from .pine_engine import PineScriptInterpreter
from .okx_client import OKXClient
from .web_socket_manager import OKXWebSocketManager

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        self.interpreter = None
        self.is_running = False
        
        self.ws_manager = OKXWebSocketManager()
        
        self.timeframe_minutes = 30
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        self.pending_buy = False
        self.pending_sell = False
        self.position_size = 0
        self.position_side = None
        self.current_trade_id = None
        
        self.current_price = None
        
        self.processing_thread = None
        
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado com Pine Script")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        try:
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "./strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "./Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        return f.read()
            
            logger.error("Arquivo Pine Script não encontrado")
            return None
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None
    
    def start(self):
        logger.info("🚀 Strategy Runner iniciado (versão simplificada)")
        logger.info("⚠️  Modo: SIMULAÇÃO - Sem WebSocket ou trades reais")
        
        self.is_running = True
        return True
    
    def stop(self):
        self.is_running = False
        logger.info("⏹️ Strategy Runner parado")
    
    def get_strategy_status(self):
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": f"BARRAS_{self.timeframe_minutes}m",
            "simulation_mode": True,
            "current_price": 2500.0,
            "next_bar_at": "00:00:00",
            "bars_processed": 0,
            "pending_buy": False,
            "pending_sell": False,
            "position_size": 0,
            "position_side": None,
            "ws_connected": False,
            "price_fresh": False
        }
