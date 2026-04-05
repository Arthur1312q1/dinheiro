# backtest/engine.py
import time
import pandas as pd
import numpy as np
from datetime import timezone, timedelta
from typing import List, Dict, Any, Optional
from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA

BRT = timezone(timedelta(hours=-3))


def _to_brt_str(ts) -> str:
    try:
        if isinstance(ts, pd.Timestamp):
            if ts.tzinfo is None:
                ts = ts.tz_localize('UTC')
            return ts.tz_convert(BRT).strftime('%Y-%m-%dT%H:%M:%S')
        elif hasattr(ts, 'tzinfo'):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(BRT).strftime('%Y-%m-%dT%H:%M:%S')
    except Exception:
        pass
    return str(ts)[:19]


class BacktestEngine:
    """
    Motor de backtest com suporte a taxas de abertura e fechamento.

    open_fee_pct  : taxa de abertura  em % do valor nocional (ex: 0.06 = 0.06%)
    close_fee_pct : taxa de fechamento em % do valor nocional (ex: 0.06 = 0.06%)

    Bitget taker futures: 0.06% + 0.06%
    Timestamps em horário de Brasília (BRT, UTC-3).
    """

    def __init__(
        self,
        strategy: AdaptiveZeroLagEMA,
        data: pd.DataFrame,
        open_fee_pct: float = 0.0,
        close_fee_pct: float = 0.0,
        # Parâmetros exclusivos para modo LIVE
        symbol: str = "ETH/USDT",
        interval: str = "45m",
        collector=None,           # objeto com método get_ohlcv(symbol, interval, limit)
    ):
        self.strategy      = strategy
        self.data          = data.reset_index(drop=True)
        self.open_fee_pct  = open_fee_pct
        self.close_fee_pct = close_fee_pct
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        self.timestamp_list: List = []
        self.total_fees_paid: float = 0.0

        # Live
        self.symbol     = symbol
        self.interval   = interval
        self.collector  = collector
        self.is_running = False

    def _fee(self, price: float, qty: float, pct: float) -> float:
        """Taxa = valor_nocional × pct / 100"""
        return abs(price * qty * pct / 100.0)

    def run(self) -> Dict[str, Any]:
        for idx, row in self.data.iterrows():
            candle = {
                'open':      float(row['open']),
                'high':      float(row['high']),
                'low':       float(row['low']),
                'close':     float(row['close']),
                'timestamp': row.get('timestamp', idx),
                'index':     idx,
            }

            actions = self.strategy.next(candle)

            for action in actions:
                act    = action['action']
                ts_str = _to_brt_str(action['timestamp'])
                price  = action['price']
                qty    = action['qty']

                if act in ('BUY', 'SELL'):
                    open_fee = self._fee(price, qty, self.open_fee_pct)
                    self.total_fees_paid += open_fee
                    self.trades.append({
                        'entry_time':    ts_str,
                        'entry_price':   price,
                        'action':        act,
                        'qty':           qty,
                        'comment':       action.get('comment', ''),
                        'balance':       action['balance'],
                        'open_fee':      round(open_fee, 6),
                        # preenchidos ao fechar:
                        'exit_time':     None,
                        'exit_price':    None,
                        'pnl_usdt':      None,   # bruto (sem taxas)
                        'pnl_net':       None,   # líquido (após taxas)
                        'pnl_percent':   None,   # % bruto
                        'pnl_pct_net':   None,   # % líquido
                        'close_fee':     None,
                        'fees_total':    None,
                        'exit_comment':  None,
                    })

                elif act in ('EXIT_LONG', 'EXIT_SHORT'):
                    open_trade = self._find_open_trade(act)
                    if open_trade is not None:
                        entry_price = open_trade['entry_price']
                        trade_qty   = open_trade['qty']
                        pnl_gross   = action.get('pnl', 0.0)

                        close_fee  = self._fee(price, trade_qty, self.close_fee_pct)
                        open_fee   = open_trade.get('open_fee', 0.0)
                        fees_total = open_fee + close_fee
                        self.total_fees_paid += close_fee

                        pnl_net = pnl_gross - fees_total

                        if open_trade['action'] == 'BUY':
                            pnl_pct = ((price - entry_price) / entry_price) * 100
                        else:
                            pnl_pct = ((entry_price - price) / entry_price) * 100

                        nocional    = entry_price * trade_qty
                        pnl_pct_net = (pnl_net / nocional * 100) if nocional > 0 else 0.0

                        open_trade.update({
                            'exit_time':    ts_str,
                            'exit_price':   price,
                            'pnl_usdt':     round(pnl_gross,    6),
                            'pnl_net':      round(pnl_net,      6),
                            'pnl_percent':  round(pnl_pct,      4),
                            'pnl_pct_net':  round(pnl_pct_net,  4),
                            'close_fee':    round(close_fee,    6),
                            'fees_total':   round(fees_total,   6),
                            'exit_comment': action.get('exit_reason', act),
                        })

            self.equity_curve.append(self.strategy.balance)
            self.timestamp_list.append(_to_brt_str(candle['timestamp']))

        return self._generate_report()


    # ═══════════════════════════════════════════════════════════════════════
    # LIVE TRADING
    # ═══════════════════════════════════════════════════════════════════════

    def run_live(self):
        """
        Loop principal de live trading.

        Separa duas responsabilidades:
          • INTRABAR  : verifica stop/trail a cada ~5 s com o candle em formação.
                        Executa saída imediata ao preço EXATO do stop (sem slippage).
          • FECHAMENTO: só chama strategy.next() uma vez por candle fechado.
                        Registra entradas pendentes para o próximo open.

        Requer que o engine tenha sido construído com `symbol`, `interval` e
        `collector` (objeto com método get_ohlcv(symbol, interval, limit) → list[dict]).
        """
        if self.collector is None:
            raise RuntimeError(
                "run_live requer um `collector` com método get_ohlcv(). "
                "Passe collector=<seu_objeto> no construtor do BacktestEngine."
            )

        print(f"Iniciando modo LIVE: {self.symbol} ({self.interval})")
        self.is_running = True
        last_processed_ts = None

        while self.is_running:
            try:
                # 1. Busca os últimos candles
                #    bars[-2] = candle que acabou de fechar
                #    bars[-1] = candle em formação (intrabar)
                bars = self.collector.get_ohlcv(self.symbol, self.interval, limit=5)
                if not bars or len(bars) < 2:
                    time.sleep(5)
                    continue

                closed_bar  = bars[-2]   # candle fechado
                current_bar = bars[-1]   # candle aberto (intrabar)

                # ── LÓGICA INTRABAR ──────────────────────────────────────────
                # FIX-18: is_entry_candle só no PRIMEIRO poll após confirm_fill.
                # Evita saída imediata no preço de entrada causada pelo H/L do
                # candle em formação que inclui preços anteriores ao fill.
                is_entry = getattr(self.strategy, '_just_filled', False)
                if is_entry:
                    self.strategy._just_filled = False  # consome o flag imediatamente

                # Usa mark price real como current_price (injetado apenas quando
                # is_entry_candle=True; poll normal usa H/L do candle diretamente)
                ticker_px = self._mark_price_fast() if hasattr(self, '_mark_price_fast') \
                            else float(current_bar.get('close', 0))
                exit_action = self.strategy.update_trailing_live(
                    high=float(current_bar['high']),
                    low=float(current_bar['low']),
                    ts=current_bar['timestamp'],
                    is_entry_candle=is_entry,
                    current_price=ticker_px,
                )

                if exit_action:
                    exit_price = exit_action.get('price', exit_action.get('exit_price', ticker_px))
                    reason = exit_action.get('exit_reason', 'INTRABAR_STOP')
                    print(
                        f"!!! STOP HIT INTRABAR: {reason} "
                        f"em {exit_price:.2f} (is_entry_candle={is_entry}) !!!"
                    )
                    self.execute_live_exit(exit_action)
                    time.sleep(2)
                    continue   # reavalia o loop após a saída

                # ── LÓGICA DE FECHAMENTO DE CANDLE ───────────────────────────
                # Roda strategy.next() apenas uma vez por candle fechado
                # (quando o timestamp muda).
                if last_processed_ts != closed_bar['timestamp']:
                    print(f"Processando fechamento do candle: {closed_bar['timestamp']}")

                    actions = self.strategy.next(closed_bar)

                    # Saídas técnicas geradas pelo next() (ex: reversão no close)
                    for action in actions:
                        act = action.get('action', '')
                        if act in ('EXIT_LONG', 'EXIT_SHORT'):
                            self.execute_live_exit(action)

                    # Entradas pendentes para o próximo open
                    pending_orders = self.strategy.get_pending_orders()
                    for order in pending_orders:
                        self.execute_live_entry(order)

                    last_processed_ts = closed_bar['timestamp']

                # Polling intrabar a cada 5 segundos
                time.sleep(5)

            except KeyboardInterrupt:
                print("Loop LIVE interrompido pelo usuário.")
                self.is_running = False
            except Exception as e:
                print(f"Erro no loop Live: {e}")
                time.sleep(10)

    def execute_live_exit(self, action: Dict) -> None:
        """
        Executa uma saída de posição na exchange.

        Implemente este método para enviar a ordem de fechamento à exchange.
        O `action` contém os campos retornados por `_exit_at` / `update_trailing_live`:
            'action'      : 'EXIT_LONG' | 'EXIT_SHORT'
            'price'       : preço exato do stop (usar como limit ou market)
            'qty'         : quantidade
            'pnl'         : PnL bruto (simulado)
            'exit_reason' : 'TRAIL' | 'SL' | 'REVERSAL' | ...
            'timestamp'   : timestamp do candle que gerou o sinal

        Exemplo de integração (Bitget / ccxt):
            order = exchange.create_market_order(
                symbol=self.symbol,
                side='sell' if action['action'] == 'EXIT_LONG' else 'buy',
                amount=action['qty'],
            )
        """
        raise NotImplementedError(
            "Implemente execute_live_exit() com a lógica de ordem da sua exchange."
        )

    def execute_live_entry(self, order: Dict) -> None:
        """
        Executa uma entrada de posição na exchange.

        Implemente este método para enviar a ordem de abertura à exchange.
        O `order` contém os campos retornados por `get_pending_orders()`:
            'side'          : 'BUY' | 'SELL'
            'qty'           : quantidade calculada pelo gerenciamento de risco
            'sl_ticks'      : stop loss em ticks
            'sl_price_dist' : distância do SL em USDT por unidade
            'trail_points'  : ticks para ativar o trailing
            'trail_offset'  : ticks de distância do peak
            'tick_size'     : syminfo.mintick
            'comment'       : label da ordem

        IMPORTANTE — FIX-18: após confirmar o fill com strategy.confirm_fill(),
        obrigatoriamente setar:
            self.strategy._just_filled = True
        Isso garante que o PRIMEIRO poll intrabar pós-fill use is_entry_candle=True
        em update_trailing_live(), evitando saídas imediatas no preço de entrada.

        Exemplo de integração (Bitget / ccxt):
            order_resp = exchange.create_market_order(
                symbol=self.symbol,
                side=order['side'].lower(),
                amount=order['qty'],
            )
            # Após confirmar o fill, chamar strategy.confirm_fill(...)
            # e obrigatoriamente:
            self.strategy._just_filled = True
        """
        raise NotImplementedError(
            "Implemente execute_live_entry() com a lógica de ordem da sua exchange."
        )

    def _find_open_trade(self, exit_action: str) -> Dict | None:
        expected = 'BUY' if exit_action == 'EXIT_LONG' else 'SELL'
        for t in reversed(self.trades):
            if t['action'] == expected and t['exit_time'] is None:
                return t
        return None

    def _generate_report(self) -> Dict[str, Any]:
        closed    = [t for t in self.trades if t.get('exit_time') is not None]
        df_closed = pd.DataFrame(closed) if closed else pd.DataFrame()
        use_fees  = self.open_fee_pct > 0 or self.close_fee_pct > 0

        if not df_closed.empty:
            pnl_col   = 'pnl_net' if use_fees else 'pnl_usdt'
            total_pnl = df_closed[pnl_col].sum()
            wins      = df_closed[df_closed[pnl_col] > 0]
            n_closed  = len(df_closed)
        else:
            total_pnl = 0.0
            wins      = pd.DataFrame()
            n_closed  = 0

        return {
            'trades':          self.trades,
            'closed_trades':   closed,
            'equity_curve':    self.equity_curve,
            'timestamps':      self.timestamp_list,
            'total_trades':    n_closed,
            'win_rate':        len(wins) / n_closed * 100 if n_closed else 0.0,
            'total_pnl_usdt':  total_pnl,
            'total_fees_paid': round(self.total_fees_paid, 4),
            'final_balance':   self.strategy.balance,
            'max_drawdown':    self._calculate_max_drawdown(),
            'sharpe':          self._calculate_sharpe(),
            'open_fee_pct':    self.open_fee_pct,
            'close_fee_pct':   self.close_fee_pct,
            'fees_enabled':    use_fees,
        }

    def _calculate_max_drawdown(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        peak   = self.equity_curve[0]
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
        if len(self.equity_curve) < 2:
            return 0.0
        eq      = np.array(self.equity_curve, dtype=float)
        safe    = np.where(eq[:-1] != 0, eq[:-1], np.nan)
        returns = np.diff(eq) / safe
        returns = returns[~np.isnan(returns)]
        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0
        excess = np.mean(returns) - (risk_free_rate / periods_per_year)
        return float((excess / np.std(returns)) * np.sqrt(periods_per_year))
