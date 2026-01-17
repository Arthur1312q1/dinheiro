"""
strategy_runner.py - MODIFICADO PARA TEMPO REAL
"""
import os
import logging
import time
import threading
import json
from typing import Dict, List, Optional
import websocket  # <-- NOVA DEPENDÊNCIA: instale com 'pip install websocket-client'

from pine_engine import PineScriptInterpreter
from okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client: OKXClient):
        self.okx_client = okx_client
        self.interpreter = None
        self.is_running = False
        self.last_processed_candle_id = None  # ID único do último candle processado
        self.candle_buffer = []  # Buffer para manter os últimos 30 candles
        self.current_price = None  # Preço mais recente do WebSocket
        self.ws = None  # Objeto da conexão WebSocket
        self.ws_thread = None

        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado para modo tempo real (WebSocket).")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")

    def _load_pine_script(self) -> Optional[str]:
        """Carrega o código Pine Script do arquivo."""
        try:
            script_path = "strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            with open(script_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None

    # ==================== WEBSOCKET - NOVO ====================
    def _on_ws_message(self, ws, message):
        """Processa mensagens recebidas do WebSocket."""
        try:
            data = json.loads(message)
            
            # Canal 'tickers' fornece o último preço de negociação em tempo real[citation:1]
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                self.current_price = float(ticker_data.get('last', 0))
                
                # Log a cada 100 mensagens para não poluir
                if hasattr(self, '_tick_count'):
                    self._tick_count += 1
                    if self._tick_count % 100 == 0:
                        logger.debug(f"📡 Tick #{self._tick_count}: Preço via WS = ${self.current_price}")
                else:
                    self._tick_count = 1
                    
        except Exception as e:
            logger.error(f"Erro ao processar mensagem WS: {e}")

    def _on_ws_error(self, ws, error):
        logger.error(f"💥 Erro no WebSocket: {error}")

    def _on_ws_close(self, ws, close_status_code, close_msg):
        logger.warning(f"🔌 WebSocket fechado. Código: {close_status_code}, Msg: {close_msg}")
        self.ws = None

    def _on_ws_open(self, ws):
        logger.info("🌐 Conexão WebSocket estabelecida.")
        # Subscreve ao canal de tickers para ETH-USDT-SWAP[citation:1]
        subscribe_msg = {
            "op": "subscribe",
            "args": [{
                "channel": "tickers",
                "instId": "ETH-USDT-SWAP"
            }]
        }
        ws.send(json.dumps(subscribe_msg))
        logger.info("   📊 Inscrito no canal 'tickers' em tempo real.")

    def _start_websocket(self):
        """Inicia a conexão WebSocket em uma thread separada."""
        if self.ws and self.ws.sock and self.ws.sock.connected:
            return

        # URL do WebSocket público da OKX[citation:1]
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
        logger.info("Thread WebSocket iniciada.")

    def _stop_websocket(self):
        """Para a conexão WebSocket."""
        if self.ws:
            self.ws.close()
        self.ws = None

    # ==================== LÓGICA PRINCIPAL CORRIGIDA ====================
    def start(self):
        """Inicia o Strategy Runner e o WebSocket."""
        if not self.interpreter:
            return False
        
        self._start_websocket()
        time.sleep(2)  # Aguarda conexão estabelecer
        
        self.is_running = True
        self.last_processed_candle_id = None
        self.candle_buffer = []
        
        # Inicializa buffer com últimos 30 candles históricos (apenas uma vez)
        self._initialize_candle_buffer()
        
        logger.info("🚀 Strategy Runner iniciado em modo TEMPO REAL.")
        return True

    def _initialize_candle_buffer(self):
        """Busca os últimos 30 candles históricos para inicializar a estratégia."""
        logger.info("📈 Inicializando buffer com 30 candles históricos...")
        historical_candles = self.okx_client.get_candles(limit=30)
        
        if len(historical_candles) >= 30:
            self.candle_buffer = historical_candles[-30:]  # Mantém apenas os 30 mais recentes
            logger.info(f"   ✅ Buffer inicializado com {len(self.candle_buffer)} candles.")
            
            # **CRÍTICO:** Processa candles históricos apenas para inicializar indicadores (EMA, etc.)
            # MAS NÃO para gerar sinais de trade.
            for candle in self.candle_buffer[:-1]:  # Processa todos, exceto o ÚLTIMO
                self.interpreter.process_candle(candle)
            logger.info("   🔧 Indicadores da estratégia (EMA, etc.) aquecidos com dados históricos.")
        else:
            logger.warning("⚠️  Não foi possível obter candles suficientes para inicializar.")

    def stop(self):
        """Para o Strategy Runner e o WebSocket."""
        self.is_running = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado.")

    def run_strategy_realtime(self):
        """
        Loop principal OTIMIZADO para tempo real.
        Deve ser chamado em um loop externo rápido (ex: a cada 30ms).
        """
        if not self.is_running or not self.current_price:
            return {"signal": "HOLD", "strength": 0}

        # 1. Cria um candle 'virtual' para o momento atual com o preço do WebSocket
        #    Usa o preço atual para 'open', 'high', 'low', 'close'.
        current_timestamp = int(time.time() * 1000)
        current_candle = {
            "timestamp": current_timestamp,
            "open": self.current_price,
            "high": self.current_price,
            "low": self.current_price,
            "close": self.current_price,
            "volume": 0  # Volume não disponível em tempo real via ticker
        }

        # 2. **EVITA BUG DE TRADE HISTÓRICO:**
        #    Gera um ID único para este candle e verifica se já foi processado.
        candle_id = f"{current_timestamp}_{self.current_price}"
        if candle_id == self.last_processed_candle_id:
            return {"signal": "HOLD", "strength": 0}  # Já processou este tick

        # 3. Atualiza o buffer: substitui o último candle 'corrente' por este novo
        if len(self.candle_buffer) >= 30:
            self.candle_buffer[-1] = current_candle
        else:
            self.candle_buffer.append(current_candle)

        # 4. Executa a estratégia APENAS no candle atual (o último do buffer)
        result = self.interpreter.process_candle(current_candle)
        self.last_processed_candle_id = candle_id  # Marca como processado

        # 5. Se houver sinal, executa a ordem (aqui está a lógica de trade REAL)
        if result['signal'] in ['BUY', 'SELL'] and result['strength'] > 0:
            logger.info(f"🚨 SINAL DE TRADE EM TEMPO REAL: {result['signal']} a ${self.current_price}")
            position_size = self.okx_client.calculate_position_size()
            if position_size > 0:
                success = self.okx_client.place_order(side=result['signal'], quantity=position_size)
                if success:
                    logger.info(f"✅ Ordem {result['signal']} executada na OKX (Tempo Real).")
                else:
                    logger.error(f"❌ Falha na ordem {result['signal']}.")

        return result

    def get_strategy_status(self) -> Dict:
        """Retorna o status atual da estratégia."""
        if not self.interpreter:
            return {"status": "not_initialized"}
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": "REAL_TIME_WEBSOCKET",
            "current_price": self.current_price,
            "candle_buffer_size": len(self.candle_buffer),
            "last_signal": self.last_processed_candle_id,
        }
