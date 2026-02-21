# data/collector.py
#
# DIAGNÓSTICO DE ALINHAMENTO COM TRADINGVIEW:
# ─────────────────────────────────────────────────────────────────────────
# Se os resultados têm métricas idênticas mas ~41 trades a menos,
# a causa é QUANTIDADE DE DADOS, não lógica da estratégia.
#
# TradingView tem ~282 trades / 3.01 trades/dia ≈ 93.7 dias de dados.
# Com 4500 candles de 30min = 93.75 dias → o período bate.
#
# Se ainda há diferença, é porque:
# 1. TradingView usa uma exchange diferente (Binance, Bybit) → OHLC ligeiramente diferentes
# 2. OKX entrega os candles com timestamps ligeiramente desalinhados
# 3. TradingView conta barras parciais (a última barra aberta) como dados
#
# SOLUÇÃO: Usar 6000 candles (125 dias) para garantir cobertura total.
# Configurar o backtest do TradingView para o mesmo timestamp inicial.

import pandas as pd
import requests
import random
from datetime import datetime, timedelta
from typing import Optional


class OKXDataCollector:
    """
    Coleta candles OHLCV da OKX para backtesting e live trading.
    
    Para máxima paridade com TradingView:
    - Aumentar limit para 6000+ candles
    - Verificar se TradingView usa OKX ou Binance/Bybit como fonte
    - Comparar o timestamp do primeiro candle com o start date do TradingView
    """

    def __init__(
        self,
        symbol:    str = "ETH-USDT",
        timeframe: str = "30m",
        limit:     int = 6000,   # ← Aumentado de 4500 para 6000 (125 dias)
    ):
        self.symbol    = symbol.strip().upper().replace('/', '-').replace('_', '-')
        self.timeframe = self._convert_timeframe(timeframe)
        self.limit     = limit
        self.base_url  = "https://www.okx.com"
        self.MAX_PER_REQUEST = 300

    def _convert_timeframe(self, tf: str) -> str:
        mapping = {
            '1m':'1m',  '3m':'3m',   '5m':'5m',   '15m':'15m', '30m':'30m',
            '1h':'1H',  '2h':'2H',   '4h':'4H',   '6h':'6H',   '12h':'12H',
            '1d':'1D',  '1w':'1W',   '1M':'1M',
        }
        return mapping.get(tf.lower(), '30m')

    def fetch_ohlcv(self) -> pd.DataFrame:
        """
        Busca candles históricos da OKX.
        Retorna DataFrame com colunas: timestamp, open, high, low, close, volume.
        timestamp está em datetime64 UTC.
        """
        print(f"🔍 Buscando {self.limit} candles | {self.symbol} {self.timeframe}...")
        all_candles = []
        after = None
        pages = 0

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
                    print(f"⚠️  OKX erro: {data.get('msg')}")
                    return self._mock(self.limit)

                page = data.get('data', [])
                if not page:
                    break

                all_candles.extend(page)
                pages += 1
                print(f"   Página {pages}: +{len(page)} candles (total {len(all_candles)})")

                if len(page) < self.MAX_PER_REQUEST:
                    break

                after = page[-1][0]   # timestamp do mais antigo recebido

            if not all_candles:
                print("⚠️  Sem dados — usando mock")
                return self._mock(self.limit)

            # OKX retorna mais recente primeiro → inverte
            all_candles.reverse()
            all_candles = all_candles[-self.limit:]

            rows = [
                [int(c[0]), float(c[1]), float(c[2]),
                 float(c[3]), float(c[4]), float(c[5])]
                for c in all_candles
            ]

            df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
            df = df.assign(timestamp=pd.to_datetime(df['timestamp'], unit='ms'))

            # ── Diagnóstico de alinhamento com TradingView ─────────────
            first = df['timestamp'].iloc[0]
            last  = df['timestamp'].iloc[-1]
            n     = len(df)
            days  = (last - first).total_seconds() / 86400
            print(f"✅ {n} candles reais | {first} → {last} ({days:.1f} dias)")
            print(f"   💡 Para alinhar com TradingView: configure o backtest")
            print(f"      para iniciar em: {first.strftime('%Y-%m-%d %H:%M')} UTC")

            return df

        except Exception as e:
            print(f"⚠️  Falha OKX ({e}) — usando mock")
            return self._mock(self.limit)

    def _mock(self, n: int) -> pd.DataFrame:
        """Dados sintéticos para fallback (desenvolvimento/testes)."""
        print(f"📊 Gerando {n} candles mock...")
        base = 2500.0
        vol  = 0.012
        end  = datetime.utcnow()
        dt   = timedelta(minutes=30)
        rows = []
        p    = base
        for i in range(n):
            chg = random.uniform(-vol, vol)
            p   = max(p * (1 + chg), base * 0.5)
            hi  = p * (1 + random.uniform(0, 0.004))
            lo  = p * (1 - random.uniform(0, 0.004))
            cl  = p * (1 + random.uniform(-0.002, 0.002))
            ts  = end - dt * (n - i)
            rows.append([ts, round(p,2), round(hi,2), round(lo,2),
                         round(cl,2), round(random.uniform(5000,15000),2)])
        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
