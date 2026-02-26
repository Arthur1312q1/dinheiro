# data/collector.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# OKX COLLECTOR — Histórico completo via history-candles + candles
# Suporta tanto spot (ETH-USDT) quanto futuros (ETH-USDT-SWAP)
# ═══════════════════════════════════════════════════════════════════════════════

import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional


class DataCollector:
    """
    Coleta candles históricos da OKX usando ambos os endpoints:
    - /market/history-candles → dados históricos antigos
    - /market/candles         → dados recentes (últimas horas/dias)

    Suporta qualquer instId OKX: spot (ETH-USDT), swap (ETH-USDT-SWAP), etc.
    """

    BASE = "https://www.okx.com"
    MAX_PER_REQ = 300

    _TF_MAP = {
        '1m':'1m',  '3m':'3m',   '5m':'5m',   '15m':'15m', '30m':'30m',
        '1h':'1H',  '2h':'2H',   '4h':'4H',   '6h':'6H',   '12h':'12H',
        '1d':'1D',  '1w':'1W',   '1M':'1M',
    }

    def __init__(
        self,
        symbol:    str = "ETH-USDT-SWAP",
        timeframe: str = "30m",
        limit:     int = 5500,
        exchange:  str = "okx",
    ):
        # Normaliza símbolo para formato OKX
        # Aceita: ETH-USDT, ETH-USDT-SWAP, ETHUSDT, ETH/USDT etc.
        s = symbol.strip().upper().replace('/', '-').replace('_', '-')
        # Só faz substituição simples se NÃO tiver sufixo como -SWAP, -FUTURES etc.
        if '-' not in s and s.endswith('USDT'):
            s = s[:-4] + '-USDT'
        self.symbol    = s
        self.timeframe = self._TF_MAP.get(timeframe.lower(), '30m')
        self.limit     = limit

        # Detecta o tipo de instrumento para usar o endpoint correto
        # SWAP e FUTURES têm endpoint diferente para history-candles
        self._inst_type = self._detect_inst_type(s)

    def _detect_inst_type(self, symbol: str) -> str:
        """Detecta se é SPOT, SWAP ou FUTURES baseado no símbolo."""
        if symbol.endswith('-SWAP'):
            return 'SWAP'
        elif 'FUTURES' in symbol or symbol.count('-') >= 2:
            return 'FUTURES'
        return 'SPOT'

    # ─────────────────────────────────────────────────────────────────────────
    # Busca HISTÓRICA — /api/v5/market/history-candles
    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_history(self, limit: int, before_ts_ms: Optional[int] = None) -> list:
        """
        Busca candles históricos. Retorna lista em ordem CRESCENTE.
        """
        collected = []
        after_ts  = str(before_ts_ms) if before_ts_ms else None

        while len(collected) < limit:
            params = {
                'instId': self.symbol,
                'bar':    self.timeframe,
                'limit':  self.MAX_PER_REQ,
            }
            if after_ts:
                params['after'] = after_ts

            try:
                resp = requests.get(
                    self.BASE + "/api/v5/market/history-candles",
                    params=params, timeout=20
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  ⚠️ history-candles erro: {e}")
                break

            if data.get('code') != '0':
                print(f"  ⚠️ OKX history-candles: {data.get('msg')}")
                break

            page = data.get('data', [])
            if not page:
                break

            collected.extend(page)
            oldest_ts = page[-1][0]
            after_ts  = oldest_ts

            if len(page) < self.MAX_PER_REQ:
                break

        collected.reverse()
        return collected

    # ─────────────────────────────────────────────────────────────────────────
    # Busca RECENTE — /api/v5/market/candles
    # ─────────────────────────────────────────────────────────────────────────
    def _fetch_recent(self, limit: int = 300) -> list:
        """
        Busca candles recentes. Retorna lista em ordem CRESCENTE.
        """
        params = {
            'instId': self.symbol,
            'bar':    self.timeframe,
            'limit':  min(limit, self.MAX_PER_REQ),
        }
        try:
            resp = requests.get(
                self.BASE + "/api/v5/market/candles",
                params=params, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️ candles recentes erro: {e}")
            return []

        if data.get('code') != '0':
            print(f"  ⚠️ OKX candles: {data.get('msg')}")
            return []

        page = data.get('data', [])
        page.reverse()
        return page

    # ─────────────────────────────────────────────────────────────────────────
    # FETCH PRINCIPAL
    # ─────────────────────────────────────────────────────────────────────────
    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca candles da OKX.
        - Se limit <= 300: uma única requisição rápida (/candles)
        - Se limit > 300:  paginação via /history-candles + /candles
        """
        print(f"🔍 OKX: {self.symbol} ({self._inst_type}) {self.timeframe} | {self.limit} candles...")

        if self.limit <= 300:
            recent = self._fetch_recent(limit=self.limit)
            if not recent:
                print("  ⚠️ Sem dados — usando mock")
                return self._mock()
            all_raw = recent
            print(f"  ✅ {len(all_raw)} candles (1 request)")

        else:
            print(f"   [1/2] Candles recentes...")
            recent = self._fetch_recent(limit=300)
            if not recent:
                print("  ⚠️ Sem dados recentes — usando mock")
                return self._mock()
            oldest_recent_ts = int(recent[0][0])
            print(f"   ✓ {len(recent)} recentes")
            needed = self.limit - len(recent) + 50
            print(f"   [2/2] Histórico ({needed} candles)...")
            historical = self._fetch_history(limit=needed, before_ts_ms=oldest_recent_ts)
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

        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
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
        print(f"\n✅ {len(df)} candles | {first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')} ({days:.1f} dias)")

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
            rows.append([ts, round(p,2), round(hi,2), round(lo,2),
                         round(cl,2), round(random.uniform(5000,15000),2)])
        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['index']     = df.index
        return df


# Aliases para retrocompatibilidade
OKXDataCollector = DataCollector
