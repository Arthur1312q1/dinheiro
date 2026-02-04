#!/usr/bin/env python3
"""
STRATEGY RUNNER EXACT - VERSÃO CORRIGIDA COM FECHAMENTO PRECISO
100% IDÊNTICO AO TRADINGVIEW
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
    """Gerencia trailing stop EXATO como TradingView - VERSÃO PRECISA"""
    
    def __init__(self, side: str, entry_price: float, 
                 fixed_sl: int, fixed_tp: int, 
                 trail_offset: int, mintick: float):
        self.side = side
        self.entry_price = entry_price
        self.fixed_sl = fixed_sl
        self.fixed_tp = fixed_tp
        self.trail_offset = trail_offset
        self.mintick = mintick
        
        # IMPORTANTE: TradingView usa syminfo.mintick para ETH/USDT = 0.01
        # 1 ponto = $0.01
        logger.info(f"🔍 Mintick configurado: {mintick} (1 ponto = ${mintick:.4f})")
        
        # Estado do trailing
        self.trailing_activated = False
        self.best_price = entry_price
        self.current_stop = self._calculate_initial_stop()
        
        # Calcular gatilho do trailing (fixedTP)
        if side == 'long':
            self.tp_trigger = entry_price + (fixed_tp * mintick)
        else:
            self.tp_trigger = entry_price - (fixed_tp * mintick)
        
        # Para logging
        self.last_log_time = time.time()
        self.initial_logged = False
        
        if not self.initial_logged:
            logger.info("=" * 60)
            logger.info(f"🎯 TRAILING STOP CONFIGURADO ({side.upper()}):")
            logger.info(f"   Entrada: ${entry_price:.2f}")
            logger.info(f"   Stop inicial: ${self.current_stop:.2f} (SL={fixed_sl}p = ${fixed_sl * mintick:.2f})")
            logger.info(f"   TP Trigger: ${self.tp_trigger:.2f} (TP={fixed_tp}p = ${fixed_tp * mintick:.2f})")
            logger.info(f"   Trail Offset: {trail_offset}p = ${trail_offset * mintick:.2f}")
            logger.info("=" * 60)
            self.initial_logged = True
    
    def _calculate_initial_stop(self):
        """Calcula stop loss inicial EM USD"""
        if self.side == 'long':
            return self.entry_price - (self.fixed_sl * self.mintick)
        else:
            return self.entry_price + (self.fixed_sl * self.mintick)
    
    def update(self, current_price: float) -> float:
        """Atualiza trailing stop - EXATO como TradingView"""
        
        # Verificar se deve ativar trailing (TP atingido)
        if not self.trailing_activated:
            if self.side == 'long' and current_price >= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop baseado no trail_offset
                new_stop = current_price - (self.trail_offset * self.mintick)
                
                # IMPORTANTE: O stop NUNCA pode ser pior que o inicial (só protege lucros)
                if new_stop < self._calculate_initial_stop():
                    new_stop = self._calculate_initial_stop()
                
                self.current_stop = new_stop
                
                current_time = time.time()
                if current_time - self.last_log_time > 1:  # Evitar logs excessivos
                    logger.info("🎯 TRAILING ATIVADO (LONG):")
                    logger.info(f"   Preço atual: ${current_price:.2f}")
                    logger.info(f"   TP Trigger: ${self.tp_trigger:.2f}")
                    logger.info(f"   Novo stop: ${self.current_stop:.2f}")
                    self.last_log_time = current_time
                
            elif self.side == 'short' and current_price <= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop baseado no trail_offset
                new_stop = current_price + (self.trail_offset * self.mintick)
                
                # IMPORTANTE: O stop NUNCA pode ser pior que o inicial
                if new_stop > self._calculate_initial_stop():
                    new_stop = self._calculate_initial_stop()
                
                self.current_stop = new_stop
                
                current_time = time.time()
                if current_time - self.last_log_time > 1:
                    logger.info("🎯 TRAILING ATIVADO (SHORT):")
                    logger.info(f"   Preço atual: ${current_price:.2f}")
                    logger.info(f"   TP Trigger: ${self.tp_trigger:.2f}")
                    logger.info(f"   Novo stop: ${self.current_stop:.2f}")
                    self.last_log_time = current_time
        
        # Se trailing ativado, atualizar
        if self.trailing_activated:
            if self.side == 'long':
                # Atualizar best price (preço mais ALTO)
                if current_price > self.best_price:
                    old_best = self.best_price
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price - (self.trail_offset * self.mintick)
                    
                    # IMPORTANTE: Stop só move para CIMA (nunca para baixo)
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
                        
                        current_time = time.time()
                        if current_time - self.last_log_time > 5:  # Log a cada 5s
                            logger.debug(f"📈 Trailing atualizado (LONG):")
                            logger.debug(f"   Best price: ${old_best:.2f} → ${self.best_price:.2f}")
                            logger.debug(f"   Stop: ${self.current_stop:.2f}")
                            self.last_log_time = current_time
                
            else:  # short
                # Atualizar best price (preço mais BAIXO)
                if current_price < self.best_price:
                    old_best = self.best_price
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price + (self.trail_offset * self.mintick)
                    
                    # IMPORTANTE: Stop só move para BAIXO (nunca para cima)
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
                        
                        current_time = time.time()
                        if current_time - self.last_log_time > 5:
                            logger.debug(f"📉 Trailing atualizado (SHORT):")
                            logger.debug(f"   Best price: ${old_best:.2f} → ${self.best_price:.2f}")
                            logger.debug(f"   Stop: ${self.current_stop:.2f}")
                            self.last_log_time = current_time
        
        return self.current_stop
    
    def should_close(self, current_price: float) -> bool:
        """Verifica se deve fechar posição - EXATO como TradingView"""
        if self.side == 'long':
            # LONG: Fecha quando preço CAI para ou abaixo do stop
            should_close = current_price <= self.current_stop
            if should_close:
                current_time = time.time()
                if current_time - self.last_log_time > 1:
                    logger.info("🔴 CONDIÇÃO DE FECHAMENTO (LONG):")
                    logger.info(f"   Preço atual: ${current_price:.2f}")
                    logger.info(f"   Stop atual: ${self.current_stop:.2f}")
                    logger.info(f"   Diferença: {current_price - self.current_stop:.2f}")
                    self.last_log_time = current_time
            return should_close
        else:
            # SHORT: Fecha quando preço SOBE para ou acima do stop
            should_close = current_price >= self.current_stop
            if should_close:
                current_time = time.time()
                if current_time - self.last_log_time > 1:
                    logger.info("🔴 CONDIÇÃO DE FECHAMENTO (SHORT):")
                    logger.info(f"   Preço atual: ${current_price:.2f}")
                    logger.info(f"   Stop atual: ${self.current_stop:.2f}")
                    logger.info(f"   Diferença: {self.current_stop - current_price:.2f}")
                    self.last_log_time = current_time
            return should_close

class StrategyRunnerExact:
    """Executa estratégia EXATAMENTE como TradingView - TIMING CORRIGIDO"""
    
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # Carregar código Pine Script
        pine_code = self._load_pine_script()
        if not pine_code:
            raise Exception("Não foi possível carregar Pine Script")
        
        # Inicializar interpretador FIEL
        self.engine = AdaptiveZeroLagEMA(pine_code)
        
        # Configurações
        self.timeframe_minutes = 30
        self.mintick = 0.01  # PARA ETH/USDT NO TRADINGVIEW: 1 ponto = $0.01
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Estado da execução
        self.is_running = False
        self.current_price = None
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais pendentes (do TradingView) - CORRIGIDO: Delay de 1 barra
        self.signal_buy_detected_at_bar_close = False
        self.signal_sell_detected_at_bar_close = False
        self.pending_buy_for_next_bar = False
        self.pending_sell_for_next_bar = False
        
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
        
        # Controle de tempo
        self.last_check_time = 0
        self.check_interval = 1
        self.last_log_time = time.time()
        self.last_trailing_check = time.time()
        
        logger.info("✅ StrategyRunnerExact inicializado (TIMING CORRIGIDO)")
    
    def _load_pine_script(self):
        """Carrega código Pine Script do arquivo"""
        try:
            paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "Adaptive_Zero_Lag_EMA_v2.pine",
                "/home/ubuntu/strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.info(f"✅ Pine Script carregado: {len(content)} bytes")
                        return content
            
            logger.error("❌ Arquivo Pine Script não encontrado")
            return None
            
        except Exception as e:
            logger.error(f"❌ Erro ao carregar Pine Script: {e}")
            return None
    
    def _calculate_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição EXATO como TradingView"""
        try:
            # Obter balance
            balance = self.okx_client.get_balance()
            
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
            max_qty = 100
            if quantity > max_qty:
                quantity = max_qty
            
            # Arredondar para 4 casas decimais (padrão ETH)
            quantity = round(quantity, 4)
            
            if quantity > 0:
                logger.info(f"📊 Cálculo de posição:")
                logger.info(f"   Balance: ${balance:.2f}")
                logger.info(f"   Risk: {self.engine.risk*100}% = ${risk_amount:.2f}")
                logger.info(f"   Stop Loss: {self.engine.fixedSL}p = ${stop_loss_usdt:.2f}")
                logger.info(f"   Quantidade: {quantity:.4f} ETH")
            
            return quantity
            
        except Exception as e:
            logger.error(f"❌ Erro cálculo posição: {e}")
            return 0
    
    def _open_position(self, side: str, entry_price: float) -> bool:
        """Abre posição EXATAMENTE como TradingView"""
        logger.info("=" * 60)
        logger.info(f"🔍 VERIFICANDO ABERTURA {side.upper()}")
        logger.info(f"   Preço atual: ${entry_price:.2f}")
        logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
        
        # REGRA EXATA DO PINE SCRIPT:
        # BUY só se strategy.position_size <= 0 (flat ou short)
        # SELL só se strategy.position_size >= 0 (flat ou long)
        
        if side == 'buy' and self.position_size > 0:
            logger.info("⏭️  IGNORADO: Já está em LONG")
            logger.info("=" * 60)
            return False
        
        if side == 'sell' and self.position_size < 0:
            logger.info("⏭️  IGNORADO: Já está em SHORT")
            logger.info("=" * 60)
            return False
        
        # Se tem posição oposta, fecha primeiro
        if self.position_size != 0:
            logger.info(f"🔄 Fechando posição {self.position_side} para inverter")
            self._close_position(entry_price, "inversão")
        
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
        
        # Inicializar Trailing Stop Manager com valores EXATOS
        trailing_info = self.engine.get_trailing_stop_info(side, entry_price)
        
        self.trailing_manager = TrailingStopManager(
            side=side,
            entry_price=entry_price,
            fixed_sl=self.engine.fixedSL,
            fixed_tp=self.engine.fixedTP,
            trail_offset=15,  # FIXO no Pine Script
            mintick=self.mintick
        )
        
        # Log detalhado
        logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {abs(quantity):.4f} ETH")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.trailing_manager.current_stop:.2f}")
        logger.info(f"   TP Trigger: ${self.trailing_manager.tp_trigger:.2f}")
        logger.info(f"   Trailing Offset: 15p = ${15 * self.mintick:.2f}")
        
        # IMPORTANTE: Resetar sinais pendentes APÓS execução
        if side == 'buy':
            self.pending_buy_for_next_bar = False
        else:
            self.pending_sell_for_next_bar = False
        
        logger.info("=" * 60)
        return True
    
    def _close_position(self, exit_price: float, reason: str = "") -> bool:
        """Fecha posição EXATAMENTE como TradingView - COM PREÇO PRECISO"""
        if not self.trade_id or self.position_size == 0:
            logger.warning("⚠️  Nenhuma posição para fechar")
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()}")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Preço entrada: ${self.entry_price:.2f}")
        logger.info(f"   Preço saída proposto: ${exit_price:.2f}")
        
        # CRÍTICO: Obter preço PRECISO para fechamento
        # Para trailing stop, precisamos do preço exato que atingiu o stop
        precise_exit_price = exit_price
        
        if reason == "trailing_stop" and self.trailing_manager:
            # Usar método especial do OKXClient para preço preciso
            # Isso simula o spread do mercado real
            precise_exit_price = self.okx_client.get_precise_price_for_close(
                self.position_side,
                self.trailing_manager.current_stop
            )
            logger.info(f"   Preço preciso calculado: ${precise_exit_price:.2f}")
        
        # Garantir que preço seja arredondado para 2 casas decimais
        precise_exit_price = round(precise_exit_price, 2)
        
        # Calcular PnL com preço preciso
        if self.entry_price:
            if self.position_side == 'long':
                pnl_pct = ((precise_exit_price - self.entry_price) / self.entry_price) * 100
                pnl_usdt = (precise_exit_price - self.entry_price) * abs(self.position_size)
            else:
                pnl_pct = ((self.entry_price - precise_exit_price) / self.entry_price) * 100
                pnl_usdt = (self.entry_price - precise_exit_price) * abs(self.position_size)
            
            # Formatar PnL para 2 casas decimais
            pnl_pct = round(pnl_pct, 2)
            pnl_usdt = round(pnl_usdt, 2)
            
            logger.info(f"   PnL: {pnl_pct}% (${pnl_usdt})")
        
        # Fechar no histórico com preço preciso
        success = self.trade_history.close_trade(self.trade_id, precise_exit_price)
        
        if success:
            logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${precise_exit_price:.2f}")
            
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
        """Verifica trailing stop EXATO como TradingView"""
        if not self.position_size or not self.current_price or not self.trailing_manager:
            return
        
        current_time = time.time()
        
        # Verificar apenas a cada 1 segundo (para performance)
        if current_time - self.last_trailing_check < self.check_interval:
            return
        
        self.last_trailing_check = current_time
        
        # Atualizar trailing stop com preço atual
        current_stop = self.trailing_manager.update(self.current_price)
        
        # Verificar se deve fechar
        if self.trailing_manager.should_close(self.current_price):
            logger.info("=" * 60)
            logger.info(f"🎯 TRAILING STOP ATINGIDO!")
            logger.info(f"   Preço atual: ${self.current_price:.2f}")
            logger.info(f"   Stop calculado: ${current_stop:.2f}")
            logger.info(f"   Entrada: ${self.entry_price:.2f}")
            
            # Fechar com preço atual (será ajustado para preciso no _close_position)
            self._close_position(self.current_price, "trailing_stop")
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30m começou - TIMING 100% CORRETO"""
        if not self.current_price:
            return False
        
        now_brazil = datetime.now(self.tz_brazil)
        
        # Calcular início da barra atual (arredondando para múltiplos de 30min)
        current_minute = now_brazil.minute
        bar_minute = (current_minute // self.timeframe_minutes) * self.timeframe_minutes
        
        current_bar_start = now_brazil.replace(
            minute=bar_minute,
            second=0,
            microsecond=0
        )
        
        # Primeira barra
        if self.last_bar_timestamp is None:
            self.last_bar_timestamp = current_bar_start
            self.current_bar_data = {
                'timestamp': int(current_bar_start.timestamp() * 1000),
                'open': self.current_price,
                'high': self.current_price,
                'low': self.current_price,
                'close': self.current_price,
                'volume': 0
            }
            logger.info(f"⏰ Primeira barra: {current_bar_start.strftime('%H:%M')}")
            return False
        
        # Se nova barra começou
        if current_bar_start > self.last_bar_timestamp:
            logger.info("=" * 60)
            logger.info(f"📊 NOVA BARRA 30m: {current_bar_start.strftime('%H:%M')}")
            logger.info(f"   Preço abertura: ${self.current_price:.2f}")
            
            # ⚠️ TIMING CRÍTICO: TradingView executa na ABERTURA da nova barra
            # 1. Primeiro: Executar sinais pendentes da barra ANTERIOR
            self._execute_pending_signals_at_bar_open()
            
            # 2. Segundo: Processar barra ANTERIOR para gerar NOVOS sinais
            if self.current_bar_data:
                self._process_completed_bar_for_signals()
            
            # 3. Terceiro: Iniciar nova barra
            self.last_bar_timestamp = current_bar_start
            self.current_bar_data = {
                'timestamp': int(current_bar_start.timestamp() * 1000),
                'open': self.current_price,
                'high': self.current_price,
                'low': self.current_price,
                'close': self.current_price,
                'volume': 0
            }
            self.bar_count += 1
            
            logger.info(f"   Barra #{self.bar_count} iniciada")
            logger.info("=" * 60)
            return True
        
        # Atualizar dados da barra atual
        if self.current_bar_data:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False
    
    def _execute_pending_signals_at_bar_open(self):
        """Executa sinais pendentes na ABERTURA da barra"""
        if not self.current_price:
            return
        
        logger.info(f"🔍 EXECUTANDO SINAIS PENDENTES (abertura da barra):")
        logger.info(f"   Preço atual (abertura): ${self.current_price:.2f}")
        logger.info(f"   Pending BUY: {self.pending_buy_for_next_bar}")
        logger.info(f"   Pending SELL: {self.pending_sell_for_next_bar}")
        logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
        
        # REGRA EXATA DO PINE SCRIPT
        if self.pending_buy_for_next_bar and self.position_size <= 0:
            logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior)")
            self._open_position('buy', self.current_price)
        
        elif self.pending_sell_for_next_bar and self.position_size >= 0:
            logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior)")
            self._open_position('sell', self.current_price)
        
        # Resetar flags após tentativa de execução
        self.pending_buy_for_next_bar = False
        self.pending_sell_for_next_bar = False
    
    def _process_completed_bar_for_signals(self):
        """Processa barra completa para gerar NOVOS sinais"""
        if not self.current_bar_data:
            return
        
        logger.info(f"📈 Processando barra #{self.bar_count} para sinais...")
        logger.info(f"   Preço de fechamento: ${self.current_bar_data['close']:.2f}")
        
        try:
            # Processar através do interpretador
            signals = self.engine.process_candle(self.current_bar_data)
            
            # TradingView detecta sinais no FECHAMENTO, executa na PRÓXIMA ABERTURA
            self.signal_buy_detected_at_bar_close = signals['buy_signal_current']
            self.signal_sell_detected_at_bar_close = signals['sell_signal_current']
            
            # Marcar para execução na próxima barra
            if self.signal_buy_detected_at_bar_close:
                self.pending_buy_for_next_bar = True
                logger.info(f"   🟢 SINAL BUY DETECTADO (executará na próxima abertura)")
            
            if self.signal_sell_detected_at_bar_close:
                self.pending_sell_for_next_bar = True
                logger.info(f"   🔴 SINAL SELL DETECTADO (executará na próxima abertura)")
            
            if not self.signal_buy_detected_at_bar_close and not self.signal_sell_detected_at_bar_close:
                logger.info(f"   ⚪ Nenhum sinal detectado nesta barra")
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")
    
    # Métodos WebSocket
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                # Atualizar preço (arredondar para 2 casas decimais)
                self.current_price = round(new_price, 2)
                
                # Verificar trailing stop
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
            on_close=self._on_ws_close
        )
        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()
        logger.info("Thread WebSocket iniciada")
        time.sleep(3)
    
    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def start(self):
        """Inicia a execução FIEL ao TradingView"""
        logger.info("🚀 Iniciando execução 100% TradingView...")
        
        # Iniciar WebSocket
        self._start_websocket()
        
        # Aguardar preço atual
        logger.info("⏳ Aguardando preço atual...")
        for _ in range(30):
            if self.current_price is not None:
                break
            time.sleep(1)
        
        if self.current_price is None:
            logger.error("❌ Não foi possível obter preço atual")
            return False
        
        logger.info(f"✅ Preço atual: ${self.current_price:.2f}")
        
        # Inicializar com candles históricos
        self._initialize_candle_buffer()
        
        self.is_running = True
        logger.info("✅ Execução iniciada (100% TradingView)")
        return True
    
    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos"""
        logger.info("📈 Inicializando com candles históricos...")
        
        try:
            historical_candles = self.okx_client.get_candles(limit=100)
            
            if len(historical_candles) >= 30:
                logger.info(f"✅ {len(historical_candles)} candles históricos")
                
                # Processar candles para "aquecer" o algoritmo
                for candle in historical_candles:
                    self.engine.process_candle(candle)
                
                logger.info(f"   🔧 {len(historical_candles)} candles processados")
                
                # Definir último timestamp
                if historical_candles:
                    last_ts = historical_candles[-1]['timestamp'] / 1000
                    last_dt = datetime.fromtimestamp(last_ts, self.tz_brazil)
                    
                    # Arredondar para início da barra de 30m
                    minute = (last_dt.minute // 30) * 30
                    self.last_bar_timestamp = last_dt.replace(
                        minute=minute, 
                        second=0, 
                        microsecond=0
                    )
                    
                    self.bar_count = len(historical_candles)
                    logger.info(f"   ⏰ Última barra histórica: {self.last_bar_timestamp.strftime('%H:%M')}")
                    
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles)} candles")
                
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar candles: {e}")
    
    def run_strategy_realtime(self):
        """Executa estratégia em tempo real - IDÊNTICO ao TradingView"""
        if not self.is_running:
            return {"status": "stopped"}
        
        try:
            # Verificar e atualizar barra
            new_bar = self._check_and_update_bar()
            
            # Verificar trailing stop
            self._check_trailing_stop()
            
            # Log periódico
            current_time = time.time()
            if current_time - self.last_log_time > 30:
                if self.current_price:
                    position_str = f"{self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH"
                    logger.info(f"📈 Status: ${self.current_price:.2f} | Posição: {position_str}")
                    
                    if self.position_size != 0 and self.trailing_manager:
                        logger.info(f"   Entrada: ${self.entry_price:.2f}")
                        logger.info(f"   Stop atual: ${self.trailing_manager.current_stop:.2f}")
                        logger.info(f"   Trailing ativado: {self.trailing_manager.trailing_activated}")
                
                self.last_log_time = current_time
            
            return {
                "status": "running",
                "new_bar": new_bar,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "signal_buy_detected": self.signal_buy_detected_at_bar_close,
                "signal_sell_detected": self.signal_sell_detected_at_bar_close,
                "pending_buy": self.pending_buy_for_next_bar,
                "pending_sell": self.pending_sell_for_next_bar,
                "position_size": self.position_size,
                "position_side": self.position_side,
                "entry_price": self.entry_price,
                "trailing_stop": self.trailing_manager.current_stop if self.trailing_manager else None,
                "trailing_activated": self.trailing_manager.trailing_activated if self.trailing_manager else False
            }
            
        except Exception as e:
            logger.error(f"Erro em run_strategy_realtime: {e}")
            return {"status": "error", "error": str(e)}
    
    def force_close_current_position(self):
        """Força fechamento da posição atual"""
        if not self.position_size or not self.current_price:
            return {"success": False, "message": "Sem posição aberta"}
        
        try:
            success = self._close_position(self.current_price, "force_close")
            if success:
                return {"success": True, "message": f"Posição fechada @ ${self.current_price:.2f}"}
            else:
                return {"success": False, "message": "Falha ao fechar posição"}
        except Exception as e:
            return {"success": False, "message": f"Erro: {str(e)}"}
    
    def stop(self):
        """Para a execução"""
        # Fechar posição aberta se existir
        if self.position_size != 0 and self.current_price:
            self._close_position(self.current_price, "stop_bot")
        
        self.is_running = False
        self._stop_websocket()
        logger.info("⏹️ Execução parada")
