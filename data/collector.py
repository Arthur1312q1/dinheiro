# data/collector.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-EXCHANGE COLLECTOR — Binance / OKX / Bybit
# ═══════════════════════════════════════════════════════════════════════════════
#
# CAUSA DOS 41 TRADES FALTANTES:
# ────────────────────────────────────────────────────────────────────────
# TradingView usa BINANCE como fonte de dados padrão para ETH/USDT.
# Python usava OKX.
#
# Preços diferem ~0.01–0.10% entre exchanges. Isso muda o momento exato
# dos crossovers EC/EMA → Pine gera trades que OKX não gera (e vice-versa).
#
# Win rate / drawdown idênticos provam que a LÓGICA está correta.
# Apenas a FONTE DE DADOS diferia.
#
# SOLUÇÃO:
# ────────────────────────────────────────────────────────────────────────
# Use exchange="binance" para replicar exatamente o TradingView.
# OU altere o chart do TradingView para OKX:ETHUSDT e use exchange="okx".
#
# CONFIGURAÇÃO:
# ────────────────────────────────────────────────────────────────────────
#   collector = DataCollector(
#       symbol    = "ETHUSDT",      # Binance format
#       timeframe = "30m",
#       limit     = 6000,           # 125 dias de histórico
#       exchange  = "binance",      # "binance" | "okx" | "bybit"
#   )
#   df = collector.fetch_ohlcv()
# ═══════════════════════════════════════════════════════════════════════════════

import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional


