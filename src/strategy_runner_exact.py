#!/usr/bin/env python3
"""
STRATEGY RUNNER EXACT - VERSÃO CORRIGIDA COM FLUXO EXATO
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
from .balance_manager import BalanceManager
from .comparison_logger import ComparisonLogger

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
        
        self.trail_offset = 15  # FIXO no Pine
        self.trailing_activated = False
        self.best_price = entry_price
        
        if side == 'long':
            self.current_stop = entry_price - (fixed_sl * mintick)
            self.tp_trigger = entry_price + (fixed_tp * mintick)
        else:  # short
            self.current_stop = entry_price + (fixed_sl * mintick)
            self.tp_trigger = entry_price - (fixed_tp * mintick)
        
        logger.info(f"🎯 TRAILING STOP CONFIGURADO ({side.upper()}):")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.current_stop:.2f}")
        logger.info(f"   TP Trigger: ${self.tp_trigger:.2f}")
    
    def update(self, current_price: float) -> float:
        """Atualiza trailing stop"""
        
        if not self.trailing_activated:
            if self.side == 'long' and current_price >= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                new_stop = current_price - (self.trail_offset * self.mintick)
                initial_stop = self.entry_price - (self.fixed_sl * self.mintick)
                if new_stop < initial_stop:
                    new_stop = initial_stop
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (LONG)")
                
            elif self.side == 'short' and current_price <= self.tp_trigger:
                self.trailing_activated = True
                self.best_price = current_price
                new_stop = current_price + (self.trail_offset * self.mintick)
                initial_stop = self.entry_price + (self.fixed_sl * self.mintick)
                if new_stop > initial_stop:
                    new_stop = initial_stop
                self.current_stop = new_stop
                logger.info(f"   🎯 TRAILING ATIVADO (SHORT)")
        
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
    """Executa estratégia 100% IDÊNTICA ao TradingView - FLUXO CORRETO"""
    
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # NOVO: Sincronização temporal
        self.time_sync = TimeSync(timeframe_minutes=30)
        
        # NOVO: Gerenciador de balance
        self.balance_manager = BalanceManager(initial_capital=1000.0)
        
        # NOVO: Logger de comparação
        self.comparison_logger = ComparisonLogger()
        
        # Carregar código Pine Script
        pine_code = self._load_pine_script()
        if not pine_code:
            raise Exception("Não foi possível carregar Pine Script")
        
        # Inicializar interpretador Pine
        self.engine = AdaptiveZeroLagEMA(pine_code)
        
        # Configurações EXATAS
        self.timeframe_minutes = 30
        self.mintick = 0.01  # ETH/USDT no TradingView
        
        # Estado da execução
        self.is_running = False
        self.current_price = None
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais EXATAMENTE como Pine Script
        self.buy_signal_current = False
        self.sell_signal_current = False
        self.buy_signal_prev = False
        self.sell_signal_prev = False
        
        # FLAGS PERSISTENTES
        self.pending_buy = False
        self.pending_sell = False
        
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
        
        # Controle de fluxo
        self.bar_processing_complete = False
        self.waiting_for_bar_close = True
        
        logger.info("✅ StrategyRunnerExact inicializado (FLUXO CORRETO)")
        logger.info("   Timing: Fechamento → Processamento → Execução na próxima abertura")

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

    def _calculate_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição IDÊNTICO ao Pine Script"""
        try:
            # Obter balance ATUAL (initial_capital + netprofit)
            balance = self.balance_manager.get_balance()
            
            risk_amount = self.engine.risk * balance
            stop_loss_usdt = self.engine.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                logger.error(f"❌ Stop Loss USDT inválido: {stop_loss_usdt}")
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Aplicar limite máximo
            if quantity > self.engine.limit:
                quantity = self.engine.limit
            
            quantity = round(quantity, 4)
            
            if quantity > 0:
                logger.info("   📊 CÁLCULO DE POSIÇÃO (EXATO Pine Script):")
                logger.info(f"     Balance atual: ${balance:.2f}")
                logger.info(f"     Risk: {self.engine.risk*100}% = ${risk_amount:.2f}")
                logger.info(f"     Stop Loss: {self.engine.fixedSL}p = ${stop_loss_usdt:.2f}")
                logger.info(f"     Quantidade: {quantity:.4f} ETH")
                logger.info(f"     Limit máximo: {self.engine.limit} ETH")
            
            return quantity
            
        except Exception as e:
            logger.error(f"❌ Erro cálculo posição: {e}")
            return 0

    def _open_position(self, side: str, entry_price: float) -> bool:
        """Abre posição EXATAMENTE como Pine Script"""
        logger.info("=" * 60)
        logger.info(f"🔍 VERIFICANDO ABERTURA {side.upper()} (Pine Script rules)")
        logger.info(f"   Preço de abertura: ${entry_price:.2f}")
        logger.info(f"   Posição atual: {self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH")
        
        # REGRA EXATA DO PINE SCRIPT
        if side == 'buy':
            if not self.pending_buy:
                logger.info("⏭️  IGNORADO: pendingBuy = false")
                logger.info("=" * 60)
                return False
            
            if self.position_size > 0:
                logger.info("⏭️  IGNORADO: Já está em LONG")
                logger.info("=" * 60)
                return False
                
        else:  # sell
            if not self.pending_sell:
                logger.info("⏭️  IGNORADO: pendingSell = false")
                logger.info("=" * 60)
                return False
            
            if self.position_size < 0:
                logger.info("⏭️  IGNORADO: Já está em SHORT")
                logger.info("=" * 60)
                return False
        
        quantity = self._calculate_position_size(entry_price)
        if quantity <= 0:
            logger.error("❌ Quantidade inválida")
            logger.info("=" * 60)
            return False
        
        trade_id = self.trade_history.add_trade(
            side=side,
            entry_price=entry_price,
            quantity=quantity
        )
        
        if not trade_id:
            logger.error("❌ Falha ao registrar trade")
            logger.info("=" * 60)
            return False
        
        self.trade_id = trade_id
        self.position_side = side
        self.position_size = quantity if side == 'buy' else -quantity
        self.entry_price = entry_price
        
        self.trailing_manager = TrailingStopManager(
            side=side,
            entry_price=entry_price,
            fixed_sl=self.engine.fixedSL,
            fixed_tp=self.engine.fixedTP,
            mintick=self.mintick
        )
        
        # RESETAR FLAGS APÓS EXECUÇÃO
        if side == 'buy':
            self.pending_buy = False
            logger.info(f"   ✅ pendingBuy = false (resetado)")
        else:
            self.pending_sell = False
            logger.info(f"   ✅ pendingSell = false (resetado)")
        
        self.comparison_logger.log_trade_execution({
            'side': side,
            'price': entry_price,
            'quantity': quantity,
            'balance': self.balance_manager.get_balance(),
            'reason': f"pending{side.upper()}"
        })
        
        logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {quantity:.4f} ETH")
        logger.info(f"   Entrada: ${entry_price:.2f}")
        logger.info(f"   Stop inicial: ${self.trailing_manager.current_stop:.2f}")
        logger.info("=" * 60)
        return True

    def _close_position(self, exit_price: float, reason: str = "") -> bool:
        """Fecha posição e atualiza balance"""
        if not self.trade_id or self.position_size == 0:
            logger.warning("⚠️  Nenhuma posição para fechar")
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()}")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Preço entrada: ${self.entry_price:.2f}")
        logger.info(f"   Preço saída: ${exit_price:.2f}")
        
        pnl_pct = 0.0
        pnl_usdt = 0.0
        
        if self.entry_price:
            if self.position_side == 'long':
                pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100
                pnl_usdt = (exit_price - self.entry_price) * abs(self.position_size)
            else:
                pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100
                pnl_usdt = (self.entry_price - exit_price) * abs(self.position_size)
            
            pnl_pct = round(pnl_pct, 4)
            pnl_usdt = round(pnl_usdt, 2)
            
            logger.info(f"   PnL: {pnl_pct:.4f}% (${pnl_usdt:.2f})")
            
            result = self.trade_history.close_trade(self.trade_id, exit_price)
            if result['success']:
                self.balance_manager.update_netprofit(pnl_usdt, self.trade_id)
        
        success = self.trade_history.close_trade(self.trade_id, exit_price)
        
        if success['success']:
            logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${exit_price:.2f}")
            
            self.position_size = 0
            self.position_side = None
            self.entry_price = None
            self.trade_id = None
            self.trailing_manager = None
            
            logger.info(f"💰 Balance atualizado: ${self.balance_manager.get_balance():.2f}")
            logger.info("=" * 60)
            return True
        else:
            logger.error("❌ Falha ao fechar trade")
            logger.info("=" * 60)
            return False

    def _check_trailing_stop(self):
        """Verifica trailing stop"""
        if not self.position_size or not self.current_price or not self.trailing_manager:
            return
        
        current_stop = self.trailing_manager.update(self.current_price)
        
        if self.trailing_manager.should_close(self.current_price):
            logger.info("=" * 60)
            logger.info(f"🎯 TRAILING STOP ATINGIDO!")
            logger.info(f"   Preço atual: ${self.current_price:.2f}")
            logger.info(f"   Stop atual: ${current_stop:.2f}")
            
            self._close_position(self.current_price, "trailing_stop")

    def _check_bar_completion_exact(self):
        """
        Verifica se uma barra foi completada - TIMING EXATO
        """
        if not self.current_price:
            return False
        
        bar_info = self.time_sync.get_current_bar_info()
        current_time = bar_info['current_timestamp']
        
        if bar_info['is_bar_start']:
            logger.info("=" * 60)
            logger.info(f"📊 NOVA BARRA 30m INICIADA: {current_time.strftime('%H:%M:%S')} UTC")
            logger.info(f"   Preço de abertura: ${self.current_price:.2f}")
            
            # 1. EXECUTAR SINAIS PENDENTES DA BARRA ANTERIOR
            self._execute_pending_signals_at_open()
            
            # 2. Processar barra anterior para gerar NOVOS sinais
            if self.current_bar_data and not self.bar_processing_complete:
                self._process_completed_bar_for_signals()
            
            # 3. Iniciar nova barra
            self.last_bar_timestamp = current_time
            self.bar_count += 1
            self.current_bar_data = {
                'timestamp': int(current_time.timestamp() * 1000),
                'open': self.current_price,
                'high': self.current_price,
                'low': self.current_price,
                'close': self.current_price,
                'volume': 0
            }
            
            self.bar_processing_complete = False
            
            logger.info(f"   Barra #{self.bar_count} iniciada")
            logger.info("=" * 60)
            
            return True
        
        else:
            if self.current_bar_data:
                self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
                self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
                self.current_bar_data['close'] = self.current_price
            
            time_to_next_bar = bar_info['seconds_to_next_bar']
            if time_to_next_bar < 5.0 and not self.bar_processing_complete:
                logger.debug(f"   ⏰ Barra fechando em {time_to_next_bar:.1f}s")
                self.waiting_for_bar_close = True
            
            return False

    def _execute_pending_signals_at_open(self):
        """Executa sinais pendentes na ABERTURA da barra"""
        if not self.current_price:
            return
        
        logger.info(f"🔍 EXECUTANDO SINAIS PENDENTES (abertura da barra):")
        logger.info(f"   Preço de abertura: ${self.current_price:.2f}")
        logger.info(f"   pendingBuy: {self.pending_buy}")
        logger.info(f"   pendingSell: {self.pending_sell}")
        
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"   ✅ Condição BUY atendida")
            logger.info(f"🎯 EXECUTANDO BUY")
            success = self._open_position('buy', self.current_price)
            
        if self.pending_sell and self.position_size >= 0:
            logger.info(f"   ✅ Condição SELL atendida")
            logger.info(f"🎯 EXECUTANDO SELL")
            success = self._open_position('sell', self.current_price)

    def _process_completed_bar_for_signals(self):
        """Processa barra ANTERIOR no FECHAMENTO para gerar NOVOS sinais"""
        if not self.current_bar_data:
            return
        
        logger.info(f"📈 PROCESSANDO BARRA ANTERIOR #{self.bar_count} para sinais...")
        
        try:
            signals = self.engine.process_candle(self.current_bar_data)
            
            self.buy_signal_current = signals.get('buy_signal', False)
            self.sell_signal_current = signals.get('sell_signal', False)
            
            if self.buy_signal_prev:
                self.pending_buy = True
                logger.info(f"   🟢 pendingBuy = true (buy_signal[1] era true)")
            
            if self.sell_signal_prev:
                self.pending_sell = True
                logger.info(f"   🔴 pendingSell = true (sell_signal[1] era true)")
            
            logger.info(f"   Sinais desta barra (serão [1] na próxima):")
            logger.info(f"     buy_signal: {self.buy_signal_current}")
            logger.info(f"     sell_signal: {self.sell_signal_current}")
            
            self.comparison_logger.log_bar_data({
                'timestamp': datetime.fromtimestamp(self.current_bar_data['timestamp'] / 1000, pytz.utc),
                'bar_number': self.bar_count,
                'open_price': self.current_bar_data['open'],
                'close_price': self.current_bar_data['close'],
                'ec_value': signals.get('ec', 0),
                'ema_value': signals.get('ema', 0),
                'least_error': signals.get('least_error', 0),
                'error_percent': signals.get('error_pct', 0),
                'buy_signal': self.buy_signal_current,
                'sell_signal': self.sell_signal_current,
                'pending_buy': self.pending_buy,
                'pending_sell': self.pending_sell,
                'position_size': self.position_size,
                'position_side': self.position_side or 'FLAT',
                'balance': self.balance_manager.get_balance(),
                'notes': "Processado no fechamento"
            })
            
            self.buy_signal_prev = self.buy_signal_current
            self.sell_signal_prev = self.sell_signal_current
            
            self.bar_processing_complete = True
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")

    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                self.current_price = round(new_price, 2)
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
        """Inicia a execução"""
        logger.info("🚀 Iniciando execução 100% IDÊNTICA ao Pine Script...")
        
        self._start_websocket()
        
        logger.info("⏳ Aguardando preço atual...")
        for _ in range(30):
            if self.current_price is not None:
                break
            time.sleep(1)
        
        if self.current_price is None:
            logger.error("❌ Não foi possível obter preço atual")
            return False
        
        logger.info(f"✅ Preço atual: ${self.current_price:.2f}")
        
        self._initialize_candle_buffer()
        
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        self.last_bar_timestamp = now_utc
        self.current_bar_data = {
            'timestamp': int(now_utc.timestamp() * 1000),
            'open': self.current_price,
            'high': self.current_price,
            'low': self.current_price,
            'close': self.current_price,
            'volume': 0
        }
        
        self.is_running = True
        logger.info("✅ Execução iniciada")
        return True

    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos"""
        logger.info("📈 Inicializando com candles históricos...")
        
        try:
            historical_candles = self.okx_client.get_candles(limit=100)
            
            if len(historical_candles) >= 30:
                logger.info(f"✅ {len(historical_candles)} candles históricos")
                
                for candle in historical_candles:
                    self.engine.process_candle(candle)
                
                logger.info(f"   🔧 {len(historical_candles)} candles processados")
                
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles)} candles")
                
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar candles: {e}")

    def run_strategy_realtime(self):
        """Executa estratégia em tempo real"""
        if not self.is_running:
            return {"status": "stopped"}
        
        try:
            new_bar = self._check_bar_completion_exact()
            self._check_trailing_stop()
            
            return {
                "status": "running",
                "new_bar": new_bar,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "pending_buy": self.pending_buy,
                "pending_sell": self.pending_sell,
                "position_size": self.position_size,
                "position_side": self.position_side,
                "entry_price": self.entry_price,
                "balance": self.balance_manager.get_balance(),
                "trailing_stop": self.trailing_manager.current_stop if self.trailing_manager else None
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
