"""
strategy_runner.py - VERSÃO FINAL COM DIAGNÓSTICO DETALHADO
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
        
        # Parâmetros da estratégia
        self.fixedSL = 2000  # pontos
        self.fixedTP = 55    # pontos
        self.risk = 0.01     # 1%
        self.mintick = 0.01  # tick mínimo do ETH/USDT
        
        # Stop Loss e Take Profit
        self.stop_loss_price = None
        self.take_profit_price = None
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        self.last_log_time = time.time()
        
        # Timezone
        self.tz_utc = pytz.UTC
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # DIAGNÓSTICO
        self.last_signal_check = None
        self.signal_history = []
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado com interpretador Pine Script")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        """Carrega o código Pine Script do arquivo"""
        try:
            # Procurar arquivo em vários locais
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "../strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.info(f"✅ Arquivo Pine Script encontrado: {path} ({len(content)} bytes)")
                        return content
            
            logger.error("❌ Arquivo Pine Script não encontrado")
            return None
        except Exception as e:
            logger.error(f"❌ Erro ao ler arquivo Pine Script: {e}")
            return None
    
    def _calculate_position_size(self, entry_price):
        """Calcula tamanho da posição baseado no risco"""
        try:
            balance = self.okx_client.get_balance()
            risk_amount = self.risk * balance
            stop_loss_usdt = self.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Limitar ao saldo disponível
            max_quantity = balance / entry_price * 0.95
            if quantity > max_quantity:
                quantity = max_quantity
            
            return round(quantity, 4)
        except Exception as e:
            logger.error(f"Erro ao calcular tamanho da posição: {e}")
            return 0
    
    def _open_position(self, side, entry_price):
        """Abre uma nova posição"""
        try:
            # Verificar se já está na mesma posição
            if (side == 'buy' and self.position_side == 'long') or (side == 'sell' and self.position_side == 'short'):
                logger.info(f"⏭️  Já está na posição {side.upper()}, ignorando")
                return False
            
            # Se já tem posição oposta, fechar primeiro
            if self.position_size != 0:
                logger.info(f"🔀 Fechando posição {self.position_side} para abrir {side}")
                self._close_position(entry_price, "inversao")
            
            # Calcular quantidade
            quantity = self._calculate_position_size(entry_price)
            if quantity <= 0:
                logger.error("❌ Quantidade inválida")
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
                
                # Calcular Stop Loss e Take Profit
                if side == 'buy':
                    self.stop_loss_price = entry_price - (self.fixedSL * self.mintick)
                    self.take_profit_price = entry_price + (self.fixedTP * self.mintick)
                else:
                    self.stop_loss_price = entry_price + (self.fixedSL * self.mintick)
                    self.take_profit_price = entry_price - (self.fixedTP * self.mintick)
                
                logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {abs(quantity):.4f} ETH @ ${entry_price:.2f}")
                logger.info(f"   Stop Loss: ${self.stop_loss_price:.2f}")
                logger.info(f"   Take Profit: ${self.take_profit_price:.2f}")
                
                return True
            
            return False
        except Exception as e:
            logger.error(f"Erro ao abrir posição: {e}")
            return False
    
    def _close_position(self, exit_price, reason=""):
        """Fecha a posição atual"""
        if not self.trade_id or self.position_size == 0:
            return False
        
        try:
            success = self.trade_history.close_trade(self.trade_id, exit_price)
            if success:
                logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${exit_price:.2f} ({reason})")
                self.position_size = 0
                self.position_side = None
                self.entry_price = None
                self.trade_id = None
                self.stop_loss_price = None
                self.take_profit_price = None
                return True
            return False
        except Exception as e:
            logger.error(f"Erro ao fechar posição: {e}")
            return False
    
    def _check_stop_take(self):
        """Verifica se Stop Loss ou Take Profit foram atingidos"""
        if not self.position_size or not self.current_price:
            return
        
        try:
            if self.position_side == 'long':
                if self.stop_loss_price and self.current_price <= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS ATINGIDO (LONG): ${self.current_price:.2f} <= ${self.stop_loss_price:.2f}")
                    self._close_position(self.current_price, "stop_loss")
                    return
                
                if self.take_profit_price and self.current_price >= self.take_profit_price:
                    logger.info(f"🎯 TAKE PROFIT ATINGIDO (LONG): ${self.current_price:.2f} >= ${self.take_profit_price:.2f}")
                    self._close_position(self.current_price, "take_profit")
                    return
            
            elif self.position_side == 'short':
                if self.stop_loss_price and self.current_price >= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS ATINGIDO (SHORT): ${self.current_price:.2f} >= ${self.stop_loss_price:.2f}")
                    self._close_position(self.current_price, "stop_loss")
                    return
                
                if self.take_profit_price and self.current_price <= self.take_profit_price:
                    logger.info(f"🎯 TAKE PROFIT ATINGIDO (SHORT): ${self.current_price:.2f} <= ${self.take_profit_price:.2f}")
                    self._close_position(self.current_price, "take_profit")
                    return
                    
        except Exception as e:
            logger.error(f"Erro ao verificar stop/take: {e}")
    
    def _process_pending_signals(self):
        """Processa sinais pendentes com delay de 1 barra"""
        if not self.current_price:
            return
        
        logger.info(f"🔍 Processando sinais pendentes...")
        logger.info(f"   Preço atual: ${self.current_price:.2f}")
        logger.info(f"   Sinais: BUY={self.pending_buy_signal}, SELL={self.pending_sell_signal}")
        logger.info(f"   Posição atual: {self.position_side} {abs(self.position_size):.4f} ETH")
        
        # BUY: só executa se position_size <= 0 (flat ou short)
        if self.pending_buy_signal:
            if self.position_size <= 0:
                logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior)")
                self._open_position('buy', self.current_price)
            else:
                logger.info(f"⏭️  BUY ignorado (já em LONG)")
            
            self.pending_buy_signal = False
        
        # SELL: só executa se position_size >= 0 (flat ou long)
        elif self.pending_sell_signal:
            if self.position_size >= 0:
                logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior)")
                self._open_position('sell', self.current_price)
            else:
                logger.info(f"⏭️  SELL ignorado (já em SHORT)")
            
            self.pending_sell_signal = False
    
    # WebSocket methods
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                self.current_price = float(ticker_data.get('last', 0))
                
                # Verificar Stop Loss/Take Profit
                self._check_stop_take()
                
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
        time.sleep(3)
    
    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30 minutos começou"""
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
            
            # 1. PRIMEIRO: Executar sinais da barra ANTERIOR
            self._process_pending_signals()
            
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
            logger.info("=" * 60)
            return True
        
        # Atualizar dados da barra atual
        if self.current_bar_data:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False
    
    def _process_completed_bar(self):
        """Processa uma barra completa para detectar sinais"""
        if not self.current_bar_data:
            logger.warning("⚠️ Nenhum dado de barra para processar")
            return
        
        logger.info(f"📈 Processando barra #{self.bar_count}...")
        logger.info(f"   Preço de fechamento: ${self.current_bar_data['close']:.2f}")
        
        try:
            # Processar através do interpretador
            result = self.interpreter.process_candle(self.current_bar_data)
            
            # DIAGNÓSTICO DETALHADO
            logger.info(f"   EMA: {result['ema']:.2f}, EC: {result['ec']:.2f}")
            logger.info(f"   EC anterior: {result.get('ec_prev', 0):.2f}")
            logger.info(f"   EMA anterior: {result.get('ema_prev', 0):.2f}")
            logger.info(f"   Erro: {result['error_pct']:.2f}%")
            
            # Verificar sinais do interpretador
            pending_buy = result.get('pending_buy', False)
            pending_sell = result.get('pending_sell', False)
            buy_signal_current = result.get('buy_signal_current', False)
            sell_signal_current = result.get('sell_signal_current', False)
            
            logger.info(f"   Sinais na barra atual: BUY={buy_signal_current}, SELL={sell_signal_current}")
            logger.info(f"   Sinais pendentes: BUY={pending_buy}, SELL={pending_sell}")
            
            # Registrar para diagnóstico
            signal_info = {
                'timestamp': datetime.now().isoformat(),
                'bar_count': self.bar_count,
                'price': self.current_bar_data['close'],
                'ema': result['ema'],
                'ec': result['ec'],
                'buy_signal_current': buy_signal_current,
                'sell_signal_current': sell_signal_current,
                'pending_buy': pending_buy,
                'pending_sell': pending_sell
            }
            self.signal_history.append(signal_info)
            if len(self.signal_history) > 10:
                self.signal_history.pop(0)
            
            # Atualizar sinais pendentes para a PRÓXIMA barra
            if pending_buy:
                self.pending_buy_signal = True
                self.pending_sell_signal = False  # Resetar sinal oposto
                logger.info(f"   🟢 SINAL BUY PENDENTE DETECTADO! (executará na próxima barra)")
            
            if pending_sell:
                self.pending_sell_signal = True
                self.pending_buy_signal = False  # Resetar sinal oposto
                logger.info(f"   🔴 SINAL SELL PENDENTE DETECTADO! (executará na próxima barra)")
            
            if not pending_buy and not pending_sell:
                logger.info(f"   ⚪ Nenhum sinal pendente detectado")
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador Pine Script não inicializado")
            return False
        
        logger.info("🚀 Iniciando Strategy Runner...")
        logger.info("📊 Estratégia: Adaptive Zero Lag EMA v2")
        
        # Iniciar WebSocket
        self._start_websocket()
        
        # Aguardar preço atual
        logger.info("⏳ Aguardando preço atual do WebSocket...")
        for _ in range(30):
            if self.current_price is not None:
                break
            time.sleep(1)
        
        if self.current_price is None:
            logger.error("❌ Não foi possível obter preço atual")
            return False
        
        logger.info(f"✅ Preço atual obtido: ${self.current_price:.2f}")
        
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
                    if processed_count <= 5 or processed_count >= len(historical_candles) - 5:
                        logger.info(f"   Candle {processed_count}: Preço=${candle['close']:.2f}, "
                                  f"EMA={result['ema']:.2f}, EC={result['ec']:.2f}")
                
                logger.info(f"   🔧 {processed_count} candles processados para aquecimento")
                
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
                    logger.info(f"   ⏰ Última barra histórica (BRT): {self.last_bar_timestamp.strftime('%H:%M')}")
                    
            else:
                logger.warning(f"⚠️ Apenas {len(historical_candles)} candles históricos obtidos")
                
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar candles históricos: {e}")
    
    def stop(self):
        """Para o strategy runner"""
        # Fechar posição aberta se existir
        if self.position_size != 0 and self.current_price:
            self._close_position(self.current_price, "stop_bot")
        
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
            
            # Log periódico a cada 30 segundos
            current_time = time.time()
            if current_time - self.last_log_time > 30:
                if self.current_price:
                    logger.info(f"📈 Status: ${self.current_price:.2f} | "
                              f"Posição: {self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH")
                self.last_log_time = current_time
            
            return {
                "signal": "HOLD",
                "new_bar": new_bar,
                "current_price": self.current_price,
                "bar_count": self.bar_count,
                "pending_buy": self.pending_buy_signal,
                "pending_sell": self.pending_sell_signal,
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
            "pending_buy_signal": self.pending_buy_signal,
            "pending_sell_signal": self.pending_sell_signal,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss_price,
            "take_profit": self.take_profit_price,
            "signal_history": self.signal_history[-5:] if self.signal_history else []
        }
    
    def get_detailed_diagnostic(self):
        """Retorna diagnóstico detalhado"""
        diagnostic = {
            "current_price": self.current_price,
            "last_bar_timestamp": self.last_bar_timestamp.isoformat() if self.last_bar_timestamp else None,
            "bar_count": self.bar_count,
            "interpreter_initialized": self.interpreter is not None,
            "candle_count": self.interpreter.candle_count if self.interpreter else 0,
            "pending_buy_signal": self.pending_buy_signal,
            "pending_sell_signal": self.pending_sell_signal,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "entry_price": self.entry_price,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "websocket_connected": self.ws is not None,
            "current_time_brazil": datetime.now(self.tz_brazil).isoformat()
        }
        
        # Adicionar informações do interpretador se disponível
        if self.interpreter:
            diagnostic.update({
                "interpreter_params": self.interpreter.params,
                "interpreter_series_count": len(self.interpreter.series_data),
                "ec_current": self.interpreter.series_data['EC'].current() if 'EC' in self.interpreter.series_data else 0,
                "ema_current": self.interpreter.series_data['EMA'].current() if 'EMA' in self.interpreter.series_data else 0,
                "ec_prev": self.interpreter.series_data['EC'][1] if 'EC' in self.interpreter.series_data and len(self.interpreter.series_data['EC']) > 1 else 0,
                "ema_prev": self.interpreter.series_data['EMA'][1] if 'EMA' in self.interpreter.series_data and len(self.interpreter.series_data['EMA']) > 1 else 0
            })
        
        return diagnostic
