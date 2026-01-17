import os
import sys
import logging
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify, render_template_string

# ============================================================================
# 1. CONFIGURAÇÃO INICIAL
# ============================================================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================================
# 2. IMPORTAR MÓDULOS INTERNOS
# ============================================================================
try:
    from okx_client import OKXClient
    from keep_alive import KeepAliveSystem
    from strategy_runner import StrategyRunner
    logger.info("✅ Módulos internos importados com sucesso.")
except ImportError as e:
    logger.error(f"❌ Erro ao importar módulos: {e}")
    OKXClient = KeepAliveSystem = StrategyRunner = None

# ============================================================================
# 3. INICIALIZAR COMPONENTES
# ============================================================================
try:
    okx_client = OKXClient() if OKXClient else None
    keep_alive = KeepAliveSystem() if KeepAliveSystem else None
    
    # Inicializar o Strategy Runner (que agora usa WebSocket)
    strategy_runner = None
    if okx_client:
        strategy_runner = StrategyRunner(okx_client)
        logger.info("✅ Strategy Runner (WebSocket) inicializado.")
    
    logger.info("✅ Componentes do bot inicializados.")
except Exception as e:
    logger.error(f"⚠️  Falha na inicialização: {e}")
    okx_client = None
    keep_alive = None
    strategy_runner = None

# ============================================================================
# 4. VARIÁVEIS DE ESTADO
# ============================================================================
trading_active = False
trade_thread = None

