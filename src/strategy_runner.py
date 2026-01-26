import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        self.is_running = False
        self.current_price = 2500.0
        self.position_size = 0
        self.position_side = None
        self.current_trade_id = None
        self.bars_processed = 0
        
        logger.info("✅ Strategy Runner inicializado (modo simulação)")
    
    def start(self):
        if self.is_running:
            return False
        
        self.is_running = True
        logger.info("🚀 Strategy Runner iniciado")
        
        # Iniciar thread de simulação
        import threading
        def simulation_loop():
            while self.is_running:
                try:
                    self.bars_processed += 1
                    logger.info(f"📊 Barra {self.bars_processed} processada (simulação)")
                    time.sleep(30)  # Simula barra de 30 segundos para teste
                except:
                    time.sleep(5)
        
        thread = threading.Thread(target=simulation_loop, daemon=True)
        thread.start()
        
        return True
    
    def stop(self):
        self.is_running = False
        logger.info("⏹️ Strategy Runner parado")
    
    def get_strategy_status(self):
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": "BARRAS_30m",
            "simulation_mode": True,
            "current_price": self.current_price,
            "next_bar_at": "00:00:00",
            "bars_processed": self.bars_processed,
            "pending_buy": False,
            "pending_sell": False,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "ws_connected": False,
            "price_fresh": False
        }
