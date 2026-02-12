# data/collector.py
import os
import ccxt
import pandas as pd
from typing import Optional
from utils.env_loader import env

class OKXDataCollector:
    """
    Baixa dados históricos da OKX via CCXT.
    Utiliza variáveis de ambiente: OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE.
    """

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
        """
        Retorna DataFrame com colunas: timestamp, open, high, low, close, volume.
        """
        since_ts = None
        if since:
            since_ts = pd.Timestamp(since).timestamp() * 1000

        ohlcv = self.exchange.fetch_ohlcv(
            symbol=self.symbol,
            timeframe=self.timeframe,
            since=since_ts,
            limit=self.limit
        )

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df

    def fetch_recent(self, days: int = 30) -> pd.DataFrame:
        """
        Baixa os últimos 'days' dias (útil para backtesting rápido).
        """
        since = pd.Timestamp.now() - pd.Timedelta(days=days)
        return self.fetch_ohlcv(since=since.isoformat())
