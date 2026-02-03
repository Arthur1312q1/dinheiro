"""
Motor que replica EXATAMENTE o comportamento do TradingView
com calc_on_every_tick=false
"""
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

class TradingViewEngine:
    def __init__(self, pine_interpreter, timeframe_minutes=30):
        self.interpreter = pine_interpreter
        self.timeframe = timeframe_minutes
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Estado das barras
        self.current_bar = None
        self.last_bar_timestamp = None
        self.bar_count = 0
        
        # Sinais (como no Pine)
        self.buy_signal_current = False
        self.sell_signal_current = False
        self.buy_signal_previous = False  # buy_signal[1]
        self.sell_signal_previous = False # sell_signal[1]
        
        # Flags pendentes (como pendingBuy/pendingSell no Pine)
        self.pending_buy = False
        self.pending_sell = False
        
        # Cache de preços da barra atual
        self.bar_prices = []
        
        logger.info(f"✅ TradingView Engine: timeframe={timeframe_minutes}m, calc_on_every_tick=false")
    
    def process_tick_for_bar(self, price: float, timestamp: datetime):
        """
        Processa um tick para formar a barra atual
        NO TRADINGVIEW: calc_on_every_tick=false significa que o script
        só roda quando a barra FECHA (não a cada tick)
        """
        # Verificar se começou nova barra
        bar_start = self._get_bar_start(timestamp)
        
        if self.last_bar_timestamp is None:
            self.last_bar_timestamp = bar_start
            self.current_bar = {
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'start': bar_start
            }
            self.bar_prices = [price]
            return False
        
        # Se ainda na mesma barra
        if bar_start == self.last_bar_timestamp:
            self.bar_prices.append(price)
            self.current_bar['high'] = max(self.current_bar['high'], price)
            self.current_bar['low'] = min(self.current_bar['low'], price)
            self.current_bar['close'] = price
            return False
        
        # 🔴🔴🔴 BARRA FECHOU! (MOMENTO CRÍTICO)
        # Este é o momento que o Pine Script roda com calc_on_every_tick=false
        bar_closed = self.current_bar['close']
        previous_bar_start = self.last_bar_timestamp
        
        logger.info("=" * 60)
        logger.info(f"📊 BARRA FECHADA: {previous_bar_start.strftime('%H:%M')}")
        logger.info(f"   Preço de fechamento: ${bar_closed:.2f}")
        
        # 1. PROCESSAR INDICADORES com o FECHAMENTO da barra anterior
        indicators = self.interpreter.process_candle_close(bar_closed)
        
        # 2. Atualizar sinais (como no Pine)
        self.buy_signal_previous = self.buy_signal_current
        self.sell_signal_previous = self.sell_signal_current
        
        self.buy_signal_current = indicators.get('buy_signal', False)
        self.sell_signal_current = indicators.get('sell_signal', False)
        
        # 3. Aplicar delay de 1 barra (como if (buy_signal[1]) no Pine)
        # Só marca como pendente se o sinal veio da BARRA ANTERIOR
        if self.buy_signal_previous:
            self.pending_buy = True
            self.pending_sell = False  # Resetar oposto (como no Pine)
            logger.info(f"   🟢 SINAL BUY CONFIRMADO (da barra anterior)")
        
        if self.sell_signal_previous:
            self.pending_sell = True
            self.pending_buy = False  # Resetar oposto (como no Pine)
            logger.info(f"   🔴 SINAL SELL CONFIRMADO (da barra anterior)")
        
        logger.info(f"   Sinais pendentes: BUY={self.pending_buy}, SELL={self.pending_sell}")
        logger.info("=" * 60)
        
        # 4. Iniciar nova barra
        self.last_bar_timestamp = bar_start
        self.current_bar = {
            'open': price,
            'high': price,
            'low': price,
            'close': price,
            'start': bar_start
        }
        self.bar_prices = [price]
        self.bar_count += 1
        
        return True  # Barra fechou
    
    def should_execute_entry(self):
        """
        Verifica se deve executar entrada NO FECHAMENTO DA BARRA ATUAL
        Como no Pine: if (pendingBuy and strategy.position_size <= 0)
        """
        return {
            'buy': self.pending_buy,
            'sell': self.pending_sell
        }
    
    def reset_pending_signals(self, side):
        """Reseta sinais pendentes após execução (como no Pine)"""
        if side == 'buy':
            self.pending_buy = False
        elif side == 'sell':
            self.pending_sell = False
    
    def _get_bar_start(self, timestamp):
        """Calcula início da barra de 30m"""
        return timestamp.replace(
            minute=(timestamp.minute // self.timeframe) * self.timeframe,
            second=0,
            microsecond=0
        )
    
    def get_status(self):
        """Retorna status para debug"""
        return {
            'bar_count': self.bar_count,
            'current_bar_start': self.last_bar_timestamp.strftime('%H:%M') if self.last_bar_timestamp else None,
            'buy_signal_current': self.buy_signal_current,
            'sell_signal_current': self.sell_signal_current,
            'buy_signal_previous': self.buy_signal_previous,
            'sell_signal_previous': self.sell_signal_previous,
            'pending_buy': self.pending_buy,
            'pending_sell': self.pending_sell,
            'current_price': self.current_bar['close'] if self.current_bar else None
        }
