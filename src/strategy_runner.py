import os
import logging
import time
import threading
import json
import websocket
from typing import Dict, List, Optional

from pine_engine import PineScriptInterpreter
from okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client: OKXClient):
        self.okx_client = okx_client
        self.interpreter = None
        self.is_running = False
        self.current_price = None
        self.candle_buffer = []
        self.last_processed_candle_id = None
        self.initialization_complete = False
        self.ws = None
        self.ws_thread = None
        
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        """Carrega o código Pine Script"""
        try:
            script_path = "strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            with open(script_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None
    
    # WebSocket
    def _on_ws_message(self, ws, message):
        """Processa mensagens do WebSocket"""
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
        logger.info("📊 Inscrito no canal 'tickers'")

    def _start_websocket(self):
        """Inicia a conexão WebSocket"""
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
        """Para a conexão WebSocket"""
        if self.ws:
            self.ws.close()
        self.ws = None

    # Lógica Principal
    def start(self):
        """Inicia o Strategy Runner"""
        if not self.interpreter:
            return False
        
        # Inicia WebSocket
        self._start_websocket()
        time.sleep(2)
        
        # Inicializa buffer com candles históricos (SEM executar trades)
        self._initialize_candle_buffer()
        
        self.is_running = True
        self.initialization_complete = True
        
        logger.info("🚀 Strategy Runner iniciado")
        return True

    def _initialize_candle_buffer(self):
        """Busca candles históricos APENAS para aquecer indicadores"""
        logger.info("📈 Aquecendo estratégia com candles históricos...")
        logger.info("⚠️  Sinais durante aquecimento serão IGNORADOS")
        
        historical_candles = self.okx_client.get_candles(limit=100)
        
        if len(historical_candles) >= 30:
            self.candle_buffer = historical_candles[-30:]
            logger.info(f"✅ Buffer inicializado com {len(self.candle_buffer)} candles")
            
            # Processa candles APENAS para cálculos, ignora sinais
            for i, candle in enumerate(self.candle_buffer):
                # Marca como aquecimento nos primeiros 30 candles
                is_warming_up = i < 30
                result = self.interpreter.process_candle(candle, is_warming_up=is_warming_up)
                
                if result.get('signal') != 'HOLD' and is_warming_up:
                    logger.debug(f"   [IGNORADO] Sinal {result['signal']} durante aquecimento")
            
            logger.info("🔧 Indicadores aquecidos com sucesso")
            logger.info("✅ ESTRATÉGIA PRONTA PARA OPERAR")
        else:
            logger.warning(f"⚠️  Apenas {len(historical_candles)} candles obtidos")
            self.candle_buffer = historical_candles

    def stop(self):
        """Para o Strategy Runner"""
        self.is_running = False
        self.initialization_complete = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado")

    def run_strategy_realtime(self):
        """Loop principal para tempo real"""
        if not self.is_running or not self.initialization_complete:
            return {"signal": "HOLD", "strength": 0}
        
        if not self.current_price:
            return {"signal": "HOLD", "strength": 0}
        
        # Cria candle atual
        current_timestamp = int(time.time() * 1000)
        current_candle = {
            "timestamp": current_timestamp,
            "open": self.current_price,
            "high": self.current_price,
            "low": self.current_price,
            "close": self.current_price,
            "volume": 0
        }

        # Evita processamento duplicado
        candle_id = f"{current_timestamp}_{self.current_price}"
        if candle_id == self.last_processed_candle_id:
            return {"signal": "HOLD", "strength": 0}

        # Atualiza buffer
        if len(self.candle_buffer) >= 30:
            self.candle_buffer[-1] = current_candle
        else:
            self.candle_buffer.append(current_candle)

        # Executa estratégia (NÃO ignora sinais agora)
        result = self.interpreter.process_candle(current_candle, is_warming_up=False)
        self.last_processed_candle_id = candle_id

        # Executa ordem se houver sinal
        if result['signal'] in ['BUY', 'SELL'] and result['strength'] > 0:
            logger.info(f"🚨 SINAL EM TEMPO REAL: {result['signal']} a ${self.current_price}")
            position_size = self.okx_client.calculate_position_size()
            if position_size > 0:
                success = self.okx_client.place_order(side=result['signal'], quantity=position_size)
                if success:
                    logger.info(f"✅ Ordem {result['signal']} executada")
                else:
                    logger.error(f"❌ Falha na ordem {result['signal']}")

        return result

    def get_strategy_status(self):
        """Retorna status atual"""
        if not self.interpreter:
            return {"status": "not_initialized"}
        return {
            "status": "running" if self.is_running else "stopped",
            "current_price": self.current_price,
            "candle_buffer_size": len(self.candle_buffer),
            "initialization_complete": self.initialization_complete,
        }
