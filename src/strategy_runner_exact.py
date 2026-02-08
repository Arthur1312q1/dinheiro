#!/usr/bin/env python3
"""
STRATEGY_RUNNER_EXACT.py - VERSÃO FINAL COM FLUXO IDÊNTICO AO TRADINGVIEW
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
from .trade_history import TradeHistory

logger = logging.getLogger(__name__)

class TrailingStopManagerExact:
    """Gerencia trailing stop EXATO como Pine Script"""
    
    def __init__(self, side: str, entry_price: float, 
                 fixed_sl: int, fixed_tp: int, mintick: float):
        self.side = side
        self.entry_price = entry_price
        self.fixed_sl = fixed_sl
        self.fixed_tp = fixed_tp
        self.mintick = mintick
        
        # FIXO: trail_offset = 15 (do Pine Script)
        self.trail_offset = 15
        
        self.trailing_activated = False
        self.best_price = entry_price
        
        # Calcular stop inicial (loss = fixedSL)
        if side == 'buy':
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
            if self.side == 'buy' and current_price >= self.tp_trigger:
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
            if self.side == 'buy':
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
        if self.side == 'buy':
            return current_price <= self.current_stop
        else:  # short
            return current_price >= self.current_stop


class StrategyRunnerExact:
    """Executa estratégia com FLUXO IDÊNTICO ao TradingView"""
    
    def __init__(self, okx_client: OKXClient, trade_history: TradeHistory, config: Dict = None):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # Configurações EXATAS do Pine Script
        self.config = config or {
            'timeframe': '30m',
            'period': 20,
            'adaptive': 'Cos IFM',
            'gain_limit': 900,
            'threshold': 0.0,
            'fixed_sl': 2000,
            'fixed_tp': 55,
            'risk': 0.01,
            'limit': 100,
            'initial_capital': 1000.0,
            'mintick': 0.01
        }
        
        logger.info("=" * 70)
        logger.info("🎯 STRATEGY RUNNER EXATO INICIALIZADO")
        logger.info(f"   Timeframe: {self.config['timeframe']}")
        logger.info(f"   Período: {self.config['period']}")
        logger.info(f"   Método adaptativo: {self.config['adaptive']}")
        logger.info(f"   Risk: {self.config['risk']*100}%")
        logger.info(f"   SL: {self.config['fixed_sl']}p, TP: {self.config['fixed_tp']}p")
        logger.info("=" * 70)
        
        # Sistema de sincronização temporal
        self.time_sync = TimeSync(timeframe_str=self.config['timeframe'])
        
        # Sistema de balance EXATO
        self.balance_manager = ExactBalanceManager(
            initial_capital=self.config['initial_capital']
        )
        
        # Sistema de flags EXATO
        self.flag_system = FlagSystem()
        
        # Loggers
        self.exact_logger = ExactExecutionLogger()
        self.comparison_logger = ComparisonLogger()
        
        # Carregar código Pine Script
        pine_code = self._load_pine_script()
        if not pine_code:
            raise Exception("❌ Não foi possível carregar Pine Script")
        
        # Inicializar interpretador Pine com configurações exatas
        self.engine = AdaptiveZeroLagEMA(pine_code)
        
        # Estado da execução
        self.is_running = False
        self.current_price = None
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais da barra atual (gerados no fechamento)
        self.buy_signal_current = False
        self.sell_signal_current = False
        
        # Sinais da barra anterior [1] (usados para setar flags)
        self.buy_signal_prev = False
        self.sell_signal_prev = False
        
        # Controle de fluxo temporal
        self.last_close_processed = None
        self.last_open_processed = None
        
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
        
        logger.info("✅ StrategyRunnerExact inicializado com sucesso")
    
    def _load_pine_script(self):
        """Carrega código Pine Script do arquivo"""
        try:
            # Tentar diferentes caminhos
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "Adaptive_Zero_Lag_EMA_v2.pine",
                "./strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "/app/strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.info(f"✅ Pine Script carregado: {path} ({len(content)} bytes)")
                        return content
            
            logger.error("❌ Arquivo Pine Script não encontrado em nenhum local")
            return None
            
        except Exception as e:
            logger.error(f"❌ Erro ao carregar Pine Script: {e}")
            return None
    
    def _calculate_position_size(self, entry_price: float) -> float:
        """Calcula tamanho da posição EXATAMENTE como Pine Script"""
        try:
            balance = self.balance_manager.get_balance()
            
            # riskAmount = risk * balance
            risk_amount = self.config['risk'] * balance
            
            # stopLossUSDT = fixedSL * syminfo.mintick
            stop_loss_usdt = self.config['fixed_sl'] * self.config['mintick']
            
            if stop_loss_usdt <= 0:
                logger.error("❌ Stop Loss USDT inválido")
                return 0
            
            # lots = riskAmount / stopLossUSDT
            quantity = risk_amount / stop_loss_usdt
            
            # Aplicar limite máximo
            if quantity > self.config['limit']:
                quantity = self.config['limit']
            
            # Arredondar para 4 casas decimais (ETH)
            quantity = round(quantity, 4)
            
            logger.info(f"   📊 Cálculo de posição EXATO:")
            logger.info(f"     Balance: ${balance:.2f}")
            logger.info(f"     Risk: {self.config['risk']*100}% = ${risk_amount:.2f}")
            logger.info(f"     Stop Loss: {self.config['fixed_sl']}p = ${stop_loss_usdt:.2f}")
            logger.info(f"     Quantidade: {quantity:.4f} ETH")
            logger.info(f"     Limit máximo: {self.config['limit']} ETH")
            
            return quantity
            
        except Exception as e:
            logger.error(f"❌ Erro cálculo posição: {e}")
            return 0
    
    def _open_position_exact(self, side: str, entry_price: float) -> bool:
        """
        Abre posição EXATAMENTE como Pine Script
        
        Pine Script:
        if (pendingBuy and strategy.position_size <= 0)
            strategy.entry(...)
            pendingBuy := false
        """
        logger.info("=" * 60)
        logger.info(f"🔍 VERIFICANDO ABERTURA {side.upper()}")
        logger.info(f"   Preço: ${entry_price:.2f}")
        logger.info(f"   Posição atual: {self.position_side or 'FLAT'} {abs(self.position_size):.4f}")
        
        # Verificar condições EXATAS do Pine
        if side == 'buy':
            if not self.flag_system.pending_buy:
                logger.info("⏭️ IGNORADO: pendingBuy = false")
                return False
            
            if self.position_size > 0:
                logger.info("⏭️ IGNORADO: Já está em LONG")
                return False
                
        elif side == 'sell':
            if not self.flag_system.pending_sell:
                logger.info("⏭️ IGNORADO: pendingSell = false")
                return False
            
            if self.position_size < 0:
                logger.info("⏭️ IGNORADO: Já está em SHORT")
                return False
        
        # Calcular quantidade
        quantity = self._calculate_position_size(entry_price)
        if quantity <= 0:
            logger.error("❌ Quantidade inválida")
            return False
        
        # Registrar trade no histórico
        trade_id = self.trade_history.add_trade(
            side=side,
            entry_price=entry_price,
            quantity=quantity
        )
        
        if not trade_id:
            logger.error("❌ Falha ao registrar trade")
            return False
        
        # Atualizar estado
        self.trade_id = trade_id
        self.position_side = side
        self.position_size = quantity if side == 'buy' else -quantity
        self.entry_price = entry_price
        
        # Inicializar trailing stop
        self.trailing_manager = TrailingStopManagerExact(
            side=side,
            entry_price=entry_price,
            fixed_sl=self.config['fixed_sl'],
            fixed_tp=self.config['fixed_tp'],
            mintick=self.config['mintick']
        )
        
        # RESETAR FLAG após execução (EXATO como Pine)
        if side == 'buy':
            self.flag_system.reset_buy_flag()
        else:
            self.flag_system.reset_sell_flag()
        
        # Log de execução
        self.exact_logger.log_trade_execution({
            'side': side,
            'price': entry_price,
            'quantity': quantity,
            'balance_before': self.balance_manager.get_balance(),
            'balance_after': self.balance_manager.get_balance(),
            'reason': f'pending{side.upper()} and position_size {"<= 0" if side == "buy" else ">= 0"}'
        })
        
        logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {quantity:.4f} ETH @ ${entry_price:.2f}")
        logger.info(f"   Trade ID: {trade_id}")
        logger.info(f"   Stop inicial: ${self.trailing_manager.current_stop:.2f}")
        logger.info("=" * 60)
        
        return True
    
    def _close_position_exact(self, exit_price: float, reason: str = "") -> bool:
        """Fecha posição e atualiza balance"""
        if not self.trade_id or self.position_size == 0:
            logger.warning("⚠️ Nenhuma posição para fechar")
            return False
        
        logger.info("=" * 60)
        logger.info(f"🔍 FECHANDO POSIÇÃO {self.position_side.upper()}")
        logger.info(f"   Motivo: {reason}")
        logger.info(f"   Entrada: ${self.entry_price:.2f}")
        logger.info(f"   Saída: ${exit_price:.2f}")
        
        # Fechar no histórico
        result = self.trade_history.close_trade(self.trade_id, exit_price)
        
        if result['success']:
            pnl_usdt = result['pnl_usdt']
            pnl_percent = result['pnl_percent']
            
            # Atualizar balance EXATO
            new_balance = self.balance_manager.update_from_closed_trade({
                'pnl_usdt': pnl_usdt,
                'trade_id': self.trade_id
            })
            
            logger.info(f"   PnL: {pnl_percent:.4f}% (${pnl_usdt:.2f})")
            logger.info(f"💰 Balance atualizado: ${new_balance:.2f}")
            
            # Resetar estado
            self.position_size = 0
            self.position_side = None
            self.entry_price = None
            self.trade_id = None
            self.trailing_manager = None
            
            logger.info(f"✅ POSIÇÃO FECHADA: {result['side'].upper()} @ ${exit_price:.2f}")
            logger.info("=" * 60)
            return True
        else:
            logger.error("❌ Falha ao fechar trade")
            return False
    
    def _check_trailing_stop(self):
        """Verifica trailing stop a cada tick"""
        if not self.position_size or not self.current_price or not self.trailing_manager:
            return
        
        current_stop = self.trailing_manager.update(self.current_price)
        
        if self.trailing_manager.should_close(self.current_price):
            logger.info("🎯 TRAILING STOP ATINGIDO!")
            self._close_position_exact(self.current_price, "trailing_stop")
    
    def _process_bar_close(self):
        """
        Processa FECHAMENTO da barra atual
        EXATO: Processa candle completo no último momento
        """
        if not self.current_price or not self.current_bar_data:
            return
        
        bar_info = self.time_sync.get_precise_bar_info()
        
        # Verificar se é momento de fechamento
        if not bar_info['is_exact_close']:
            return
        
        # Evitar processar o mesmo fechamento múltiplas vezes
        current_close_time = bar_info['current_bar_timestamp']
        if self.last_close_processed == current_close_time:
            return
        
        logger.info("=" * 60)
        logger.info("📊 PROCESSANDO FECHAMENTO DA BARRA")
        logger.info(f"   Barra #{self.bar_count} fechando")
        logger.info(f"   Tempo para próxima: {bar_info['milliseconds_to_next']:.1f}ms")
        
        # Completar candle com preço de fechamento
        self.current_bar_data['close'] = self.current_price
        
        try:
            # Processar candle no engine Pine
            signals = self.engine.process_candle(self.current_bar_data)
            
            # Sinais gerados na barra N (atual)
            self.buy_signal_current = signals['buy_signal']
            self.sell_signal_current = signals['sell_signal']
            
            logger.info(f"   Sinais calculados: buy={self.buy_signal_current}, sell={self.sell_signal_current}")
            
            # ATUALIZAR FLAGS com sinais da barra ANTERIOR [1]
            # IMPORTANTE: Pine usa buy_signal[1] para setar flags
            updated_flags = self.flag_system.update_flags(
                buy_signal_prev=self.buy_signal_prev,  # Sinais da barra N-1
                sell_signal_prev=self.sell_signal_prev  # Sinais da barra N-1
            )
            
            # EXECUTAR ORDENS IMEDIATAMENTE (como Pine faz)
            executed_trades = []
            
            # BUY: pendingBuy and strategy.position_size <= 0
            if self.flag_system.should_execute_buy(self.position_size):
                logger.info("   ✅ Condição BUY atendida")
                if self._open_position_exact('buy', self.current_price):
                    executed_trades.append({
                        'side': 'buy',
                        'price': self.current_price,
                        'quantity': abs(self.position_size)
                    })
            
            # SELL: pendingSell and strategy.position_size >= 0
            if self.flag_system.should_execute_sell(self.position_size):
                logger.info("   ✅ Condição SELL atendida")
                if self._open_position_exact('sell', self.current_price):
                    executed_trades.append({
                        'side': 'sell',
                        'price': self.current_price,
                        'quantity': abs(self.position_size)
                    })
            
            # Salvar sinais atuais como [1] para próxima barra
            self.flag_system.set_previous_signals(
                buy_signal=self.buy_signal_current,
                sell_signal=self.sell_signal_current
            )
            
            self.buy_signal_prev = self.buy_signal_current
            self.sell_signal_prev = self.sell_signal_current
            
            # Log detalhado
            self.exact_logger.log_bar_close(
                timestamp=datetime.now(pytz.utc),
                bar_number=self.bar_count,
                close_price=self.current_price,
                signals={
                    'buy_signal': self.buy_signal_current,
                    'sell_signal': self.sell_signal_current
                },
                next_flags=updated_flags,
                executed_trades=executed_trades
            )
            
            # Marcar como processado
            self.last_close_processed = current_close_time
            
            logger.info(f"✅ Fechamento processado para barra #{self.bar_count}")
            logger.info("=" * 60)
            
            return True
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar fechamento: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
    
    def _process_bar_open(self):
        """
        Processa ABERTURA da nova barra
        EXATO: Inicializa novo candle
        """
        if not self.current_price:
            return
        
        bar_info = self.time_sync.get_precise_bar_info()
        
        # Verificar se é momento de abertura
        if not bar_info['is_exact_open']:
            return
        
        # Evitar processar a mesma abertura múltiplas vezes
        current_open_time = bar_info['current_bar_timestamp']
        if self.last_open_processed == current_open_time:
            return
        
        logger.info("=" * 60)
        logger.info("📊 INICIANDO NOVA BARRA")
        logger.info(f"   Barra #{self.bar_count + 1} iniciando")
        logger.info(f"   Tempo desde abertura: {bar_info['milliseconds_since_open']:.1f}ms")
        
        # Inicializar novo candle
        self.last_bar_timestamp = current_open_time
        self.bar_count += 1
        
        self.current_bar_data = {
            'timestamp': int(current_open_time.timestamp() * 1000),
            'open': self.current_price,
            'high': self.current_price,
            'low': self.current_price,
            'close': self.current_price,
            'volume': 0
        }
        
        # Log da abertura
        self.exact_logger.log_bar_open(
            timestamp=datetime.now(pytz.utc),
            bar_number=self.bar_count,
            open_price=self.current_price,
            flags_state=self.flag_system.get_state(),
            position_size=self.position_size
        )
        
        # Marcar como processado
        self.last_open_processed = current_open_time
        
        logger.info(f"✅ Barra #{self.bar_count} iniciada")
        logger.info(f"   Preço abertura: ${self.current_price:.2f}")
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
            # Atualizar dados do candle em andamento
            if self.current_bar_data and self.current_price:
                self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
                self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            
            # Verificar trailing stop
            self._check_trailing_stop()
            
            # Processar abertura da barra (primeiro)
            open_processed = self._process_bar_open()
            
            # Processar fechamento da barra (depois)
            close_processed = self._process_bar_close()
            
            return {
                "status": "running",
                "bar_count": self.bar_count,
                "current_price": self.current_price,
                "position_size": self.position_size,
                "position_side": self.position_side,
                "pending_buy": self.flag_system.pending_buy,
                "pending_sell": self.flag_system.pending_sell,
                "balance": self.balance_manager.get_balance(),
                "bar_open_processed": open_processed,
                "bar_close_processed": close_processed
            }
            
        except Exception as e:
            logger.error(f"💥 Erro em run_strategy_exact: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "error", "error": str(e)}
    
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                # Atualizar preço com precisão de 2 casas
                self.current_price = round(new_price, 2)
                    
        except Exception as e:
            logger.error(f"❌ Erro ao processar mensagem WS: {e}")
    
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
        logger.info("✅ Thread WebSocket iniciada")
        time.sleep(3)  # Aguardar conexão
    
    def start(self):
        """Inicia a execução EXATA"""
        logger.info("🚀 Iniciando execução EXATA...")
        
        # Validar precisão temporal
        self.time_sync.validate_precision(duration_seconds=10)
        
        # Iniciar WebSocket
        self._start_websocket()
        
        # Aguardar preço atual
        logger.info("⏳ Aguardando preço atual...")
        for i in range(30):
            if self.current_price is not None:
                break
            time.sleep(1)
            if i % 5 == 0:
                logger.info(f"   Aguardando... ({i+1}/30)")
        
        if self.current_price is None:
            # Usar preço da API REST como fallback
            logger.warning("⚠️ WebSocket não retornou preço, usando API REST")
            self.current_price = self.okx_client.get_ticker_price()
        
        if self.current_price is None:
            logger.error("❌ Não foi possível obter preço atual")
            return False
        
        logger.info(f"✅ Preço atual: ${self.current_price:.2f}")
        
        # Inicializar com candles históricos
        self._initialize_candle_buffer()
        
        # Iniciar primeira barra
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
        self.last_bar_timestamp = self.time_sync.timeframe_manager.get_bar_start(now_utc)
        self.bar_count = 1
        
        self.current_bar_data = {
            'timestamp': int(self.last_bar_timestamp.timestamp() * 1000),
            'open': self.current_price,
            'high': self.current_price,
            'low': self.current_price,
            'close': self.current_price,
            'volume': 0
        }
        
        self.is_running = True
        
        logger.info("=" * 70)
        logger.info("✅ EXECUÇÃO EXATA INICIADA COM SUCESSO")
        logger.info(f"   Timeframe: {self.config['timeframe']}")
        logger.info(f"   Preço inicial: ${self.current_price:.2f}")
        logger.info(f"   Barra inicial: #{self.bar_count}")
        logger.info(f"   Balance inicial: ${self.balance_manager.get_balance():.2f}")
        logger.info("=" * 70)
        
        return True
    
    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos"""
        logger.info("📈 Carregando candles históricos...")
        
        try:
            historical_candles = self.okx_client.get_candles(
                timeframe=self.config['timeframe'],
                limit=50
            )
            
            if historical_candles and len(historical_candles) >= 20:
                logger.info(f"✅ {len(historical_candles)} candles históricos carregados")
                
                # Processar candles no engine (warm-up)
                for candle in historical_candles:
                    self.engine.process_candle(candle)
                
                logger.info(f"   🔧 {len(historical_candles)} candles processados (warm-up)")
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles) if historical_candles else 0} candles")
                
        except Exception as e:
            logger.error(f"❌ Erro ao carregar candles: {e}")
    
    def force_close_position(self):
        """Força fechamento da posição atual"""
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
        logger.info("⏹️ Execução parada")
    
    def get_status(self):
        """Retorna status atual"""
        return {
            'is_running': self.is_running,
            'current_price': self.current_price,
            'bar_count': self.bar_count,
            'position_size': self.position_size,
            'position_side': self.position_side,
            'entry_price': self.entry_price,
            'pending_buy': self.flag_system.pending_buy,
            'pending_sell': self.flag_system.pending_sell,
            'balance': self.balance_manager.get_balance(),
            'balance_stats': self.balance_manager.get_stats(),
            'flags_state': self.flag_system.get_state(),
            'last_bar_timestamp': self.last_bar_timestamp.isoformat() if self.last_bar_timestamp else None
        }
