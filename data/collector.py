# data/collector.py
import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional

class OKXDataCollector:
    def __init__(self, symbol: str = "ETH-USDT", timeframe: str = "30m", limit: int = 1500):
        self.symbol = symbol.strip().upper().replace('/', '-').replace('_', '-')
        self.timeframe = self._convert_timeframe(timeframe)
        self.limit = limit
        self.base_url = "https://www.okx.com"
        self.MAX_PER_REQUEST = 300  # OKX máximo por página

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
            high  = price * (1 + random.uniform(0, 0.005))
            low   = price * (1 - random.uniform(0, 0.005))
            close = price * (1 + random.uniform(-0.002, 0.002))
            volume = random.uniform(5000, 15000)
            timestamp = int((end_time - delta * (self.limit - i)).timestamp() * 1000)
            candles.append([timestamp, round(price,2), round(high,2), round(low,2), round(close,2), round(volume,2)])
        df = pd.DataFrame(candles, columns=['timestamp','open','high','low','close','volume'])
        df = df.copy()
        df.loc[:,'timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca candles da OKX com paginação automática.
        OKX retorna no máximo 300 candles por request.
        Usa o parâmetro 'after' para paginar para trás no tempo.
        """
        print(f"🔍 Buscando {self.limit} candles de {self.symbol} ({self.timeframe})...")

        all_candles = []
        after = None
        pages = 0

        try:
            while len(all_candles) < self.limit:
                params = {
                    'instId': self.symbol,
                    'bar':    self.timeframe,
                    'limit':  min(self.MAX_PER_REQUEST, self.limit - len(all_candles))
                }
                if after:
                    params['after'] = after  # candles mais antigos que este timestamp

                response = requests.get(
                    self.base_url + "/api/v5/market/candles",
                    params=params, timeout=10
                )
                response.raise_for_status()
                data = response.json()

                if data.get('code') != '0':
                    print(f"⚠️ Erro OKX: {data.get('msg')}")
                    return self._generate_mock_candles()

                page_data = data.get('data', [])
                if not page_data:
                    break

                all_candles.extend(page_data)
                pages += 1
                print(f"  📄 Página {pages}: +{len(page_data)} (total: {len(all_candles)})")

                if len(page_data) < self.MAX_PER_REQUEST:
                    break  # não há mais páginas

                # oldest timestamp desta página → próxima página vai antes dele
                after = page_data[-1][0]

            if not all_candles:
                print("⚠️ Nenhum candle da API, usando mock.")
                return self._generate_mock_candles()

            # OKX retorna mais recente primeiro → inverte para ordem cronológica
            all_candles.reverse()
            all_candles = all_candles[-self.limit:]  # garante exatamente `limit` candles

            processed = [[int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                         for c in all_candles]

            df = pd.DataFrame(processed, columns=['timestamp','open','high','low','close','volume'])
            df = df.copy()
            df.loc[:,'timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ {len(df)} candles reais da OKX ({pages} página(s))")
            return df

        except Exception as e:
            print(f"⚠️ Falha na API OKX: {e}")
            return self._generate_mock_candles()
