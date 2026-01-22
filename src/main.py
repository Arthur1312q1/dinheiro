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
# 2. DETECTAR AMBIENTE RENDER ANTES DE IMPORTAR MÓDULOS
# ============================================================================
IS_RENDER = os.getenv('RENDER', '').lower() == 'true'
PORT = int(os.environ.get('PORT', 10000))

if IS_RENDER:
    logger.info("🌍 AMBIENTE RENDER DETECTADO - Configurando keep-alive automático")
    # No Render, construir URL externa
    SERVICE_NAME = os.getenv('RENDER_SERVICE_NAME', 'okx-eth-trading-bot')
    RENDER_DOMAIN = os.getenv('RENDER_EXTERNAL_URL', 'onrender.com')
    EXTERNAL_URL = f"https://{SERVICE_NAME}.{RENDER_DOMAIN}"
    logger.info(f"🔗 URL Externa do Render: {EXTERNAL_URL}")
else:
    EXTERNAL_URL = f"http://localhost:{PORT}"
    logger.info("💻 Ambiente local detectado")

# ============================================================================
# 3. IMPORTAR MÓDULOS INTERNOS
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
# 4. INICIALIZAR COMPONENTES
# ============================================================================
try:
    okx_client = OKXClient() if OKXClient else None
    
    # Inicializar KeepAliveSystem com URL externa no Render
    # IMPORTANTE: Sempre usar URL externa no Render
    if IS_RENDER:
        base_url = EXTERNAL_URL
        logger.info(f"🔗 Keep-alive usando URL externa: {base_url}")
    else:
        base_url = EXTERNAL_URL
    
    keep_alive = KeepAliveSystem(base_url=base_url) if KeepAliveSystem else None
    
    # Inicializar o Strategy Runner
    strategy_runner = None
    if okx_client:
        strategy_runner = StrategyRunner(okx_client)
        logger.info("✅ Strategy Runner (WebSocket + Barras 30m) inicializado.")
    
    logger.info("✅ Componentes do bot inicializados.")
    
except Exception as e:
    logger.error(f"⚠️  Falha na inicialização: {e}")
    okx_client = None
    keep_alive = None
    strategy_runner = None

# ============================================================================
# 5. INICIALIZAÇÃO AUTOMÁTICA DO KEEP-ALIVE (APENAS NO RENDER)
# ============================================================================
if IS_RENDER and keep_alive:
    try:
        # Iniciar keep-alive imediatamente no Render
        keep_alive.start_keep_alive()
        logger.info("✅ Keep-alive iniciado automaticamente no Render")
        logger.info("🔄 Enviando sinais a cada ~26 segundos para manter serviço ativo")
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar keep-alive automático: {e}")

# ============================================================================
# 6. VARIÁVEIS DE ESTADO
# ============================================================================
trading_active = False
trade_thread = None

