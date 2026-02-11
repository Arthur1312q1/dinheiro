# backtest/reporter.py
import pandas as pd
import webbrowser
from pathlib import Path
from jinja2 import Template
from typing import Dict, Any

class BacktestReporter:
    """
    Gera relatório HTML profissional com gráfico de preços,
    marcadores de entrada/saída e tabela detalhada de trades.
    """

    def __init__(self, report_data: Dict[str, Any], candles_df: pd.DataFrame):
        """
        Args:
            report_data: dicionário gerado por BacktestEngine._generate_report()
            candles_df: DataFrame original com os candles (timestamp, open, high, low, close)
        """
        self.data = report_data
        self.df = candles_df

    def generate(self, output_path: str = "backtest_report.html") -> str:
        """
        Renderiza o template HTML e salva o arquivo.
        Retorna o caminho absoluto do arquivo gerado.
        """
        template_path = Path(__file__).parent / "templates" / "report_template.html"
        with open(template_path, 'r', encoding='utf-8') as f:
            template = Template(f.read())

        # Prepara lista de candles para o gráfico
        candles = []
        for _, row in self.df.iterrows():
            candles.append({
                'time': row['timestamp'].isoformat() if hasattr(row['timestamp'], 'isoformat') else str(row['timestamp']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close'])
            })

        # Prepara marcadores de trade (entrada/saída)
        markers = []
        for trade in self.data['trades']:
            if 'entry_time' in trade and 'entry_price' in trade:
                markers.append({
                    'time': trade['entry_time'].isoformat() if hasattr(trade['entry_time'], 'isoformat') else str(trade['entry_time']),
                    'price': trade['entry_price'],
                    'type': 'entry',
                    'action': trade['action']
                })
            if 'exit_time' in trade and 'exit_price' in trade:
                markers.append({
                    'time': trade['exit_time'].isoformat() if hasattr(trade['exit_time'], 'isoformat') else str(trade['exit_time']),
                    'price': trade['exit_price'],
                    'type': 'exit',
                    'action': 'EXIT'
                })

        html = template.render(
            candles=candles,
            markers=markers,
            trades=self.data['trades'],
            stats=self.data
        )

        out_path = Path(output_path).absolute()
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)

        # Abre automaticamente no navegador
        webbrowser.open(f"file://{out_path}")
        return str(out_path)
