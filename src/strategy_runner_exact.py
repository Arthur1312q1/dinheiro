#!/usr/bin/env python3
"""
STRATEGY RUNNER EXACT - VERSÃO FINAL COM TODAS AS CORREÇÕES
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
    """Gerencia trailing stop EXATO como TradingView"""
    
    def __init__(self, side: str, entry_price: float, 
                 fixed_sl: int, fixed_tp: int, 
                 trail_offset: int, mintick: float):
        self.side = side
        self.entry_price = entry_price
        self.fixed_sl = fixed_sl
        self.fixed_tp = fixed_tp
        self.trail_offset = trail_offset
        self.mintick = mintick
        
        # Estado do trailing
        self.trailing_activated = False
        self.best_price = entry_price
        self.current_stop = self._calculate_initial_stop()
        
        # Calcular gatilho do trailing (fixedTP)
        if side == 'long':
            self.tp_trigger = entry_price + (fixed_tp * mintick)
        else:
            self.tp_trigger = entry_price - (fixed_tp * mintick)
        
        logger.info(f"🎯 TRAILING STOP INICIALIZADO ({side.upper()}):")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.current_stop:.2f}")
        logger.info(f"   TP Trigger: ${self.tp_trigger:.2f}")
        logger.info(f"   Trail Offset: {trail_offset}p = ${trail_offset * mintick:.2f}")
    
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
                
                # O stop NUNCA pode ser pior que o inicial
                if new_stop < self._calculate_initial_stop():
                    new_stop = self._calculate_initial_stop()
                
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (LONG): ${current_price:.2f} >= ${self.tp_trigger:.2f}")
                
            elif self.side == 'short' and current_price <= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop baseado no trail_offset
                new_stop = current_price + (self.trail_offset * self.mintick)
                
                # O stop NUNCA pode ser pior que o inicial
                if new_stop > self._calculate_initial_stop():
                    new_stop = self._calculate_initial_stop()
                
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (SHORT): ${current_price:.2f} <= ${self.tp_trigger:.2f}")
        
        # Se trailing ativado, atualizar
        if self.trailing_activated:
            if self.side == 'long':
                # Atualizar best price (preço mais ALTO)
                if current_price > self.best_price:
                    old_best = self.best_price
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price - (self.trail_offset * self.mintick)
                    
                    # Stop só move para CIMA (nunca para baixo)
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
                        logger.debug(f"   📈 Trailing atualizado: ${self.current_stop:.2f}")
                
            else:  # short
                # Atualizar best price (preço mais BAIXO)
                if current_price < self.best_price:
                    old_best = self.best_price
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price + (self.trail_offset * self.mintick)
                    
                    # Stop só move para BAIXO (nunca para cima)
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
                        logger.debug(f"   📉 Trailing atualizado: ${self.current_stop:.2f}")
        
        return self.current_stop
    
    def should_close(self, current_price: float) -> bool:
        """Verifica se deve fechar posição"""
        if self.side == 'long':
            return current_price <= self.current_stop
        else:
            return current_price >= self.current_stop

class StrategyRunnerExact:
    """Executa estratégia EXATAMENTE como TradingView"""
    
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
        self.mintick = 0.01  # ETH/USDT NO TRADINGVIEW
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Estado da execução
        self.is_running = False
        self.current_price = None
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais pendentes (CRÍTICO: TradingView usa delay de 1 barra)
        self.pending_buy = False  # Sinal BUY da barra anterior (para executar agora)
        self.pending_sell = False  # Sinal SELL da barra anterior (para executar agora)
        self.new_buy_signal = False  # Sinal BUY detectado nesta barra (executará na próxima)
        self.new_sell_signal = False  # Sinal SELL detectado nesta barra (executará na próxima)
        
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
        
        logger.info("✅ StrategyRunnerExact inicializado (DELAY 1 BARRA CORRETO)")
    
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
            
            # Arredondar para 4 casas decimais (ETH)
            quantity = round(quantity, 4)
            
            if quantity > 0:
                logger.info(f"   Cálculo de posição:")
                logger.info(f"     Balance: ${balance:.2f}")
                logger.info(f"     Risk: {self.engine.risk*100}% = ${risk_amount:.2f}")
                logger.info(f"     Stop Loss: {self.engine.fixedSL}p = ${stop_loss_usdt:.2f}")
                logger.info(f"     Quantidade: {quantity:.4f} ETH")
            
            return quantity
            
        except Exception as e:
            logger.error(f"❌ Erro cálculo posição: {e}")
            return 0
    
    def _open_position(self, side: str, entry_price: float) -> bool:
        """Abre posição EXATAMENTE como TradingView"""
        logger.info("=" * 60)
        logger.info(f"🔍 VERIFICANDO ABERTURA {side.upper()}")
        logger.info(f"   Preço: ${entry_price:.2f}")
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
        if self.position_size != 0 and self.position_side != side:
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
        
        # Inicializar Trailing Stop Manager
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
        
        # Resetar sinais pendentes APÓS execução (como no Pine)
        if side == 'buy':
            self.pending_buy = False
        else:
            self.pending_sell = False
        
        logger.info("=" * 60)
        return True
    
    def _close_position(self, exit_price: float, reason: str = "") -> bool:
        """Fecha posição EXATAMENTE como TradingView"""
        if not self.trade_id or self.position_size == 0:
            logger.warning("⚠️  Nenhuma posição para fechar")
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()}")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Preço entrada: ${self.entry_price:.2f}")
        logger.info(f"   Preço saída proposto: ${exit_price:.2f}")
        
        # Obter preço PRECISO para fechamento
        precise_exit_price = exit_price
        
        if reason == "trailing_stop" and self.trailing_manager:
            # Usar método especial para preço preciso (considera spread)
            precise_exit_price = self.okx_client.get_precise_price_for_close(
                self.position_side,
                self.trailing_manager.current_stop
            )
            logger.info(f"   Preço preciso calculado: ${precise_exit_price:.2f}")
        
        # Garantir arredondamento para 2 casas decimais
        precise_exit_price = round(precise_exit_price, 2)
        
        # Calcular PnL
        if self.entry_price:
            if self.position_side == 'long':
                pnl_pct = ((precise_exit_price - self.entry_price) / self.entry_price) * 100
                pnl_usdt = (precise_exit_price - self.entry_price) * abs(self.position_size)
            else:
                pnl_pct = ((self.entry_price - precise_exit_price) / self.entry_price) * 100
                pnl_usdt = (self.entry_price - precise_exit_price) * abs(self.position_size)
            
            # Formatar para 2 casas decimais
            pnl_pct = round(pnl_pct, 2)
            pnl_usdt = round(pnl_usdt, 2)
            
            logger.info(f"   PnL: {pnl_pct}% (${pnl_usdt})")
        
        # Fechar no histórico
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
        
        # Atualizar trailing stop com preço atual
        current_stop = self.trailing_manager.update(self.current_price)
        
        # Verificar se deve fechar
        if self.trailing_manager.should_close(self.current_price):
            logger.info("=" * 60)
            logger.info(f"🎯 TRAILING STOP ATINGIDO!")
            logger.info(f"   Preço atual: ${self.current_price:.2f}")
            logger.info(f"   Stop calculado: ${current_stop:.2f}")
            logger.info(f"   Entrada: ${self.entry_price:.2f}")
            
            # Fechar posição
            self._close_position(self.current_price, "trailing_stop")
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30m começou - TIMING CORRETO"""
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
            
            # ⚠️ CRÍTICO: TIMING EXATO DO TRADINGVIEW
            # 1. Primeiro executar sinais pendentes da barra ANTERIOR
            self._execute_pending_signals()
            
            # 2. Depois processar barra ANTERIOR para gerar NOVOS sinais
            if self.current_bar_data:
                self._process_completed_bar()
            
            # 3. Iniciar nova barra
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
    
    def _execute_pending_signals(self):
        """Executa sinais pendentes na ABERTURA da nova barra"""
        if not self.current_price:
            return
        
        logger.info(f"🔍 EXECUTANDO SINAIS PENDENTES (abertura da barra):")
        logger.info(f"   Preço: ${self.current_price:.2f}")
        logger.info(f"   Pending BUY: {self.pending_buy}")
        logger.info(f"   Pending SELL: {self.pending_sell}")
        logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
        
        # REGRA EXATA DO PINE SCRIPT
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior)")
            self._open_position('buy', self.current_price)
        
        elif self.pending_sell and self.position_size >= 0:
            logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior)")
            self._open_position('sell', self.current_price)
        
        # Resetar após tentativa de execução
        self.pending_buy = False
        self.pending_sell = False
    
    def _process_completed_bar(self):
        """Processa barra ANTERIOR para gerar NOVOS sinais"""
        if not self.current_bar_data:
            return
        
        logger.info(f"📈 Processando barra #{self.bar_count} para sinais...")
        logger.info(f"   Preço de fechamento: ${self.current_bar_data['close']:.2f}")
        
        try:
            # Processar através do interpretador
            signals = self.engine.process_candle(self.current_bar_data)
            
            # TradingView: Sinais detectados no FECHAMENTO, executados na PRÓXIMA ABERTURA
            # Salvar sinais detectados AGORA (para próxima barra)
            self.new_buy_signal = signals.get('buy_signal_current', False)
            self.new_sell_signal = signals.get('sell_signal_current', False)
            
            # Marcar como pendentes para a PRÓXIMA barra
            if self.new_buy_signal:
                self.pending_buy = True
                logger.info(f"   🟢 NOVO SINAL BUY DETECTADO (executará na próxima barra)")
            
            if self.new_sell_signal:
                self.pending_sell = True
                logger.info(f"   🔴 NOVO SINAL SELL DETECTADO (executará na próxima barra)")
            
            if not self.new_buy_signal and not self.new_sell_signal:
                logger.info(f"   ⚪ Nenhum sinal detectado")
                
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
                
                # Verificar trailing stop a cada tick de preço
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
                "pending_buy": self.pending_buy,
                "pending_sell": self.pending_sell,
                "new_buy_signal": self.new_buy_signal,
                "new_sell_signal": self.new_sell_signal,
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
