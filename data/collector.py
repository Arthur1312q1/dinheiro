# data/collector.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# BITGET COLLECTOR — Histórico completo via Bitget Futures API
#
# MOTIVO DA MUDANÇA (OKX → Bitget):
#   O trader ao vivo busca candles da Bitget. O backtest e o warmup
#   precisam usar A MESMA FONTE DE DADOS para que o trailing stop
#   produza preços idênticos (LOW/HIGH idênticos → _lowest/_highest
#   idênticos → stop_price idêntico).
#
#   OKX e Bitget têm H/L DIFERENTES para o mesmo período:
#     Ex.: OKX LOW = 2024.00, Bitget LOW = 2055.73 → stop 2024.15 vs 2055.88
#
#   Usando Bitget para tudo: backtest = warmup = live → 100% paridade.
# ═══════════════════════════════════════════════════════════════════════════════

import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional


class DataCollector:
    """
    Coleta candles históricos da Bitget Futures usando:
    - /api/v2/mix/market/history-candles → dados históricos antigos
    - /api/v2/mix/market/candles         → dados recentes

    Usa a MESMA fonte que o trader ao vivo → trailing stop idêntico.
    """

    BASE         = "https://api.bitget.com"
    SYMBOL       = "ETHUSDT"
    PRODUCT_TYPE = "usdt-futures"
    MAX_RECENT   = 1000   # Bitget /candles: máx 1000 por req
    MAX_HISTORY  = 200    # Bitget /history-candles: máx 200 por req

    _TF_MAP = {
        '1m':  '1m',  '3m':  '3m',  '5m':  '5m',
        '15m': '15m', '30m': '30m',
        '1h':  '1H',  '2h':  '2H',  '4h':  '4H',
        '6h':  '6H',  '12h': '12H',
        '1d':  '1D',  '1w':  '1W',
    }

    def __init__(
        self,
        symbol:    str = "ETH-USDT-SWAP",   # aceita qualquer formato, ignora (usa ETHUSDT fixo)
        timeframe: str = "30m",
        limit:     int = 5500,
        exchange:  str = "bitget",           # mantido por compatibilidade
    ):
        self.timeframe = self._TF_MAP.get(timeframe.lower(), '30m')
        self.limit     = limit
        self._session  = requests.Session()
        # Symbol ignorado: sempre usa ETHUSDT usdt-futures (mesmo do live trader)

    # ─────────────────────────────────────────────────────────────────────────
    # Busca RECENTE — /api/v2/mix/market/candles
    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_recent(self, limit: int = 200) -> list:
        """
        Busca até `limit` candles recentes.
        Retorna lista em ordem CRESCENTE (mais antigo primeiro).
        """
        params = {
            'symbol':      self.SYMBOL,
            'productType': self.PRODUCT_TYPE,
            'granularity': self.timeframe,
            'limit':       str(min(limit, self.MAX_RECENT)),
        }
        try:
            r = self._session.get(
                self.BASE + "/api/v2/mix/market/candles",
                params=params, timeout=15
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ⚠️ Bitget candles erro: {e}")
            return []

        if data.get('code') != '00000':
            print(f"  ⚠️ Bitget candles: {data.get('msg')}")
            return []

        page = data.get('data', [])
        page.reverse()   # Bitget retorna decrescente → inverte para crescente
        return page

    # ─────────────────────────────────────────────────────────────────────────
    # Busca HISTÓRICA — /api/v2/mix/market/history-candles
    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_history(self, limit: int, end_time_ms: int) -> list:
        """
        Busca candles históricos anteriores a `end_time_ms`.
        Retorna lista em ordem CRESCENTE.
        """
        collected = []
        end_time  = str(end_time_ms)

        while len(collected) < limit:
            params = {
                'symbol':      self.SYMBOL,
                'productType': self.PRODUCT_TYPE,
                'granularity': self.timeframe,
                'endTime':     end_time,
                'limit':       str(self.MAX_HISTORY),
            }
            try:
                r = self._session.get(
                    self.BASE + "/api/v2/mix/market/history-candles",
                    params=params, timeout=20
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                print(f"  ⚠️ Bitget history-candles erro: {e}")
                break

            if data.get('code') != '00000':
                print(f"  ⚠️ Bitget history-candles: {data.get('msg')}")
                break

            page = data.get('data', [])
            if not page:
                break

            collected.extend(page)
            # oldest entry = last element (Bitget retorna decrescente)
            end_time = page[-1][0]

            if len(page) < self.MAX_HISTORY:
                break

        collected.reverse()   # crescente
        return collected

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH PRINCIPAL
    # ─────────────────────────────────────────────────────────────────────────
    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca candles da Bitget Futures.
        - limit ≤ 1000 : uma única requisição rápida (/candles)
        - limit > 1000 : /candles + paginação via /history-candles
        """
        print(f"🔍 Bitget Futures: {self.SYMBOL} {self.PRODUCT_TYPE} "
              f"{self.timeframe} | {self.limit} candles...")

        if self.limit <= self.MAX_RECENT:
            recent = self._fetch_recent(limit=self.limit)
            if not recent:
                print("  ⚠️ Sem dados — usando mock")
                return self._mock()
            all_raw = recent
            print(f"  ✅ {len(all_raw)} candles (1 request)")

        else:
            print(f"   [1/2] Candles recentes...")
            recent = self._fetch_recent(limit=self.MAX_RECENT)
            if not recent:
                print("  ⚠️ Sem dados recentes — usando mock")
                return self._mock()
            # oldest timestamp do bloco recente (para paginar para trás)
            oldest_recent_ts = int(recent[0][0])
            print(f"   ✓ {len(recent)} recentes")

            needed = self.limit - len(recent) + 50
            print(f"   [2/2] Histórico ({needed} candles)...")
            historical = self._fetch_history(limit=needed, end_time_ms=oldest_recent_ts)
            print(f"   ✓ {len(historical)} históricos")
            all_raw = historical + recent

        if not all_raw:
            print("  ⚠️ Sem dados — usando mock")
            return self._mock()

        rows = []
        for c in all_raw:
            try:
                rows.append([
                    int(c[0]),
                    float(c[1]),
                    float(c[2]),
                    float(c[3]),
                    float(c[4]),
                    float(c[5]),
                ])
            except (IndexError, ValueError):
                continue

        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))
        df = (df.sort_values('timestamp')
                .drop_duplicates('timestamp')
                .reset_index(drop=True))

        if len(df) > self.limit:
            df = df.iloc[-self.limit:].reset_index(drop=True)

        df['index'] = df.index

        first = df['timestamp'].iloc[0]
        last  = df['timestamp'].iloc[-1]
        days  = (last - first).total_seconds() / 86400
        print(f"\n✅ {len(df)} candles | {first.strftime('%Y-%m-%d')} → "
              f"{last.strftime('%Y-%m-%d')} ({days:.1f} dias)")

        if len(df) < self.limit * 0.8:
            print(f"  ⚠️ ATENÇÃO: recebeu apenas {len(df)}/{self.limit} candles esperados")

        return df

    # ─────────────────────────────────────────────────────────────────────────
    # Mock (fallback)
    # ─────────────────────────────────────────────────────────────────────────
    def _mock(self) -> pd.DataFrame:
        print(f"📊 Gerando {self.limit} candles mock (fallback)...")
        base = 2500.0
        end  = datetime.utcnow()
        dt   = timedelta(minutes=30)
        rows = []
        p    = base
        for i in range(self.limit):
            p  = max(p * (1 + random.uniform(-0.012, 0.012)), base * 0.5)
            hi = p * (1 + random.uniform(0, 0.004))
            lo = p * (1 - random.uniform(0, 0.004))
            cl = p * (1 + random.uniform(-0.002, 0.002))
            ts = end - dt * (self.limit - i)
            rows.append([ts, round(p, 2), round(hi, 2), round(lo, 2),
                         round(cl, 2), round(random.uniform(5000, 15000), 2)])
        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['index']     = df.index
        return df


# Aliases para retrocompatibilidade
OKXDataCollector = DataCollector