# ============================================================================
# 5. INTERFACE WEB (HTML COM BOTÕES)
# ============================================================================
@app.route('/', methods=['GET'])
def home():
    """Página inicial com interface para controlar o bot."""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading - AZLEMA v2 (Tempo Real)</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; padding: 20px; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; display: flex; justify-content: center; align-items: center; color: #fff; }
            .container { background: rgba(255, 255, 255, 0.1); backdrop-filter: blur(10px); padding: 40px 30px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); max-width: 600px; width: 90%; border: 1px solid rgba(255,255,255,0.1); }
            h1 { color: #00ff88; margin-bottom: 5px; font-size: 28px; text-shadow: 0 0 10px rgba(0, 255, 136, 0.5); }
            .subtitle { color: #a0a0c0; margin-bottom: 30px; font-size: 16px; }
            .status-box { padding: 20px; margin: 25px 0; border-radius: 12px; font-weight: bold; font-size: 18px; border: 2px solid transparent; background: rgba(0, 0, 0, 0.3); }
            .status-active { color: #00ff88; border-color: #00ff88; box-shadow: 0 0 20px rgba(0, 255, 136, 0.3); }
            .status-inactive { color: #ff4444; border-color: #ff4444; }
            .stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; margin: 25px 0; text-align: left; }
            .stat-item { background: rgba(255, 255, 255, 0.05); padding: 15px; border-radius: 10px; border-left: 4px solid #00ff88; }
            .stat-label { font-size: 12px; color: #a0a0c0; text-transform: uppercase; }
            .stat-value { font-size: 18px; color: #fff; font-weight: bold; }
            .button-group { display: flex; justify-content: center; gap: 20px; margin: 30px 0; flex-wrap: wrap; }
            .btn { padding: 16px 32px; font-size: 18px; border: none; border-radius: 50px; cursor: pointer; color: white; font-weight: bold; transition: all 0.3s; min-width: 160px; display: flex; align-items: center; justify-content: center; gap: 10px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); }
            .btn:hover { transform: translateY(-3px); box-shadow: 0 8px 25px rgba(0,0,0,0.4); }
            .btn:active { transform: translateY(-1px); }
            .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }
            .btn-start { background: linear-gradient(to right, #00b09b, #96c93d); }
            .btn-stop { background: linear-gradient(to right, #ff416c, #ff4b2b); }
            .info-links { margin-top: 35px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,0.1); }
            .info-links a { color: #00ff88; text-decoration: none; margin: 0 15px; font-size: 15px; transition: color 0.3s; }
            .info-links a:hover { text-decoration: underline; color: #00cc6a; }
            #message { height: 25px; margin-top: 20px; color: #00ff88; font-weight: bold; font-size: 16px; }
            .icon { font-size: 22px; }
            .speed-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; animation: pulse 0.1s infinite; }
            @keyframes pulse { 0% { opacity: 0.3; } 50% { opacity: 1; } 100% { opacity: 0.3; } }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚡ Bot Trading ETH/USDT (TEMPO REAL)</h1>
            <p class="subtitle">Estratégia: Adaptive Zero Lag EMA v2 • Conexão: WebSocket • Loop: ~30ms</p>
            
            <div class="status-box {{ 'status-active' if trading_active else 'status-inactive' }}">
                <span class="speed-indicator" style="background-color: {{ '#00ff88' if trading_active else '#ff4444' }}"></span>
                Status: 
                {% if trading_active %}
                    🟢 ATIVO - Operando em Tempo Real
                {% else %}
                    🔴 INATIVO - Aguardando ativação
                {% endif %}
            </div>
            
            <div class="stats">
                <div class="stat-item">
                    <div class="stat-label">Engine</div>
                    <div class="stat-value">Pine Script v3</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Conexão</div>
                    <div class="stat-value">WebSocket OKX</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Velocidade</div>
                    <div class="stat-value">~30ms por tick</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Símbolo</div>
                    <div class="stat-value">ETH-USDT-SWAP</div>
                </div>
            </div>
            
            <div class="button-group">
                <button id="startBtn" onclick="controlBot('start')" 
                        class="btn btn-start" 
                        {% if trading_active %}disabled{% endif %}>
                    <span class="icon">⚡</span> Ligar Bot
                </button>
                <button id="stopBtn" onclick="controlBot('stop')" 
                        class="btn btn-stop"
                        {% if not trading_active %}disabled{% endif %}>
                    <span class="icon">⏹️</span> Parar Bot
                </button>
            </div>
            
            <p id="message"></p>
            
            <div class="info-links">
                <a href="/status" target="_blank">📊 Status Detalhado</a>
                <a href="/strategy-status" target="_blank">📈 Status Estratégia</a>
                <a href="/health" target="_blank">❤️ Saúde do Serviço</a>
                <br><br>
                <small style="color: #888;">
                    Operando com dados de ticker em tempo real via WebSocket.
                </small>
            </div>
        </div>
        
        <script>
        async function controlBot(action) {
            const startBtn = document.getElementById('startBtn');
            const stopBtn = document.getElementById('stopBtn');
            const messageEl = document.getElementById('message');
            
            startBtn.disabled = true;
            stopBtn.disabled = true;
            messageEl.textContent = '⚡ Processando...';
            
            try {
                const response = await fetch('/' + action, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    messageEl.textContent = '✅ ' + (data.message || 'Sucesso!');
                    messageEl.style.color = '#00ff88';
                    setTimeout(() => window.location.reload(), 1000);
                } else {
                    messageEl.textContent = '❌ ' + (data.message || 'Erro desconhecido');
                    messageEl.style.color = '#ff4444';
                    startBtn.disabled = false;
                    stopBtn.disabled = false;
                }
            } catch (error) {
                messageEl.textContent = '❌ Erro de conexão';
                messageEl.style.color = '#ff4444';
                console.error('Erro:', error);
                startBtn.disabled = false;
                stopBtn.disabled = false;
            }
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, trading_active=trading_active)

# ============================================================================
# 6. ENDPOINTS DA API
# ============================================================================
@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint para keep-alive (UptimeRobot)."""
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot (Tempo Real - WebSocket)",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/start', methods=['GET', 'POST'])
def start_trading():
    """Liga o bot - aceita GET e POST para facilitar."""
    global trading_active, trade_thread
    
    if request.method == 'GET':
        return render_template_string(
            '<script>window.location.href="/";</script>'
            '<p>Redirecionando... <a href="/">Clique aqui se não redirecionar</a></p>'
        )
    
    if trading_active:
        return jsonify({"status": "error", "message": "O bot já está ativo!"}), 400
    
    if not okx_client:
        return jsonify({"status": "error", "message": "Cliente OKX não configurado."}), 500
    
    if not strategy_runner:
        return jsonify({"status": "error", "message": "Strategy Runner não inicializado."}), 500
    
    try:
        if keep_alive:
            keep_alive.start_keep_alive()
            logger.info("✅ Sistema de keep-alive iniciado.")
        
        # Iniciar o strategy runner (isso já inicia o WebSocket internamente)
        if not strategy_runner.start():
            return jsonify({"status": "error", "message": "Falha ao iniciar WebSocket."}), 500
        
        trading_active = True
        trade_thread = threading.Thread(target=trading_loop_realtime, daemon=True)
        trade_thread.start()
        
        logger.info("⚡ BOT LIGADO em modo TEMPO REAL (WebSocket)!")
        return jsonify({
            "status": "success", 
            "message": "Bot iniciado em modo tempo real (WebSocket)!",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    """Desliga o bot."""
    global trading_active
    
    try:
        trading_active = False
        
        # Aguardar o loop de trading terminar
        time.sleep(0.1)
        
        if keep_alive:
            keep_alive.stop_keep_alive()
        
        if strategy_runner:
            strategy_runner.stop()
        
        if okx_client:
            okx_client.close_all_positions()
        
        logger.info("⏹️ BOT PARADO (modo tempo real).")
        return jsonify({
            "status": "success",
            "message": "Bot parado.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Erro ao parar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Retorna status detalhado."""
    balance = okx_client.get_balance() if okx_client else 0
    price = strategy_runner.current_price if strategy_runner else None
    
    return jsonify({
        "trading_active": trading_active,
        "balance_usdt": balance,
        "current_eth_price_realtime": price,
        "api_connected": okx_client is not None,
        "strategy_loaded": strategy_runner is not None,
        "keep_alive_active": keep_alive is not None,
        "mode": "WebSocket Real-time",
        "server_time": datetime.now().isoformat()
    })

@app.route('/strategy-status', methods=['GET'])
def get_strategy_status():
    """Retorna status detalhado da estratégia Pine Script."""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    status = strategy_runner.get_strategy_status()
    return jsonify(status)

@app.route('/test-auth', methods=['GET'])
def test_auth():
    """Endpoint para testar autenticação OKX"""
    if not okx_client:
        return jsonify({"status": "error", "message": "Cliente OKX não inicializado"}), 500
    
    try:
        balance = okx_client.get_balance()
        price = strategy_runner.current_price if strategy_runner else None
        
        return jsonify({
            "auth_test": "success" if balance is not None else "failed",
            "balance_usdt": balance,
            "current_eth_price": price,
            "api_key_exists": bool(okx_client.api_key),
            "secret_key_exists": bool(okx_client.secret_key),
            "passphrase_exists": bool(okx_client.passphrase)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# 7. NOVO LOOP DE TRADING ULTRA-RÁPIDO (WEBSOCKET)
# ============================================================================
def trading_loop_realtime():
    """
    Loop principal ULTRA-RÁPIDO para operações em tempo real.
    Executa a cada ~30ms (0.03 segundos).
    """
    logger.info("⏳ Loop de trading ULTRA-RÁPIDO iniciado (WebSocket Real-time)")
    
    cycle = 0
    last_log_time = time.time()
    
    while trading_active and strategy_runner:
        try:
            cycle += 1
            
            # 1. Executar estratégia em tempo real (WebSocket)
            # Esta função é NÃO-BLOQUEANTE e muito rápida
            signal = strategy_runner.run_strategy_realtime()
            
            # 2. Log reduzido para não sobrecarregar (apenas a cada ~3 segundos)
            current_time = time.time()
            if current_time - last_log_time > 3.0:
                price = strategy_runner.current_price
                if price:
                    logger.info(f"🔁 Ciclo #{cycle} | Preço: ${price:.2f} | Sinal: {signal.get('signal', 'HOLD')}")
                last_log_time = current_time
            
            # 3. Aguarda ~30ms para próxima iteração
            # Isso é CRÍTICO para operar em tempo real sem sobrecarregar a CPU
            time.sleep(0.03)
            
        except Exception as e:
            logger.error(f"💥 Erro no loop tempo real: {e}")
            time.sleep(1)  # Pausa maior em caso de erro

# ============================================================================
# 8. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Iniciando servidor na porta {port}...")
    
    if keep_alive:
        keep_alive.start_keep_alive()
    
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
