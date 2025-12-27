from flask import Flask, request, jsonify
import threading
import time
import os
import logging
from datetime import datetime
from src.okx_client import OKXClient
from src.trading_logic import AdaptiveZeroLagEMA
from src.keep_alive import KeepAliveSystem

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Inicializar componentes
okx_client = OKXClient()
strategy = AdaptiveZeroLagEMA()
keep_alive = KeepAliveSystem()

# Variáveis globais
trading_active = False
trade_thread = None

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "OKX ETH Trading Bot",
        "strategy": "Adaptive Zero Lag EMA v2",
        "timeframe": "45 minutes",
        "symbol": "ETH-USDT",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint de saúde para o Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "last_signal": keep_alive.last_signal_time
    })

@app.route('/start', methods=['POST'])
def start_trading():
    """Inicia o bot de trading"""
    global trading_active, trade_thread
    
    if trading_active:
        return jsonify({"status": "error", "message": "Trading já está ativo"})
    
    try:
        # Iniciar keep-alive
        keep_alive.start_keep_alive()
        
        # Iniciar thread de trading
        trading_active = True
        trade_thread = threading.Thread(target=trading_loop, daemon=True)
        trade_thread.start()
        
        return jsonify({
            "status": "success",
            "message": "Bot de trading iniciado",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Erro ao iniciar trading: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    """Para o bot de trading"""
    global trading_active
    
    try:
        trading_active = False
        keep_alive.stop_keep_alive()
        
        # Fechar todas as posições
        okx_client.close_all_positions()
        
        return jsonify({
            "status": "success",
            "message": "Bot de trading parado",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Erro ao parar trading: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Obtém status atual do bot"""
    balance = okx_client.get_balance()
    price = okx_client.get_ticker_price()
    
    return jsonify({
        "trading_active": trading_active,
        "balance_usdt": balance,
        "current_price": price,
        "strategy_params": {
            "period": strategy.period,
            "gain_limit": strategy.gain_limit,
            "threshold": strategy.threshold
        },
        "timestamp": datetime.now().isoformat()
    })

def trading_loop():
    """Loop principal de trading"""
    logger.info("Loop de trading iniciado")
    
    while trading_active:
        try:
            # Obter candles de 45 minutos
            candles = okx_client.get_candles(timeframe="45m", limit=100)
            
            if len(candles) >= 30:  # Esperar dados suficientes
                # Calcular sinal
                signal = strategy.calculate_signals(candles)
                
                if signal["signal"] in ["BUY", "SELL"]:
                    logger.info(f"Sinal gerado: {signal}")
                    
                    # Calcular tamanho da posição (95% do saldo)
                    position_size = okx_client.calculate_position_size(sl_points=2000)
                    
                    if position_size > 0:
                        # Executar ordem
                        success = okx_client.place_order(
                            side=signal["signal"],
                            quantity=position_size,
                            sl_points=2000,
                            tp_points=55
                        )
                        
                        if success:
                            logger.info(f"Ordem {signal['signal']} executada com sucesso")
                        else:
                            logger.error(f"Falha ao executar ordem {signal['signal']}")
            
            # Esperar 5 minutos antes de verificar novamente
            # (não queremos verificar a cada tick, apenas próximo candle)
            for _ in range(300):  # 300 segundos = 5 minutos
                if not trading_active:
                    break
                time.sleep(1)
                
        except Exception as e:
            logger.error(f"Erro no loop de trading: {e}")
            time.sleep(60)

if __name__ == '__main__':
    # Iniciar keep-alive imediatamente
    keep_alive.start_keep_alive()
    
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
