# data/collector.py
import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional


class OKXDataCollector:
    def __init__(self, symbol: str = "ETH-USDT", timeframe: str = "30m", limit: int = 4500):
        self.symbol   = symbol.strip().upper().replace('/', '-').replace('_', '-')
        self.timeframe = self._convert_timeframe(timeframe)
        self.limit    = limit
        self.base_url = "https://www.okx.com"
        self.MAX_PER_REQUEST = 300

    def _convert_timeframe(self, tf: str) -> str:
        mapping = {
            '1m':'1m','3m':'3m','5m':'5m','15m':'15m','30m':'30m',
            '1h':'1H','2h':'2H','4h':'4H','6h':'6H','12h':'12H',
            '1d':'1D','1w':'1W','1M':'1M'
        }
        return mapping.get(tf.lower(), '30m')

    def _generate_mock_candles(self) -> pd.DataFrame:
        print("📊 Usando dados mockados (fallback)...")
        base_price = 2500.0
        volatility = 0.012
        end_time   = datetime.utcnow()
        delta      = timedelta(minutes=30)
        rows       = []
        price      = base_price
        for i in range(self.limit):
            change = random.uniform(-volatility, volatility)
            price  = max(price * (1 + change), base_price * 0.5)
            high   = price * (1 + random.uniform(0, 0.004))
            low    = price * (1 - random.uniform(0, 0.004))
            close  = price * (1 + random.uniform(-0.002, 0.002))
            volume = random.uniform(5000, 15000)
            ts_ms  = int((end_time - delta * (self.limit - i)).timestamp() * 1000)
            rows.append([ts_ms, round(price,2), round(high,2), round(low,2),
                         round(close,2), round(volume,2)])

        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        # FIX: cria coluna timestamp como datetime diretamente, sem cast de int64
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def fetch_ohlcv(self) -> pd.DataFrame:
        print(f"🔍 Buscando {self.limit} candles de {self.symbol} ({self.timeframe})...")
        all_candles = []
        after       = None
        pages       = 0

        try:
            while len(all_candles) < self.limit:
                batch  = min(self.MAX_PER_REQUEST, self.limit - len(all_candles))
                params = {'instId': self.symbol, 'bar': self.timeframe, 'limit': batch}
                if after:
                    params['after'] = after

                resp = requests.get(
                    self.base_url + "/api/v5/market/candles",
                    params=params, timeout=15
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get('code') != '0':
                    print(f"⚠️ Erro OKX: {data.get('msg')}")
                    return self._generate_mock_candles()

                page = data.get('data', [])
                if not page:
                    break

                all_candles.extend(page)
                pages += 1
                print(f"  📄 Página {pages}: +{len(page)} (total: {len(all_candles)})")

                if len(page) < self.MAX_PER_REQUEST:
                    break

                after = page[-1][0]

            if not all_candles:
                print("⚠️ Sem dados, usando mock.")
                return self._generate_mock_candles()

            # OKX retorna mais recente primeiro → inverte
            all_candles.reverse()
            all_candles = all_candles[-self.limit:]

            rows = [
                [int(c[0]), float(c[1]), float(c[2]),
                 float(c[3]), float(c[4]), float(c[5])]
                for c in all_candles
            ]

            df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
            # FIX: usa assign para evitar FutureWarning de dtype incompatível
            df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))
            print(f"✅ {len(df)} candles reais da OKX ({pages} página(s))")
            return df

        except Exception as e:
            print(f"⚠️ Falha OKX: {e}")
            return self._generate_mock_candles()
