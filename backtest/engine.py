# backtest/engine.py
import pandas as pd
from typing import List, Dict, Any
from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA

class BacktestEngine:
    def __init__(self, strategy: AdaptiveZeroLagEMA, data: pd.DataFrame):
        self.strategy = strategy
        self.data = data.reset_index(drop=True)
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        self.timestamp_list: List = []

    def run(self) -> Dict[str, Any]:
        for idx, row in self.data.iterrows():
            candle = {
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'timestamp': row.get('timestamp', idx),
                'index': idx
            }

            # Estratégia pode retornar múltiplas ações (entradas/saídas)
            actions = self.strategy.next(candle)

            for action in actions:
                # Registra cada ação como trade
                if action['action'] in ('BUY', 'SELL'):
                    self.trades.append({
                        'entry_time': action['timestamp'],
                        'entry_price': action['price'],
                        'action': action['action'],
                        'qty': action['qty'],
                        'comment': action['comment'],
                        'balance': action['balance']
                    })
                elif action['action'] in ('EXIT_LONG', 'EXIT_SHORT'):
                    # Encontra o último trade aberto (assumindo pyramiding=1)
                    if self.trades and 'exit_time' not in self.trades[-1]:
                        self.trades[-1].update({
                            'exit_time': action['timestamp'],
                            'exit_price': action['price'],
                            'pnl_usdt': action.get('pnl', 0),
                            'pnl_percent': (action['price'] / self.trades[-1]['entry_price'] - 1) * 100,
                            'exit_comment': action['comment']
                        })

            self.equity_curve.append(self.strategy.balance)
            self.timestamp_list.append(candle['timestamp'])

        return self._generate_report()

    def _generate_report(self) -> Dict[str, Any]:
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
        if len(self.equity_curve) < 2:
            return 0.0
        returns = pd.Series(self.equity_curve).pct_change().dropna()
        if returns.std() == 0:
            return 0.0
        return (returns.mean() / returns.std()) * (48 * 365) ** 0.5
