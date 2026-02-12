# data/collector.py
import os
import ccxt
import pandas as pd
from typing import Optional
from utils.env_loader import env

class OKXDataCollector:
    def __init__(self, symbol: str = "ETH/USDT", timeframe: str = "30m", limit: int = 1000):
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit

        self.exchange = ccxt.okx({
            'apiKey': env('OKX_API_KEY', ''),
            'secret': env('OKX_SECRET', ''),
            'password': env('OKX_PASSPHRASE', ''),
            'enableRateLimit': True,
        })

    def fetch_ohlcv(self, since: Optional[str] = None) -> pd.DataFrame:
        """Retorna DataFrame com candles, garantindo que não haja valores nulos."""
        since_ts = None
        if since:
            since_ts = pd.Timestamp(since).timestamp() * 1000

        ohlcv = self.exchange.fetch_ohlcv(
            symbol=self.symbol,
            timeframe=self.timeframe,
            since=since_ts,
            limit=self.limit
        )

        # Converte para DataFrame e trata valores nulos
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Converte timestamp para datetime (nunca será None)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', errors='coerce')
        
        # Remove linhas com timestamp inválido
        df = df.dropna(subset=['timestamp'])
        
        # Garante que colunas numéricas sejam float e preenche NaN com 0
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0.0)
        
        return df

    def fetch_recent(self, days: int = 30) -> pd.DataFrame:
        """Baixa os últimos 'days' dias."""
        since = pd.Timestamp.now() - pd.Timedelta(days=days)
        return self.fetch_ohlcv(since=since.isoformat())
