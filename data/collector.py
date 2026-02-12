# data/collector.py
import os
import ccxt
import pandas as pd
import time
from typing import Optional
from utils.env_loader import env

class OKXDataCollector:
    def __init__(self, symbol: str = "ETH/USDT", timeframe: str = "30m", limit: int = 1000):
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit

        # Inicializa a exchange sem autenticação para dados públicos
        self.exchange = ccxt.okx({
            'apiKey': env('OKX_API_KEY', ''),
            'secret': env('OKX_SECRET', ''),
            'password': env('OKX_PASSPHRASE', ''),
            'enableRateLimit': True,
        })

        # Desabilita carregamento automático de mercados problemáticos
        self.exchange.load_markets = self._safe_load_markets

    def _safe_load_markets(self, reload: bool = False, params: dict = {}) -> dict:
        """
        Carrega mercados com tratamento de erro para símbolos inválidos.
        Se falhar, tenta novamente após limpar o cache.
        """
        try:
            # Tenta carregar normalmente
            markets = self.exchange.fetch_markets(params)
            # Filtra mercados válidos (base e quote não nulos)
            valid_markets = {}
            for market in markets:
                if market.get('base') and market.get('quote'):
                    symbol = market['symbol']
                    valid_markets[symbol] = market
            self.exchange.markets = valid_markets
            self.exchange.symbols = list(valid_markets.keys())
            self.exexchange.markets_by_id = {m['id']: m for m in valid_markets.values()}
            return valid_markets
        except Exception as e:
            print(f"⚠️ Erro ao carregar mercados: {e}")
            # Se falhar, tenta recarregar limpando o cache
            time.sleep(1)
            self.exchange.markets = None
            self.exchange.markets_by_id = None
            self.exchange.symbols = None
            # Segunda tentativa
            markets = self.exchange.fetch_markets(params)
            valid_markets = {}
            for market in markets:
                if market.get('base') and market.get('quote'):
                    symbol = market['symbol']
                    valid_markets[symbol] = market
            self.exchange.markets = valid_markets
            self.exchange.symbols = list(valid_markets.keys())
            return valid_markets

    def fetch_ohlcv(self, since: Optional[str] = None) -> pd.DataFrame:
        """Retorna DataFrame com candles, garantindo que não haja valores nulos."""
        since_ts = None
        if since:
            since_ts = pd.Timestamp(since).timestamp() * 1000

        # Garante que os mercados foram carregados com segurança
        if not self.exchange.markets:
            self._safe_load_markets()

        # Verifica se o símbolo existe
        if self.symbol not in self.exchange.markets:
            # Tenta normalizar o símbolo
            normalized = self.symbol.replace('/', '').upper()
            for sym in self.exchange.markets:
                if sym.replace('/', '').upper() == normalized:
                    self.symbol = sym
                    break
            else:
                raise ValueError(f"Símbolo {self.symbol} não encontrado na OKX. Mercados disponíveis: {list(self.exchange.markets.keys())[:5]}...")

        ohlcv = self.exchange.fetch_ohlcv(
            symbol=self.symbol,
            timeframe=self.timeframe,
            since=since_ts,
            limit=self.limit
        )

        # Converte para DataFrame e trata valores nulos
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', errors='coerce')
        df = df.dropna(subset=['timestamp'])
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0.0)
        return df

    def fetch_recent(self, days: int = 30) -> pd.DataFrame:
        """Baixa os últimos 'days' dias."""
        since = pd.Timestamp.now() - pd.Timedelta(days=days)
        return self.fetch_ohlcv(since=since.isoformat())
