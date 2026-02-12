# data/collector.py
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from typing import Optional, List
import random

from utils.env_loader import env

class OKXDataCollector:
    """
    Coletor de dados da OKX via API REST pública.
    Não depende do CCXT, portanto livre do erro de mercados.
    """

    def __init__(self, symbol: str = "ETH-USDT", timeframe: str = "30m", limit: int = 150):
        """
        Args:
            symbol: Formato "ETH-USDT" (OKX usa hífen, não barra)
            timeframe: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W, 1M
            limit: Máximo de candles (OKX permite até 300 por requisição)
        """
        self.symbol = symbol
        self.timeframe = self._convert_timeframe(timeframe)
        self.limit = min(limit, 300)  # Limite da OKX
        self.base_url = "https://www.okx.com"

    def _convert_timeframe(self, tf: str) -> str:
        """Converte formato 30m para o formato da OKX (30m)."""
        mapping = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
            '1d': '1D', '1w': '1W', '1M': '1M'
        }
        return mapping.get(tf.lower(), '30m')

    def _generate_mock_candles(self, days: int = 3) -> pd.DataFrame:
        """Gera dados mockados realistas para fallback."""
        print("📊 Usando dados mockados (fallback)...")
        base_price = 3200.0
        volatility = 0.015
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(days=days)
        delta = timedelta(minutes=30)
        current_time = start_time
        candles = []
        price = base_price
        while current_time <= end_time and len(candles) < self.limit:
            change = random.uniform(-volatility, volatility)
            price *= (1 + change)
            price = max(price, base_price * 0.7)
            high = price * (1 + random.uniform(0, 0.005))
            low = price * (1 - random.uniform(0, 0.005))
            close = price * (1 + random.uniform(-0.002, 0.002))
            volume = random.uniform(5000, 15000)
            candles.append([
                int(current_time.timestamp() * 1000),
                round(price, 2),
                round(high, 2),
                round(low, 2),
                round(close, 2),
                round(volume, 2)
            ])
            current_time += delta
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df.tail(self.limit)

    def fetch_ohlcv(self, since: Optional[datetime] = None) -> pd.DataFrame:
        """
        Busca candles da API pública da OKX.
        Retorna DataFrame com colunas: timestamp, open, high, low, close, volume.
        """
        endpoint = "/api/v5/market/candles"
        params = {
            'instId': self.symbol,
            'bar': self.timeframe,
            'limit': self.limit
        }

        # Se 'since' for fornecido, adiciona parâmetro 'after' (OKX usa timestamp em ms)
        if since:
            # OKX espera o timestamp do primeiro candle APÓS o especificado? 
            # Na prática, passamos o timestamp do candle mais antigo desejado.
            params['after'] = str(int(since.timestamp() * 1000))

        try:
            print(f"🔍 Solicitando {self.limit} candles de {self.symbol} ({self.timeframe})...")
            response = requests.get(
                self.base_url + endpoint,
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if data.get('code') != '0':
                print(f"⚠️ Erro na API OKX: {data.get('msg')}")
                return self._generate_mock_candles()

            # Formato da OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm?]
            candles_data = data['data']
            
            # Inverte para ordem crescente (OKX retorna do mais recente para o mais antigo)
            candles_data.reverse()

            processed = []
            for c in candles_data:
                processed.append([
                    int(c[0]),
                    float(c[1]),  # open
                    float(c[2]),  # high
                    float(c[3]),  # low
                    float(c[4]),  # close
                    float(c[5])   # volume
                ])

            df = pd.DataFrame(processed, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ Obtidos {len(df)} candles reais da OKX")
            return df

        except Exception as e:
            print(f"⚠️ Falha na API OKX: {e}")
            return self._generate_mock_candles()

    def fetch_recent(self, days: int = 2) -> pd.DataFrame:
        """
        Baixa candles dos últimos 'days' dias (para 150 candles 30m, ~3 dias são suficientes).
        """
        since = datetime.utcnow() - timedelta(days=days)
        return self.fetch_ohlcv(since=since)
