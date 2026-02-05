#!/usr/bin/env python3
"""
STRATEGY RUNNER EXACT - VERSÃO FINAL 100% IDÊNTICA AO TRADINGVIEW
"""
import os
import logging
import time
import threading
import json
import websocket
from datetime import datetime, timedelta
import pytz
from typing import Dict, Any

from .pine_engine_v2 import AdaptiveZeroLagEMA
from .okx_client import OKXClient

logger = logging.getLogger(__name__)

class TrailingStopManager:
    """Gerencia trailing stop IDÊNTICO ao strategy.exit do Pine Script"""
    
    def __init__(self, side: str, entry_price: float, 
                 fixed_sl: int, fixed_tp: int, mintick: float):
        self.side = side
        self.entry_price = entry_price
        self.fixed_sl = fixed_sl
        self.fixed_tp = fixed_tp
        self.mintick = mintick
        
        # Estado inicial (trail_offset = 15 FIXO no Pine)
        self.trail_offset = 15  # FIXO no código Pine
        self.trailing_activated = False
        self.best_price = entry_price
        
        # Stop inicial (loss = fixedSL)
        if side == 'long':
            self.current_stop = entry_price - (fixed_sl * mintick)
            self.tp_trigger = entry_price + (fixed_tp * mintick)
        else:  # short
            self.current_stop = entry_price + (fixed_sl * mintick)
            self.tp_trigger = entry_price - (fixed_tp * mintick)
        
        logger.info(f"🎯 TRAILING STOP CONFIGURADO ({side.upper()}):")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.current_stop:.2f}")
        logger.info(f"   TP Trigger (trail_points={fixed_tp}): ${self.tp_trigger:.2f}")
        logger.info(f"   Trail Offset: {self.trail_offset}p = ${self.trail_offset * mintick:.2f}")
    
    def update(self, current_price: float) -> float:
        """Atualiza trailing stop - IDÊNTICO ao strategy.exit"""
        
        # Verificar se deve ativar trailing (TP atingido)
        if not self.trailing_activated:
            if self.side == 'long' and current_price >= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop com trail_offset
                new_stop = current_price - (self.trail_offset * self.mintick)
                
                # Stop nunca pode ser pior que o inicial
                initial_stop = self.entry_price - (self.fixed_sl * self.mintick)
                if new_stop < initial_stop:
                    new_stop = initial_stop
                
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (LONG): ${current_price:.2f} >= ${self.tp_trigger:.2f}")
                logger.info(f"   Stop ajustado para: ${self.current_stop:.2f}")
                
            elif self.side == 'short' and current_price <= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop com trail_offset
                new_stop = current_price + (self.trail_offset * self.mintick)
                
                # Stop nunca pode ser pior que o inicial
                initial_stop = self.entry_price + (self.fixed_sl * self.mintick)
                if new_stop > initial_stop:
                    new_stop = initial_stop
                
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (SHORT): ${current_price:.2f} <= ${self.tp_trigger:.2f}")
                logger.info(f"   Stop ajustado para: ${self.current_stop:.2f}")
        
        # Se trailing ativado, atualizar
        if self.trailing_activated:
            if self.side == 'long':
                # Atualizar best price (preço mais ALTO)
                if current_price > self.best_price:
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price - (self.trail_offset * self.mintick)
                    
                    # Stop só move para CIMA
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
                        logger.debug(f"   📈 Trailing atualizado: ${self.current_stop:.2f}")
                
            else:  # short
                # Atualizar best price (preço mais BAIXO)
                if current_price < self.best_price:
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price + (self.trail_offset * self.mintick)
                    
                    # Stop só move para BAIXO
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
                        logger.debug(f"   📉 Trailing atualizado: ${self.current_stop:.2f}")
        
        return self.current_stop
    
    def should_close(self, current_price: float) -> bool:
        """Verifica se deve fechar posição"""
        if self.side == 'long':
            return current_price <= self.current_stop
        else:  # short
            return current_price >= self.current_stop

