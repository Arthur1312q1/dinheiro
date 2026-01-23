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
        self.trade_history = trade_history  # Recebe o histórico
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
                        return f.read()
            
            logger.error("Arquivo Pine Script não encontrado")
            return None
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None
    
    # WebSocket methods (mantenha os existentes)
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                self.current_price = float(ticker_data.get('last', 0))
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
            return True
        
        if current_bar_start > self.last_bar_timestamp:
            logger.info(f"📊 NOVA BARRA 30m: {current_bar_start.strftime('%H:%M')}")
            
            # Processar barra anterior
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
            return
        
        # Processar através do interpretador
        result = self.interpreter.process_candle(self.current_bar_data)
        
        buy_signal_raw = result.get('buy_signal_raw', False)
        sell_signal_raw = result.get('sell_signal_raw', False)
        
        # Atualizar flags persistentes
        if buy_signal_raw:
            self.pending_buy = True
            logger.info(f"   ✅ pendingBuy ATIVADO (executará na próxima barra)")
        
        if sell_signal_raw:
            self.pending_sell = True
            logger.info(f"   ✅ pendingSell ATIVADO (executará na próxima barra)")
        
        # SIMULAÇÃO: Executar trades baseado nos sinais
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"🚀 SIMULAÇÃO: EXECUTANDO BUY")
            logger.info(f"   Preço: ${self.current_bar_data['close']:.2f}")
            
            # Fechar trade anterior se existir
            if self.current_trade_id:
                self.trade_history.close_trade(self.current_trade_id, self.current_bar_data['close'])
                self.current_trade_id = None
            
            # Calcular quantidade
            quantity = self.okx_client.calculate_position_size(self.current_bar_data['close'])
            logger.info(f"   [SIMULAÇÃO] BUY {quantity:.4f} ETH")
            
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
            self.pending_buy = False
        
        elif self.pending_sell and self.position_size >= 0:
            logger.info(f"🚀 SIMULAÇÃO: EXECUTANDO SELL")
            logger.info(f"   Preço: ${self.current_bar_data['close']:.2f}")
            
            # Fechar trade anterior se existir
            if self.current_trade_id:
                self.trade_history.close_trade(self.current_trade_id, self.current_bar_data['close'])
                self.current_trade_id = None
            
            quantity = self.okx_client.calculate_position_size(self.current_bar_data['close'])
            logger.info(f"   [SIMULAÇÃO] SELL {quantity:.4f} ETH")
            
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
            self.pending_sell = False
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            return False
        
        # Iniciar WebSocket
        self._start_websocket()
        time.sleep(2)  # Aguardar conexão
        
        # Inicializar com candles históricos
        self._initialize_candle_buffer()
        
        self.is_running = True
        logger.info("🚀 Strategy Runner iniciado (MODO BARRAS 30m)")
        logger.info("⚠️  EXECUÇÃO EM MODO SIMULAÇÃO - Sem ordens reais")
        return True
    
    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos"""
        logger.info("📈 Inicializando com candles históricos...")
        
        historical_candles = self.okx_client.get_candles(limit=100)
        
        if len(historical_candles) >= 30:
            # Processar candles para aquecer indicadores
            for candle in historical_candles[-30:]:
                result = self.interpreter.process_candle(candle)
                if result.get('buy_signal_raw'):
                    self.pending_buy = True
                if result.get('sell_signal_raw'):
                    self.pending_sell = True
            
            logger.info("   🔧 Indicadores aquecidos (EMA/EC calculados)")
            
            # Definir último timestamp
            if historical_candles:
                last_ts = historical_candles[-1]['timestamp'] / 1000
                self.last_bar_timestamp = datetime.utcfromtimestamp(last_ts)
                self.bar_count = len(historical_candles)
                logger.info(f"   ⏰ Última barra: {self.last_bar_timestamp.strftime('%H:%M')}")
    
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
        
        # Verificar e atualizar barra
        self._check_and_update_bar()
        
        return {
            "signal": "HOLD",
            "current_price": self.current_price,
            "bar_count": self.bar_count,
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size
        }
    
    def get_strategy_status(self):
        """Retorna status da estratégia"""
        next_bar_time = None
        if self.last_bar_timestamp:
            next_bar = self.last_bar_timestamp + timedelta(minutes=self.timeframe_minutes)
            next_bar_time = next_bar.strftime('%H:%M:%S')
        
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": "BARRAS_30m",
            "simulation_mode": True,
            "current_price": self.current_price,
            "next_bar_at": next_bar_time,
            "bars_processed": self.bar_count,
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size,
            "position_side": self.position_side
        }
