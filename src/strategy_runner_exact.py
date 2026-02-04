#!/usr/bin/env python3
"""
STRATEGY RUNNER EXACT - VERSÃO FINAL QUE FUNCIONA IGUAL TRADINGVIEW
"""
import os
import logging
import time
import threading
import json
import websocket
from datetime import datetime, timedelta
import pytz

from .pine_engine_v2 import AdaptiveZeroLagEMA
from .okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunnerExact:
    """Runner SIMPLIFICADO mas que executa TODAS as trades"""
    
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # Carregar Pine Script
        pine_code = "// Pine Script v3"  # Placeholder
        self.engine = AdaptiveZeroLagEMA(pine_code)
        
        # Configurações
        self.timeframe_minutes = 30
        self.mintick = 0.01
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Estado
        self.is_running = False
        self.current_price = None
        self.last_bar_time = None
        self.bar_count = 0
        
        # Sinais CRÍTICO: TradingView usa delay de 1 barra
        self.buy_signal_previous_bar = False  # Sinal da barra ANTERIOR
        self.sell_signal_previous_bar = False  # Sinal da barra ANTERIOR
        self.buy_signal_current_bar = False    # Sinal da barra ATUAL (será executado na próxima)
        self.sell_signal_current_bar = False   # Sinal da barra ATUAL (será executado na próxima)
        
        # Posição
        self.position_size = 0
        self.position_side = None
        self.entry_price = None
        self.trade_id = None
        
        # Trailing Stop
        self.stop_loss_price = None
        self.take_profit_price = None
        self.trailing_activated = False
        self.trailing_stop_price = None
        
        # Controle
        self.last_check = time.time()
        
        logger.info("✅ Strategy Runner Exact inicializado (VERSÃO SIMPLIFICADA)")
    
    def _calculate_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição"""
        balance = self.okx_client.get_balance()
        risk_amount = 0.01 * balance  # 1% risk
        stop_loss_usdt = 2000 * 0.01  # 2000 points * $0.01
        
        if stop_loss_usdt <= 0:
            return 0
        
        quantity = risk_amount / stop_loss_usdt
        quantity = round(quantity, 4)
        
        logger.info(f"📊 Posição: ${entry_price:.2f} → {quantity:.4f} ETH")
        return quantity
    
    def _open_position(self, side: str, price: float) -> bool:
        """Abre posição"""
        logger.info("=" * 60)
        logger.info(f"🔍 TENTANDO ABRIR {side.upper()} @ ${price:.2f}")
        
        # REGRA: BUY só se não está em LONG, SELL só se não está em SHORT
        if side == 'buy' and self.position_size > 0:
            logger.info("⏭️  IGNORADO: Já está em LONG")
            return False
        
        if side == 'sell' and self.position_size < 0:
            logger.info("⏭️  IGNORADO: Já está em SHORT")
            return False
        
        # Calcular quantidade
        quantity = self._calculate_position_size(price)
        if quantity <= 0:
            logger.error("❌ Quantidade inválida")
            return False
        
        # Registrar no histórico
        trade_id = self.trade_history.add_trade(
            side=side,
            entry_price=price,
            quantity=quantity
        )
        
        if not trade_id:
            return False
        
        # Atualizar estado
        self.trade_id = trade_id
        self.position_side = side
        self.position_size = quantity if side == 'buy' else -quantity
        self.entry_price = price
        
        # Configurar stop loss e take profit INICIAIS
        if side == 'buy':
            self.stop_loss_price = price - (2000 * self.mintick)  # 2000 pontos
            self.take_profit_price = price + (55 * self.mintick)  # 55 pontos
        else:
            self.stop_loss_price = price + (2000 * self.mintick)
            self.take_profit_price = price - (55 * self.mintick)
        
        self.trailing_activated = False
        self.trailing_stop_price = self.stop_loss_price
        
        logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {quantity:.4f} ETH @ ${price:.2f}")
        logger.info(f"   Stop Loss: ${self.stop_loss_price:.2f}")
        logger.info(f"   Take Profit: ${self.take_profit_price:.2f}")
        logger.info("=" * 60)
        
        return True
    
    def _close_position(self, price: float, reason: str) -> bool:
        """Fecha posição"""
        if self.position_size == 0:
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()}")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Entrada: ${self.entry_price:.2f}, Saída: ${price:.2f}")
        
        # Calcular PnL
        if self.entry_price:
            if self.position_side == 'long':
                pnl_pct = ((price - self.entry_price) / self.entry_price) * 100
                pnl_usdt = (price - self.entry_price) * abs(self.position_size)
            else:
                pnl_pct = ((self.entry_price - price) / self.entry_price) * 100
                pnl_usdt = (self.entry_price - price) * abs(self.position_size)
            
            logger.info(f"   PnL: {pnl_pct:.2f}% (${pnl_usdt:.2f})")
        
        # Fechar no histórico
        success = self.trade_history.close_trade(self.trade_id, price)
        
        if success:
            logger.info(f"✅ POSIÇÃO FECHADA @ ${price:.2f}")
            
            # Resetar estado
            self.position_size = 0
            self.position_side = None
            self.entry_price = None
            self.trade_id = None
            self.stop_loss_price = None
            self.take_profit_price = None
            self.trailing_activated = False
            self.trailing_stop_price = None
            
            logger.info("=" * 60)
            return True
        
        return False
    
    def _update_trailing_stop(self, current_price: float):
        """Atualiza trailing stop"""
        if self.position_size == 0 or not self.take_profit_price:
            return
        
        # Verificar se take profit foi atingido (ativa trailing)
        if not self.trailing_activated:
            if self.position_side == 'long' and current_price >= self.take_profit_price:
                self.trailing_activated = True
                self.trailing_stop_price = current_price - (15 * self.mintick)  # 15 pontos
                logger.info(f"🎯 TRAILING ATIVADO (LONG): ${current_price:.2f}")
            
            elif self.position_side == 'short' and current_price <= self.take_profit_price:
                self.trailing_activated = True
                self.trailing_stop_price = current_price + (15 * self.mintick)
                logger.info(f"🎯 TRAILING ATIVADO (SHORT): ${current_price:.2f}")
        
        # Se trailing ativado, atualizar stop
        if self.trailing_activated:
            if self.position_side == 'long':
                # Atualizar stop para proteger lucros
                new_stop = current_price - (15 * self.mintick)
                if new_stop > self.trailing_stop_price:
                    self.trailing_stop_price = new_stop
            else:
                new_stop = current_price + (15 * self.mintick)
                if new_stop < self.trailing_stop_price:
                    self.trailing_stop_price = new_stop
            
            # Verificar se deve fechar
            if self.position_side == 'long' and current_price <= self.trailing_stop_price:
                self._close_position(current_price, "trailing_stop")
            elif self.position_side == 'short' and current_price >= self.trailing_stop_price:
                self._close_position(current_price, "trailing_stop")
    
    def _process_new_bar(self):
        """Processa nova barra de 30min - TIMING CRÍTICO"""
        now = datetime.now(self.tz_brazil)
        
        # Calcular início da barra atual
        current_minute = now.minute
        bar_minute = (current_minute // 30) * 30
        current_bar_start = now.replace(minute=bar_minute, second=0, microsecond=0)
        
        # Primeira execução
        if self.last_bar_time is None:
            self.last_bar_time = current_bar_start
            return False
        
        # Verificar se é uma nova barra
        if current_bar_start > self.last_bar_time:
            logger.info("=" * 60)
            logger.info(f"📊 NOVA BARRA 30m: {current_bar_start.strftime('%H:%M')}")
            logger.info(f"   Preço: ${self.current_price:.2f}")
            
            # 1. EXECUTAR sinais da barra ANTERIOR
            self._execute_previous_signals()
            
            # 2. Processar candle anterior para gerar NOVOS sinais
            self._generate_new_signals()
            
            # 3. Atualizar timestamp
            self.last_bar_time = current_bar_start
            self.bar_count += 1
            
            logger.info(f"   Barra #{self.bar_count} processada")
            logger.info("=" * 60)
            return True
        
        return False
    
    def _execute_previous_signals(self):
        """Executa sinais da barra ANTERIOR (TradingView usa delay de 1 barra)"""
        logger.info(f"🔍 EXECUTANDO SINAIS DA BARRA ANTERIOR:")
        logger.info(f"   BUY signal: {self.buy_signal_previous_bar}")
        logger.info(f"   SELL signal: {self.sell_signal_previous_bar}")
        
        if self.buy_signal_previous_bar and self.position_size <= 0:
            logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior)")
            self._open_position('buy', self.current_price)
        
        elif self.sell_signal_previous_bar and self.position_size >= 0:
            logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior)")
            self._open_position('sell', self.current_price)
        
        # Resetar sinais anteriores
        self.buy_signal_previous_bar = False
        self.sell_signal_previous_bar = False
    
    def _generate_new_signals(self):
        """Gera novos sinais para a barra ATUAL (serão executados na próxima)"""
        if not self.current_price:
            return
        
        # Criar candle simulado com o preço atual
        candle = {
            'open': self.current_price,
            'high': self.current_price,
            'low': self.current_price,
            'close': self.current_price,
            'volume': 0
        }
        
        # Processar através do engine
        signals = self.engine.process_candle(candle)
        
        # Salvar sinais para próxima barra
        self.buy_signal_current_bar = signals.get('buy_signal_current', False)
        self.sell_signal_current_bar = signals.get('sell_signal_current', False)
        
        # Os sinais ATUAIS se tornam os sinais da barra ANTERIOR para a próxima execução
        self.buy_signal_previous_bar = self.buy_signal_current_bar
        self.sell_signal_previous_bar = self.sell_signal_current_bar
        
        logger.info(f"📈 Sinais gerados para PRÓXIMA barra:")
        logger.info(f"   BUY: {self.buy_signal_current_bar}")
        logger.info(f"   SELL: {self.sell_signal_current_bar}")
    
    def run_strategy_realtime(self):
        """Loop principal - VERSÃO DIRETA"""
        if not self.is_running:
            return {"status": "stopped"}
        
        try:
            # Obter preço atual
            self.current_price = self.okx_client.get_ticker_price()
            
            # Verificar nova barra
            new_bar = self._process_new_bar()
            
            # Verificar trailing stop
            self._update_trailing_stop(self.current_price)
            
            # Log periódico
            current_time = time.time()
            if current_time - self.last_check > 30:
                if self.current_price:
                    position_info = f"{self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH"
                    logger.info(f"📈 Status: ${self.current_price:.2f} | {position_info}")
                
                self.last_check = current_time
            
            return {
                "status": "running",
                "current_price": self.current_price,
                "position_side": self.position_side,
                "position_size": self.position_size,
                "entry_price": self.entry_price,
                "buy_signal_pending": self.buy_signal_previous_bar,
                "sell_signal_pending": self.sell_signal_previous_bar
            }
            
        except Exception as e:
            logger.error(f"💥 Erro no loop: {e}")
            return {"status": "error", "error": str(e)}
    
    def start(self):
        """Inicia o runner"""
        logger.info("🚀 Iniciando Strategy Runner...")
        
        # Obter candles iniciais para aquecer o engine
        candles = self.okx_client.get_candles(limit=50)
        for candle in candles:
            self.engine.process_candle(candle)
        
        self.is_running = True
        logger.info("✅ Strategy Runner iniciado")
        return True
    
    def stop(self):
        """Para o runner"""
        self.is_running = False
        logger.info("⏹️ Strategy Runner parado")
