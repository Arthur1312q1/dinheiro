# data/collector.py
import pandas as pd
import requests
import random
import time
from datetime import datetime, timedelta
from typing import Optional, List

class OKXDataCollector:
    """
    Coletor de dados da OKX com suporte a múltiplas requisições para obter até 1000 candles.
    """

    def __init__(self, symbol: str = "ETH-USDT", timeframe: str = "30m", limit: int = 1000):
        self.symbol = symbol.strip().upper().replace('/', '-').replace('_', '-')
        self.timeframe = self._convert_timeframe(timeframe)
        self.limit = limit
        self.base_url = "https://www.okx.com"
        self.per_request_limit = 300  # máximo por chamada

    def _convert_timeframe(self, tf: str) -> str:
        mapping = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
            '1d': '1D', '1w': '1W', '1M': '1M'
        }
        return mapping.get(tf.lower(), '30m')

    def _generate_mock_candles(self, count: int) -> pd.DataFrame:
        """Gera dados mockados (fallback) com o número especificado de candles."""
        print(f"📊 Gerando {count} candles mockados...")
        base_price = 3200.0
        volatility = 0.015
        end_time = datetime.utcnow()
        delta = timedelta(minutes=30)
        candles = []
        price = base_price
        for i in range(count):
            change = random.uniform(-volatility, volatility)
            price *= (1 + change)
            price = max(price, base_price * 0.7)
            high = price * (1 + random.uniform(0, 0.005))
            low = price * (1 - random.uniform(0, 0.005))
            close = price * (1 + random.uniform(-0.002, 0.002))
            volume = random.uniform(5000, 15000)
            timestamp = int((end_time - delta * (count - i)).timestamp() * 1000)
            candles.append([timestamp, round(price,2), round(high,2), round(low,2), round(close,2), round(volume,2)])
        df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def _fetch_page(self, after: Optional[int] = None) -> List:
        """
        Busca uma página de até 300 candles.
        Se after for fornecido, retorna candles anteriores a esse timestamp.
        """
        endpoint = "/api/v5/market/candles"
        params = {
            'instId': self.symbol,
            'bar': self.timeframe,
            'limit': self.per_request_limit
        }
        if after:
            params['after'] = str(after)

        response = requests.get(self.base_url + endpoint, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get('code') != '0':
            raise Exception(f"API error: {data.get('msg')}")
        return data['data']

    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca até `self.limit` candles (máx 1000) usando paginação.
        """
        all_candles = []
        after = None
        remaining = self.limit

        print(f"🔍 Buscando até {self.limit} candles de {self.symbol} ({self.timeframe})...")

        try:
            while remaining > 0:
                # A cada requisição, pede no máximo o permitido
                page = self._fetch_page(after)
                if not page:
                    break

                # A API retorna do mais recente para o mais antigo
                # Vamos acumulando na ordem correta (mais antigo primeiro)
                page.reverse()
                all_candles.extend(page)

                if len(page) < self.per_request_limit:
                    # Não há mais dados
                    break

                # Prepara próximo 'after' (timestamp do candle mais antigo desta página)
                # O campo timestamp está na posição 0
                after = int(page[0][0])
                remaining -= len(page)

                # Pequena pausa para não sobrecarregar a API
                time.sleep(0.1)

            # Limita ao número solicitado (pega os mais recentes)
            if len(all_candles) > self.limit:
                all_candles = all_candles[-self.limit:]

            if not all_candles:
                print("⚠️ Nenhum candle retornado, usando mock.")
                return self._generate_mock_candles(self.limit)

            processed = []
            for c in all_candles:
                processed.append([
                    int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])
                ])

            df = pd.DataFrame(processed, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            print(f"✅ Obtidos {len(df)} candles reais da OKX")
            return df

        except Exception as e:
            print(f"⚠️ Falha na API OKX: {e}")
            return self._generate_mock_candles(self.limit)
