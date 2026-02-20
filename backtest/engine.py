# backtest/engine.py
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA


class BacktestEngine:
    """
    Motor de backtest que processa candles e registra trades.
    Compatível com a estratégia AdaptiveZeroLagEMA.
    """

    def __init__(self, strategy: AdaptiveZeroLagEMA, data: pd.DataFrame):
        self.strategy = strategy
        self.data = data.reset_index(drop=True)
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        self.timestamp_list: List = []

    def run(self) -> Dict[str, Any]:
        for idx, row in self.data.iterrows():
            candle = {
                'open':      float(row['open']),
                'high':      float(row['high']),
                'low':       float(row['low']),
                'close':     float(row['close']),
                'timestamp': row.get('timestamp', idx),
                'index':     idx
            }

            actions = self.strategy.next(candle)

            for action in actions:
                act = action['action']

                if act in ('BUY', 'SELL'):
                    # Novo trade aberto
                    self.trades.append({
                        'entry_time':   action['timestamp'],
                        'entry_price':  action['price'],
                        'action':       act,
                        'qty':          action['qty'],
                        'comment':      action.get('comment', ''),
                        'balance':      action['balance'],
                        # Campos de saída serão preenchidos depois
                        'exit_time':    None,
                        'exit_price':   None,
                        'pnl_usdt':     None,
                        'pnl_percent':  None,
                        'exit_comment': None,
                    })

                elif act in ('EXIT_LONG', 'EXIT_SHORT'):
                    # Encontra o último trade aberto (pyramiding=1 → apenas 1 aberto)
                    open_trade = self._find_open_trade(act)
                    if open_trade is not None:
                        entry_price = open_trade['entry_price']
                        exit_price  = action['price']
                        qty         = open_trade['qty']
                        pnl_usdt    = action.get('pnl', 0.0)

                        # PnL % relativo à entrada
                        if open_trade['action'] == 'BUY':
                            pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - exit_price) / entry_price) * 100

                        open_trade.update({
                            'exit_time':    action['timestamp'],
                            'exit_price':   exit_price,
                            'pnl_usdt':     pnl_usdt,
                            'pnl_percent':  pnl_pct,
                            'exit_comment': action.get('exit_reason', act),
                        })

            self.equity_curve.append(self.strategy.balance)
            self.timestamp_list.append(candle['timestamp'])

        return self._generate_report()

    def _find_open_trade(self, exit_action: str) -> Dict | None:
        """
        Encontra o último trade sem exit_time.
        exit_action: 'EXIT_LONG' → procura trade 'BUY'; 'EXIT_SHORT' → 'SELL'
        """
        expected = 'BUY' if exit_action == 'EXIT_LONG' else 'SELL'
        for t in reversed(self.trades):
            if t['action'] == expected and t['exit_time'] is None:
                return t
        return None

    def _generate_report(self) -> Dict[str, Any]:
        # Filtra apenas trades com entrada E saída
        closed = [t for t in self.trades if t.get('exit_time') is not None]
        df_closed = pd.DataFrame(closed) if closed else pd.DataFrame()

        total_pnl  = df_closed['pnl_usdt'].sum()  if not df_closed.empty else 0.0
        win_trades = df_closed[df_closed['pnl_usdt'] > 0] if not df_closed.empty else pd.DataFrame()
        n_closed   = len(df_closed)

        return {
            'trades':         self.trades,
            'closed_trades':  closed,
            'equity_curve':   self.equity_curve,
            'timestamps':     self.timestamp_list,
            'total_trades':   n_closed,
            'win_rate':       len(win_trades) / n_closed * 100 if n_closed else 0.0,
            'total_pnl_usdt': total_pnl,
            'final_balance':  self.strategy.balance,
            'max_drawdown':   self._calculate_max_drawdown(),
            'sharpe':         self._calculate_sharpe(),
        }

    def _calculate_max_drawdown(self) -> float:
        """Max drawdown em % da curva de equity."""
        if len(self.equity_curve) < 2:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for v in self.equity_curve:
            if v > peak:
                peak = v
            if peak > 0:
                dd = (peak - v) / peak * 100
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    def _calculate_sharpe(self, risk_free_rate: float = 0.0,
                          periods_per_year: int = 252) -> float:
        """Sharpe Ratio anualizado."""
        if len(self.equity_curve) < 2:
            return 0.0
        eq = np.array(self.equity_curve, dtype=float)
        # Evita divisão por zero
        safe = np.where(eq[:-1] != 0, eq[:-1], np.nan)
        returns = np.diff(eq) / safe
        returns = returns[~np.isnan(returns)]
        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0
        excess = np.mean(returns) - (risk_free_rate / periods_per_year)
        return float((excess / np.std(returns)) * np.sqrt(periods_per_year))
