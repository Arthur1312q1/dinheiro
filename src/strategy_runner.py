"""
strategy_runner.py - VERSÃO CORRIGIDA PARA ESTRATÉGIA NÃO-INVERSORA
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
        
        # CORREÇÃO: Sinais com delay de 1 barra (como no Pine Script)
        self.pending_buy_signal = False  # Sinal BUY detectado na barra anterior
        self.pending_sell_signal = False  # Sinal SELL detectado na barra anterior
        
        # Estado da posição atual
        self.position_size = 0
        self.position_side = None  # 'long', 'short', ou None
        self.entry_price = None
        self.trade_id = None
        
        # Parâmetros da estratégia (extraídos do Pine)
        self.fixedSL = 2000  # pontos
        self.fixedTP = 55    # pontos
        self.risk = 0.01     # 1%
        self.mintick = 0.01  # tick mínimo do ETH/USDT
        
        # Stop Loss e Take Profit ativos
        self.stop_loss_price = None
        self.take_profit_price = None
        self.trailing_active = False
        self.trailing_stop_price = None
        self.trail_offset = 15  # pontos para ativar trailing
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        self.last_log_time = time.time()
        
        # Timezone do Brasil
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            # Extrair parâmetros do Pine
            self._extract_pine_parameters()
            logger.info("✅ Strategy Runner inicializado com estratégia NÃO-INVERSORA")
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _load_pine_script(self):
        try:
            # Procurar arquivo em vários locais
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "../strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "Adaptive_Zero_Lag_EMA_v2.pine"
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
    
    def _extract_pine_parameters(self):
        """Extrai parâmetros do código Pine Script"""
        if not self.interpreter:
            return
        
        try:
            # Extrair parâmetros do interpretador
            if hasattr(self.interpreter, 'params'):
                self.fixedSL = self.interpreter.params.get('fixedSL', 2000)
                self.fixedTP = self.interpreter.params.get('fixedTP', 55)
                self.risk = self.interpreter.params.get('risk', 0.01)
                logger.info(f"📊 Parâmetros extraídos: SL={self.fixedSL}p, TP={self.fixedTP}p, Risk={self.risk*100}%")
        except Exception as e:
            logger.error(f"Erro ao extrair parâmetros: {e}")
    
    def _calculate_position_size(self, entry_price):
        """Calcula tamanho da posição baseado no risco (igual Pine Script)"""
        try:
            balance = self.okx_client.get_balance()
            risk_amount = self.risk * balance
            stop_loss_usdt = self.fixedSL * self.mintick
            if stop_loss_usdt <= 0:
                return 0
            
            lots = risk_amount / stop_loss_usdt
            
            # Limitar ao saldo disponível
            max_quantity = balance / entry_price * 0.95  # 95% do saldo
            if lots > max_quantity:
                lots = max_quantity
            
            # Arredondar para 4 casas decimais (tamanho mínimo da OKX)
            return round(lots, 4)
            
        except Exception as e:
            logger.error(f"Erro ao calcular tamanho da posição: {e}")
            return 0
    
    def _calculate_stop_take_prices(self, side, entry_price):
        """Calcula preços de Stop Loss e Take Profit (em pontos)"""
        if side == 'buy':
            stop_loss = entry_price - (self.fixedSL * self.mintick)
            take_profit = entry_price + (self.fixedTP * self.mintick)
        else:  # sell
            stop_loss = entry_price + (self.fixedSL * self.mintick)
            take_profit = entry_price - (self.fixedTP * self.mintick)
        
        return stop_loss, take_profit
    
    def _check_stop_take(self):
        """Verifica se Stop Loss ou Take Profit foram atingidos"""
        if not self.position_size or not self.current_price:
            return
        
        try:
            if self.position_side == 'long':
                # Verificar Stop Loss
                if self.stop_loss_price and self.current_price <= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS ATINGIDO (LONG): {self.current_price:.2f} <= {self.stop_loss_price:.2f}")
                    self._close_position(self.current_price, "stop_loss")
                    return
                
                # Verificar Take Profit
                if self.take_profit_price and self.current_price >= self.take_profit_price:
                    logger.info(f"🎯 TAKE PROFIT ATINGIDO (LONG): {self.current_price:.2f} >= {self.take_profit_price:.2f}")
                    self._close_position(self.current_price, "take_profit")
                    return
                
                # Verificar trailing stop (se ativo)
                if self.trailing_active and self.trailing_stop_price:
                    # Atualizar trailing stop se preço subiu
                    new_trailing_stop = self.current_price - (self.trail_offset * self.mintick)
                    if new_trailing_stop > self.trailing_stop_price:
                        self.trailing_stop_price = new_trailing_stop
                        logger.info(f"📈 Trailing Stop atualizado: {self.trailing_stop_price:.2f}")
                    
                    # Verificar se trailing stop foi atingido
                    if self.current_price <= self.trailing_stop_price:
                        logger.info(f"📉 TRAILING STOP ATINGIDO: {self.current_price:.2f} <= {self.trailing_stop_price:.2f}")
                        self._close_position(self.current_price, "trailing_stop")
                        return
            
            elif self.position_side == 'short':
                # Verificar Stop Loss
                if self.stop_loss_price and self.current_price >= self.stop_loss_price:
                    logger.info(f"🛑 STOP LOSS ATINGIDO (SHORT): {self.current_price:.2f} >= {self.stop_loss_price:.2f}")
                    self._close_position(self.current_price, "stop_loss")
                    return
                
                # Verificar Take Profit
                if self.take_profit_price and self.current_price <= self.take_profit_price:
                    logger.info(f"🎯 TAKE PROFIT ATINGIDO (SHORT): {self.current_price:.2f} <= {self.take_profit_price:.2f}")
                    self._close_position(self.current_price, "take_profit")
                    return
                
                # Verificar trailing stop (se ativo)
                if self.trailing_active and self.trailing_stop_price:
                    # Atualizar trailing stop se preço desceu
                    new_trailing_stop = self.current_price + (self.trail_offset * self.mintick)
                    if new_trailing_stop < self.trailing_stop_price:
                        self.trailing_stop_price = new_trailing_stop
                        logger.info(f"📈 Trailing Stop atualizado: {self.trailing_stop_price:.2f}")
                    
                    # Verificar se trailing stop foi atingido
                    if self.current_price >= self.trailing_stop_price:
                        logger.info(f"📉 TRAILING STOP ATINGIDO: {self.current_price:.2f} >= {self.trailing_stop_price:.2f}")
                        self._close_position(self.current_price, "trailing_stop")
                        return
                        
        except Exception as e:
            logger.error(f"Erro ao verificar stop/take: {e}")
    
    def _open_position(self, side, entry_price):
        """Abre uma nova posição"""
        try:
            # CORREÇÃO: Verificar se já está na mesma posição
            if (side == 'buy' and self.position_side == 'long') or (side == 'sell' and self.position_side == 'short'):
                logger.info(f"⏭️  Já está na posição {side.upper()}, ignorando")
                return False
            
            # Fechar posição oposta se existir (apenas se for inversão necessária)
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
                self.stop_loss_price, self.take_profit_price = self._calculate_stop_take_prices(side, entry_price)
                
                # Inicializar trailing stop
                if side == 'buy':
                    self.trailing_stop_price = entry_price - (self.trail_offset * self.mintick)
                else:
                    self.trailing_stop_price = entry_price + (self.trail_offset * self.mintick)
                
                self.trailing_active = False
                
                logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {abs(quantity):.4f} ETH @ ${entry_price:.2f}")
                logger.info(f"   Stop Loss: ${self.stop_loss_price:.2f}")
                logger.info(f"   Take Profit: ${self.take_profit_price:.2f}")
                logger.info(f"   Trailing Stop inicial: ${self.trailing_stop_price:.2f}")
                
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
                self.trailing_active = False
                self.trailing_stop_price = None
                return True
            return False
        except Exception as e:
            logger.error(f"Erro ao fechar posição: {e}")
            return False
    
    def _process_pending_signals(self):
        """Processa sinais pendentes com delay de 1 barra (igual Pine Script)"""
        if not self.current_price:
            return
        
        # CORREÇÃO: Verificar condições exatas do Pine Script
        # BUY só se: pending_buy_signal AND position_size <= 0 (flat ou short)
        if self.pending_buy_signal:
            if self.position_size <= 0:  # Flat ou short
                logger.info(f"🎯 EXECUTANDO BUY (sinal da barra anterior)")
                self._open_position('buy', self.current_price)
            else:
                logger.info(f"⏭️  BUY ignorado (já em LONG)")
            
            # Resetar sinal
            self.pending_buy_signal = False
        
        # SELL só se: pending_sell_signal AND position_size >= 0 (flat ou long)
        elif self.pending_sell_signal:
            if self.position_size >= 0:  # Flat ou long
                logger.info(f"🎯 EXECUTANDO SELL (sinal da barra anterior)")
                self._open_position('sell', self.current_price)
            else:
                logger.info(f"⏭️  SELL ignorado (já em SHORT)")
            
            # Resetar sinal
            self.pending_sell_signal = False
    
    # WebSocket methods
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                self.current_price = float(ticker_data.get('last', 0))
                
                # Verificar Stop Loss/Take Profit a cada preço novo
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
            
            # 1. PRIMEIRO: Executar sinais da barra ANTERIOR (delay de 1 barra)
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
            logger.info(f"   Estado: {self.position_side or 'FLAT'} | Size: {abs(self.position_size):.4f} ETH")
            logger.info(f"   Sinais pendentes: BUY={self.pending_buy_signal}, SELL={self.pending_sell_signal}")
            logger.info("=" * 60)
            return True
        
        # Atualizar dados da barra atual (high, low, close)
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
            
            buy_signal_raw = result.get('buy_signal_raw', False)
            sell_signal_raw = result.get('sell_signal_raw', False)
            
            logger.info(f"   EMA: {result['ema']:.2f}, EC: {result['ec']:.2f}, Erro: {result['error_pct']:.2f}%")
            
            # CORREÇÃO: Marcar sinais para execução na PRÓXIMA barra (delay de 1 barra)
            if buy_signal_raw:
                self.pending_buy_signal = True
                logger.info(f"   🟢 SINAL BUY DETECTADO! (executará na PRÓXIMA barra)")
            
            if sell_signal_raw:
                self.pending_sell_signal = True
                logger.info(f"   🔴 SINAL SELL DETECTADO! (executará na PRÓXIMA barra)")
            
            if not buy_signal_raw and not sell_signal_raw:
                logger.info(f"   ⚪ Nenhum sinal detectado nesta barra")
                
        except Exception as e:
            logger.error(f"💥 Erro ao processar barra: {e}")
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador Pine Script não inicializado")
            return False
        
        logger.info("🚀 Iniciando Strategy Runner...")
        logger.info("📊 Estratégia: NÃO-INVERSORA com Stop Loss/Take Profit")
        
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
            
            # Log periódico a cada 60 segundos
            current_time = time.time()
            if current_time - self.last_log_time > 60:
                if self.current_price:
                    logger.info(f"📊 STATUS ATUAL: Preço ${self.current_price:.2f}")
                    logger.info(f"   Posição: {self.position_side or 'FLAT'} {abs(self.position_size):.4f} ETH")
                    logger.info(f"   Entrada: ${self.entry_price or 0:.2f}")
                    logger.info(f"   SL: ${self.stop_loss_price or 0:.2f}, TP: ${self.take_profit_price or 0:.2f}")
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
            "trailing_stop": self.trailing_stop_price
        }
