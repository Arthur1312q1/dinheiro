# data/collector.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# OKX COLLECTOR — Histórico completo via history-candles + candles
# ═══════════════════════════════════════════════════════════════════════════════
#
# POR QUE DOIS ENDPOINTS OKX?
# ──────────────────────────────────────────────────────────────────────────────
# OKX tem dois endpoints de candles com comportamentos DIFERENTES:
#
#   /api/v5/market/candles
#       → Dados RECENTES: retorna apenas os últimos ~1440 candles
#       → Rápido, baixa latência, ideal para dados de hoje
#       → Com paginação 'after', vai um pouco mais atrás (até ~3000 candles)
#       → MAS NÃO vai até 4500+ candles necessários para o backtest!
#
#   /api/v5/market/history-candles
#       → Dados HISTÓRICOS: vai anos atrás
#       → 300 candles por request, paginação via 'after'
#       → Cobertura completa do histórico OKX desde 2019
#       → Usado por TradingView internamente para dados antigos
#
# ESTRATÉGIA:
#   1. Buscar histórico antigo via /history-candles (paginando para trás)
#   2. Complementar com /candles para os dados mais recentes
#   3. Merge + deduplicação por timestamp
#   4. Retornar exatamente `limit` candles mais recentes
#
# RESULTADO: Consegue 5500+ candles de 30min (> 4 meses de história) ✅
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

    Exporta também como OKXDataCollector para retrocompatibilidade.
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
        symbol:    str = "ETH-USDT",
        timeframe: str = "30m",
        limit:     int = 5500,    # BACKTEST_CANDLES + WARMUP_CANDLES
        exchange:  str = "okx",   # mantido por compatibilidade (só OKX aqui)
    ):
        # Normaliza símbolo para formato OKX: ETH-USDT
        s = symbol.strip().upper().replace('/', '-').replace('_', '-')
        if '-' not in s and s.endswith('USDT'):
            s = s[:-4] + '-USDT'
        self.symbol    = s
        self.timeframe = self._TF_MAP.get(timeframe.lower(), '30m')
        self.limit     = limit

    # ───────────────────────────────────────────────────────────────────────────
    # Busca HISTÓRICA — /api/v5/market/history-candles
    # Paginação para trás via 'after'. Traz dados de anos atrás.
    # ───────────────────────────────────────────────────────────────────────────
    def _fetch_history(self, limit: int, before_ts_ms: Optional[int] = None) -> list:
        """
        Busca candles históricos usando /history-candles.
        Retorna lista de candles em ordem CRESCENTE (mais antigo primeiro).
        """
        collected = []
        after_ts  = str(before_ts_ms) if before_ts_ms else None
        page_num  = 0

        while len(collected) < limit:
            batch  = min(self.MAX_PER_REQ, limit - len(collected))
            params = {
                'instId': self.symbol,
                'bar':    self.timeframe,
                'limit':  self.MAX_PER_REQ,  # OKX: pede max, trunca depois
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

            # OKX retorna em ordem DECRESCENTE (mais recente primeiro)
            collected.extend(page)
            page_num += 1

            oldest_ts = page[-1][0]   # timestamp do candle mais antigo desta página
            after_ts  = oldest_ts     # próxima página: mais antigo que este

            if len(page) < self.MAX_PER_REQ:
                break

        # Inverte para ordem crescente (mais antigo primeiro)
        collected.reverse()
        return collected

    # ───────────────────────────────────────────────────────────────────────────
    # Busca RECENTE — /api/v5/market/candles
    # Traz os candles mais recentes (últimas horas/dias).
    # ───────────────────────────────────────────────────────────────────────────
    def _fetch_recent(self, limit: int = 300) -> list:
        """
        Busca candles recentes usando /candles.
        Retorna lista de candles em ordem CRESCENTE.
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
            return []

        page = data.get('data', [])
        page.reverse()   # crescente
        return page

    # ───────────────────────────────────────────────────────────────────────────
    # FETCH PRINCIPAL
    # ───────────────────────────────────────────────────────────────────────────
    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca exatamente `self.limit` candles históricos da OKX.

        Estratégia:
          1. Busca dados recentes (/candles) → ancora o timestamp mais recente
          2. Busca histórico (/history-candles) para preencher o restante
          3. Merge e deduplicação por timestamp
          4. Retorna os `limit` candles mais recentes em ordem cronológica

        Returns:
            DataFrame com colunas: timestamp, open, high, low, close, volume, index
        """
        print(f"🔍 OKX: {self.symbol} {self.timeframe} | buscando {self.limit} candles...")

        # ── Passo 1: dados recentes (âncora) ──────────────────────────────
        print(f"   [1/2] Candles recentes...")
        recent = self._fetch_recent(limit=300)

        if not recent:
            print("  ⚠️ Sem dados recentes — usando mock")
            return self._mock()

        oldest_recent_ts = int(recent[0][0])   # timestamp do mais antigo no batch recente
        print(f"   ✓ {len(recent)} candles recentes | mais antigo: {oldest_recent_ts}")

        # ── Passo 2: histórico para trás até ter `limit` candles ──────────
        needed = self.limit - len(recent) + 50   # +50 overlap para dedup
        print(f"   [2/2] Histórico ({needed} candles)...")

        historical = self._fetch_history(limit=needed, before_ts_ms=oldest_recent_ts)
        print(f"   ✓ {len(historical)} candles históricos")

        # ── Passo 3: Merge ────────────────────────────────────────────────
        all_raw = historical + recent

        if not all_raw:
            print("  ⚠️ Sem dados — usando mock")
            return self._mock()

        rows = []
        for c in all_raw:
            try:
                rows.append([
                    int(c[0]),      # timestamp ms
                    float(c[1]),    # open
                    float(c[2]),    # high
                    float(c[3]),    # low
                    float(c[4]),    # close
                    float(c[5]),    # volume
                ])
            except (IndexError, ValueError):
                continue

        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))

        # Deduplicação e ordenação
        df = (df.sort_values('timestamp')
                .drop_duplicates('timestamp')
                .reset_index(drop=True))

        # Garante que temos exatamente `limit` candles (os mais recentes)
        if len(df) > self.limit:
            df = df.iloc[-self.limit:].reset_index(drop=True)

        df['index'] = df.index

        # ── Diagnóstico ───────────────────────────────────────────────────
        first = df['timestamp'].iloc[0]
        last  = df['timestamp'].iloc[-1]
        days  = (last - first).total_seconds() / 86400
        print(f"\n✅ {len(df)} candles | {first.strftime('%Y-%m-%d')} → {last.strftime('%Y-%m-%d')} ({days:.1f} dias)")

        if len(df) < self.limit * 0.8:
            print(f"  ⚠️ ATENÇÃO: recebeu apenas {len(df)}/{self.limit} candles esperados")
            print(f"  💡 OKX pode não ter histórico suficiente para este período/timeframe")

        return df

    # ───────────────────────────────────────────────────────────────────────────
    # Mock (fallback)
    # ───────────────────────────────────────────────────────────────────────────
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
