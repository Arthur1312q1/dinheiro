import os
import logging
import random
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class OKXClient:
    def __init__(self):
        self.api_key = os.getenv('OKX_API_KEY', '')
        self.secret_key = os.getenv('OKX_SECRET_KEY', '')
        self.passphrase = os.getenv('OKX_PASSPHRASE', '')
        
        # Preço inicial baseado no debug atual
        self.simulated_price = 2358.49  # Preço da trade aberta
        
        if not all([self.api_key, self.secret_key, self.passphrase]):
            logger.warning("⚠️ Credenciais OKX não configuradas. Modo simulação ativado.")
        
        logger.info("✅ Cliente OKX inicializado")
    
    def get_balance(self):
        """Retorna saldo simulado"""
        return 1000.0
    
    def get_ticker_price(self, symbol="ETH-USDT-SWAP"):
        """Retorna preço simulado - AGORA COM QUEDA PROGRESSIVA"""
        # Simular queda do preço para testar Take Profit
        variation = random.uniform(-10, -5)  # Só queda
        self.simulated_price += variation
        
        # Limitar queda
        if self.simulated_price < 2200:
            self.simulated_price = 2200 + random.uniform(0, 50)
        
        return round(self.simulated_price, 2)
    
    def get_candles(self, symbol="ETH-USDT-SWAP", timeframe="30m", limit=100):
        """Retorna candles simulados com padrão REALISTA"""
        from datetime import datetime, timedelta
        
        candles = []
        base_price = 2630.0
        
        for i in range(limit):
            timestamp = int((datetime.now().timestamp() - i * 1800) * 1000)
            
            # Se não for o primeiro candle, usar close anterior como open
            if candles:
                prev_close = candles[-1]['close']
                open_price = prev_close
            else:
                open_price = base_price
            
            # Criar padrão realista com tendência de baixa
            close_price = open_price + random.uniform(-50, 30)
            
            # Calcular high e low
            high_price = max(open_price, close_price) + random.uniform(0, 20)
            low_price = min(open_price, close_price) - random.uniform(0, 20)
            
            candles.append({
                "timestamp": timestamp,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": round(random.uniform(100, 5000), 2)
            })
        
        return list(reversed(candles))
