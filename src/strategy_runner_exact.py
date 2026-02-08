#!/usr/bin/env python3
"""
STRATEGY_RUNNER_EXACT.py - VERSÃO REEESCRITA COM FLUXO TEMPORAL EXATO
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
from .time_sync import TimeSync
from .balance_manager import ExactBalanceManager
from .flag_system import FlagSystem
from .exact_execution_logger import ExactExecutionLogger
from .comparison_logger import ComparisonLogger

logger = logging.getLogger(__name__)

class TrailingStopManager:
    """Gerencia trailing stop EXATO como Pine Script"""
    
    def __init__(self, side: str, entry_price: float, 
                 fixed_sl: int, fixed_tp: int, mintick: float):
        self.side = side
        self.entry_price = entry_price
        self.fixed_sl = fixed_sl
        self.fixed_tp = fixed_tp
        self.mintick = mintick
        
        # FIXO: trail_offset = 15 (linha do Pine)
        self.trail_offset = 15
        
        self.trailing_activated = False
        self.best_price = entry_price
        
        # Calcular stop inicial (loss = fixedSL)
        if side == 'long':
            self.current_stop = entry_price - (fixed_sl * mintick)
            self.tp_trigger = entry_price + (fixed_tp * mintick)
        else:  # short
            self.current_stop = entry_price + (fixed_sl * mintick)
            self.tp_trigger = entry_price - (fixed_tp * mintick)
        
        logger.info(f"🎯 TRAILING STOP EXATO ({side.upper()}):")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.current_stop:.2f}")
        logger.info(f"   TP Trigger: ${self.tp_trigger:.2f} (trail_points={fixed_tp})")
        logger.info(f"   Trail Offset: {self.trail_offset}p = ${self.trail_offset * mintick:.2f} (FIXO)")
    
    def update(self, current_price: float) -> float:
        """Atualiza trailing stop EXATAMENTE como strategy.exit"""
        
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
                
            elif self.side == 'short' and current_price <= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                
                new_stop = current_price + (self.trail_offset * self.mintick)
                initial_stop = self.entry_price + (self.fixed_sl * self.mintick)
                if new_stop > initial_stop:
                    new_stop = initial_stop
                
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (SHORT): ${current_price:.2f} <= ${self.tp_trigger:.2f}")
        
        # Se trailing ativado, atualizar
        if self.trailing_activated:
            if self.side == 'long':
                if current_price > self.best_price:
                    self.best_price = current_price
                    new_stop = current_price - (self.trail_offset * self.mintick)
                    if new_stop > self.current_stop:
                        self.current_stop = new_stop
                
            else:  # short
                if current_price < self.best_price:
                    self.best_price = current_price
                    new_stop = current_price + (self.trail_offset * self.mintick)
                    if new_stop < self.current_stop:
                        self.current_stop = new_stop
        
        return self.current_stop
    
    def should_close(self, current_price: float) -> bool:
        """Verifica se deve fechar posição"""
        if self.side == 'long':
            return current_price <= self.current_stop
        else:  # short
            return current_price >= self.current_stop

class StrategyRunnerExact:
    """Executa estratégia com FLUXO TEMPORAL EXATO do Pine Script"""
    
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # Sistema de sincronização temporal PRECISO (50ms)
        self.time_sync = TimeSync(timeframe_minutes=30)
        
        # Sistema de balance EXATO (initial_capital + netprofit)
        self.balance_manager = ExactBalanceManager(initial_capital=1000.0)
        
        # Sistema de flags EXATO (pendingBuy/pendingSell)
        self.flag_system = FlagSystem()
        
        # Logger de execução EXATA
        self.exact_logger = ExactExecutionLogger()
        
        # Logger de comparação (mantido)
        self.comparison_logger = ComparisonLogger()
        
        # Carregar código Pine Script
        pine_code = self._load_pine_script()
        if not pine_code:
            raise Exception("Não foi possível carregar Pine Script")
        
        # Inicializar interpretador Pine
        self.engine = AdaptiveZeroLagEMA(pine_code)
        
        # Configurações EXATAS
        self.timeframe_minutes = 30
        self.mintick = 0.01  # syminfo.mintick para ETH/USDT
        
        # Estado da execução
        self.is_running = False
        self.current_price = None
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais da barra atual (gerados no FECHAMENTO)
        self.buy_signal_current = False
        self.sell_signal_current = False
        
        # Sinais da barra anterior [1] (usados para setar flags)
        self.buy_signal_prev = False
        self.sell_signal_prev = False
        
        # Controle de fluxo temporal
        self.bar_close_processed = False  # Indica se fechamento já foi processado
        self.signals_for_next_bar = None  # Sinais a serem usados na próxima abertura
        
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
        
        logger.info("=" * 60)
        logger.info("✅ StrategyRunnerExact inicializado (FLUXO TEMPORAL EXATO)")
        logger.info("   FECHAMENTO → Processamento → Execução na ABERTURA seguinte")
        logger.info("   Precisão temporal: 50ms")
        logger.info("   Sistema de flags EXATO como Pine Script")
        logger.info("   Balance dinâmico: initial_capital + netprofit")
        logger.info("=" * 60)

    def _load_pine_script(self):
        """Carrega código Pine Script"""
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

    def _calculate_exact_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição EXATAMENTE como Pine Script"""
        try:
            # Usar método EXATO do balance_manager
            quantity = self.balance_manager.calculate_exact_position_size(
                risk_percent=self.engine.risk,
                fixed_sl_points=self.engine.fixedSL,
                mintick=self.mintick,
                limit=self.engine.limit
            )
            
            return quantity
            
        except Exception as e:
            logger.error(f"❌ Erro cálculo posição exata: {e}")
            return 0

    def _open_position_exact(self, side: str, entry_price: float) -> bool:
        """
        Abre posição EXATAMENTE como Pine Script linhas 113-134
        
        Pine Script:
        if (pendingBuy and strategy.position_size <= 0)
            strategy.entry(...)
            pendingBuy := false
        
        if (pendingSell and strategy.position_size >= 0)
            strategy.entry(...)
            pendingSell := false
        """
        logger.info("=" * 60)
        logger.info(f"🔍 VERIFICANDO ABERTURA {side.upper()} (Pine Script EXATO)")
        logger.info(f"   Preço de abertura: ${entry_price:.2f}")
        logger.info(f"   Posição atual: {self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH")
        
        # Verificar flags (já verificadas no nível superior, mas double-check)
        if side == 'buy' and not self.flag_system.pending_buy:
            logger.info("⏭️  IGNORADO: pendingBuy = false")
            logger.info("=" * 60)
            return False
            
        if side == 'sell' and not self.flag_system.pending_sell:
            logger.info("⏭️  IGNORADO: pendingSell = false")
            logger.info("=" * 60)
            return False
        
        # REGRA EXATA DO PINE SCRIPT para verificação de position_size
        if side == 'buy' and self.position_size > 0:
            logger.info("⏭️  IGNORADO: Já está em LONG (position_size > 0)")
            logger.info("=" * 60)
            return False
                
        if side == 'sell' and self.position_size < 0:
            logger.info("⏭️  IGNORADO: Já está em SHORT (position_size < 0)")
            logger.info("=" * 60)
            return False
        
        # Calcular quantidade EXATA
        quantity = self._calculate_exact_position_size(entry_price)
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
        
        # Inicializar Trailing Stop Manager EXATO
        self.trailing_manager = TrailingStopManager(
            side=side,
            entry_price=entry_price,
            fixed_sl=self.engine.fixedSL,
            fixed_tp=self.engine.fixedTP,
            mintick=self.mintick
        )
        
        # RESETAR FLAGS APÓS EXECUÇÃO BEM-SUCEDIDA (EXATO como Pine)
        if side == 'buy':
            self.flag_system.reset_buy_flag()
        else:
            self.flag_system.reset_sell_flag()
        
        # Log de execução EXATA
        self.exact_logger.log_condition_check(
            timestamp=datetime.now(pytz.utc),
            condition_type=side,
            pending_flag=True,
            position_size=self.position_size,
            result=True
        )
        
        logger.info(f"🚀 POSIÇÃO ABERTA (EXATA): {side.upper()} {quantity:.4f} ETH")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Balance utilizado: ${self.balance_manager.get_balance():.2f}")
        logger.info(f"   Stop inicial: ${self.trailing_manager.current_stop:.2f}")
        logger.info("=" * 60)
        return True

    def _close_position_exact(self, exit_price: float, reason: str = "") -> bool:
        """Fecha posição e atualiza balance EXATAMENTE"""
        if not self.trade_id or self.position_size == 0:
            logger.warning("⚠️  Nenhuma posição para fechar")
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()} (EXATO)")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Preço entrada: ${self.entry_price:.2f}")
        logger.info(f"   Preço saída: ${exit_price:.2f}")
        
        # Fechar no histórico (retorna PnL)
        result = self.trade_history.close_trade(self.trade_id, exit_price)
        
        if result['success']:
            pnl_usdt = result['pnl_usdt']
            pnl_percent = result['pnl_percent']
            
            logger.info(f"   PnL: {pnl_percent:.4f}% (${pnl_usdt:.2f})")
            
            # ATUALIZAR BALANCE EXATAMENTE (initial_capital + netprofit)
            new_balance = self.balance_manager.update_from_closed_trade({
                'pnl_usdt': pnl_usdt,
                'trade_id': self.trade_id
            })
            
            logger.info(f"✅ POSIÇÃO FECHADA (EXATA): {self.position_side.upper()} @ ${exit_price:.2f}")
            
            # Resetar estado
            self.position_size = 0
            self.position_side = None
            self.entry_price = None
            self.trade_id = None
            self.trailing_manager = None
            
            logger.info(f"💰 Balance atualizado: ${new_balance:.2f}")
            logger.info("=" * 60)
            return True
        else:
            logger.error("❌ Falha ao fechar trade no histórico")
            logger.info("=" * 60)
            return False

    def _check_trailing_stop_exact(self):
        """Verifica trailing stop EXATAMENTE"""
        if not self.position_size or not self.current_price or not self.trailing_manager:
            return
        
        current_stop = self.trailing_manager.update(self.current_price)
        
        if self.trailing_manager.should_close(self.current_price):
            logger.info("=" * 60)
            logger.info(f"🎯 TRAILING STOP ATINGIDO (EXATO)!")
            logger.info(f"   Preço atual: ${self.current_price:.2f}")
            logger.info(f"   Stop atual: ${current_stop:.2f}")
            
            self._close_position_exact(self.current_price, "trailing_stop_exact")

    def _process_bar_close_exact(self):
        """
        Processa o FECHAMENTO da barra atual (últimos 100ms)
        EXATO: Processa candle completo e gera sinais para próxima barra
        """
        if not self.current_price or not self.current_bar_data:
            return False
        
        # Obter informações temporais precisas
        bar_info = self.time_sync.get_precise_bar_info()
        
        # Verificar se estamos nos últimos 100ms antes do fechamento
        if not bar_info['is_exact_close']:
            return False
        
        # Se já processamos o fechamento desta barra, não processar novamente
        if self.bar_close_processed:
            return False
        
        logger.info("=" * 60)
        logger.info("📊 PROCESSANDO FECHAMENTO DA BARRA (EXATO)")
        logger.info(f"   Tempo até próxima barra: {bar_info['milliseconds_to_next']:.1f}ms")
        
        # 1. COMPLETAR CANDLE com preço de fechamento
        self.current_bar_data['close'] = self.current_price
        
        try:
            # 2. PROCESSAR CANDLE COMPLETO para gerar sinais
            signals = self.engine.process_candle(self.current_bar_data)
            
            # Sinais gerados no FECHAMENTO da barra N
            self.buy_signal_current = signals.get('buy_signal', False)
            self.sell_signal_current = signals.get('sell_signal', False)
            
            logger.info(f"   Sinais calculados: buy_signal={self.buy_signal_current}, sell_signal={self.sell_signal_current}")
            
            # 3. ATUALIZAR FLAGS com sinais da barra ANTERIOR [1]
            # Nota: Neste momento, buy_signal_prev/sell_signal_prev são os sinais da barra N-1
            self.flag_system.update_flags(
                buy_signal_current=self.buy_signal_current,
                sell_signal_current=self.sell_signal_current
            )
            
            # Log de atualização de flags
            self.exact_logger.log_flag_update(
                timestamp=datetime.now(pytz.utc),
                buy_signal_prev=self.flag_system.buy_signal_prev,
                sell_signal_prev=self.flag_system.sell_signal_prev,
                new_flags=self.flag_system.get_state()
            )
            
            # 4. Preparar informações para abertura da próxima barra
            self.signals_for_next_bar = {
                'buy_signal': self.buy_signal_current,  # Será buy_signal[1] na próxima
                'sell_signal': self.sell_signal_current,  # Será sell_signal[1] na próxima
                'flags': self.flag_system.get_state(),
                'timestamp': datetime.now(pytz.utc)
            }
            
            # 5. Log do fechamento
            self.exact_logger.log_bar_close(
                timestamp=datetime.now(pytz.utc),
                bar_number=self.bar_count,
                close_price=self.current_price,
                signals={
                    'buy_signal': self.buy_signal_current,
                    'sell_signal': self.sell_signal_current
                },
                next_flags=self.flag_system.get_state()
            )
            
            # 6. Atualizar sinais anteriores para próxima iteração
            self.buy_signal_prev = self.buy_signal_current
            self.sell_signal_prev = self.sell_signal_current
            
            logger.info(f"✅ Fechamento processado EXATO para barra #{self.bar_count}")
            logger.info("=" * 60)
            
            # 7. Marcar barra como processada
            self.bar_close_processed = True
            
            return True
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar fechamento EXATO: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _process_bar_open_exact(self):
        """
        Processa a ABERTURA da próxima barra (primeiros 50ms)
        EXATO: Executa ordens com base nas flags setadas no fechamento
        """
        if not self.current_price:
            return False
        
        # Obter informações temporais precisas
        bar_info = self.time_sync.get_precise_bar_info()
        
        # Verificar se estamos nos primeiros 50ms após a abertura
        if not bar_info['is_exact_open']:
            return False
        
        logger.info("=" * 60)
        logger.info("📊 PROCESSANDO ABERTURA DA NOVA BARRA (EXATO)")
        logger.info(f"   Tempo desde abertura: {bar_info['milliseconds_since_open']:.1f}ms")
        
        executed_trades = []
        
        # 1. VERIFICAR CONDIÇÃO BUY (Pine linha 113)
        if self.flag_system.should_execute_buy(self.position_size):
            logger.info(f"   ✅ Condição BUY atendida EXATA")
            
            # Log da condição
            self.exact_logger.log_condition_check(
                timestamp=datetime.now(pytz.utc),
                condition_type='buy',
                pending_flag=self.flag_system.pending_buy,
                position_size=self.position_size,
                result=True
            )
            
            # Executar BUY
            success = self._open_position_exact('buy', self.current_price)
            if success:
                executed_trades.append({
                    'side': 'buy',
                    'price': self.current_price,
                    'quantity': abs(self.position_size),
                    'condition': 'pendingBuy and position_size <= 0'
                })
        
        # 2. VERIFICAR CONDIÇÃO SELL (Pine linha 126)
        if self.flag_system.should_execute_sell(self.position_size):
            logger.info(f"   ✅ Condição SELL atendida EXATA")
            
            self.exact_logger.log_condition_check(
                timestamp=datetime.now(pytz.utc),
                condition_type='sell',
                pending_flag=self.flag_system.pending_sell,
                position_size=self.position_size,
                result=True
            )
            
            success = self._open_position_exact('sell', self.current_price)
            if success:
                executed_trades.append({
                    'side': 'sell',
                    'price': self.current_price,
                    'quantity': abs(self.position_size),
                    'condition': 'pendingSell and position_size >= 0'
                })
        
        # 3. Log da abertura
        self.exact_logger.log_bar_open(
            timestamp=datetime.now(pytz.utc),
            bar_number=self.bar_count + 1,
            open_price=self.current_price,
            received_flags=self.flag_system.get_state(),
            position_size=self.position_size,
            executed=executed_trades
        )
        
        # 4. INICIAR NOVA BARRA
        self.last_bar_timestamp = bar_info['current_timestamp']
        self.bar_count += 1
        self.current_bar_data = {
            'timestamp': int(bar_info['current_timestamp'].timestamp() * 1000),
            'open': self.current_price,
            'high': self.current_price,
            'low': self.current_price,
            'close': self.current_price,
            'volume': 0
        }
        
        # 5. Resetar controle de fluxo
        self.bar_close_processed = False
        self.signals_for_next_bar = None
        
        logger.info(f"✅ Barra #{self.bar_count} iniciada EXATA")
        logger.info("=" * 60)
        
        return True

    def run_strategy_exact(self):
        """
        Executa estratégia com fluxo temporal EXATO
        Deve ser chamado em loop rápido (ex: a cada 10ms)
        """
        if not self.is_running:
            return {"status": "stopped"}
        
        try:
            # Atualizar dados da barra atual (em andamento)
            if self.current_bar_data and self.current_price:
                self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
                self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            
            # Verificar trailing stop EXATO a cada tick
            self._check_trailing_stop_exact()
            
            # Processar fechamento da barra (últimos 100ms)
            close_processed = self._process_bar_close_exact()
            
            # Processar abertura da próxima barra (primeiros 50ms)
            open_processed = self._process_bar_open_exact()
            
            return {
                "status": "running",
                "bar_close_processed": close_processed,
                "bar_open_processed": open_processed,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "pending_buy": self.flag_system.pending_buy,
                "pending_sell": self.flag_system.pending_sell,
                "position_size": self.position_size,
                "position_side": self.position_side,
                "balance": self.balance_manager.get_balance(),
                "flags_state": self.flag_system.get_state()
            }
            
        except Exception as e:
            logger.error(f"Erro em run_strategy_exact: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "error", "error": str(e)}

    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                # Atualizar preço com precisão de 2 casas (como TradingView)
                self.current_price = round(new_price, 2)
                    
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
        """Inicia a execução EXATA"""
        logger.info("🚀 Iniciando execução EXATA (Fluxo Temporal Correto)...")
        
        # Validar precisão temporal
        self.time_sync.validate_precision(duration_seconds=10)
        
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
        
        # Inicializar com candles históricos (apenas warm-up, não afeta timing)
        self._initialize_candle_buffer()
        
        # Iniciar primeira barra com preço atual
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        self.last_bar_timestamp = now_utc
        self.bar_count = 1
        self.current_bar_data = {
            'timestamp': int(now_utc.timestamp() * 1000),
            'open': self.current_price,
            'high': self.current_price,
            'low': self.current_price,
            'close': self.current_price,
            'volume': 0
        }
        
        self.is_running = True
        logger.info("✅ Execução EXATA iniciada")
        logger.info("   Fluxo: FECHAMENTO → Processamento → Execução na ABERTURA")
        return True

    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos (apenas warm-up)"""
        logger.info("📈 Inicializando com candles históricos (WARM-UP)...")
        
        try:
            historical_candles = self.okx_client.get_candles(limit=50)
            
            if len(historical_candles) >= 20:
                logger.info(f"✅ {len(historical_candles)} candles históricos para warm-up")
                
                for candle in historical_candles:
                    self.engine.process_candle(candle)
                
                logger.info(f"   🔧 {len(historical_candles)} candles processados (warm-up)")
                
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles)} candles")
                
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar candles: {e}")

    def force_close_position(self):
        """Força fechamento da posição atual (emergência)"""
        if not self.position_size or not self.current_price:
            return {"success": False, "message": "Sem posição aberta"}
        
        try:
            success = self._close_position_exact(self.current_price, "force_close")
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
        logger.info("⏹️ Execução EXATA parada")
