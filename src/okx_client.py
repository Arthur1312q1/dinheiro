import os
import logging

logger = logging.getLogger(__name__)

class OKXClient:
    def __init__(self):
        self.api_key = os.getenv('OKX_API_KEY', '')
        self.secret_key = os.getenv('OKX_SECRET_KEY', '')
        self.passphrase = os.getenv('OKX_PASSPHRASE', '')
        
        if not all([self.api_key, self.secret_key, self.passphrase]):
            logger.warning("⚠️ Credenciais OKX não configuradas. Modo simulação ativado.")
        
        logger.info("✅ Cliente OKX inicializado (modo simulação)")
    
    def get_balance(self):
        """Retorna saldo simulado"""
        return 1000.0  # Saldo fixo para simulação
    
    def get_ticker_price(self, symbol="ETH-USDT-SWAP"):
        """Retorna preço simulado"""
        return 2500.0  # Preço fixo para simulação
    
    def calculate_position_size(self):
        """Calcula tamanho de posição simulado"""
        balance = self.get_balance()
        price = self.get_ticker_price()
        risk_capital = balance * 0.95  # 95% do saldo
        return risk_capital / price
    
    def get_candles(self, symbol="ETH-USDT-SWAP", timeframe="30m", limit=100):
        """Retorna candles simulados"""
        import random
        from datetime import datetime, timedelta
        
        candles = []
        base_price = 2500.0
        
        for i in range(limit):
            timestamp = int((datetime.now().timestamp() - i * 1800) * 1000)
            open_price = base_price + random.uniform(-50, 50)
            close_price = open_price + random.uniform(-30, 30)
            high_price = max(open_price, close_price) + random.uniform(0, 20)
            low_price = min(open_price, close_price) - random.uniform(0, 20)
            
            candles.append({
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": random.uniform(100, 1000)
            })
        
        return list(reversed(candles))
