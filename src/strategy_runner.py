import os
import logging
import time
import threading
import json
import websocket
from datetime import datetime, timedelta

from .pine_engine import PineScriptInterpreter
from .okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        self.interpreter = None
        self.is_running = False
        self.current_price = None
        self.current_trade_id = None
        
        # Configurações
        self.timeframe_minutes = 30
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.bar_count = 0
        
        # Estado da estratégia
        self.pending_buy = False
        self.pending_sell = False
        self.position_size = 0
        self.position_side = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        
        # Controle de tempo
        self.last_log_time = time.time()
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado com histórico")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        try:
            # Procurar arquivo em vários locais
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "../strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.info(f"✅ Arquivo Pine Script encontrado: {path} ({len(content)} bytes)")
                        return content
            
            logger.error("Arquivo Pine Script não encontrado em nenhum dos locais:")
            for path in possible_paths:
                logger.error(f"  - {path}")
            return None
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None
    
    # WebSocket methods
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                self.current_price = float(ticker_data.get('last', 0))
                
                # Log periódico de preço (a cada 60 segundos)
                current_time = time.time()
                if current_time - self.last_log_time > 60:
                    logger.info(f"📈 Preço atual: ${self.current_price:.2f}")
                    self.last_log_time = current_time
                    
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
        logger.info("📊 Inscrito no canal 'tickers' (tempo real)")

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

    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30 minutos começou"""
        if not self.current_price:
            logger.warning("⚠️ Sem preço atual para verificar barra")
            return False
        
        now = datetime.utcnow()
        
        current_bar_start = now.replace(
            minute=(now.minute // self.timeframe_minutes) * self.timeframe_minutes,
            second=0,
            microsecond=0
        )
        
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
            logger.info(f"⏰ Primeira barra definida: {current_bar_start.strftime('%H:%M')}")
            return True
        
        if current_bar_start > self.last_bar_timestamp:
            logger.info(f"📊 NOVA BARRA 30m INICIADA: {current_bar_start.strftime('%H:%M')}")
            logger.info(f"   Preço de abertura: ${self.current_price:.2f}")
            
            # Processar barra anterior (se existir)
            if self.current_bar_data:
                self._process_completed_bar()
            
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
            return True
        
        # Atualizar dados da barra atual
        if self.current_bar_data:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False
    
    def _process_completed_bar(self):
        """Processa uma barra completa (SIMULAÇÃO)"""
        if not self.current_bar_data:
            logger.warning("⚠️ Nenhum dado de barra para processar")
            return
        
        logger.info(f"📈 Processando barra #{self.bar_count}...")
        logger.info(f"   Preço de fechamento: ${self.current_bar_data['close']:.2f}")
        
        try:
            # Processar através do interpretador
            result = self.interpreter.process_candle(self.current_bar_data)
            
            buy_signal_raw = result.get('buy_signal_raw', False)
            sell_signal_raw = result.get('sell_signal_raw', False)
            
            logger.info(f"   EMA: {result['ema']:.2f}, EC: {result['ec']:.2f}, Erro: {result['error_pct']:.2f}%")
            
            # Atualizar flags persistentes
            if buy_signal_raw:
                self.pending_buy = True
                self.pending_sell = False  # Resetar sinal oposto
                logger.info(f"   🟢 SINAL BUY DETECTADO! (executará na próxima barra)")
            
            if sell_signal_raw:
                self.pending_sell = True
                self.pending_buy = False  # Resetar sinal oposto
                logger.info(f"   🔴 SINAL SELL DETECTADO! (executará na próxima barra)")
            
            # Verificar se não há sinal
            if not buy_signal_raw and not sell_signal_raw:
                logger.info(f"   ⚪ Nenhum sinal detectado")
            
            # SIMULAÇÃO: Executar trades baseado nos sinais da barra ANTERIOR
            if self.pending_buy and self.position_size <= 0:
                logger.info(f"🚀🚀🚀 SIMULAÇÃO: EXECUTANDO ORDEM BUY")
                logger.info(f"   Preço de entrada: ${self.current_bar_data['close']:.2f}")
                
                # Fechar trade anterior se existir
                if self.current_trade_id:
                    self.trade_history.close_trade(self.current_trade_id, self.current_bar_data['close'])
                    self.current_trade_id = None
                
                # Calcular quantidade
                quantity = self.okx_client.calculate_position_size()
                if quantity > 0:
                    logger.info(f"   Quantidade calculada: {quantity:.4f} ETH")
                    
                    # Registrar nova trade no histórico
                    trade_id = self.trade_history.add_trade(
                        side='buy',
                        entry_price=self.current_bar_data['close'],
                        quantity=quantity
                    )
                    
                    if trade_id:
                        self.current_trade_id = trade_id
                        logger.info(f"   📝 Trade #{trade_id} registrada no histórico")
                    
                    # Atualizar estado
                    self.position_size = quantity
                    self.position_side = 'long'
                    self.pending_buy = False  # Resetar após executar
                else:
                    logger.error("❌ Quantidade inválida para BUY")
                    self.pending_buy = False
            
            elif self.pending_sell and self.position_size >= 0:
                logger.info(f"🚀🚀🚀 SIMULAÇÃO: EXECUTANDO ORDEM SELL")
                logger.info(f"   Preço de entrada: ${self.current_bar_data['close']:.2f}")
                
                # Fechar trade anterior se existir
                if self.current_trade_id:
                    self.trade_history.close_trade(self.current_trade_id, self.current_bar_data['close'])
                    self.current_trade_id = None
                
                quantity = self.okx_client.calculate_position_size()
                if quantity > 0:
                    logger.info(f"   Quantidade calculada: {quantity:.4f} ETH")
                    
                    # Registrar nova trade no histórico
                    trade_id = self.trade_history.add_trade(
                        side='sell',
                        entry_price=self.current_bar_data['close'],
                        quantity=quantity
                    )
                    
                    if trade_id:
                        self.current_trade_id = trade_id
                        logger.info(f"   📝 Trade #{trade_id} registrada no histórico")
                    
                    # Atualizar estado
                    self.position_size = -quantity
                    self.position_side = 'short'
                    self.pending_sell = False  # Resetar após executar
                else:
                    logger.error("❌ Quantidade inválida para SELL")
                    self.pending_sell = False
            
            # Log do estado atual
            if self.position_size > 0:
                logger.info(f"   📊 Posição atual: LONG {self.position_size:.4f} ETH")
            elif self.position_size < 0:
                logger.info(f"   📊 Posição atual: SHORT {abs(self.position_size):.4f} ETH")
            else:
                logger.info(f"   📊 Posição atual: Nenhuma")
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador Pine Script não inicializado")
            return False
        
        logger.info("🚀 Iniciando Strategy Runner...")
        
        # Iniciar WebSocket
        self._start_websocket()
        time.sleep(3)  # Aguardar conexão
        
        # Inicializar com candles históricos
        self._initialize_candle_buffer()
        
        self.is_running = True
        logger.info("✅ Strategy Runner iniciado (MODO BARRAS 30m)")
        logger.info("⚠️  EXECUÇÃO EM MODO SIMULAÇÃO - Sem ordens reais")
        return True
    
    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos"""
        logger.info("📈 Inicializando com candles históricos...")
        
        try:
            historical_candles = self.okx_client.get_candles(limit=100)
            
            if len(historical_candles) >= 30:
                logger.info(f"✅ {len(historical_candles)} candles históricos obtidos")
                
                # Processar candles para aquecer indicadores
                processed_count = 0
                for candle in historical_candles:
                    result = self.interpreter.process_candle(candle)
                    processed_count += 1
                    
                    # Log dos primeiros e últimos candles
                    if processed_count <= 3 or processed_count >= len(historical_candles) - 2:
                        logger.info(f"   Candle {processed_count}: Preço=${candle['close']:.2f}, "
                                  f"EMA={result['ema']:.2f}, EC={result['ec']:.2f}")
                
                logger.info(f"   🔧 {processed_count} candles processados para aquecimento")
                
                # Definir último timestamp
                if historical_candles:
                    last_ts = historical_candles[-1]['timestamp'] / 1000
                    self.last_bar_timestamp = datetime.utcfromtimestamp(last_ts)
                    self.bar_count = len(historical_candles)
                    logger.info(f"   ⏰ Última barra histórica: {self.last_bar_timestamp.strftime('%H:%M')}")
                    
                    # Verificar se há sinais pendentes após o aquecimento
                    if self.interpreter.series_data['pendingBuy'].current() > 0:
                        self.pending_buy = True
                        logger.info("   ⚠️ Sinal BUY pendente detectado após aquecimento")
                    
                    if self.interpreter.series_data['pendingSell'].current() > 0:
                        self.pending_sell = True
                        logger.info("   ⚠️ Sinal SELL pendente detectado após aquecimento")
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles)} candles históricos obtidos (mínimo 30 recomendado)")
                
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar candles históricos: {e}")
    
    def stop(self):
        """Para o strategy runner"""
        # Fechar trade aberta se existir
        if self.current_trade_id and self.current_price:
            self.trade_history.close_trade(self.current_trade_id, self.current_price)
        
        self.is_running = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado")
    
    def run_strategy_realtime(self):
        """Executa a estratégia em tempo real"""
        if not self.is_running:
            return {"signal": "HOLD"}
        
        try:
            # Verificar e atualizar barra
            new_bar = self._check_and_update_bar()
            
            return {
                "signal": "HOLD",
                "new_bar": new_bar,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "pending_buy": self.pending_buy,
                "pending_sell": self.pending_sell,
                "position_size": self.position_size
            }
            
        except Exception as e:
            logger.error(f"Erro em run_strategy_realtime: {e}")
            return {"signal": "HOLD", "error": str(e)}
    
    def get_strategy_status(self):
        """Retorna status da estratégia"""
        next_bar_time = None
        time_to_next_bar = None
        
        if self.last_bar_timestamp:
            next_bar = self.last_bar_timestamp + timedelta(minutes=self.timeframe_minutes)
            next_bar_time = next_bar.strftime('%H:%M:%S')
            
            # Calcular tempo restante para próxima barra
            now = datetime.utcnow()
            time_to_next_bar = (next_bar - now).total_seconds()
            if time_to_next_bar < 0:
                time_to_next_bar = 0
        
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": "BARRAS_30m",
            "simulation_mode": True,
            "current_price": self.current_price,
            "next_bar_at": next_bar_time,
            "time_to_next_bar_seconds": time_to_next_bar,
            "bars_processed": self.bar_count,
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size,
            "position_side": self.position_side
        }
    
    def get_detailed_status(self):
        """Retorna status detalhado para diagnóstico"""
        ws_status = "connected" if self.ws and self.ws.sock and self.ws.sock.connected else "disconnected"
        
        # Obter candle_count de forma segura
        candles_processed = 0
        if self.interpreter:
            try:
                if hasattr(self.interpreter, 'candle_count'):
                    candles_processed = self.interpreter.candle_count
            except:
                candles_processed = 0
        
        return {
            "status": "running" if self.is_running else "stopped",
            "websocket": ws_status,
            "current_price": self.current_price,
            "bar_count": self.bar_count,
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size,
            "last_bar_time": self.last_bar_timestamp.strftime('%H:%M:%S') if self.last_bar_timestamp else None,
            "candles_processed": candles_processed
        }
