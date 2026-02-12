# backtest/reporter.py
import pandas as pd
from pathlib import Path
from jinja2 import Template
from typing import Dict, Any
import webbrowser

class BacktestReporter:
    def __init__(self, report_data: Dict[str, Any], candles_df: pd.DataFrame):
        self.data = report_data
        self.df = candles_df

    def generate_html(self) -> str:
        """Gera o HTML do relatório e retorna como string."""
        template_path = Path(__file__).parent / "templates" / "report_template.html"
        with open(template_path, 'r', encoding='utf-8') as f:
            template = Template(f.read())

        candles = []
        for _, row in self.df.iterrows():
            candles.append({
                'time': row['timestamp'].isoformat() if hasattr(row['timestamp'], 'isoformat') else str(row['timestamp']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close'])
            })

        markers = []
        for trade in self.data['trades']:
            if 'entry_time' in trade:
                markers.append({
                    'time': trade['entry_time'].isoformat() if hasattr(trade['entry_time'], 'isoformat') else str(trade['entry_time']),
                    'price': trade['entry_price'],
                    'type': 'entry',
                    'action': trade['action']
                })
            if 'exit_time' in trade:
                markers.append({
                    'time': trade['exit_time'].isoformat() if hasattr(trade['exit_time'], 'isoformat') else str(trade['exit_time']),
                    'price': trade['exit_price'],
                    'type': 'exit',
                    'action': 'EXIT'
                })

        # Adiciona função now() para a data no rodapé
        from datetime import datetime
        def now():
            return datetime.now()

        html = template.render(
            candles=candles,
            markers=markers,
            trades=self.data['trades'],
            stats=self.data,
            now=now
        )
        return html

    def save_html(self, output_path: str = "backtest_report.html") -> str:
        """Salva o HTML em arquivo (para compatibilidade)."""
        html = self.generate_html()
        out_path = Path(output_path).absolute()
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        webbrowser.open(f"file://{out_path}")
        return str(out_path)
