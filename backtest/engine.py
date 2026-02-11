# backtest/engine.py
import pandas as pd
from typing import List, Dict, Any
from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA

class BacktestEngine:
    """
    Executa a estratégia sobre um DataFrame de candles (30min).
    Acumula trades, equity, drawdown, etc.
    """

    def __init__(self, strategy: AdaptiveZeroLagEMA, data: pd.DataFrame):
        self.strategy = strategy
        self.data = data.reset_index(drop=True)
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        self.timestamp_list: List = []

    def run(self) -> Dict[str, Any]:
        """Executa o backtest completo."""
        for idx, row in self.data.iterrows():
            candle = {
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'timestamp': row.get('timestamp', idx)
            }

            # 1. Verifica saídas (trailing) e atualiza PnL
            # 2. Executa estratégia
            signal = self.strategy.next(candle)

            # Registra trades
            if signal['action'] in ('BUY', 'SELL'):
                self.trades.append({
                    'entry_time': candle['timestamp'],
                    'entry_price': signal['price'],
                    'action': signal['action'],
                    'qty': signal['qty'],
                    'comment': signal['comment']
                })
            elif signal['action'] in ('EXIT_LONG', 'EXIT_SHORT'):
                # Encontra o trade aberto correspondente
                if self.trades and 'exit_time' not in self.trades[-1]:
                    self.trades[-1].update({
                        'exit_time': candle['timestamp'],
                        'exit_price': signal['price'],
                        'pnl_usdt': signal.get('pnl', 0),
                        'pnl_percent': (signal['price'] / self.trades[-1]['entry_price'] - 1) * 100,
                        'exit_comment': signal['comment']
                    })

            self.equity_curve.append(self.strategy.balance)
            self.timestamp_list.append(candle['timestamp'])

        return self._generate_report()

    def _generate_report(self) -> Dict[str, Any]:
        """Compila estatísticas finais."""
        df_trades = pd.DataFrame(self.trades)
        total_pnl = df_trades['pnl_usdt'].sum() if not df_trades.empty else 0
        win_trades = df_trades[df_trades['pnl_usdt'] > 0] if not df_trades.empty else pd.DataFrame()
        loss_trades = df_trades[df_trades['pnl_usdt'] <= 0] if not df_trades.empty else pd.DataFrame()

        return {
            'trades': self.trades,
            'equity_curve': self.equity_curve,
            'timestamps': self.timestamp_list,
            'total_trades': len(self.trades),
            'win_rate': len(win_trades) / len(self.trades) * 100 if self.trades else 0,
            'total_pnl_usdt': total_pnl,
            'final_balance': self.strategy.balance,
            'max_drawdown': self._calculate_max_drawdown(),
            'sharpe': self._calculate_sharpe()
        }

    def _calculate_max_drawdown(self) -> float:
        """Calcula o máximo drawdown percentual."""
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for value in self.equity_curve:
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _calculate_sharpe(self) -> float:
        """Sharpe ratio anualizado (assumindo 30min = 48 trades/dia aprox)."""
        if len(self.equity_curve) < 2:
            return 0.0
        returns = pd.Series(self.equity_curve).pct_change().dropna()
        if returns.std() == 0:
            return 0.0
        return (returns.mean() / returns.std()) * (48 * 365) ** 0.5