# ============================================================================
# 7. INTERFACE WEB (HTML COM BOTÕES)
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
            .container { background: rgba(255, 255, 255, 0.1); backdrop-filter: blur(10px); padding: 40px 30px; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.5); max-width: 650px; width: 90%; border: 1px solid rgba(255,255,255,0.1); }
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
            .env-badge { display: inline-block; padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; margin-left: 10px; }
            .env-render { background: #5a67d8; color: white; }
            .env-local { background: #38a169; color: white; }
            @keyframes pulse { 0% { opacity: 0.3; } 50% { opacity: 1; } 100% { opacity: 0.3; } }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>⚡ Bot Trading ETH/USDT (MODO SIMULAÇÃO)
                <span class="env-badge {{ 'env-render' if is_render else 'env-local' }}">
                    {{ 'RENDER' if is_render else 'LOCAL' }}
                </span>
            </h1>
            <p class="subtitle">Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
            
            <div class="status-box {{ 'status-active' if trading_active else 'status-inactive' }}">
                <span class="speed-indicator" style="background-color: {{ '#00ff88' if trading_active else '#ff4444' }}"></span>
                Status: 
                {% if trading_active %}
                    🟢 ATIVO - Simulando (sem ordens reais)
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
                    <div class="stat-label">Timeframe</div>
                    <div class="stat-value">30 minutos</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Modo</div>
                    <div class="stat-value">SIMULAÇÃO</div>
                </div>
                <div class="stat-item">
                    <div class="stat-label">Símbolo</div>
                    <div class="stat-value">ETH-USDT-SWAP</div>
                </div>
            </div>
            
            {% if is_render %}
            <div style="background: rgba(0, 255, 136, 0.1); border: 1px solid #00ff88; border-radius: 10px; padding: 15px; margin: 20px 0;">
                <strong>✅ KEEP-ALIVE ATIVO:</strong> Serviço mantido automaticamente pelo sistema interno
                <br><small>4 endpoints pingados a cada ~26s</small>
            </div>
            {% endif %}
            
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
                <a href="/render-ping" target="_blank">🔄 Ping Render</a>
                <a href="/test-auth" target="_blank">🔐 Testar Autenticação</a>
                <br><br>
                <small style="color: #888;">
                    <strong>MODO SIMULAÇÃO ATIVO:</strong> Nenhuma ordem real será enviada à OKX.
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
                    setTimeout(() => window.location.reload(), 1000)
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
    return render_template_string(html, trading_active=trading_active, is_render=IS_RENDER)

# ============================================================================
# 8. ENDPOINTS DA API (INCLUINDO NOVO /render-ping)
# ============================================================================
@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint para keep-alive (UptimeRobot)."""
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot (Simulação - Barras 30m)",
        "environment": "render" if IS_RENDER else "local",
        "trading_active": trading_active,
        "keep_alive_active": keep_alive.is_running if keep_alive else False,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/ping-internal-1', methods=['GET'])
def internal_ping_1():
    """PRIMEIRO ENDPOINT DE PING INTERNO"""
    return jsonify({
        "status": "pong_internal_1",
        "message": "Sinal interno de keep-alive #1",
        "environment": "render" if IS_RENDER else "local",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/ping-internal-2', methods=['GET'])
def internal_ping_2():
    """SEGUNDO ENDPOINT DE PING INTERNO"""
    return jsonify({
        "status": "pong_internal_2",
        "message": "Sinal interno de keep-alive #2",
        "environment": "render" if IS_RENDER else "local",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/render-ping', methods=['GET'])
def render_ping():
    """Endpoint específico para ping de serviços externos (Render)"""
    cycle_count = keep_alive.cycle_count if keep_alive else 0
    return jsonify({
        "status": "pong",
        "service": "OKX ETH Trading Bot",
        "environment": "render" if IS_RENDER else "local",
        "keep_alive_cycles": cycle_count,
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat(),
        "message": "✅ Serviço ativo no Render - Keep-alive funcionando"
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
        # No Render, o keep-alive já está rodando automaticamente
        # Em ambiente local, iniciamos manualmente
        if not IS_RENDER and keep_alive:
            keep_alive.start_keep_alive()
            logger.info("✅ Sistema de keep-alive interno iniciado (ambiente local).")
        
        # Iniciar o strategy runner (modo SIMULAÇÃO)
        if not strategy_runner.start():
            return jsonify({"status": "error", "message": "Falha ao iniciar WebSocket."}), 500
        
        trading_active = True
        trade_thread = threading.Thread(target=trading_loop_realtime, daemon=True)
        trade_thread.start()
        
        logger.info("⚡ BOT LIGADO em modo SIMULAÇÃO (Barras 30m)!")
        logger.info("⚠️  NENHUMA ORDEM REAL SERÁ ENVIADA À OKX")
        return jsonify({
            "status": "success", 
            "message": "Bot iniciado em modo SIMULAÇÃO (Barras 30m)!",
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
        
        # Parar keep-alive apenas em ambiente local
        # No Render, mantemos rodando para manter serviço ativo
        if not IS_RENDER and keep_alive:
            keep_alive.stop_keep_alive()
        
        if strategy_runner:
            strategy_runner.stop()
        
        logger.info("⏹️ BOT PARADO (modo simulação).")
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
        "environment": "render" if IS_RENDER else "local",
        "balance_usdt": balance,
        "current_eth_price_realtime": price,
        "api_connected": okx_client is not None,
        "strategy_loaded": strategy_runner is not None,
        "keep_alive_active": keep_alive.is_running if keep_alive else False,
        "keep_alive_cycles": keep_alive.cycle_count if keep_alive else 0,
        "mode": "SIMULAÇÃO (Barras 30m)",
        "simulation_mode": True,
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
            "passphrase_exists": bool(okx_client.passphrase),
            "environment": "render" if IS_RENDER else "local"
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# 9. NOVO LOOP DE TRADING ULTRA-RÁPIDO (WEBSOCKET + BARRAS 30m)
# ============================================================================
def trading_loop_realtime():
    """
    Loop principal ULTRA-RÁPIDO para operações em tempo real.
    Executa a cada ~30ms, mas só processa no fechamento de barras de 30min.
    """
    logger.info("⏳ Loop de trading iniciado (WebSocket + Barras 30m)")
    
    cycle = 0
    last_log_time = time.time()
    
    while trading_active and strategy_runner:
        try:
            cycle += 1
            
            # 1. Executa estratégia em tempo real
            # Esta função sempre retorna HOLD - a execução real acontece internamente
            status = strategy_runner.run_strategy_realtime()
            
            # 2. Log reduzido para não sobrecarregar (apenas a cada ~10 segundos)
            current_time = time.time()
            if current_time - last_log_time > 10.0:  # Aumentado para 10 segundos
                price = status.get('current_price')
                bar_count = status.get('bar_count', 0)
                pending_buy = status.get('pending_buy', False)
                pending_sell = status.get('pending_sell', False)
                
                if price:
                    logger.info(f"🔁 Ciclo #{cycle} | Barra #{bar_count} | Preço: ${price:.2f}")
                    logger.info(f"   Estados: pendingBuy={pending_buy}, pendingSell={pending_sell}")
                
                last_log_time = current_time
            
            # 3. Aguarda ~30ms para próxima iteração
            # Isso é CRÍTICO para operar em tempo real sem sobrecarregar a CPU
            time.sleep(0.03)
            
        except Exception as e:
            logger.error(f"💥 Erro no loop tempo real: {e}")
            time.sleep(1)  # Pausa maior em caso de erro

# ============================================================================
# 10. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🌐 Iniciando servidor na porta {PORT}...")
    logger.info(f"🌍 Ambiente: {'RENDER' if IS_RENDER else 'LOCAL'}")
    
    # Se estiver no Render e o keep-alive ainda não iniciou, inicie (segurança extra)
    if IS_RENDER and keep_alive and not keep_alive.is_running:
        keep_alive.start_keep_alive()
        logger.info("🔄 Keep-alive iniciado via ponto de entrada (backup)")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
