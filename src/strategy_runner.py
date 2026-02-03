"""
strategy_runner.py - EXECUÇÃO TOTALMENTE POR TICK
"""
import os
import logging
import time
import threading
import json
import websocket
from datetime import datetime, timedelta
import pytz

from .pine_engine import PineScriptInterpreter
from .trailing_stop import TrailingStopManager
from .okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        self.interpreter = None
        self.is_running = False
        self.current_price = None
        
        # Estado da posição atual
        self.position_size = 0
        self.position_side = None
        self.entry_price = None
        self.trade_id = None
        
        # Parâmetros da estratégia
        self.fixedSL = 2000
        self.fixedTP = 55
        self.risk = 0.01
        self.mintick = 0.01
        
        # Trailing Stop Manager
        self.trailing_stop = TrailingStopManager(
            trail_points=self.fixedTP,
            trail_offset=15,
            mintick=self.mintick
        )
        
        # Stop Loss e Take Profit estáticos
        self.stop_loss_price = None
        self.take_profit_price = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        
        # Timezone
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Controle de barras (apenas para logging)
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        self.timeframe_minutes = 30
        
        # Sinais pendentes
        self.pending_buy_signal = False
        self.pending_sell_signal = False
        
        # Buffer de ticks para processamento
        self.tick_buffer = []
        self.max_ticks = 1000
        
        # Último processamento
        self.last_tick_time = 0
        self.tick_interval = 0.1  # Processar a cada 0.1 segundos
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado (MODO TICK TOTAL)")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        """Carrega o código Pine Script do arquivo"""
        try:
            path = "strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
                logger.error(f"❌ Arquivo não encontrado: {path}")
                return None
        except Exception as e:
            logger.error(f"❌ Erro ao ler Pine Script: {e}")
            return None
    
    def _calculate_position_size(self, entry_price):
        """Calcula tamanho da posição EXATAMENTE como no Pine Script"""
        try:
            # balance = strategy.initial_capital + strategy.netprofit
            balance = self.okx_client.get_balance()
            if balance <= 0:
                balance = 1000  # initial_capital do Pine
            
            risk_amount = self.risk * balance
            stop_loss_usdt = self.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Aplicar limite máximo (limit do Pine)
            limit = 100
            if quantity > limit:
                quantity = limit
            
            # Limitar ao saldo disponível
            max_quantity = balance / entry_price * 0.95
            if quantity > max_quantity:
                quantity = max_quantity
            
            quantity = round(quantity, 4)
            
            return quantity if quantity > 0 else 0
        except Exception as e:
            logger.error(f"❌ Erro ao calcular tamanho: {e}")
            return 0
    
    def _open_position(self, side, entry_price):
        """Abre uma nova posição - EXATO como Pine Script"""
        try:
            # REGRA DO PINE SCRIPT (EXATA):
            # BUY só se strategy.position_size <= 0 (flat ou short)
            # SELL só se strategy.position_size >= 0 (flat ou long)
            
            if side == 'buy':
                if self.position_size > 0:
                    logger.info(f"⏭️  IGNORANDO BUY - já está em LONG")
                    return False
            else:  # sell
                if self.position_size < 0:
                    logger.info(f"⏭️  IGNORANDO SELL - já está em SHORT")
                    return False
            
            # Se já tem posição oposta, fechar primeiro
            if self.position_size != 0:
                logger.info(f"🔀 Fechando posição {self.position_side} para abrir {side}")
                self._close_position(entry_price, "inversao")
            
            # Calcular quantidade
            quantity = self._calculate_position_size(entry_price)
            if quantity <= 0:
                return False
            
            # Registrar trade
            trade_id = self.trade_history.add_trade(
                side=side,
                entry_price=entry_price,
                quantity=quantity
            )
            
            if trade_id:
                self.trade_id = trade_id
                self.position_side = side
                self.position_size = quantity if side == 'buy' else -quantity
                self.entry_price = entry_price
                
                # Inicializar trailing stop
                self.trailing_stop.on_entry(entry_price, side)
                
                # Calcular Stop Loss e Take Profit estáticos (fallback)
                if side == 'buy':
                    self.stop_loss_price = entry_price - (self.fixedSL * self.mintick)
                    self.take_profit_price = entry_price + (self.fixedTP * self.mintick)
                else:  # sell
                    self.stop_loss_price = entry_price + (self.fixedSL * self.mintick)
                    self.take_profit_price = entry_price - (self.fixedTP * self.mintick)
                
                logger.info("=" * 60)
                logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {abs(quantity):.4f} ETH @ ${entry_price:.2f}")
                logger.info(f"   Trailing Stop ativado: offset=15p, trail={self.fixedTP}p")
                logger.info("=" * 60)
                
                # Resetar sinais pendentes (como no Pine)
                if side == 'buy':
                    self.pending_buy_signal = False
                else:
                    self.pending_sell_signal = False
                
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Erro ao abrir posição: {e}")
            return False
    
    def _close_position(self, exit_price, reason=""):
        """Fecha a posição atual"""
        if not self.trade_id or self.position_size == 0:
            return False
        
        try:
            # Calcular PnL
            if self.entry_price:
                if self.position_side == 'long':
                    pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100
                else:
                    pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100
            
            # Fechar trade no histórico
            success = self.trade_history.close_trade(self.trade_id, exit_price)
            if success:
                logger.info("=" * 60)
                logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${exit_price:.2f}")
                logger.info(f"   Motivo: {reason}")
                if self.entry_price:
                    logger.info(f"   PnL: {pnl_pct:.2f}%")
                logger.info("=" * 60)
                
                # Resetar estado
                self.position_size = 0
                self.position_side = None
                self.entry_price = None
                self.trade_id = None
                self.stop_loss_price = None
                self.take_profit_price = None
                self.trailing_stop.reset()
                
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao fechar posição: {e}")
            return False
    
    def _process_tick_realtime(self, price: float):
        """
        Processa um tick em TEMPO REAL
        Lógica: 
        1. Processar indicador no interpretador
        2. Verificar sinais de ENTRADA (com delay de 1 barra)
        3. Verificar condições de SAÍDA (trailing stop, SL, TP) a cada tick
        """
        if not self.interpreter or price <= 0:
            return
        
        try:
            # 1. Processar tick no interpretador
            tick_result = self.interpreter.process_tick(price, datetime.now())
            
            # 2. Atualizar sinais pendentes para ENTRADA
            # No Pine: sinais são gerados no FECHAMENTO da barra anterior
            # e ficam pendentes para execução na próxima barra
            self.pending_buy_signal = tick_result.get('pending_buy', False)
            self.pending_sell_signal = tick_result.get('pending_sell', False)
            
            # 3. VERIFICAÇÕES DE SAÍDA (a cada tick se houver posição)
            if self.position_size != 0:
                # Atualizar trailing stop com preço atual
                self.trailing_stop.update(price)
                
                # Verificar SE TRAILING STOP FOI ATINGIDO
                if self.trailing_stop.should_close(price):
                    logger.info(f"🎯 TRAILING STOP ATINGIDO @ ${price:.2f}")
                    self._close_position(price, "trailing_stop")
                    return
                
                # Verificar STOP LOSS/Take Profit estático (fallback)
                self._check_static_stop_take(price)
            
            # 4. VERIFICAÇÕES DE ENTRADA (a cada tick se houver sinal pendente)
            # IMPORTANTE: No Pine, a entrada ocorre no PRIMEIRO TICK da nova barra
            # após confirmação do sinal na barra anterior
            
            # Se tem sinal BUY pendente E não está em LONG
            if self.pending_buy_signal and self.position_size <= 0:
                logger.info(f"🎯 EXECUTANDO BUY (sinal confirmado) @ ${price:.2f}")
                if self._open_position('buy', price):
                    self.pending_buy_signal = False  # Resetar após execução
            
            # Se tem sinal SELL pendente E não está em SHORT
            elif self.pending_sell_signal and self.position_size >= 0:
                logger.info(f"🎯 EXECUTANDO SELL (sinal confirmado) @ ${price:.2f}")
                if self._open_position('sell', price):
                    self.pending_sell_signal = False  # Resetar após execução
            
            # 5. Atualizar barra atual (apenas para logging)
            self._update_current_bar(price)
            
        except Exception as e:
            logger.error(f"❌ Erro ao processar tick: {e}")
    
    def _check_static_stop_take(self, current_price: float):
        """Verifica stop loss/take profit estático (fallback)"""
        if not self.position_size:
            return
        
        try:
            if self.position_side == 'long':
                if self.stop_loss_price and current_price <= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS (LONG) @ ${current_price:.2f}")
                    self._close_position(current_price, "stop_loss")
                    return
                if self.take_profit_price and current_price >= self.take_profit_price:
                    logger.info(f"💰 TAKE PROFIT (LONG) @ ${current_price:.2f}")
                    self._close_position(current_price, "take_profit")
                    return
            
            elif self.position_side == 'short':
                if self.stop_loss_price and current_price >= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS (SHORT) @ ${current_price:.2f}")
                    self._close_position(current_price, "stop_loss")
                    return
                if self.take_profit_price and current_price <= self.take_profit_price:
                    logger.info(f"💰 TAKE PROFIT (SHORT) @ ${current_price:.2f}")
                    self._close_position(current_price, "take_profit")
                    return
                    
        except Exception as e:
            logger.error(f"❌ Erro ao verificar stop/take: {e}")
    
    def _update_current_bar(self, price: float):
        """Atualiza dados da barra atual (apenas para logging)"""
        now_brazil = datetime.now(self.tz_brazil)
        
        # Calcular início da barra atual
        current_bar_start = now_brazil.replace(
            minute=(now_brazil.minute // self.timeframe_minutes) * self.timeframe_minutes,
            second=0,
            microsecond=0
        )
        
        # Se é a primeira barra
        if self.last_bar_timestamp is None:
            self.last_bar_timestamp = current_bar_start
            self.current_bar_data = {
                'open': price,
                'high': price,
                'low': price,
                'close': price
            }
            return
        
        # Se uma nova barra começou
        if current_bar_start > self.last_bar_timestamp:
            logger.info(f"📊 BARRA {self.last_bar_timestamp.strftime('%H:%M')} → {current_bar_start.strftime('%H:%M')}")
            self.last_bar_timestamp = current_bar_start
            self.current_bar_data = {
                'open': price,
                'high': price,
                'low': price,
                'close': price
            }
            self.bar_count += 1
        else:
            # Atualizar dados da barra atual
            if self.current_bar_data:
                self.current_bar_data['high'] = max(self.current_bar_data['high'], price)
                self.current_bar_data['low'] = min(self.current_bar_data['low'], price)
                self.current_bar_data['close'] = price
    
    # WebSocket methods
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                # Atualizar preço atual
                self.current_price = new_price
                
                # Processar tick em TEMPO REAL
                current_time = time.time()
                if current_time - self.last_tick_time >= self.tick_interval:
                    self._process_tick_realtime(new_price)
                    self.last_tick_time = current_time
                    
        except Exception as e:
            logger.error(f"❌ Erro no WebSocket: {e}")

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
        logger.info("📊 Inscrito no canal 'tickers' (processando cada tick)")
    
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
        logger.info("✅ WebSocket iniciado (processamento por tick)")
        time.sleep(3)
        return True
    
    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def _initialize_with_historical(self):
        """Inicializa com candles históricos"""
        try:
            historical_candles = self.okx_client.get_candles(limit=100)
            
            if historical_candles:
                logger.info(f"✅ {len(historical_candles)} candles históricos")
                
                # Processar candles para aquecer indicadores
                for candle in historical_candles:
                    self.interpreter.process_tick(candle['close'], datetime.now())
                
                logger.info(f"   🔧 Indicadores aquecidos")
                
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar histórico: {e}")
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador não inicializado")
            return False
        
        logger.info("🚀 Iniciando Strategy Runner (MODO TICK TOTAL)...")
        
        # Inicializar com dados históricos
        self._initialize_with_historical()
        
        # Iniciar WebSocket
        if not self._start_websocket():
            return False
        
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
        logger.info("✅ Processando cada tick em tempo real...")
        
        self.is_running = True
        return True
    
    def stop(self):
        """Para o strategy runner"""
        # Fechar posição aberta se existir
        if self.position_size != 0 and self.current_price:
            self._close_position(self.current_price, "stop_bot")
        
        self.is_running = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado")
    
    def force_close_current_position(self):
        """Força o fechamento da posição atual"""
        if not self.position_size or not self.current_price:
            return {"success": False, "message": "Sem posição aberta"}
        
        try:
            success = self._close_position(self.current_price, "force_close_api")
            if success:
                return {"success": True, "message": f"Posição fechada @ ${self.current_price:.2f}"}
            else:
                return {"success": False, "message": "Falha ao fechar posição"}
        except Exception as e:
            return {"success": False, "message": f"Erro: {str(e)}"}
    
    def get_strategy_status(self):
        """Retorna status da estratégia"""
        return {
            "is_running": self.is_running,
            "current_price": self.current_price,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "pending_buy": self.pending_buy_signal,
            "pending_sell": self.pending_sell_signal,
            "trailing_stop": self.trailing_stop.get_stop_price() if self.position_size else None,
            "trailing_status": self.trailing_stop.get_status() if self.position_size else None,
            "bar_count": self.bar_count
        }
