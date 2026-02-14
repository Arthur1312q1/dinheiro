# backtest/reporter.py
import pandas as pd
from pathlib import Path
from jinja2 import Template
from typing import Dict, Any
from datetime import datetime

class BacktestReporter:
    def __init__(self, report_data: Dict[str, Any], candles_df: pd.DataFrame):
        self.data = report_data
        self.df = candles_df

    def generate_html(self) -> str:
        print(f"📊 Gerando relatório com {len(self.df)} candles e {len(self.data.get('trades', []))} trades")
        
        template_path = Path(__file__).parent / "templates" / "report_template.html"
        with open(template_path, 'r', encoding='utf-8') as f:
            template = Template(f.read())

        # Prepara candles com timestamps em ISO
        candles = []
        for _, row in self.df.iterrows():
            candles.append({
                'time': row['timestamp'].isoformat() if hasattr(row['timestamp'], 'isoformat') else str(row['timestamp']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close'])
            })

        # Prepara marcadores de trade
        markers = []
        for trade in self.data.get('trades', []):
            if 'entry_time' in trade and trade['entry_time']:
                markers.append({
                    'time': trade['entry_time'].isoformat() if hasattr(trade['entry_time'], 'isoformat') else str(trade['entry_time']),
                    'price': trade['entry_price'],
                    'type': 'entry',
                    'action': trade['action']
                })
            if 'exit_time' in trade and trade['exit_time']:
                markers.append({
                    'time': trade['exit_time'].isoformat() if hasattr(trade['exit_time'], 'isoformat') else str(trade['exit_time']),
                    'price': trade['exit_price'],
                    'type': 'exit',
                    'action': 'EXIT'
                })

        # Prepara trades para a tabela
        trades_table = []
        for trade in self.data.get('trades', []):
            trades_table.append({
                'entry_time': trade.get('entry_time', ''),
                'exit_time': trade.get('exit_time', ''),
                'action': trade.get('action', ''),
                'qty': trade.get('qty', 0),
                'entry_price': trade.get('entry_price', 0),
                'exit_price': trade.get('exit_price', 0),
                'pnl_usdt': trade.get('pnl_usdt', 0),
                'pnl_percent': trade.get('pnl_percent', 0)
            })

        # Último candle para debug
        ultimo_candle = candles[-1] if candles else None

        html = template.render(
            candles=candles,
            markers=markers,
            trades=trades_table,
            stats=self.data,
            ultimo_candle=ultimo_candle,
            now=datetime.now
        )
        return html
