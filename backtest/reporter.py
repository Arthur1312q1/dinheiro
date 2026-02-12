# backtest/reporter.py
import pandas as pd
from pathlib import Path
from jinja2 import Template
from typing import Dict, Any, Optional
from datetime import datetime

class BacktestReporter:
    def __init__(self, report_data: Dict[str, Any], candles_df: pd.DataFrame):
        self.data = report_data
        self.df = candles_df

    def _safe_str(self, value: Any) -> str:
        """Converte qualquer valor para string, tratando None."""
        if value is None:
            return ""
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        return str(value)

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        """Converte para float, tratando None e erros."""
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def generate_html(self) -> str:
        """Gera o HTML do relatório com proteção contra None."""
        template_path = Path(__file__).parent / "templates" / "report_template.html"
        with open(template_path, 'r', encoding='utf-8') as f:
            template = Template(f.read())

        # Prepara candles com timestamps seguros
        candles = []
        for _, row in self.df.iterrows():
            candles.append({
                'time': self._safe_str(row.get('timestamp')),
                'open': self._safe_float(row.get('open')),
                'high': self._safe_float(row.get('high')),
                'low': self._safe_float(row.get('low')),
                'close': self._safe_float(row.get('close'))
            })

        # Prepara marcadores de trade com valores seguros
        markers = []
        for trade in self.data.get('trades', []):
            entry_time = self._safe_str(trade.get('entry_time'))
            exit_time = self._safe_str(trade.get('exit_time'))
            
            if entry_time:
                markers.append({
                    'time': entry_time,
                    'price': self._safe_float(trade.get('entry_price')),
                    'type': 'entry',
                    'action': trade.get('action', 'UNKNOWN')
                })
            if exit_time:
                markers.append({
                    'time': exit_time,
                    'price': self._safe_float(trade.get('exit_price')),
                    'type': 'exit',
                    'action': 'EXIT'
                })

        # Prepara trades para a tabela com valores seguros
        trades_table = []
        for trade in self.data.get('trades', []):
            trades_table.append({
                'entry_time': self._safe_str(trade.get('entry_time')),
                'exit_time': self._safe_str(trade.get('exit_time')),
                'action': trade.get('action', ''),
                'qty': self._safe_float(trade.get('qty')),
                'entry_price': self._safe_float(trade.get('entry_price')),
                'exit_price': self._safe_float(trade.get('exit_price')),
                'pnl_usdt': self._safe_float(trade.get('pnl_usdt')),
                'pnl_percent': self._safe_float(trade.get('pnl_percent'))
            })

        # Função now para o rodapé
        def now():
            return datetime.now()

        html = template.render(
            candles=candles,
            markers=markers,
            trades=trades_table,
            stats=self.data,
            now=now
        )
        return html

    def save_html(self, output_path: str = "backtest_report.html") -> str:
        """Salva o HTML em arquivo (para compatibilidade local)."""
        html = self.generate_html()
        out_path = Path(output_path).absolute()
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        import webbrowser
        webbrowser.open(f"file://{out_path}")
        return str(out_path)
