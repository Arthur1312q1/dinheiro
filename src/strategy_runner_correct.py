"""
Strategy Runner CORRETO que replica EXATAMENTE o TradingView
"""
import os
import logging
import time
import threading
import json
import websocket
from datetime import datetime
import pytz

from .tradingview_engine import TradingViewEngine
from .pine_engine_correct import PineScriptCorrect
from .trailing_stop import TrailingStopManager
from .okx_client import OKXClient

logger = logging.getLogger(__name__)

class StrategyRunnerCorrect:
    def __init__(self, okx_client: OKXClient, trade_history):
        self.okx_client = okx_client
        self.trade_history = trade_history
        
        # Carregar Pine Script
        pine_code = self._load_pine_script()
        if not pine_code:
            raise Exception("Não foi possível carregar Pine Script")
        
        # Inicializar componentes
        self.pine_interpreter = PineScriptCorrect(pine_code)
        self.tv_engine = TradingViewEngine(self.pine_interpreter, timeframe_minutes=30)
        self.trailing_stop = TrailingStopManager(trail_points=55, trail_offset=15, mintick=0.01)
        
        # Estado da posição
        self.position_size = 0
        self.position_side = None
        self.entry_price = None
        self.trade_id = None
        
        # Parâmetros
        self.fixedSL = 2000
        self.fixedTP = 55
        self.risk = 0.01
        self.mintick = 0.01
        
        # WebSocket
        self.ws = None
        self.ws_thread = None
        self.current_price = None
        self.is_running = False
        
        # Timezone
        self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        
        logger.info("✅ Strategy Runner Correct inicializado")
        logger.info("📊 Replicação EXATA do TradingView")
        logger.info("⏰ Entradas: Apenas no FECHAMENTO da barra")
        logger.info("🎯 Saídas: A qualquer momento (trailing stop)")
    
    def _load_pine_script(self):
        """Carrega o código Pine Script"""
        try:
            path = "strategy/Adaptive_Zero_Lag_EMA_v2.pine"
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return f.read()
            return None
        except Exception as e:
            logger.error(f"Erro ao carregar Pine: {e}")
            return None
    
    def _calculate_position_size(self, entry_price):
        """Calcula tamanho da posição como no Pine"""
        try:
            balance = self.okx_client.get_balance()
            if balance <= 0:
                balance = 1000
            
            risk_amount = self.risk * balance
            stop_loss_usdt = self.fixedSL * self.mintick
            
            if stop_loss_usdt <= 0:
                return 0
            
            quantity = risk_amount / stop_loss_usdt
            
            # Limite máximo
            limit = 100
            if quantity > limit:
                quantity = limit
            
            # Limitar ao saldo
            max_quantity = balance / entry_price * 0.95
            if quantity > max_quantity:
                quantity = max_quantity
            
            return round(quantity, 4)
        except Exception as e:
            logger.error(f"Erro cálculo tamanho: {e}")
            return 0
    
    def _open_position(self, side, entry_price):
        """Abre posição (no FECHAMENTO da barra)"""
        try:
            # Verificar regras do Pine
            if side == 'buy' and self.position_size > 0:
                logger.info("⏭️ Ignorando BUY - já está em LONG")
                return False
            if side == 'sell' and self.position_size < 0:
                logger.info("⏭️ Ignorando SELL - já está em SHORT")
                return False
            
            # Fechar posição oposta se existir
            if self.position_size != 0:
                logger.info(f"🔀 Fechando {self.position_side} para abrir {side}")
                self._close_position(entry_price, "inversao")
            
            # Calcular quantidade
            quantity = self._calculate_position_size(entry_price)
            if quantity <= 0:
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
                
                # Iniciar trailing stop
                self.trailing_stop.on_entry(entry_price, side)
                
                # Resetar sinal pendente (como no Pine)
                if side == 'buy':
                    self.tv_engine.reset_pending_signals('buy')
                else:
                    self.tv_engine.reset_pending_signals('sell')
                
                logger.info("=" * 60)
                logger.info(f"🚀 POSIÇÃO ABERTA: {side.upper()} {quantity:.4f} ETH @ ${entry_price:.2f}")
                logger.info("=" * 60)
                return True
            
            return False
        except Exception as e:
            logger.error(f"Erro abrir posição: {e}")
            return False
    
    def _close_position(self, exit_price, reason=""):
        """Fecha posição (pode ser a qualquer momento)"""
        if not self.trade_id or self.position_size == 0:
            return False
        
        try:
            # Calcular PnL
            if self.entry_price:
                if self.position_side == 'long':
                    pnl_pct = ((exit_price - self.entry_price) / self.entry_price) * 100
                else:
                    pnl_pct = ((self.entry_price - exit_price) / self.entry_price) * 100
            
            # Fechar no histórico
            success = self.trade_history.close_trade(self.trade_id, exit_price)
            if success:
                logger.info("=" * 60)
                logger.info(f"✅ POSIÇÃO FECHADA: {self.position_side.upper()} @ ${exit_price:.2f}")
                logger.info(f"   Motivo: {reason}")
                if self.entry_price:
                    logger.info(f"   PnL: {pnl_pct:.2f}%")
                logger.info("=" * 60)
                
                # Resetar estado
                self.position_size = 0
                self.position_side = None
                self.entry_price = None
                self.trade_id = None
                self.trailing_stop.reset()
                
                return True
            return False
        except Exception as e:
            logger.error(f"Erro fechar posição: {e}")
            return False
    
    def _process_tick_for_trailing(self, price: float, timestamp: datetime):
        """
        Processa tick APENAS para trailing stop (não para indicadores)
        No TradingView: calc_on_every_tick=false, mas o trailing stop
        é monitorado a cada tick pelo sistema, não pelo script
        """
        if not self.current_price:
            self.current_price = price
        
        # Atualizar preço atual
        self.current_price = price
        
        # 1. Verificar se barra fechou (para processar indicadores)
        bar_closed = self.tv_engine.process_tick_for_bar(price, timestamp)
        
        # 2. Se barra fechou, verificar entradas (no FECHAMENTO)
        if bar_closed:
            entry_signals = self.tv_engine.should_execute_entry()
            
            # EXECUTAR ENTRADAS (no fechamento da barra)
            if entry_signals['buy'] and self.position_size <= 0:
                logger.info(f"🎯 EXECUTANDO BUY no fechamento da barra")
                self._open_position('buy', price)
            
            elif entry_signals['sell'] and self.position_size >= 0:
                logger.info(f"🎯 EXECUTANDO SELL no fechamento da barra")
                self._open_position('sell', price)
        
        # 3. SEMPRE verificar trailing stop (a cada tick)
        if self.position_size != 0:
            self.trailing_stop.update(price)
            
            if self.trailing_stop.should_close(price):
                logger.info(f"🎯 TRAILING STOP ATINGIDO @ ${price:.2f}")
                self._close_position(price, "trailing_stop")
                return
        
        # 4. Verificar stop loss/take profit estático (a cada tick)
        self._check_static_stop_take(price)
    
    def _check_static_stop_take(self, price: float):
        """Verifica stop loss/take profit estático"""
        if not self.position_size or not self.entry_price:
            return
        
        try:
            if self.position_side == 'long':
                stop_price = self.entry_price - (self.fixedSL * self.mintick)
                take_price = self.entry_price + (self.fixedTP * self.mintick)
                
                if price <= stop_price:
                    logger.info(f"🛑 STOP LOSS (LONG) @ ${price:.2f}")
                    self._close_position(price, "stop_loss")
                elif price >= take_price:
                    logger.info(f"💰 TAKE PROFIT (LONG) @ ${price:.2f}")
                    self._close_position(price, "take_profit")
            
            elif self.position_side == 'short':
                stop_price = self.entry_price + (self.fixedSL * self.mintick)
                take_price = self.entry_price - (self.fixedTP * self.mintick)
                
                if price >= stop_price:
                    logger.info(f"🛑 STOP LOSS (SHORT) @ ${price:.2f}")
                    self._close_position(price, "stop_loss")
                elif price <= take_price:
                    logger.info(f"💰 TAKE PROFIT (SHORT) @ ${price:.2f}")
                    self._close_position(price, "take_profit")
                    
        except Exception as e:
            logger.error(f"Erro check stop/take: {e}")
    
    # WebSocket methods
    def _on_ws_message(self, ws, message):
        try:
            data = json.loads(message)
            if 'arg' in data and data['arg'].get('channel') == 'tickers':
                ticker_data = data.get('data', [{}])[0]
                price = float(ticker_data.get('last', 0))
                timestamp = datetime.now(self.tz_brazil)
                
                # Processar tick (apenas para trailing stop e fechamento de barra)
                self._process_tick_for_trailing(price, timestamp)
                
        except Exception as e:
            logger.error(f"Erro WS: {e}")
    
    def _on_ws_error(self, ws, error):
        logger.error(f"Erro WebSocket: {error}")
    
    def _on_ws_close(self, ws, close_status_code, close_msg):
        logger.warning("WebSocket fechado")
        self.ws = None
    
    def _on_ws_open(self, ws):
        logger.info("WebSocket conectado")
        subscribe_msg = {
            "op": "subscribe",
            "args": [{"channel": "tickers", "instId": "ETH-USDT-SWAP"}]
        }
        ws.send(json.dumps(subscribe_msg))
    
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
        time.sleep(3)
        return True
    
    def _stop_websocket(self):
        if self.ws:
            self.ws.close()
        self.ws = None
    
    def start(self):
        """Inicia o strategy runner"""
        logger.info("🚀 Iniciando Strategy Runner (modo TradingView exato)")
        
        # Iniciar WebSocket
        if not self._start_websocket():
            return False
        
        # Aguardar preço
        logger.info("⏳ Aguardando preço...")
        for _ in range(30):
            if self.current_price is not None:
                break
            time.sleep(1)
        
        if self.current_price is None:
            logger.error("❌ Sem preço")
            return False
        
        logger.info(f"✅ Preço: ${self.current_price:.2f}")
        self.is_running = True
        return True
    
    def stop(self):
        """Para o strategy runner"""
        if self.position_size != 0 and self.current_price:
            self._close_position(self.current_price, "stop_bot")
        
        self.is_running = False
        self._stop_websocket()
        logger.info("⏹️ Strategy Runner parado")
    
    def get_status(self):
        """Retorna status"""
        return {
            'is_running': self.is_running,
            'current_price': self.current_price,
            'position_size': self.position_size,
            'position_side': self.position_side,
            'entry_price': self.entry_price,
            'tv_engine': self.tv_engine.get_status(),
            'trailing_stop': self.trailing_stop.get_status() if self.position_size else None
        }
    
    def force_close_position(self):
        """Força fechamento"""
        if not self.position_size or not self.current_price:
            return {"success": False, "message": "Sem posição"}
        
        try:
            success = self._close_position(self.current_price, "force_close")
            if success:
                return {"success": True, "message": f"Fechado @ ${self.current_price:.2f}"}
            return {"success": False, "message": "Falha ao fechar"}
        except Exception as e:
            return {"success": False, "message": str(e)}
