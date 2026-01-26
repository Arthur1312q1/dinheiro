import os
import logging
import time
import threading
from datetime import datetime, timedelta

from .pine_engine import PineScriptInterpreter
from .okx_client import OKXClient
from .web_socket_manager import OKXWebSocketManager

logger = logging.getLogger(__name__)

class StrategyRunner:
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        self.interpreter = None
        self.is_running = False
        
        # WebSocket Manager
        self.ws_manager = OKXWebSocketManager()
        
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
        self.current_trade_id = None
        
        # Cache de preço
        self.current_price = None
        
        # Thread de processamento
        self.processing_thread = None
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if pine_code:
            self.interpreter = PineScriptInterpreter(pine_code)
            logger.info("✅ Strategy Runner inicializado com Pine Script")
            self._log_strategy_params()
        else:
            logger.error("❌ Não foi possível carregar o código Pine Script")
    
    def _log_strategy_params(self):
        """Loga os parâmetros da estratégia"""
        if self.interpreter:
            logger.info(f"📊 Parâmetros da estratégia:")
            logger.info(f"   • Period: {self.interpreter.period}")
            logger.info(f"   • Threshold: {self.interpreter.threshold}")
            logger.info(f"   • GainLimit: {self.interpreter.gain_limit}")
    
    def _load_pine_script(self):
        """Carrega o código Pine Script do arquivo"""
        try:
            # Procurar arquivo em vários locais
            possible_paths = [
                "strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "src/strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "./strategy/Adaptive_Zero_Lag_EMA_v2.pine",
                "./Adaptive_Zero_Lag_EMA_v2.pine"
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        logger.info(f"✅ Pine Script carregado de: {path}")
                        logger.info(f"   Tamanho: {len(content)} caracteres")
                        return content
            
            # Se não encontrou, usar código padrão de fallback
            logger.warning("⚠️  Arquivo Pine Script não encontrado, usando fallback")
            return self._get_fallback_pine_code()
            
        except Exception as e:
            logger.error(f"❌ Erro ao ler arquivo Pine Script: {e}")
            return self._get_fallback_pine_code()
    
    def _get_fallback_pine_code(self):
        """Código Pine Script de fallback (versão simplificada)"""
        return """
//@version=5
strategy("Adaptive Zero Lag EMA", overlay=true, margin_long=100, margin_short=100)

// Parâmetros
Period = input.int(20, "Period", minval=1)
GainLimit = input.int(8, "Gain Limit", minval=1)
Threshold = input.float(0.05, "Threshold", step=0.01)

// Cálculo Zero Lag EMA
src = close
ema = ta.ema(src, Period)

// Buscar melhor ganho
best_gain = 0.0
least_error = 999999.9

for i = -GainLimit to GainLimit
    gain = i / 10.0
    test_ema = ema + gain * (src - nz(test_ema[1], src))
    error = math.abs(src - test_ema)
    if error < least_error
        least_error := error
        best_gain := gain

// EC (Error Corrected)
ec = ema + best_gain * (src - nz(ec[1], src))

// Sinais
error_pct = 100 * least_error / src
signal_valid = error_pct > Threshold

buy_signal = ta.crossover(ec, ema) and signal_valid
sell_signal = ta.crossunder(ec, ema) and signal_valid

// Estratégia
if buy_signal
    strategy.entry("Buy", strategy.long)

if sell_signal
    strategy.entry("Sell", strategy.short)
"""
    
    def _initialize_candle_buffer(self):
        """Inicializa buffer com candles históricos"""
        logger.info("📈 Inicializando com candles históricos da OKX...")
        
        try:
            # Obter candles históricos
            historical_candles = self.okx_client.get_candles(
                symbol="ETH-USDT-SWAP",
                timeframe="30m",
                limit=100
            )
            
            if not historical_candles:
                logger.error("❌ Não foi possível obter candles históricos")
                return False
            
            logger.info(f"✅ {len(historical_candles)} candles históricos obtidos")
            
            # Processar os últimos 50 candles para aquecer indicadores
            candles_to_process = historical_candles[-50:] if len(historical_candles) >= 50 else historical_candles
            
            for i, candle in enumerate(candles_to_process):
                result = self.interpreter.process_candle(candle)
                
                # Atualizar flags com base nos sinais RAW
                if result.get('buy_signal_raw'):
                    self.pending_buy = True
                    logger.info(f"   🔥 Sinal BUY RAW detectado no candle histórico #{i+1}")
                
                if result.get('sell_signal_raw'):
                    self.pending_sell = True
                    logger.info(f"   🔥 Sinal SELL RAW detectado no candle histórico #{i+1}")
            
            # Configurar timestamp da última barra
            if historical_candles:
                last_candle = historical_candles[-1]
                last_ts = last_candle['timestamp'] / 1000
                self.last_bar_timestamp = datetime.utcfromtimestamp(last_ts)
                
                # Iniciar barra atual
                self.current_bar_data = {
                    'timestamp': int(datetime.utcnow().timestamp() * 1000),
                    'open': self.current_price or last_candle['close'],
                    'high': self.current_price or last_candle['close'],
                    'low': self.current_price or last_candle['close'],
                    'close': self.current_price or last_candle['close'],
                    'volume': 0
                }
                
                logger.info(f"   ⏰ Última barra histórica: {self.last_bar_timestamp.strftime('%Y-%m-%d %H:%M')}")
                logger.info(f"   📊 Indicadores aquecidos: EMA={self.interpreter.series_data['EMA'].current():.2f}, "
                          f"EC={self.interpreter.series_data['EC'].current():.2f}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Erro ao inicializar buffer de candles: {e}")
            return False
    
    def _check_and_update_bar(self):
        """Verifica se uma nova barra de 30 minutos começou"""
        if not self.ws_manager.current_price:
            return False
        
        self.current_price = self.ws_manager.current_price
        
        now = datetime.utcnow()
        
        # Calcular início da barra atual (arredondado para 30 minutos)
        current_bar_start = now.replace(
            minute=(now.minute // self.timeframe_minutes) * self.timeframe_minutes,
            second=0,
            microsecond=0
        )
        
        # Se for a primeira barra
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
            logger.info(f"📊 Primeira barra iniciada: {current_bar_start.strftime('%H:%M')}")
            return False
        
        # Se começou uma nova barra
        if current_bar_start > self.last_bar_timestamp:
            logger.info(f"📊 NOVA BARRA 30m INICIADA: {current_bar_start.strftime('%H:%M')}")
            
            # Se temos dados da barra anterior, processá-la
            if self.current_bar_data and self.current_bar_data['close'] > 0:
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
        if self.current_bar_data and self.current_price:
            self.current_bar_data['high'] = max(self.current_bar_data['high'], self.current_price)
            self.current_bar_data['low'] = min(self.current_bar_data['low'], self.current_price)
            self.current_bar_data['close'] = self.current_price
        
        return False
    
    def _process_completed_bar(self):
        """Processa uma barra completa"""
        if not self.current_bar_data:
            logger.warning("⚠️  Nenhum dado de barra para processar")
            return
        
        logger.info(f"🔨 Processando barra #{self.bar_count}...")
        logger.info(f"   📊 Dados: O={self.current_bar_data['open']:.2f}, "
                  f"H={self.current_bar_data['high']:.2f}, "
                  f"L={self.current_bar_data['low']:.2f}, "
                  f"C={self.current_bar_data['close']:.2f}")
        
        # Processar através do interpretador
        result = self.interpreter.process_candle(self.current_bar_data)
        
        buy_signal_raw = result.get('buy_signal_raw', False)
        sell_signal_raw = result.get('sell_signal_raw', False)
        
        # Log detalhado
        logger.info(f"   📈 Resultado Pine:")
        logger.info(f"      • EMA: {result['ema']:.2f}")
        logger.info(f"      • EC: {result['ec']:.2f}")
        logger.info(f"      • Erro: {result['error_pct']:.2f}%")
        logger.info(f"      • Buy Signal RAW: {buy_signal_raw}")
        logger.info(f"      • Sell Signal RAW: {sell_signal_raw}")
        
        # Atualizar flags persistentes (delay de 1 barra)
        if buy_signal_raw:
            self.pending_buy = True
            logger.info(f"   ✅ PENDING BUY ATIVADO (executará na próxima barra)")
        
        if sell_signal_raw:
            self.pending_sell = True
            logger.info(f"   ✅ PENDING SELL ATIVADO (executará na próxima barra)")
        
        # EXECUÇÃO DE TRADE (MODO SIMULAÇÃO)
        current_price = self.current_bar_data['close']
        
        # Executar BUY se tiver pendingBuy e não tiver posição long
        if self.pending_buy and self.position_size <= 0:
            logger.info(f"🚀🚀🚀 EXECUTANDO ORDEM BUY (SIMULAÇÃO)")
            
            # Fechar trade anterior se existir
            if self.current_trade_id:
                self.trade_history.close_trade(self.current_trade_id, current_price)
                self.current_trade_id = None
            
            # Calcular tamanho da posição
            quantity = self.okx_client.calculate_position_size()
            if quantity <= 0:
                logger.error("❌ Quantidade inválida para BUY")
                self.pending_buy = False
                return
            
            logger.info(f"   💰 Preço: ${current_price:.2f}")
            logger.info(f"   📦 Quantidade: {quantity:.4f} ETH")
            logger.info(f"   💵 Valor: ${quantity * current_price:.2f}")
            
            # Registrar no histórico
            trade_id = self.trade_history.add_trade(
                side='buy',
                entry_price=current_price,
                quantity=quantity
            )
            
            if trade_id:
                self.current_trade_id = trade_id
                self.position_size = quantity
                self.position_side = 'long'
                logger.info(f"   📝 Trade #{trade_id} registrada (BUY)")
            else:
                logger.error("❌ Falha ao registrar trade")
            
            self.pending_buy = False
        
        # Executar SELL se tiver pendingSell e não tiver posição short
        elif self.pending_sell and self.position_size >= 0:
            logger.info(f"🚀🚀🚀 EXECUTANDO ORDEM SELL (SIMULAÇÃO)")
            
            # Fechar trade anterior se existir
            if self.current_trade_id:
                self.trade_history.close_trade(self.current_trade_id, current_price)
                self.current_trade_id = None
            
            # Calcular tamanho da posição
            quantity = self.okx_client.calculate_position_size()
            if quantity <= 0:
                logger.error("❌ Quantidade inválida para SELL")
                self.pending_sell = False
                return
            
            logger.info(f"   💰 Preço: ${current_price:.2f}")
            logger.info(f"   📦 Quantidade: {quantity:.4f} ETH")
            logger.info(f"   💵 Valor: ${quantity * current_price:.2f}")
            
            # Registrar no histórico
            trade_id = self.trade_history.add_trade(
                side='sell',
                entry_price=current_price,
                quantity=quantity
            )
            
            if trade_id:
                self.current_trade_id = trade_id
                self.position_size = -quantity  # Negativo para short
                self.position_side = 'short'
                logger.info(f"   📝 Trade #{trade_id} registrada (SELL)")
            else:
                logger.error("❌ Falha ao registrar trade")
            
            self.pending_sell = False
    
    def _trading_loop(self):
        """Loop principal de trading"""
        logger.info("🔄 Iniciando loop de trading...")
        
        while self.is_running:
            try:
                # Atualizar preço atual
                self.current_price = self.ws_manager.get_current_price()
                
                # Verificar se temos preço válido
                if not self.current_price:
                    logger.warning("⚠️  Aguardando preço do WebSocket...")
                    time.sleep(5)
                    continue
                
                # Verificar e atualizar barra
                new_bar = self._check_and_update_bar()
                
                # Log periódico
                current_time = datetime.utcnow()
                if current_time.minute % 5 == 0 and current_time.second < 10:
                    logger.info(f"⏰ Status: Preço=${self.current_price:.2f}, "
                              f"Barra={self.bar_count}, "
                              f"Posição={'LONG' if self.position_side == 'long' else 'SHORT' if self.position_side == 'short' else 'NONE'}")
                
                time.sleep(1)  # Loop mais rápido para melhor responsividade
                
            except Exception as e:
                logger.error(f"💥 Erro no loop de trading: {e}")
                time.sleep(5)
    
    def start(self):
        """Inicia o strategy runner"""
        if not self.interpreter:
            logger.error("❌ Interpretador Pine não inicializado")
            return False
        
        # Conectar WebSocket
        logger.info("🌐 Conectando ao WebSocket da OKX...")
        if not self.ws_manager.connect():
            logger.error("❌ Falha ao conectar WebSocket")
            return False
        
        # Aguardar conexão e primeiro preço
        logger.info("⏳ Aguardando primeiro preço do WebSocket...")
        for i in range(30):
            if self.ws_manager.get_current_price():
                break
            time.sleep(1)
        
        if not self.ws_manager.get_current_price():
            logger.error("❌ Timeout aguardando preço do WebSocket")
            return False
        
        # Inicializar com candles históricos
        if not self._initialize_candle_buffer():
            logger.warning("⚠️  Falha ao inicializar candles históricos, continuando...")
        
        # Iniciar loop de trading
        self.is_running = True
        self.processing_thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.processing_thread.start()
        
        logger.info("🚀🚀🚀 STRATEGY RUNNER INICIADO COM SUCESSO!")
        logger.info(f"   • Timeframe: {self.timeframe_minutes} minutos")
        logger.info(f"   • Modo: SIMULAÇÃO (sem ordens reais)")
        logger.info(f"   • Preço atual: ${self.ws_manager.get_current_price():.2f}")
        
        return True
    
    def stop(self):
        """Para o strategy runner"""
        logger.info("⏹️ Parando Strategy Runner...")
        
        self.is_running = False
        
        # Fechar trade aberta se existir
        if self.current_trade_id and self.current_price:
            self.trade_history.close_trade(self.current_trade_id, self.current_price)
        
        # Desconectar WebSocket
        self.ws_manager.disconnect()
        
        # Aguardar thread terminar
        if self.processing_thread:
            self.processing_thread.join(timeout=5)
        
        logger.info("✅ Strategy Runner parado")
    
    def get_strategy_status(self):
        """Retorna status da estratégia"""
        next_bar_time = None
        if self.last_bar_timestamp:
            next_bar = self.last_bar_timestamp + timedelta(minutes=self.timeframe_minutes)
            next_bar_time = next_bar.strftime('%H:%M:%S')
        
        return {
            "status": "running" if self.is_running else "stopped",
            "mode": f"BARRAS_{self.timeframe_minutes}m",
            "simulation_mode": True,
            "current_price": self.current_price,
            "next_bar_at": next_bar_time,
            "bars_processed": self.bar_count,
            "pending_buy": self.pending_buy,
            "pending_sell": self.pending_sell,
            "position_size": self.position_size,
            "position_side": self.position_side,
            "ws_connected": self.ws_manager.is_connected,
            "price_fresh": self.ws_manager.is_price_fresh()
        }
