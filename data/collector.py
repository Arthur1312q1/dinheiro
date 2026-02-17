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
        # OKX máximo por request é 300 candles
        self.MAX_PER_REQUEST = 300

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
            candles.append([timestamp, round(price, 2), round(high, 2), round(low, 2), round(close, 2), round(volume, 2)])
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.copy()
        df.loc[:, 'timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def _fetch_page(self, after: Optional[str] = None) -> list:
        """
        Busca uma página de até MAX_PER_REQUEST candles.
        `after` é o timestamp (ms) do candle mais antigo da página anterior,
        usado para paginar para trás no tempo.
        """
        endpoint = "/api/v5/market/candles"
        params = {
            'instId': self.symbol,
            'bar': self.timeframe,
            'limit': self.MAX_PER_REQUEST
        }
        if after:
            params['after'] = after  # OKX: retorna candles ANTES deste timestamp

        response = requests.get(self.base_url + endpoint, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('code') != '0':
            raise ValueError(f"Erro na API OKX: {data.get('msg')}")

        return data.get('data', [])

    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca até `self.limit` candles da OKX usando paginação.
        OKX retorna candles do mais recente para o mais antigo por padrão.
        """
        print(f"🔍 Buscando até {self.limit} candles de {self.symbol} ({self.timeframe}) com paginação...")

        all_candles = []
        after = None
        pages = 0

        try:
            while len(all_candles) < self.limit:
                needed = self.limit - len(all_candles)
                page_data = self._fetch_page(after=after)

                if not page_data:
                    break

                all_candles.extend(page_data)
                pages += 1
                print(f"  📄 Página {pages}: +{len(page_data)} candles (total: {len(all_candles)})")

                if len(page_data) < self.MAX_PER_REQUEST:
                    # Não há mais páginas
                    break

                # O candle mais antigo é o último da lista (OKX retorna mais recente primeiro)
                oldest_ts = page_data[-1][0]
                after = oldest_ts

            if not all_candles:
                print("⚠️ Nenhum candle retornado pela API, usando mock.")
                return self._generate_mock_candles()

            # OKX retorna mais recente primeiro → invertemos para ordem cronológica
            all_candles.reverse()

            # Pegar apenas os últimos `limit` candles (caso tenha paginado a mais)
            all_candles = all_candles[-self.limit:]

            processed = []
            for c in all_candles:
                processed.append([
                    int(c[0]),
                    float(c[1]),
                    float(c[2]),
                    float(c[3]),
                    float(c[4]),
                    float(c[5])
                ])

            df = pd.DataFrame(processed, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = df.copy()
            df.loc[:, 'timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ Obtidos {len(df)} candles reais da OKX ({pages} página(s))")
            return df

        except Exception as e:
            print(f"⚠️ Falha na API OKX: {e}")
            return self._generate_mock_candles()
