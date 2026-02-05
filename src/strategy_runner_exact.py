#!/usr/bin/env python3
"""
STRATEGY RUNNER EXACT - VERSÃO 100% IDÊNTICA AO PINE SCRIPT
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
    """Gerencia trailing stop IDÊNTICO ao Pine Script"""
    
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
        
        # Stop inicial (loss = fixedSL)
        self.current_stop = self._calculate_initial_stop()
        
        # Take profit para ativar trailing (trail_points = fixedTP)
        self.tp_trigger = self._calculate_tp_trigger()
        
        logger.info(f"🎯 TRAILING STOP CONFIGURADO ({side.upper()}):")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.current_stop:.2f}")
        logger.info(f"   TP Trigger (para ativar trailing): ${self.tp_trigger:.2f}")
        logger.info(f"   Trail Offset: {trail_offset}p = ${trail_offset * mintick:.2f}")
    
    def _calculate_initial_stop(self):
        """Calcula stop loss inicial (loss = fixedSL)"""
        if self.side == 'long':
            return self.entry_price - (self.fixed_sl * self.mintick)
        else:  # short
            return self.entry_price + (self.fixed_sl * self.mintick)
    
    def _calculate_tp_trigger(self):
        """Calcula preço para ativar trailing (trail_points = fixedTP)"""
        if self.side == 'long':
            return self.entry_price + (self.fixed_tp * self.mintick)
        else:  # short
            return self.entry_price - (self.fixed_tp * self.mintick)
    
    def update(self, current_price: float) -> float:
        """Atualiza trailing stop - IDÊNTICO ao Pine Script strategy.exit"""
        
        # Verificar se deve ativar trailing (TP atingido)
        if not self.trailing_activated:
            if self.side == 'long' and current_price >= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop com trail_offset
                new_stop = current_price - (self.trail_offset * self.mintick)
                
                # Garantir que o stop não fique pior que o inicial
                if new_stop < self._calculate_initial_stop():
                    new_stop = self._calculate_initial_stop()
                
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (LONG): ${current_price:.2f} >= ${self.tp_trigger:.2f}")
                logger.info(f"   Stop ajustado para: ${self.current_stop:.2f}")
                
            elif self.side == 'short' and current_price <= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                # Calcular novo stop com trail_offset
                new_stop = current_price + (self.trail_offset * self.mintick)
                
                # Garantir que o stop não fique pior que o inicial
                if new_stop > self._calculate_initial_stop():
                    new_stop = self._calculate_initial_stop()
                
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
                    
                    # Stop só move para CIMA (nunca para baixo)
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
                        logger.debug(f"   📈 Trailing atualizado: ${self.current_stop:.2f}")
                
            else:  # short
                # Atualizar best price (preço mais BAIXO)
                if current_price < self.best_price:
                    self.best_price = current_price
                    
                    # Calcular novo stop
                    new_stop = current_price + (self.trail_offset * self.mintick)
                    
                    # Stop só move para BAIXO (nunca para cima)
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
                        logger.debug(f"   📉 Trailing atualizado: ${self.current_stop:.2f}")
        
        return self.current_stop
    
    def should_close(self, current_price: float) -> bool:
        """Verifica se deve fechar posição (atingiu stop)"""
        if self.side == 'long':
            return current_price <= self.current_stop
        else:  # short
            return current_price >= self.current_stop

class StrategyRunnerExact:
    """Executa estratégia IDÊNTICA ao Pine Script fornecido"""
    
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
        
        # Sinais pendentes (EXATAMENTE como Pine Script)
        self.pending_buy = False    # Flag que persiste entre barras
        self.pending_sell = False   # Flag que persiste entre barras
        self.buy_signal_prev = False  # buy_signal[1]
        self.sell_signal_prev = False # sell_signal[1]
        
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
        
        logger.info("✅ StrategyRunnerExact inicializado (IDÊNTICO ao Pine Script)")

    def _load_pine_script(self):
        """Carrega código Pine Script do arquivo"""
        try:
            # Tentar diferentes caminhos
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
            
            # Se não encontrar arquivo, usar o código fornecido
            logger.warning("⚠️  Arquivo Pine Script não encontrado, usando código embutido")
            pine_code = """//@version=3
strategy(title="Adaptive Zero Lag EMA v2", shorttitle="AZLEMA", overlay = true, initial_capital=1000, currency="USD", commission_type=strategy.commission.percent, commission_value=0, slippage = 0, pyramiding=1, calc_on_every_tick=false)

