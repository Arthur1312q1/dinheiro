# backtest/engine.py
import pandas as pd
import numpy as np
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
                        entry_trade = self.trades[-1]
                        exit_action = action
                        
                        # Cálculo de PnL em USDT (já feito pela estratégia)
                        pnl_usdt = exit_action.get('pnl', 0)
                        
                        # Cálculo de PnL em % (corrigido para LONG e SHORT)
                        entry_price = entry_trade['entry_price']
                        exit_price = exit_action['price']
                        qty = entry_trade['qty']
                        
                        if entry_trade['action'] == 'BUY':
                            # LONG: ganho quando preço sobe
                            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                        else:
                            # SHORT: ganho quando preço cai
                            pnl_percent = ((entry_price - exit_price) / entry_price) * 100
                        
                        entry_trade.update({
                            'exit_time': exit_action['timestamp'],
                            'exit_price': exit_price,
                            'pnl_usdt': pnl_usdt,
                            'pnl_percent': pnl_percent,
                            'exit_comment': exit_action['comment']
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
        """
        Calcula o máximo drawdown em % da curva de capital.
        
        Drawdown = (Peak - Current) / Peak * 100
        Max Drawdown = máximo drawdown observado
        """
        if not self.equity_curve or len(self.equity_curve) < 2:
            return 0.0
        
        peak = self.equity_curve[0]
        max_dd = 0.0
        
        for value in self.equity_curve:
            if value > peak:
                peak = value
            
            dd = ((peak - value) / peak) * 100
            if dd > max_dd:
                max_dd = dd
        
        return max_dd

    def _calculate_sharpe(self, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
        """
        Calcula o Sharpe Ratio anualizado.
        
        Sharpe = (retorno médio - taxa livre de risco) / desvio padrão * sqrt(períodos por ano)
        
        Args:
            risk_free_rate: Taxa livre de risco (padrão 0% a.a.)
            periods_per_year: Períodos por ano (252 para diário, 252*24 para horário, etc)
        """
        if len(self.equity_curve) < 2:
            return 0.0
        
        # Calcula retornos diários (ou periódicos)
        equity_array = np.array(self.equity_curve)
        returns = np.diff(equity_array) / equity_array[:-1]
        
        # Evita divisão por zero e valores muito pequenos
        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0
        
        # Sharpe ratio anualizado
        excess_return = np.mean(returns) - (risk_free_rate / periods_per_year)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
        
        sharpe = (excess_return / std_return) * np.sqrt(periods_per_year)
        return float(sharpe)
