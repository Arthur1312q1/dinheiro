"""
strategy_runner.py - VERSÃO COM EXECUÇÃO POR TICK E TRAILING STOP
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
        
        # Configurações
        self.timeframe_minutes = 30
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Sinais pendentes
        self.pending_buy_signal = False
        self.pending_sell_signal = False
        
        # Estado da posição atual
        self.position_size = 0
        self.position_side = None
        self.entry_price = None
        self.trade_id = None
        
        # Parâmetros da estratégia (EXATOS do Pine Script)
        self.fixedSL = 2000  # pontos
        self.fixedTP = 55    # pontos
        self.risk = 0.01     # 1%
        self.mintick = 0.01  # tick mínimo do ETH/USDT (syminfo.mintick)
        
        # Trailing Stop Manager
        self.trailing_stop = TrailingStopManager(
            trail_points=self.fixedTP,
            trail_offset=15,  # Valor fixo do strategy.exit no Pine
            mintick=self.mintick
        )
        
        # Stop Loss e Take Profit estáticos (fallback)
        self.stop_loss_price = None
        self.take_profit_price = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        self.last_log_time = time.time()
        self.last_tick_time = time.time()
        
        # Timezone
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Controle de execução
        self.last_bar_check = 0
        self.bar_check_interval = 1  # Verificar nova barra a cada 1 segundo
        
        # Controle de ticks
        self.tick_buffer = []
        self.max_tick_buffer = 1000
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado com processamento por tick")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        """Carrega o código Pine Script do arquivo"""
        try:
            path = "strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    logger.info(f"✅ Pine Script carregado: {len(content)} bytes")
                    return content
            else:
                logger.error(f"❌ Arquivo não encontrado: {path}")
                return None
        except Exception as e:
            logger.error(f"❌ Erro ao ler Pine Script: {e}")
            return None
    
    def _calculate_position_size(self, entry_price):
        """Calcula tamanho da posição EXATAMENTE como no Pine Script"""
        try:
            # Fórmula Pine: lots = (risk * balance) / (fixedSL * syminfo.mintick)
            balance = self.okx_client.get_balance()  # balance = strategy.initial_capital + strategy.netprofit
            
            # Se não houver saldo, usa um valor padrão para simulação
            if balance <= 0:
                balance = 1000  # initial_capital do Pine
            
            risk_amount = self.risk * balance
            stop_loss_usdt = self.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                logger.error(f"❌ Stop Loss USDT inválido: {stop_loss_usdt}")
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Aplicar limite máximo (limit do Pine)
            limit = 100  # input(title="Max Lots", defval=100)
            if quantity > limit:
                quantity = limit
            
            # Limitar ao saldo disponível
            max_quantity = balance / entry_price * 0.95  # 95% do saldo
            if quantity > max_quantity:
                quantity = max_quantity
            
            # Arredondar para 4 casas decimais (padrão cripto)
            quantity = round(quantity, 4)
            
            if quantity <= 0:
                logger.error(f"❌ Quantidade inválida: {quantity}")
                return 0
            
            logger.info(f"📊 Cálculo de posição:")
            logger.info(f"   Balance: ${balance:.2f}")
            logger.info(f"   Risk: {self.risk*100}% = ${risk_amount:.2f}")
            logger.info(f"   Stop Loss: {self.fixedSL}p = ${stop_loss_usdt:.2f}")
            logger.info(f"   Quantity: {quantity:.4f} ETH")
            
            return quantity
        except Exception as e:
            logger.error(f"❌ Erro ao calcular tamanho da posição: {e}")
            return 0
    
    def _open_position(self, side, entry_price):
        """Abre uma nova posição - EXATO como Pine Script"""
        try:
            logger.info("=" * 60)
            logger.info(f"🔍 VERIFICANDO ABERTURA DE POSIÇÃO {side.upper()}")
            logger.info(f"   Preço: ${entry_price:.2f}")
            logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
            logger.info(f"   Sinal BUY pendente: {self.pending_buy_signal}")
            logger.info(f"   Sinal SELL pendente: {self.pending_sell_signal}")
            
            # REGRA DO PINE SCRIPT (EXATA):
            # BUY só se strategy.position_size <= 0 (flat ou short)
            # SELL só se strategy.position_size >= 0 (flat ou long)
            
            if side == 'buy':
                if self.position_size > 0:
                    logger.info(f"⏭️  IGNORANDO BUY - já está em LONG")
                    logger.info("=" * 60)
                    return False
            else:  # sell
                if self.position_size < 0:
                    logger.info(f"⏭️  IGNORANDO SELL - já está em SHORT")
                    logger.info("=" * 60)
                    return False
            
            # Se já tem posição oposta, fechar primeiro
            if self.position_size != 0:
                logger.info(f"🔀 Fechando posição {self.position_side} para abrir {side}")
                self._close_position(entry_price, "inversao")
            
            # Calcular quantidade
            quantity = self._calculate_position_size(entry_price)
            if quantity <= 0:
                logger.error("❌ Quantidade inválida")
                logger.info("=" * 60)
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
                
                logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {abs(quantity):.4f} ETH @ ${entry_price:.2f}")
                logger.info(f"   Stop Loss estático: ${self.stop_loss_price:.2f}")
                logger.info(f"   Take Profit estático: ${self.take_profit_price:.2f}")
                logger.info(f"   Trailing Stop ativado: offset=15p, trail={self.fixedTP}p")
                
                # Resetar sinais pendentes após abrir posição (como no Pine)
                if side == 'buy':
                    self.pending_buy_signal = False
                else:
                    self.pending_sell_signal = False
                
                logger.info("=" * 60)
                return True
            
            logger.info("=" * 60)
            return False
        except Exception as e:
            logger.error(f"❌ Erro ao abrir posição: {e}")
            return False
    
    def _close_position(self, exit_price, reason=""):
        """Fecha a posição atual"""
        if not self.trade_id or self.position_size == 0:
            logger.warning(f"⚠️  Tentativa de fechar posição inexistente")
            return False
        
        try:
            logger.info("=" * 60)
            logger.info(f"🔍 FECHAMENTO DE POSIÇÃO")
            logger.info(f"   Posição: {self.position_side} {abs(self.position_size):.4f} ETH")
            logger.info(f"   Entrada: ${self.entry_price:.2f}")
            logger.info(f"   Saída: ${exit_price:.2f}")
            logger.info(f"   Motivo: {reason}")
            
            # Calcular PnL
            if self.entry_price:
                if self.position_side == 'long':
                    pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100
                    pnl_usdt = (exit_price - self.entry_price) * abs(self.position_size)
                else:
                    pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100
                    pnl_usdt = (self.entry_price - exit_price) * abs(self.position_size)
                logger.info(f"   PnL: {pnl_pct:.2f}% (${pnl_usdt:.2f})")
            
            # Fechar trade no histórico
            success = self.trade_history.close_trade(self.trade_id, exit_price)
            if success:
                logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${exit_price:.2f} ({reason})")
                
                # Resetar estado
                self.position_size = 0
                self.position_side = None
                self.entry_price = None
                self.trade_id = None
                self.stop_loss_price = None
                self.take_profit_price = None
                self.trailing_stop.reset()
                
                logger.info("=" * 60)
                return True
            else:
                logger.error(f"❌ Falha ao fechar trade no histórico")
                logger.info("=" * 60)
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao fechar posição: {e}")
            return False
    
    def _process_tick(self, price: float):
        """Processa um tick em tempo real"""
        if not self.interpreter:
            return
        
        try:
            # Processar tick no interpretador
            tick_result = self.interpreter.process_tick(price, datetime.now())
            
            # Atualizar sinais pendentes (com delay de 1 barra)
            self.pending_buy_signal = tick_result.get('pending_buy', False)
            self.pending_sell_signal = tick_result.get('pending_sell', False)
            
            # DEBUG: Log de sinais importantes
            if tick_result.get('buy_signal_current') or tick_result.get('sell_signal_current'):
                logger.info(f"📈 TICK: ${price:.2f} | EMA={tick_result['ema']:.2f} | EC={tick_result['ec']:.2f}")
                if tick_result['buy_signal_current']:
                    logger.info(f"   🟢 BUY SIGNAL (será pendente na próxima barra)")
                if tick_result['sell_signal_current']:
                    logger.info(f"   🔴 SELL SIGNAL (será pendente na próxima barra)")
            
            # Atualizar trailing stop se houver posição
            if self.position_size != 0:
                self.trailing_stop.update(price)
                
                # Verificar se trailing stop foi atingido
                if self.trailing_stop.should_close(price):
                    logger.info(f"🛑 TRAILING STOP ATINGIDO: ${price:.2f}")
                    self._close_position(price, "trailing_stop")
                    return
                
                # Verificar stop loss/take profit estático (fallback)
                self._check_static_stop_take(price)
            
            # Processar sinais pendentes para entrada (no início de nova barra)
            self._process_pending_signals(price)
            
        except Exception as e:
            logger.error(f"❌ Erro ao processar tick: {e}")
    
    def _check_static_stop_take(self, current_price: float):
        """Verifica stop loss/take profit estático (fallback)"""
        if not self.position_size:
            return
        
        try:
            if self.position_side == 'long':
                if self.stop_loss_price and current_price <= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS ESTÁTICO (LONG): ${current_price:.2f}")
                    self._close_position(current_price, "stop_loss")
                    return
                if self.take_profit_price and current_price >= self.take_profit_price:
                    logger.info(f"🎯 TAKE PROFIT ESTÁTICO (LONG): ${current_price:.2f}")
                    self._close_position(current_price, "take_profit")
                    return
            
            elif self.position_side == 'short':
                if self.stop_loss_price and current_price >= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS ESTÁTICO (SHORT): ${current_price:.2f}")
                    self._close_position(current_price, "stop_loss")
                    return
                if self.take_profit_price and current_price <= self.take_profit_price:
                    logger.info(f"🎯 TAKE PROFIT ESTÁTICO (SHORT): ${current_price:.2f}")
                    self._close_position(current_price, "take_profit")
                    return
                    
        except Exception as e:
            logger.error(f"❌ Erro ao verificar stop/take: {e}")
    
    def _process_pending_signals(self, current_price: float):
        """Processa sinais pendentes para entrada (execução na próxima barra após sinal)"""
        if not current_price:
            return
        
        # Verificar se é início de nova barra (para execução de entradas)
        is_new_bar = self._check_and_update_bar()
        
        if not is_new_bar:
            return  # Só executa entradas no início da barra
        
        logger.info(f"🔍 PROCESSANDO SINAIS PENDENTES (início da barra)")
        logger.info(f"   Preço: ${current_price:.2f}")
        logger.info(f"   Sinais: BUY={self.pending_buy_signal}, SELL={self.pending_sell_signal}")
        logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
        
        # BUY: só executa se position_size <= 0 (flat ou short)
        if self.pending_buy_signal and self.position_size <= 0:
            logger.info(f"🎯 EXECUTANDO BUY (sinal confirmado da barra anterior)")
            self._open_position('buy', current_price)
        
        # SELL: só executa se position_size >= 0 (flat ou long)
        elif self.pending_sell_signal and self.position_size >= 0:
            logger.info(f"🎯 EXECUTANDO SELL (sinal confirmado da barra anterior)")
            self._open_position('sell', current_price)
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30 minutos começou"""
        if not self.current_price:
            return False
        
        now_brazil = datetime.now(self.tz_brazil)
        current_time = time.time()
        
        # Verificar intervalo
        if current_time - self.last_bar_check < self.bar_check_interval:
            return False
        self.last_bar_check = current_time
        
        # Calcular início da barra atual (BRT)
        current_bar_start = now_brazil.replace(
            minute=(now_brazil.minute // self.timeframe_minutes) * self.timeframe_minutes,
            second=0,
            microsecond=0
        )
        
        # Se é a primeira barra
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
            return True
        
        # Se uma nova barra começou
        if current_bar_start > self.last_bar_timestamp:
            logger.info("=" * 60)
            logger.info(f"📊 NOVA BARRA 30m INICIADA: {current_bar_start.strftime('%H:%M')}")
            logger.info(f"   Preço de abertura: ${self.current_price:.2f}")
            logger.info(f"   Posição atual: {self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH")
            
            # Processar barra anterior se houver dados
            if self.current_bar_data:
                self.current_bar_data['close'] = self.current_price
                self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
                self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            
            # Iniciar nova barra
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
    
    # WebSocket methods
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                new_price = float(ticker_data.get('last', 0))
                
                # Atualizar preço
                self.current_price = new_price
                
                # Processar tick (EM TEMPO REAL)
                self._process_tick(new_price)
                
                # Log periódico
                current_time = time.time()
                if current_time - self.last_log_time > 30:
                    status_msg = f"📈 Status: ${new_price:.2f}"
                    if self.position_side:
                        status_msg += f" | Posição: {self.position_side} {abs(self.position_size):.4f} ETH"
                        stop_price = self.trailing_stop.get_stop_price()
                        if stop_price:
                            status_msg += f" | Trailing Stop: ${stop_price:.2f}"
                    logger.info(status_msg)
                    self.last_log_time = current_time
                    
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
        time.sleep(3)
    
    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def _initialize_with_historical(self):
        """Inicializa com candles históricos"""
        logger.info("📈 Inicializando com candles históricos...")
        
        try:
            historical_candles = self.okx_client.get_candles(limit=100)
            
            if len(historical_candles) >= 30:
                logger.info(f"✅ {len(historical_candles)} candles históricos")
                
                # Processar candles para aquecer indicadores
                for candle in historical_candles:
                    self.interpreter.process_candle(candle)
                
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
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador não inicializado")
            return False
        
        logger.info("🚀 Iniciando Strategy Runner (modo por tick)...")
        
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
        self._initialize_with_historical()
        
        self.is_running = True
        logger.info("✅ Strategy Runner iniciado (processando ticks em tempo real)")
        return True
    
    def stop(self):
        """Para o strategy runner"""
        # Fechar posição aberta se existir
        if self.position_size != 0 and self.current_price:
            self._close_position(self.current_price, "stop_bot")
        
        self.is_running = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado")
    
    def run_strategy_realtime(self):
        """Executa a estratégia em tempo real (para compatibilidade)"""
        if not self.is_running:
            return {"signal": "HOLD"}
        
        try:
            # Verificar nova barra
            new_bar = self._check_and_update_bar()
            
            return {
                "signal": "HOLD",
                "new_bar": new_bar,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "pending_buy": self.pending_buy_signal,
                "pending_sell": self.pending_sell_signal,
                "position_size": self.position_size,
                "position_side": self.position_side,
                "trailing_stop": self.trailing_stop.get_stop_price() if self.position_size else None
            }
            
        except Exception as e:
            logger.error(f"❌ Erro em run_strategy_realtime: {e}")
            return {"signal": "HOLD", "error": str(e)}
    
    def force_close_current_position(self):
        """Força o fechamento da posição atual (endpoint API)"""
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
        next_bar_time = None
        time_to_next_bar = None
        
        if self.last_bar_timestamp:
            next_bar = self.last_bar_timestamp + timedelta(minutes=self.timeframe_minutes)
            next_bar_time = next_bar.strftime('%H:%M:%S')
            
            # Calcular tempo restante
            now_brazil = datetime.now(self.tz_brazil)
            time_to_next_bar = (next_bar - now_brazil).total_seconds()
            if time_to_next_bar < 0:
                time_to_next_bar = 0
        
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": "TICK_PROCESSING",
            "simulation_mode": True,
            "current_price": self.current_price,
            "next_bar_at": next_bar_time,
            "time_to_next_bar_seconds": time_to_next_bar,
            "bars_processed": self.bar_count,
            "pending_buy_signal": self.pending_buy_signal,
            "pending_sell_signal": self.pending_sell_signal,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss_price,
            "take_profit": self.take_profit_price,
            "trailing_stop": self.trailing_stop.get_stop_price(),
            "trailing_status": self.trailing_stop.get_status(),
            "should_close": self._should_close_position() if self.position_size else False
        }
    
    def _should_close_position(self):
        """Verifica se a posição deveria estar fechada"""
        if not self.position_size or not self.current_price:
            return False
        
        # Verificar trailing stop
        if self.trailing_stop.should_close(self.current_price):
            return True
        
        # Verificar stop loss/take profit
        if self.position_side == 'short':
            if self.take_profit_price and self.current_price <= self.take_profit_price:
                return True
            if self.stop_loss_price and self.current_price >= self.stop_loss_price:
                return True
        elif self.position_side == 'long':
            if self.take_profit_price and self.current_price >= self.take_profit_price:
                return True
            if self.stop_loss_price and self.current_price <= self.stop_loss_price:
                return True
        
        return False
