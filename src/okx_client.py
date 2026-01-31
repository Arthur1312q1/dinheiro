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
        
        # Preço inicial realista (baseado no mercado)
        self.simulated_price = 2630.0  # Preço inicial próximo do atual
        
        if not all([self.api_key, self.secret_key, self.passphrase]):
            logger.warning("⚠️ Credenciais OKX não configuradas. Modo simulação ativado.")
        
        logger.info("✅ Cliente OKX inicializado (modo simulação)")
    
    def get_balance(self):
        """Retorna saldo simulado"""
        return 1000.0  # Saldo fixo para simulação
    
    def get_ticker_price(self, symbol="ETH-USDT-SWAP"):
        """Retorna preço simulado com variação realista"""
        # Simular variação de preço (pequenas flutuações)
        variation = random.uniform(-5, 5)
        self.simulated_price += variation
        
        # Manter preço em faixa razoável
        if self.simulated_price < 2500:
            self.simulated_price = 2500 + random.uniform(0, 50)
        elif self.simulated_price > 2800:
            self.simulated_price = 2800 - random.uniform(0, 50)
        
        return round(self.simulated_price, 2)
    
    def calculate_position_size(self, risk=0.01, fixedSL=2000, mintick=0.01):
        """Calcula tamanho de posição baseado no risco (igual Pine Script)"""
        balance = self.get_balance()
        risk_amount = risk * balance
        stop_loss_usdt = fixedSL * mintick
        
        if stop_loss_usdt <= 0:
            return 0
        
        quantity = risk_amount / stop_loss_usdt
        
        # Arredondar para 4 casas decimais
        return round(quantity, 4)
    
    def get_candles(self, symbol="ETH-USDT-SWAP", timeframe="30m", limit=100):
        """Retorna candles simulados com padrão realista"""
        import random
        from datetime import datetime, timedelta
        
        candles = []
        base_price = 2630.0  # Preço base realista
        
        # Criar candles com tendência e volatilidade realistas
        for i in range(limit):
            timestamp = int((datetime.now().timestamp() - i * 1800) * 1000)
            
            # Se não for o primeiro candle, usar close anterior como open
            if candles:
                prev_close = candles[-1]['close']
                open_price = prev_close
            else:
                open_price = base_price
            
            # Determinar direção da barra
            direction = random.choice(['up', 'down', 'neutral'])
            
            if direction == 'up':
                close_price = open_price + random.uniform(5, 30)
            elif direction == 'down':
                close_price = open_price - random.uniform(5, 30)
            else:  # neutral
                close_price = open_price + random.uniform(-10, 10)
            
            # Calcular high e low
            high_price = max(open_price, close_price) + random.uniform(0, 15)
            low_price = min(open_price, close_price) - random.uniform(0, 15)
            
            candles.append({
                "timestamp": timestamp,
                "open": round(open_price, 2),
                "high": round(high_price, 2),
                "low": round(low_price, 2),
                "close": round(close_price, 2),
                "volume": round(random.uniform(100, 5000), 2)
            })
        
        return list(reversed(candles))