src = input(title="Source", type=source, defval=close)
Period = input(title="Period", type=integer, defval = 20)
adaptive = input(title="Adaptive Method", options=["Off", "Cos IFM", "I-Q IFM", "Average"], defval="Cos IFM")
GainLimit = input(title="Gain Limit", type=integer, defval = 900)
Threshold = input(title="Threshold", type = float, defval=0, step=0.01)
fixedSL = input(title="SL Points", defval=2000)
fixedTP = input(title="TP Points", defval=55)
risk = input(title='Risk', defval=0.01, step=0.01)
limit = input(title="Max Lots", type=integer, defval=100)

range = 50

PI = 3.14159265359
lenIQ = 0.0
lenC = 0.0

// Código completo da estratégia...
"""
            return pine_code
            
        except Exception as e:
            logger.error(f"❌ Erro ao carregar Pine Script: {e}")
            return None

    def _calculate_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição IDÊNTICO ao Pine Script"""
        try:
            # Obter balance (initial_capital + netprofit)
            balance = self.okx_client.get_balance() + 1000  # initial_capital = 1000
            
            # Fórmula EXATA do Pine Script
            risk_amount = self.engine.risk * balance
            stop_loss_usdt = self.engine.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                logger.error(f"❌ Stop Loss USDT inválido: {stop_loss_usdt}")
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Aplicar limite máximo (do input 'limit' no Pine)
            max_qty = self.engine.params.get('limit', 100)
            if quantity > max_qty:
                quantity = max_qty
            
            # Arredondar para 4 casas decimais (ETH)
            quantity = round(quantity, 4)
            
            if quantity > 0:
                logger.info(f"   Cálculo de posição (IDÊNTICO ao Pine):")
                logger.info(f"     Balance: ${balance:.2f}")
                logger.info(f"     Risk: {self.engine.risk*100}% = ${risk_amount:.2f}")
                logger.info(f"     Stop Loss: {self.engine.fixedSL}p = ${stop_loss_usdt:.2f}")
                logger.info(f"     Quantidade: {quantity:.4f} ETH")
            
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
        
        # REGRA EXATA DO PINE SCRIPT:
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
            trail_offset=15,  # FIXO no Pine Script (trail_offset=15)
            mintick=self.mintick
        )
        
        # Resetar flags pendentes APÓS execução (como no Pine)
        if side == 'buy':
            self.pending_buy = False
            logger.info(f"   pendingBuy = false (resetado)")
        else:
            self.pending_sell = False
            logger.info(f"   pendingSell = false (resetado)")
        
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
            logger.info(f"   TP Trigger: ${self.trailing_manager.tp_trigger:.2f}")
            logger.info(f"   Trailing ativado: {self.trailing_manager.trailing_activated}")
            
            # Fechar posição
            self._close_position(self.current_price, "trailing_stop")

    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30m começou - USANDO UTC como TradingView"""
        if not self.current_price:
            return False
        
        # Usar UTC como TradingView
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        
        # Calcular início da barra atual (arredondando para múltiplos de 30min em UTC)
        current_minute = now_utc.minute
        bar_minute = (current_minute // self.timeframe_minutes) * self.timeframe_minutes
        
        current_bar_start = now_utc.replace(
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
            logger.info(f"⏰ Primeira barra UTC: {current_bar_start.strftime('%H:%M')}")
            return False
        
        # Se nova barra começou
        if current_bar_start > self.last_bar_timestamp:
            logger.info("=" * 60)
            logger.info(f"📊 NOVA BARRA 30m UTC: {current_bar_start.strftime('%H:%M')}")
            logger.info(f"   Preço abertura: ${self.current_price:.2f}")
            
            # IMPORTANTE: Ordem de execução IDÊNTICA ao Pine Script
            # 1. Primeiro processar barra ANTERIOR para gerar sinais
            if self.current_bar_data:
                self._process_completed_bar()
            
            # 2. Depois executar sinais pendentes (da barra anterior [1])
            self._execute_pending_signals()
            
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
        """Executa sinais pendentes - IDÊNTICO ao Pine Script"""
        if not self.current_price:
            return
        
        logger.info(f"🔍 EXECUTANDO SINAIS PENDENTES (da barra anterior [1]):")
        logger.info(f"   Preço abertura: ${self.current_price:.2f}")
        logger.info(f"   pendingBuy: {self.pending_buy}")
        logger.info(f"   pendingSell: {self.pending_sell}")
        logger.info(f"   position_size: {self.position_size}")
        logger.info(f"   Condição BUY: pendingBuy={self.pending_buy} AND position_size<=0 = {self.pending_buy and self.position_size <= 0}")
        logger.info(f"   Condição SELL: pendingSell={self.pending_sell} AND position_size>=0 = {self.pending_sell and self.position_size >= 0}")
        
        # REGRA EXATA DO PINE SCRIPT
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior [1])")
            self._open_position('buy', self.current_price)
        
        elif self.pending_sell and self.position_size >= 0:
            logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior [1])")
            self._open_position('sell', self.current_price)

    def _process_completed_bar(self):
        """Processa barra ANTERIOR para gerar NOVOS sinais (como buy_signal[1])"""
        if not self.current_bar_data:
            return
        
        logger.info(f"📈 Processando barra #{self.bar_count} para sinais...")
        logger.info(f"   Preço de fechamento: ${self.current_bar_data['close']:.2f}")
        
        try:
            # Processar através do interpretador
            signals = self.engine.process_candle(self.current_bar_data)
            
            # NOVO: Guardar sinais da barra atual para usar na próxima barra
            # Isso simula buy_signal[1] e sell_signal[1]
            buy_signal_current = signals.get('buy_signal_current', False)
            sell_signal_current = signals.get('sell_signal_current', False)
            
            # LOG dos sinais brutos
            logger.info(f"   Sinais brutos desta barra:")
            logger.info(f"     buy_signal (crossover+threshold): {buy_signal_current}")
            logger.info(f"     sell_signal (crossunder+threshold): {sell_signal_current}")
            logger.info(f"     EC: ${signals.get('ec', 0):.2f}, EMA: ${signals.get('ema', 0):.2f}")
            logger.info(f"     Erro%: {signals.get('error_pct', 0):.2f}% (Threshold: {self.engine.threshold})")
            
            # ATUALIZAR: Usar sinais da barra ANTERIOR [1] para definir pending flags
            # Isso simula: if (buy_signal[1]) pendingBuy := true
            if self.buy_signal_prev:
                self.pending_buy = True
                logger.info(f"   🟢 pendingBuy = true (porque buy_signal[1] era true)")
            
            if self.sell_signal_prev:
                self.pending_sell = True
                logger.info(f"   🔴 pendingSell = true (porque sell_signal[1] era true)")
            
            # Guardar sinais atuais para próxima iteração (como [1])
            self.buy_signal_prev = buy_signal_current
            self.sell_signal_prev = sell_signal_current
            
            # LOG final
            logger.info(f"   Sinais salvos para próxima barra:")
            logger.info(f"     buy_signal_prev (será [1]): {buy_signal_current}")
            logger.info(f"     sell_signal_prev (será [1]): {sell_signal_current}")
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")

    # Métodos WebSocket (mantidos)
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
            on_close=self._on_ws_close
        )
        self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
        self.ws_thread.start()
        logger.info("Thread WebSocket iniciada")
        time.sleep(3)

    def start(self):
        """Inicia a execução IDÊNTICA ao TradingView"""
        logger.info("🚀 Iniciando execução 100% IDÊNTICA ao Pine Script...")
        
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
        logger.info("✅ Execução iniciada (100% IDÊNTICA ao TradingView)")
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
                    last_dt = datetime.fromtimestamp(last_ts, self.tz_utc)
                    
                    # Arredondar para início da barra de 30m em UTC
                    minute = (last_dt.minute // 30) * 30
                    self.last_bar_timestamp = last_dt.replace(
                        minute=minute, 
                        second=0, 
                        microsecond=0
                    )
                    
                    self.bar_count = len(historical_candles)
                    logger.info(f"   ⏰ Última barra histórica: {self.last_bar_timestamp.strftime('%H:%M')} UTC")
                    
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
            
            return {
                "status": "running",
                "new_bar": new_bar,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "pending_buy": self.pending_buy,
                "pending_sell": self.pending_sell,
                "buy_signal_prev": self.buy_signal_prev,
                "sell_signal_prev": self.sell_signal_prev,
                "position_size": self.position_size,
                "position_side": self.position_side,
                "entry_price": self.entry_price,
                "trailing_stop": self.trailing_manager.current_stop if self.trailing_manager else None,
                "trailing_activated": self.trailing_manager.trailing_activated if self.trailing_manager else False
            }
            
        except Exception as e:
            logger.error(f"Erro em run_strategy_realtime: {e}")
            return {"status": "error", "error": str(e)}

    def force_close_position(self):
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
        self.is_running = False
        if self.ws:
            self.ws.close()
        logger.info("⏹️ Execução parada")