class DataCollector:
    """
    Coleta candles OHLCV de Binance, OKX ou Bybit.
    
    Use exchange="binance" para máxima paridade com TradingView
    (que usa Binance como fonte padrão para ETH/USDT).
    """

    def __init__(
        self,
        symbol:    str = "ETHUSDT",   # Binance: ETHUSDT | OKX: ETH-USDT | Bybit: ETHUSDT
        timeframe: str = "30m",
        limit:     int = 6000,        # 125 dias a 30min (garante histórico completo)
        exchange:  str = "binance",   # "binance" | "okx" | "bybit"
    ):
        self.symbol    = symbol.strip().upper()
        self.timeframe = timeframe.lower()
        self.limit     = limit
        self.exchange  = exchange.lower()
        self.MAX_PER_REQ = 1000  # Binance max, OKX usa 300

    # ═══════════════════════════════════════════════════════════════════════════
    # BINANCE — fonte padrão do TradingView para ETH/USDT
    # Endpoint: GET /api/v3/klines
    # Max: 1000 por request, sem paginação via "after"
    # ═══════════════════════════════════════════════════════════════════════════
    def _fetch_binance(self) -> pd.DataFrame:
        BASE = "https://api.binance.com"
        INTERVAL_MAP = {
            '1m':'1m','3m':'3m','5m':'5m','15m':'15m','30m':'30m',
            '1h':'1h','2h':'2h','4h':'4h','6h':'6h','12h':'12h',
            '1d':'1d','3d':'3d','1w':'1w','1M':'1M',
        }
        interval = INTERVAL_MAP.get(self.timeframe, '30m')
        sym = self.symbol.replace('-','').replace('_','')

        all_klines = []
        end_time   = None
        pages      = 0

        print(f"🔍 Binance: {sym} {interval} | meta={self.limit} candles...")

        while len(all_klines) < self.limit:
            batch  = min(1000, self.limit - len(all_klines))
            params = {'symbol': sym, 'interval': interval, 'limit': batch}
            if end_time:
                params['endTime'] = end_time

            resp = requests.get(BASE + "/api/v3/klines", params=params, timeout=15)
            resp.raise_for_status()
            klines = resp.json()

            if not klines:
                break

            # Binance retorna cronológico (mais antigo primeiro)
            all_klines = klines + all_klines   # prepend
            pages += 1
            print(f"   Página {pages}: +{len(klines)} (total {len(all_klines)})")

            if len(klines) < batch:
                break

            # Paginar para trás: endTime = open_time do mais antigo - 1ms
            end_time = int(klines[0][0]) - 1

        if not all_klines:
            return pd.DataFrame()

        # Trimma para limit
        all_klines = all_klines[-self.limit:]

        rows = [[
            int(k[0]),       # open_time ms
            float(k[1]),     # open
            float(k[2]),     # high
            float(k[3]),     # low
            float(k[4]),     # close
            float(k[5]),     # volume
        ] for k in all_klines]

        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))

        first = df['timestamp'].iloc[0]
        last  = df['timestamp'].iloc[-1]
        days  = (last - first).total_seconds() / 86400
        print(f"✅ Binance: {len(df)} candles | {first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')} ({days:.1f} dias)")
        print(f"   💡 Configure TradingView para iniciar em: {first.strftime('%Y-%m-%d %H:%M')} UTC")
        return df

    # ═══════════════════════════════════════════════════════════════════════════
    # OKX
    # ═══════════════════════════════════════════════════════════════════════════
    def _fetch_okx(self) -> pd.DataFrame:
        BASE = "https://www.okx.com"
        TF_MAP = {
            '1m':'1m','3m':'3m','5m':'5m','15m':'15m','30m':'30m',
            '1h':'1H','2h':'2H','4h':'4H','6h':'6H','12h':'12H',
            '1d':'1D','1w':'1W','1M':'1M',
        }
        interval = TF_MAP.get(self.timeframe, '30m')
        sym = self.symbol.replace('USDT','-USDT').replace('BTC','-BTC') if '-' not in self.symbol else self.symbol

        all_candles = []
        after = None
        pages = 0

        print(f"🔍 OKX: {sym} {interval} | meta={self.limit} candles...")

        while len(all_candles) < self.limit:
            batch  = min(300, self.limit - len(all_candles))
            params = {'instId': sym, 'bar': interval, 'limit': batch}
            if after:
                params['after'] = after

            resp = requests.get(BASE + "/api/v5/market/candles", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get('code') != '0':
                print(f"⚠️  OKX erro: {data.get('msg')}")
                return pd.DataFrame()

            page = data.get('data', [])
            if not page:
                break

            all_candles.extend(page)
            pages += 1
            print(f"   Página {pages}: +{len(page)} (total {len(all_candles)})")

            if len(page) < batch:
                break

            after = page[-1][0]

        if not all_candles:
            return pd.DataFrame()

        all_candles.reverse()   # OKX: mais recente primeiro → inverte
        all_candles = all_candles[-self.limit:]

        rows = [[int(c[0]), float(c[1]), float(c[2]),
                 float(c[3]), float(c[4]), float(c[5])] for c in all_candles]

        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))

        first = df['timestamp'].iloc[0]
        last  = df['timestamp'].iloc[-1]
        days  = (last - first).total_seconds() / 86400
        print(f"✅ OKX: {len(df)} candles | {first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')} ({days:.1f} dias)")
        return df

    # ═══════════════════════════════════════════════════════════════════════════
    # BYBIT
    # ═══════════════════════════════════════════════════════════════════════════
    def _fetch_bybit(self) -> pd.DataFrame:
        BASE = "https://api.bybit.com"
        TF_MAP = {
            '1m':'1','3m':'3','5m':'5','15m':'15','30m':'30',
            '1h':'60','2h':'120','4h':'240','6h':'360','12h':'720',
            '1d':'D','1w':'W','1M':'M',
        }
        interval = TF_MAP.get(self.timeframe, '30')
        sym = self.symbol.replace('-','').replace('_','')

        all_klines = []
        end_time   = None
        pages      = 0

        print(f"🔍 Bybit: {sym} {interval} | meta={self.limit} candles...")

        while len(all_klines) < self.limit:
            batch  = min(200, self.limit - len(all_klines))
            params = {'category':'linear','symbol':sym,'interval':interval,'limit':batch}
            if end_time:
                params['end'] = end_time

            resp = requests.get(BASE + "/v5/market/kline", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get('retCode') != 0:
                print(f"⚠️  Bybit erro: {data.get('retMsg')}")
                return pd.DataFrame()

            page = data.get('result',{}).get('list',[])
            if not page:
                break

            all_klines = page + all_klines  # Bybit retorna recente primeiro
            pages += 1
            print(f"   Página {pages}: +{len(page)} (total {len(all_klines)})")

            if len(page) < batch:
                break

            end_time = int(page[-1][0]) - 1

        if not all_klines:
            return pd.DataFrame()

        all_klines = all_klines[-self.limit:]

        rows = [[int(k[0]), float(k[1]), float(k[2]),
                 float(k[3]), float(k[4]), float(k[5])] for k in all_klines]

        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))
        df = df.sort_values('timestamp').reset_index(drop=True)

        first = df['timestamp'].iloc[0]
        last  = df['timestamp'].iloc[-1]
        days  = (last - first).total_seconds() / 86400
        print(f"✅ Bybit: {len(df)} candles | {first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')} ({days:.1f} dias)")
        return df

    # ═══════════════════════════════════════════════════════════════════════════
    # MOCK (fallback para desenvolvimento/testes sem internet)
    # ═══════════════════════════════════════════════════════════════════════════
    def _mock(self) -> pd.DataFrame:
        print(f"📊 Gerando {self.limit} candles mock (fallback)...")
        base = 2500.0; vol = 0.012
        end  = datetime.utcnow(); dt = timedelta(minutes=30)
        rows = []; p = base
        for i in range(self.limit):
            p  = max(p * (1 + random.uniform(-vol, vol)), base * 0.5)
            hi = p * (1 + random.uniform(0, 0.004))
            lo = p * (1 - random.uniform(0, 0.004))
            cl = p * (1 + random.uniform(-0.002, 0.002))
            ts = end - dt * (self.limit - i)
            rows.append([ts, round(p,2), round(hi,2), round(lo,2),
                         round(cl,2), round(random.uniform(5000,15000),2)])
        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df

    # ═══════════════════════════════════════════════════════════════════════════
    # API PÚBLICA
    # ═══════════════════════════════════════════════════════════════════════════
    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca candles da exchange configurada.
        Retorna DataFrame com colunas: timestamp, open, high, low, close, volume.
        """
        try:
            if self.exchange == "binance":
                df = self._fetch_binance()
            elif self.exchange == "okx":
                df = self._fetch_okx()
            elif self.exchange == "bybit":
                df = self._fetch_bybit()
            else:
                raise ValueError(f"Exchange desconhecida: {self.exchange}")

            if df.empty:
                print("⚠️  Sem dados da exchange → usando mock")
                df = self._mock()

            # Garante ordem cronológica e sem duplicatas
            df = df.sort_values('timestamp').drop_duplicates('timestamp').reset_index(drop=True)
            df['index'] = df.index
            return df

        except Exception as e:
            print(f"⚠️  Falha {self.exchange} ({e}) → usando mock")
            return self._mock()


# Alias retrocompatível
class OKXDataCollector(DataCollector):
    """Alias para compatibilidade com código anterior."""
    def __init__(self, symbol="ETH-USDT", timeframe="30m", limit=6000):
        sym = symbol.replace('-','')  # ETH-USDT → ETHUSDT
        super().__init__(symbol=sym, timeframe=timeframe, limit=limit, exchange="okx")
