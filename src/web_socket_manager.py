import logging

logger = logging.getLogger(__name__)

class OKXWebSocketManager:
    def __init__(self):
        self.is_connected = False
        self.current_price = 2500.0
        logger.info("✅ WebSocket Manager inicializado (simulação)")
    
    def connect(self):
        self.is_connected = True
        logger.info("🌐 WebSocket conectado (simulação)")
        return True
    
    def disconnect(self):
        self.is_connected = False
        logger.info("🔌 WebSocket desconectado")
    
    def get_current_price(self):
        return self.current_price
    
    def is_price_fresh(self):
        return True