class StrategyRunnerExact:
    """Executa estratégia 100% IDÊNTICA ao TradingView - SEM DELAY"""
    
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # Carregar código Pine Script
        pine_code = self._load_pine_script()
        if not pine_code:
            raise Exception("Não foi possível carregar Pine Script")
        
        # Inicializar interpretador Pine
        self.engine = AdaptiveZeroLagEMA(pine_code)
        
        # Configurações IDÊNTICAS ao TradingView
        self.timeframe_minutes = 30
        self.mintick = 0.01  # ETH/USDT no TradingView (syminfo.mintick)
        
        # Usar UTC como TradingView
        self.tz_utc = pytz.utc
        
        # Estado da execução
        self.is_running = False
        self.current_price = None
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais EXATAMENTE como Pine Script
        self.buy_signal_current = False      # buy_signal da barra atual
        self.sell_signal_current = False     # sell_signal da barra atual
        self.buy_signal_prev = False         # buy_signal[1] (barra anterior)
        self.sell_signal_prev = False        # sell_signal[1] (barra anterior)
        self.pending_buy = False             # pendingBuy (persistente)
        self.pending_sell = False            # pendingSell (persistente)
        
        # Posição atual
        self.position_size = 0
        self.position_side = None
        self.entry_price = None
        self.trade_id = None
        
        # Trailing Stop
        self.trailing_manager = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        
        # Timing crítico - SEM DELAY
        self.next_bar_check = None
        self.bar_process_lock = threading.Lock()
        
        logger.info("✅ StrategyRunnerExact inicializado (100% IDÊNTICO ao Pine Script)")

    def _load_pine_script(self):
        """Carrega código Pine Script do arquivo"""
        try:
            paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "Adaptive_Zero_Lag_EMA_v2.pine",
                "./strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.info(f"✅ Pine Script carregado: {len(content)} bytes")
                        return content
            
            logger.warning("⚠️  Arquivo Pine Script não encontrado")
            return None
            
        except Exception as e:
            logger.error(f"❌ Erro ao carregar Pine Script: {e}")
            return None

    def _calculate_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição IDÊNTICO ao Pine Script"""
        try:
            # Obter balance (initial_capital + netprofit)
            # No Pine: balance = strategy.initial_capital + strategy.netprofit
            balance = 1000.0  # initial_capital fixo em 1000 como no Pine
            
            # Fórmula EXATA do Pine Script:
            # riskAmount = risk * balance
            # stopLossUSDT = fixedSL * syminfo.mintick
            # lots = riskAmount / stopLossUSDT
            risk_amount = self.engine.risk * balance
            stop_loss_usdt = self.engine.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                logger.error(f"❌ Stop Loss USDT inválido: {stop_loss_usdt}")
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Aplicar limite máximo (do input 'limit' no Pine)
            if quantity > self.engine.limit:
                quantity = self.engine.limit
            
            # Arredondar para 4 casas decimais (ETH)
            quantity = round(quantity, 4)
            
            if quantity > 0:
                logger.info(f"   📊 Cálculo de posição (IDÊNTICO ao Pine):")
                logger.info(f"     Balance: ${balance:.2f}")
                logger.info(f"     Risk: {self.engine.risk*100}% = ${risk_amount:.2f}")
                logger.info(f"     Stop Loss: {self.engine.fixedSL}p = ${stop_loss_usdt:.2f}")
                logger.info(f"     Quantidade: {quantity:.4f} ETH")
                logger.info(f"     Limit máximo: {self.engine.limit} ETH")
            
            return quantity
            
        except Exception as e:
            logger.error(f"❌ Erro cálculo posição: {e}")
            return 0

    def _open_position(self, side: str, entry_price: float) -> bool:
        """Abre posição IDÊNTICA ao Pine Script"""
        logger.info("=" * 60)
        logger.info(f"🔍 VERIFICANDO ABERTURA {side.upper()}")
        logger.info(f"   Preço: ${entry_price:.2f}")
        logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
        
        # REGRA EXATA DO PINE SCRIPT (linhas 107-108):
        # BUY só se strategy.position_size <= 0 (flat ou short)
        # SELL só se strategy.position_size >= 0 (flat ou long)
        
        if side == 'buy':
            if self.position_size > 0:
                logger.info("⏭️  IGNORADO: Já está em LONG (position_size > 0)")
                logger.info("=" * 60)
                return False
        else:  # sell
            if self.position_size < 0:
                logger.info("⏭️  IGNORADO: Já está em SHORT (position_size < 0)")
                logger.info("=" * 60)
                return False
        
        # Calcular quantidade
        quantity = self._calculate_position_size(entry_price)
        if quantity <= 0:
            logger.error("❌ Quantidade inválida")
            logger.info("=" * 60)
            return False
        
        # Registrar trade no histórico
        trade_id = self.trade_history.add_trade(
            side=side,
            entry_price=entry_price,
            quantity=quantity
        )
        
        if not trade_id:
            logger.error("❌ Falha ao registrar trade")
            logger.info("=" * 60)
            return False
        
        # Atualizar estado da posição
        self.trade_id = trade_id
        self.position_side = side
        self.position_size = quantity if side == 'buy' else -quantity
        self.entry_price = entry_price
        
        # Inicializar Trailing Stop Manager (EXATO como strategy.exit)
        self.trailing_manager = TrailingStopManager(
            side=side,
            entry_price=entry_price,
            fixed_sl=self.engine.fixedSL,
            fixed_tp=self.engine.fixedTP,
            mintick=self.mintick
        )
        
        # Resetar flags pendentes APÓS execução (como no Pine linhas 125 e 134)
        if side == 'buy':
            self.pending_buy = False
            logger.info(f"   pendingBuy = false (resetado após execução)")
        else:
            self.pending_sell = False
            logger.info(f"   pendingSell = false (resetado após execução)")
        
        # Log detalhado
        logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {abs(quantity):.4f} ETH")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.trailing_manager.current_stop:.2f}")
        logger.info(f"   TP para trailing: ${self.trailing_manager.tp_trigger:.2f}")
        logger.info("=" * 60)
        return True

    def _close_position(self, exit_price: float, reason: str = "") -> bool:
        """Fecha posição"""
        if not self.trade_id or self.position_size == 0:
            logger.warning("⚠️  Nenhuma posição para fechar")
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()}")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Preço entrada: ${self.entry_price:.2f}")
        logger.info(f"   Preço saída: ${exit_price:.2f}")
        
        # Calcular PnL
        if self.entry_price:
            if self.position_side == 'long':
                pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100
                pnl_usdt = (exit_price - self.entry_price) * abs(self.position_size)
            else:
                pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100
                pnl_usdt = (self.entry_price - exit_price) * abs(self.position_size)
            
            pnl_pct = round(pnl_pct, 2)
            pnl_usdt = round(pnl_usdt, 2)
            
            logger.info(f"   PnL: {pnl_pct}% (${pnl_usdt})")
        
        # Fechar no histórico
        success = self.trade_history.close_trade(self.trade_id, exit_price)
        
        if success:
            logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${exit_price:.2f}")
            
            # Resetar estado
            self.position_size = 0
            self.position_side = None
            self.entry_price = None
            self.trade_id = None
            self.trailing_manager = None
            
            logger.info("=" * 60)
            return True
        else:
            logger.error("❌ Falha ao fechar trade no histórico")
            logger.info("=" * 60)
            return False

    def _check_trailing_stop(self):
        """Verifica trailing stop IDÊNTICO ao Pine Script"""
        if not self.position_size or not self.current_price or not self.trailing_manager:
            return
        
        # Atualizar trailing stop com preço atual
        current_stop = self.trailing_manager.update(self.current_price)
        
        # Verificar se deve fechar
        if self.trailing_manager.should_close(self.current_price):
            logger.info("=" * 60)
            logger.info(f"🎯 TRAILING STOP ATINGIDO!")
            logger.info(f"   Preço atual: ${self.current_price:.2f}")
            logger.info(f"   Stop atual: ${current_stop:.2f}")
            logger.info(f"   Entrada: ${self.entry_price:.2f}")
            
            # Fechar posição
            self._close_position(self.current_price, "trailing_stop")

    def _calculate_next_bar_time(self):
        """Calcula o momento exato da próxima barra de 30m em UTC"""
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        
        # Calcular início da próxima barra
        current_minute = now_utc.minute
        current_bar_minute = (current_minute // self.timeframe_minutes) * self.timeframe_minutes
        current_bar_start = now_utc.replace(minute=current_bar_minute, second=0, microsecond=0)
        
        # Próxima barra começa em +30 minutos
        next_bar_start = current_bar_start + timedelta(minutes=self.timeframe_minutes)
        
        return next_bar_start

    def _check_bar_completion(self):
        """Verifica se a barra atual foi completada - TIMING EXATO"""
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        
        # Se for a primeira vez, inicializar
        if self.last_bar_timestamp is None:
            self.last_bar_timestamp = now_utc
            self.current_bar_data = {
                'timestamp': int(now_utc.timestamp() * 1000),
                'open': self.current_price if self.current_price else 0,
                'high': self.current_price if self.current_price else 0,
                'low': self.current_price if self.current_price else 0,
                'close': self.current_price if self.current_price else 0,
                'volume': 0
            }
            self.next_bar_check = self._calculate_next_bar_time()
            logger.info(f"⏰ Primeira barra iniciada: {now_utc.strftime('%H:%M:%S')} UTC")
            return False
        
        # Verificar se é hora da próxima barra
        if now_utc >= self.next_bar_check:
            logger.info("=" * 60)
            logger.info(f"📊 BARRA COMPLETADA: {self.last_bar_timestamp.strftime('%H:%M')} UTC")
            
            # Processar a barra que acabou de fechar
            if self.current_bar_data:
                # Atualizar preço de fechamento com o último preço antes da nova barra
                self.current_bar_data['close'] = self.current_price if self.current_price else self.current_bar_data['close']
                
                # 1. Primeiro: Processar barra anterior para gerar sinais
                self._process_completed_bar()
                
                # 2. Depois: Executar sinais pendentes (da barra anterior [1])
                self._execute_pending_signals()
            
            # Iniciar nova barra
            self.last_bar_timestamp = now_utc
            self.bar_count += 1
            self.current_bar_data = {
                'timestamp': int(now_utc.timestamp() * 1000),
                'open': self.current_price if self.current_price else 0,
                'high': self.current_price if self.current_price else 0,
                'low': self.current_price if self.current_price else 0,
                'close': self.current_price if self.current_price else 0,
                'volume': 0
            }
            
            # Calcular próximo check
            self.next_bar_check = self._calculate_next_bar_time()
            
            logger.info(f"   Nova barra #{self.bar_count} iniciada: {now_utc.strftime('%H:%M:%S')} UTC")
            logger.info(f"   Próximo check: {self.next_bar_check.strftime('%H:%M:%S')} UTC")
            logger.info("=" * 60)
            return True
        
        # Atualizar dados da barra atual
        if self.current_bar_data and self.current_price:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False

    def _execute_pending_signals(self):
        """Executa sinais pendentes - EXATAMENTE como Pine Script (linhas 113-134)"""
        if not self.current_price:
            return
        
        logger.info(f"🔍 EXECUTANDO SINAIS PENDENTES (abertura da barra):")
        logger.info(f"   Preço de abertura: ${self.current_price:.2f}")
        logger.info(f"   pendingBuy: {self.pending_buy}")
        logger.info(f"   pendingSell: {self.pending_sell}")
        logger.info(f"   position_size: {self.position_size}")
        
        # REGRA EXATA DO PINE SCRIPT (linhas 113-134):
        # if (pendingBuy and strategy.position_size <= 0)
        # if (pendingSell and strategy.position_size >= 0)
        
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"   ✅ Condição BUY atendida: pendingBuy AND position_size <= 0")
            logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior [1])")
            success = self._open_position('buy', self.current_price)
            if success:
                self.pending_buy = False  # Resetar após execução bem-sucedida
        elif self.pending_buy:
            logger.info(f"   ⏭️  Condição BUY não atendida: pendingBuy={self.pending_buy}, position_size={self.position_size}")
        
        if self.pending_sell and self.position_size >= 0:
            logger.info(f"   ✅ Condição SELL atendida: pendingSell AND position_size >= 0")
            logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior [1])")
            success = self._open_position('sell', self.current_price)
            if success:
                self.pending_sell = False  # Resetar após execução bem-sucedida
        elif self.pending_sell:
            logger.info(f"   ⏭️  Condição SELL não atendida: pendingSell={self.pending_sell}, position_size={self.position_size}")

    def _process_completed_bar(self):
        """Processa barra ANTERIOR para gerar NOVOS sinais (como buy_signal[1])"""
        if not self.current_bar_data:
            return
        
        logger.info(f"📈 Processando barra #{self.bar_count} para sinais...")
        logger.info(f"   Dados do candle:")
        logger.info(f"     Open: ${self.current_bar_data['open']:.2f}")
        logger.info(f"     High: ${self.current_bar_data['high']:.2f}")
        logger.info(f"     Low: ${self.current_bar_data['low']:.2f}")
        logger.info(f"     Close: ${self.current_bar_data['close']:.2f}")
        
        try:
            # Processar através do interpretador
            signals = self.engine.process_candle(self.current_bar_data)
            
            # Atualizar sinais da barra atual
            self.buy_signal_current = signals.get('buy_signal', False)
            self.sell_signal_current = signals.get('sell_signal', False)
            
            # PINE SCRIPT EXATO (linhas 89-98):
            # pendingBuy := nz(pendingBuy[1])  (já feito no loop principal)
            # pendingSell := nz(pendingSell[1]) (já feito no loop principal)
            
            # if (buy_signal[1]) pendingBuy := true
            # if (sell_signal[1]) pendingSell := true
            
            # Usar sinais da barra ANTERIOR (buy_signal[1]) para definir pending flags
            # Note: self.buy_signal_prev é o buy_signal da barra anterior à atual
            if self.buy_signal_prev:
                self.pending_buy = True
                logger.info(f"   🟢 pendingBuy = true (porque buy_signal[1] era true)")
            
            if self.sell_signal_prev:
                self.pending_sell = True
                logger.info(f"   🔴 pendingSell = true (porque sell_signal[1] era true)")
            
            # Log dos sinais atuais (que serão [1] na próxima barra)
            logger.info(f"   Sinais desta barra (serão [1] na próxima):")
            logger.info(f"     buy_signal: {self.buy_signal_current}")
            logger.info(f"     sell_signal: {self.sell_signal_current}")
            
            # Guardar sinais atuais para próxima iteração (como [1])
            self.buy_signal_prev = self.buy_signal_current
            self.sell_signal_prev = self.sell_signal_current
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")
            import traceback
            logger.error(traceback.format_exc())

    # Métodos WebSocket
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                # Atualizar preço
                self.current_price = round(new_price, 2)
                
                # Verificar trailing stop a cada tick
                self._check_trailing_stop()
                    
        except Exception as e:
            logger.error(f"Erro ao processar mensagem WS: {e}")

    def _on_ws_error(self, ws, error):
        logger.error(f"💥 Erro no WebSocket: {error}")

    def _on_ws_close(self, ws, close_status_code, close_msg):
        logger.warning(f"🔌 WebSocket fechado")
        self.ws = None

    def _on_ws_open(self, ws):
        logger.info("🌐 Conexão WebSocket estabelecida")
        subscribe_msg = {
            "op": "subscribe",
            "args": [{
                "channel": "tickers",
                "instId": "ETH-USDT-SWAP"
            }]
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info("📊 Inscrito no canal 'tickers'")

    def _start_websocket(self):
        websocket_url = "wss://ws.okx.com:8443/ws/v5/public"
        self.ws = websocket.WebSocketApp(
            websocket_url,
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_w
