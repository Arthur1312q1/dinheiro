[file name]: web_socket_manager.py
[file content begin]
import websocket
import threading
import json
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class OKXWebSocketManager:
    def __init__(self):
        self.ws = None
        self.ws_thread = None
        self.is_connected = False
        self.current_price = None
        self.last_update = None
        
    def _on_message(self, ws, message):
        """Processa mensagens do WebSocket"""
        try:
            data = json.loads(message)
            
            # Canal de tickers
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                if ticker_data:
                    self.current_price = float(ticker_data.get('last', 0))
                    self.last_update = datetime.now()
                    
                    # Log a cada 30 segundos para não poluir
                    current_minute = datetime.now().minute
                    if current_minute % 5 == 0:  # Log a cada 5 minutos
                        logger.info(f"📈 Preço atual: ${self.current_price:.2f}")
            
            # Evento de ping/pong
            elif data.get('event') == 'pong':
                logger.debug("🏓 Pong recebido")
                
        except Exception as e:
            logger.error(f"Erro ao processar mensagem WS: {e}")
    
    def _on_error(self, ws, error):
        logger.error(f"💥 Erro no WebSocket: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"🔌 WebSocket fechado: {close_status_code} - {close_msg}")
        self.is_connected = False
        self.current_price = None
        
    def _on_open(self, ws):
        logger.info("🌐 Conexão WebSocket OKX estabelecida")
        
        # Subscribe to ETH-USDT-SWAP ticker
        subscribe_msg = {
            "op": "subscribe",
            "args": [
                {
                    "channel": "tickers",
                    "instId": "ETH-USDT-SWAP"
                }
            ]
        }
        
        ws.send(json.dumps(subscribe_msg))
        self.is_connected = True
        logger.info("📊 Inscrito no canal 'tickers' (ETH-USDT-SWAP)")
    
    def connect(self):
        """Conecta ao WebSocket da OKX"""
        if self.is_connected:
            return True
            
        try:
            websocket_url = "wss://ws.okx.com:8443/ws/v5/public"
            
            self.ws = websocket.WebSocketApp(
                websocket_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )
            
            # Configurar thread
            self.ws_thread = threading.Thread(
                target=self._run_websocket,
                daemon=True
            )
            self.ws_thread.start()
            
            # Aguardar conexão
            for _ in range(30):  # Timeout de 30 segundos
                if self.is_connected:
                    logger.info("✅ WebSocket conectado com sucesso")
                    return True
                time.sleep(1)
                
            logger.error("❌ Timeout ao conectar WebSocket")
            return False
            
        except Exception as e:
            logger.error(f"❌ Erro ao conectar WebSocket: {e}")
            return False
    
    def _run_websocket(self):
        """Executa o WebSocket"""
        try:
            self.ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            logger.error(f"Erro no run_forever: {e}")
    
    def disconnect(self):
        """Desconecta o WebSocket"""
        if self.ws:
            self.ws.close()
        self.is_connected = False
        logger.info("🔌 WebSocket desconectado")
    
    def get_current_price(self):
        """Retorna o preço atual"""
        return self.current_price
    
    def is_price_fresh(self, max_age_seconds=30):
        """Verifica se o preço é recente"""
        if not self.last_update:
            return False
        age = (datetime.now() - self.last_update).total_seconds()
        return age < max_age_seconds
[file content end]
