# data/collector.py
import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional

class OKXDataCollector:
    def __init__(self, symbol: str = "ETH-USDT", timeframe: str = "30m", limit: int = 150):
        self.symbol = symbol.strip().upper().replace('/', '-').replace('_', '-')
        self.timeframe = self._convert_timeframe(timeframe)
        self.limit = min(limit, 300)
        self.base_url = "https://www.okx.com"

    def _convert_timeframe(self, tf: str) -> str:
        mapping = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
            '1d': '1D', '1w': '1W', '1M': '1M'
        }
        return mapping.get(tf.lower(), '30m')

    def _generate_mock_candles(self) -> pd.DataFrame:
        print("📊 Usando dados mockados (fallback)...")
        base_price = 3200.0
        volatility = 0.015
        end_time = datetime.utcnow()
        delta = timedelta(minutes=30)
        candles = []
        price = base_price
        for i in range(self.limit):
            change = random.uniform(-volatility, volatility)
            price *= (1 + change)
            price = max(price, base_price * 0.7)
            high = price * (1 + random.uniform(0, 0.005))
            low = price * (1 - random.uniform(0, 0.005))
            close = price * (1 + random.uniform(-0.002, 0.002))
            volume = random.uniform(5000, 15000)
            timestamp = int((end_time - delta * (self.limit - i)).timestamp() * 1000)
            candles.append([timestamp, round(price,2), round(high,2), round(low,2), round(close,2), round(volume,2)])
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        # ✅ CORREÇÃO: atribuição direta, sem .loc
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def fetch_ohlcv(self) -> pd.DataFrame:
        endpoint = "/api/v5/market/candles"
        params = {'instId': self.symbol, 'bar': self.timeframe, 'limit': self.limit}
        try:
            print(f"🔍 Buscando até {self.limit} candles de {self.symbol} ({self.timeframe})...")
            response = requests.get(self.base_url + endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('code') != '0':
                print(f"⚠️ Erro na API OKX: {data.get('msg')}")
                return self._generate_mock_candles()
            candles_data = data['data']
            candles_data.reverse()
            processed = []
            for c in candles_data:
                processed.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])])
            df = pd.DataFrame(processed, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            # ✅ CORREÇÃO: atribuição direta
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ Obtidos {len(df)} candles reais da OKX")
            return df
        except Exception as e:
            print(f"⚠️ Falha na API OKX: {e}")
            return self._generate_mock_candles()
