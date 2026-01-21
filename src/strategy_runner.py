import os
import logging
import time
import threading
import json
import websocket
from typing import Dict, List, Optional
from datetime import datetime, timedelta

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
        self.initialization_complete = False
        self.ws = None
        self.ws_thread = None
        
        # --- NOVO: SIMULAÇÃO DO COMPORTAMENTO PINE SCRIPT ---
        self.timeframe_minutes = 30  # SEU TIMEFRAME
        self.last_bar_timestamp = None
        self.current_bar_data = None
        self.is_new_bar = False
        
        # --- ESTADO DA ESTRATÉGIA (IGUAL AO PINE) ---
        self.pending_buy = False      # Simula pendingBuy do Pine
        self.pending_sell = False     # Simula pendingSell do Pine  
        self.position_size = 0        # Simula strategy.position_size
        self.position_side = None     # 'long' ou 'short'
        self.bar_count = 0            # Contador de barras processadas
        
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado (MODO BARRAS 30m)")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        try:
            script_path = "strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            with open(script_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Erro ao ler arquivo Pine Script: {e}")
            return None
    
    # WebSocket (mantido para preço em tempo real)
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

    # --- NOVO: GERENCIAMENTO DE BARRAS DE 30 MINUTOS ---
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30 minutos começou"""
        if not self.current_price:
            return False
        
        now = datetime.utcnow()
        
        # Calcula o início da barra atual (arredonda para 30 minutos)
        current_bar_start = now.replace(
            minute=(now.minute // self.timeframe_minutes) * self.timeframe_minutes,
            second=0,
            microsecond=0
        )
        
        # Primeira execução
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
            logger.info(f"📊 Barra inicial: {current_bar_start.strftime('%H:%M')}")
            return True
        
        # Se mudou para uma nova barra
        if current_bar_start > self.last_bar_timestamp:
            logger.info(f"📊 NOVA BARRA 30m: {current_bar_start.strftime('%H:%M')}")
            
            # 1. Processa a barra anterior que acabou de fechar
            self._process_completed_bar()
            
            # 2. Inicia nova barra
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
        
        # Atualiza dados da barra atual
        if self.current_bar_data:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False

    # --- NOVO: PROCESSAMENTO DE BARRA COMPLETA (IGUAL PINE) ---
    def _process_completed_bar(self):
        """Processa uma barra completa de 30 minutos (igual TradingView)"""
        if not self.current_bar_data:
            return
        
        # Adiciona ao buffer (mantém 30 barras)
        self.candle_buffer.append(self.current_bar_data.copy())
        if len(self.candle_buffer) > 30:
            self.candle_buffer.pop(0)
        
        logger.debug(f"📈 Processando barra #{self.bar_count}: ${self.current_bar_data['close']:.2f}")
        
        # Processa através do interpretador
        result = self.interpreter.process_candle(self.current_bar_data)
        
        # --- LÓGICA IDÊNTICA AO SEU PINE SCRIPT ---
        # 1. Obtém sinais RAW desta barra (para usar na PRÓXIMA)
        buy_signal_raw = result.get('buy_signal_raw', False)
        sell_signal_raw = result.get('sell_signal_raw', False)
        
        # DEBUG: Mostra sinais detectados
        if buy_signal_raw:
            logger.debug(f"   [SINAL] buy_signal_raw = TRUE (barra {self.bar_count})")
        if sell_signal_raw:
            logger.debug(f"   [SINAL] sell_signal_raw = TRUE (barra {self.bar_count})")
        
        # 2. ATUALIZA FLAGS PERSISTENTES (igual Pine: nz(pendingBuy[1]))
        # Estes são os sinais que serão usados na PRÓXIMA barra
        if buy_signal_raw:
            self.pending_buy = True
            logger.info(f"   ✅ pendingBuy ATIVADO (executará na próxima barra)")
        
        if sell_signal_raw:
            self.pending_sell = True
            logger.info(f"   ✅ pendingSell ATIVADO (executará na próxima barra)")
        
        # 3. EXECUÇÃO: Só executa se:
        #    - Flag pendente = TRUE (setada na barra ANTERIOR)
        #    - Posição oposta ou flat (strategy.position_size <= 0 ou >= 0)
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"🚀 EXECUTANDO BUY (sinal da barra {self.bar_count-1})")
            logger.info(f"   Preço: ${self.current_bar_data['close']:.2f}")
            
            # Calcula tamanho (mas NÃO executa - modo simulação)
            position_size = self.okx_client.calculate_position_size()
            logger.info(f"   [SIMULAÇÃO] BUY {position_size:.4f} ETH")
            
            # Atualiza estado da posição (simulação)
            self.position_size = position_size
            self.position_side = 'long'
            
            # Zera flag após execução (igual Pine)
            self.pending_buy = False
            logger.info(f"   ✅ pendingBuy ZERADO")
        
        elif self.pending_sell and self.position_size >= 0:
            logger.info(f"🚀 EXECUTANDO SELL (sinal da barra {self.bar_count-1})")
            logger.info(f"   Preço: ${self.current_bar_data['close']:.2f}")
            
            position_size = self.okx_client.calculate_position_size()
            logger.info(f"   [SIMULAÇÃO] SELL {position_size:.4f} ETH")
            
            self.position_size = -position_size  # Negativo para short
            self.position_side = 'short'
            
            self.pending_sell = False
            logger.info(f"   ✅ pendingSell ZERADO")
        
        # Log do estado atual
        logger.debug(f"   [ESTADO] pendingBuy={self.pending_buy}, pendingSell={self.pending_sell}, position={self.position_size:.4f}")

    # --- MÉTODOS PRINCIPAIS ---
    def start(self):
        if not self.interpreter:
            return False
        
        # Inicia WebSocket para preço em tempo real
        self._start_websocket()
        time.sleep(2)  # Aguarda conexão
        
        # Inicializa com candles históricos (30 barras)
        self._initialize_candle_buffer()
        
        self.is_running = True
        self.initialization_complete = True
        
        logger.info("🚀 Strategy Runner iniciado (MODO BARRAS 30m)")
        logger.info("⚠️  EXECUÇÃO EM MODO SIMULAÇÃO - Sem ordens reais")
        return True

    def _initialize_candle_buffer(self):
        """Inicializa buffer com 30 candles históricos"""
        logger.info("📈 Inicializando com candles históricos (modo AQUECIMENTO)...")
        
        historical_candles = self.okx_client.get_candles(limit=100)
        
        if len(historical_candles) >= 30:
            self.candle_buffer = historical_candles[-30:]
            logger.info(f"   ✅ Buffer: {len(self.candle_buffer)} candles")
            
            # Processa candles APENAS para cálculos (ignora trades)
            for candle in self.candle_buffer:
                result = self.interpreter.process_candle(candle)
                # Apenas coleta sinais, NÃO executa
                if result.get('buy_signal_raw'):
                    self.pending_buy = True
                if result.get('sell_signal_raw'):
                    self.pending_sell = True
            
            logger.info("   🔧 Indicadores aquecidos (EMA/EC calculados)")
            
            # Define último timestamp processado
            if self.candle_buffer:
                last_ts = self.candle_buffer[-1]['timestamp'] / 1000
                self.last_bar_timestamp = datetime.utcfromtimestamp(last_ts)
                self.bar_count = len(self.candle_buffer)
                logger.info(f"   ⏰ Última barra: {self.last_bar_timestamp.strftime('%H:%M')}")
        else:
            logger.warning(f"⚠️  Apenas {len(historical_candles)} candles obtidos")
            self.candle_buffer = historical_candles

    def stop(self):
        self.is_running = False
        self.initialization_complete = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado")

    def run_strategy_realtime(self):
        """
        Loop principal - Roda a cada 30ms, mas só executa no fechamento da barra
        """
        if not self.is_running or not self.initialization_complete:
            return {"signal": "HOLD", "strength": 0}
        
        # Verifica se temos nova barra de 30min
        new_bar_started = self._check_and_update_bar()
        
        # Para o loop externo (main.py), sempre retorna HOLD
        # A execução real acontece apenas em _process_completed_bar()
        return {
            "signal": "HOLD",
            "strength": 0,
            "bar_count": self.bar_count,
            "current_price": self.current_price,
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size
        }

    def get_strategy_status(self):
        if not self.interpreter:
            return {"status": "not_initialized"}
        
        next_bar_time = None
        if self.last_bar_timestamp:
            next_bar = self.last_bar_timestamp + timedelta(minutes=self.timeframe_minutes)
            next_bar_time = next_bar.strftime('%H:%M:%S')
        
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": "BARRAS_30m",
            "simulation_mode": True,  # IMPORTANTE: Sem ordens reais
            "current_price": self.current_price,
            "next_bar_at": next_bar_time,
            "bars_processed": self.bar_count,
            "candle_buffer_size": len(self.candle_buffer),
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size,
            "position_side": self.position_side
        }
