"""
strategy_runner.py - CORRIGIDO
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
        
        # CORREÇÃO: Sinais pendentes para a PRÓXIMA barra
        self.next_bar_buy_signal = False
        self.next_bar_sell_signal = False
        
        # Estado atual
        self.position_size = 0
        self.position_side = None
        self.entry_price = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        
        # Timezone do Brasil
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
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
        time.sleep(3)  # Aguardar conexão
    
    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30 minutos começou - CORRIGIDO"""
        if not self.current_price:
            logger.warning("⚠️ Sem preço atual para verificar barra")
            return False
        
        now_brazil = datetime.now(self.tz_brazil)
        
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
            logger.info(f"⏰ Primeira barra definida (BRT): {current_bar_start.strftime('%H:%M')}")
            return False
        
        # Se uma nova barra começou
        if current_bar_start > self.last_bar_timestamp:
            logger.info("=" * 60)
            logger.info(f"📊 NOVA BARRA 30m INICIADA (BRT): {current_bar_start.strftime('%H:%M')}")
            logger.info(f"   Preço de abertura: ${self.current_price:.2f}")
            
            # 1. PRIMEIRO: Executar sinais da barra ANTERIOR (se houver)
            # Esta é a implementação correta do buy_signal[1] do Pine Script
            self._execute_pending_signals()
            
            # 2. SEGUNDO: Processar barra anterior para detectar NOVOS sinais
            if self.current_bar_data:
                self._process_completed_bar()
            
            # 3. TERCEIRO: Iniciar nova barra
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
            logger.info(f"   Sinais pendentes para PRÓXIMA barra: BUY={self.next_bar_buy_signal}, SELL={self.next_bar_sell_signal}")
            logger.info("=" * 60)
            return True
        
        # Atualizar dados da barra atual (high, low, close)
        if self.current_bar_data:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False
    
    def _execute_pending_signals(self):
        """Executa sinais que foram detectados na barra ANTERIOR - CORRIGIDO"""
        # Obter preço de ABERTURA da barra atual (não o preço atual)
        entry_price = self.current_price  # Este é o preço no momento da abertura da barra
        
        logger.info(f"🔍 Verificando sinais pendentes para execução...")
        logger.info(f"   Preço de abertura da barra: ${entry_price:.2f}")
        logger.info(f"   Sinais pendentes: BUY={self.next_bar_buy_signal}, SELL={self.next_bar_sell_signal}")
        
        # Executar BUY se houver sinal pendente
        if self.next_bar_buy_signal:
            logger.info(f"🚀🚀🚀 EXECUTANDO ORDEM BUY (sinal da barra anterior)")
            logger.info(f"   Preço de entrada: ${entry_price:.2f}")
            
            # Fechar trade anterior se existir (inversão de posição)
            if self.current_trade_id:
                self.trade_history.close_trade(self.current_trade_id, entry_price)
                self.current_trade_id = None
            
            # Calcular quantidade
            quantity = self.okx_client.calculate_position_size()
            if quantity > 0:
                logger.info(f"   Quantidade calculada: {quantity:.4f} ETH")
                
                # Registrar nova trade no histórico
                trade_id = self.trade_history.add_trade(
                    side='buy',
                    entry_price=entry_price,
                    quantity=quantity
                )
                
                if trade_id:
                    self.current_trade_id = trade_id
                    self.position_size = quantity
                    self.position_side = 'long'
                    self.entry_price = entry_price
                    logger.info(f"   📝 Trade #{trade_id} registrada no histórico")
                    logger.info(f"   Posição: LONG {quantity:.4f} ETH @ ${entry_price:.2f}")
            
            # Resetar sinal
            self.next_bar_buy_signal = False
        
        # Executar SELL se houver sinal pendente
        elif self.next_bar_sell_signal:
            logger.info(f"🚀🚀🚀 EXECUTANDO ORDEM SELL (sinal da barra anterior)")
            logger.info(f"   Preço de entrada: ${entry_price:.2f}")
            
            # Fechar trade anterior se existir (inversão de posição)
            if self.current_trade_id:
                self.trade_history.close_trade(self.current_trade_id, entry_price)
                self.current_trade_id = None
            
            quantity = self.okx_client.calculate_position_size()
            if quantity > 0:
                logger.info(f"   Quantidade calculada: {quantity:.4f} ETH")
                
                # Registrar nova trade no histórico
                trade_id = self.trade_history.add_trade(
                    side='sell',
                    entry_price=entry_price,
                    quantity=quantity
                )
                
                if trade_id:
                    self.current_trade_id = trade_id
                    self.position_size = -quantity
                    self.position_side = 'short'
                    self.entry_price = entry_price
                    logger.info(f"   📝 Trade #{trade_id} registrada no histórico")
                    logger.info(f"   Posição: SHORT {quantity:.4f} ETH @ ${entry_price:.2f}")
            
            # Resetar sinal
            self.next_bar_sell_signal = False
        
        # Se não há sinais pendentes
        else:
            logger.info(f"   ⚪ Nenhum sinal pendente para executar")
    
    def _process_completed_bar(self):
        """Processa uma barra completa para detectar sinais - CORRIGIDO"""
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
            
            # CORREÇÃO: Sinais detectados AGORA serão executados na PRÓXIMA barra
            # Isso simula exatamente o comportamento do buy_signal[1] do Pine Script
            
            if buy_signal_raw:
                self.next_bar_buy_signal = True
                self.next_bar_sell_signal = False  # Resetar sinal oposto
                logger.info(f"   🟢 SINAL BUY DETECTADO! (executará na PRÓXIMA barra)")
            
            elif sell_signal_raw:
                self.next_bar_sell_signal = True
                self.next_bar_buy_signal = False  # Resetar sinal oposto
                logger.info(f"   🔴 SINAL SELL DETECTADO! (executará na PRÓXIMA barra)")
            
            else:
                logger.info(f"   ⚪ Nenhum sinal detectado nesta barra")
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador Pine Script não inicializado")
            return False
        
        logger.info("🚀 Iniciando Strategy Runner...")
        
        # Inicializar variável de log
        self.last_log_time = time.time()
        
        # Iniciar WebSocket
        self._start_websocket()
        
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
                
                # Definir último timestamp (convertendo para BRT)
                if historical_candles:
                    last_ts = historical_candles[-1]['timestamp'] / 1000
                    last_dt_utc = datetime.utcfromtimestamp(last_ts)
                    last_dt_brazil = last_dt_utc.replace(tzinfo=pytz.utc).astimezone(self.tz_brazil)
                    
                    # Arredondar para início da barra de 30m
                    minute = (last_dt_brazil.minute // 30) * 30
                    self.last_bar_timestamp = last_dt_brazil.replace(
                        minute=minute, 
                        second=0, 
                        microsecond=0
                    )
                    
                    self.bar_count = len(historical_candles)
                    logger.info(f"   ⏰ Última barra histórica (BRT): {self.last_bar_timestamp.strftime('%H:%M')}")
                    
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles)} candles históricos obtidos")
                
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
                "next_bar_buy": self.next_bar_buy_signal,
                "next_bar_sell": self.next_bar_sell_signal,
                "position_size": self.position_size,
                "position_side": self.position_side
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
            
            # Calcular tempo restante para próxima barra (BRT)
            now_brazil = datetime.now(self.tz_brazil)
            time_to_next_bar = (next_bar - now_brazil).total_seconds()
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
            "next_bar_buy_signal": self.next_bar_buy_signal,
            "next_bar_sell_signal": self.next_bar_sell_signal,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "entry_price": self.entry_price
        }
