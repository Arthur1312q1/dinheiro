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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================================
# 2. IMPORTAR MÓDULOS INTERNOS
# ============================================================================
try:
    from okx_client import OKXClient
    from trading_logic import AdaptiveZeroLagEMA
    from keep_alive import KeepAliveSystem
    logger.info("✅ Módulos internos importados com sucesso.")
    OKXClient, AdaptiveZeroLagEMA, KeepAliveSystem = OKXClient, AdaptiveZeroLagEMA, KeepAliveSystem
except ImportError as e:
    logger.error(f"❌ Erro ao importar módulos: {e}")
    OKXClient = AdaptiveZeroLagEMA = KeepAliveSystem = None

# ============================================================================
# 3. INICIALIZAR COMPONENTES
# ============================================================================
try:
    okx_client = OKXClient() if OKXClient else None
    strategy = AdaptiveZeroLagEMA() if AdaptiveZeroLagEMA else None
    keep_alive = KeepAliveSystem() if KeepAliveSystem else None
    logger.info("✅ Componentes do bot inicializados.")
except Exception as e:
    logger.error(f"⚠️  Falha na inicialização: {e}")
    okx_client = strategy = keep_alive = None

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
        <title>Controle do Bot de Trading</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                text-align: center;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                color: #333;
            }
            .container {
                background: rgba(255, 255, 255, 0.95);
                padding: 40px 30px;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                max-width: 500px;
                width: 90%;
                border: 1px solid rgba(255,255,255,0.2);
            }
            h1 {
                color: #2d3436;
                margin-bottom: 5px;
                font-size: 28px;
            }
            .subtitle {
                color: #636e72;
                margin-bottom: 30px;
                font-size: 16px;
            }
            .status-box {
                padding: 20px;
                margin: 25px 0;
                border-radius: 12px;
                font-weight: bold;
                font-size: 18px;
                border: 2px solid transparent;
                transition: all 0.3s;
            }
            .status-active {
                background-color: #d4edda;
                color: #155724;
                border-color: #c3e6cb;
                box-shadow: 0 0 15px rgba(76, 175, 80, 0.2);
            }
            .status-inactive {
                background-color: #f8d7da;
                color: #721c24;
                border-color: #f5c6cb;
            }
            .button-group {
                display: flex;
                justify-content: center;
                gap: 20px;
                margin: 30px 0;
                flex-wrap: wrap;
            }
            .btn {
                padding: 16px 32px;
                font-size: 18px;
                border: none;
                border-radius: 50px;
                cursor: pointer;
                color: white;
                font-weight: bold;
                transition: all 0.3s;
                min-width: 160px;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            }
            .btn:hover {
                transform: translateY(-3px);
                box-shadow: 0 8px 20px rgba(0,0,0,0.3);
            }
            .btn:active {
                transform: translateY(-1px);
            }
            .btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
                transform: none !important;
            }
            .btn-start {
                background: linear-gradient(to right, #00b09b, #96c93d);
            }
            .btn-stop {
                background: linear-gradient(to right, #ff416c, #ff4b2b);
            }
            .info-links {
                margin-top: 35px;
                padding-top: 20px;
                border-top: 1px solid #eee;
            }
            .info-links a {
                color: #6c5ce7;
                text-decoration: none;
                margin: 0 15px;
                font-size: 15px;
                transition: color 0.3s;
            }
            .info-links a:hover {
                text-decoration: underline;
                color: #a29bfe;
            }
            #message {
                height: 25px;
                margin-top: 20px;
                color: #0984e3;
                font-weight: bold;
                font-size: 16px;
            }
            .icon {
                font-size: 22px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Bot de Trading ETH/USDT</h1>
            <p class="subtitle">Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 45min</p>
            
            <div class="status-box {{ 'status-active' if trading_active else 'status-inactive' }}">
                Status: 
                {% if trading_active %}
                    🟢 ATIVO - Analisando mercado e executando trades
                {% else %}
                    🔴 INATIVO - Aguardando ativação
                {% endif %}
            </div>
            
            <div class="button-group">
                <button id="startBtn" onclick="controlBot('start')" 
                        class="btn btn-start" 
                        {% if trading_active %}disabled{% endif %}>
                    <span class="icon">▶️</span> Ligar Bot
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
                <a href="/health" target="_blank">❤️ Saúde do Serviço</a>
                <br><br>
                <small style="color: #888;">
                    Bot rodando em: <strong>{{ request.host_url }}</strong>
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
            messageEl.textContent = 'Processando...';
            
            try {
                const response = await fetch('/' + action, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                
                const data = await response.json();
                
                if (response.ok) {
                    messageEl.textContent = '✅ ' + (data.message || 'Sucesso!');
                    messageEl.style.color = '#00b894';
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    messageEl.textContent = '❌ ' + (data.message || 'Erro desconhecido');
                    messageEl.style.color = '#d63031';
                    startBtn.disabled = false;
                    stopBtn.disabled = false;
                }
            } catch (error) {
                messageEl.textContent = '❌ Erro de conexão com o servidor';
                messageEl.style.color = '#d63031';
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
        "service": "OKX ETH Trading Bot",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/start', methods=['POST'])
def start_trading():
    """Liga o bot via interface web."""
    global trading_active, trade_thread
    
    if trading_active:
        return jsonify({"status": "error", "message": "O bot já está ativo!"}), 400
    
    if not okx_client:
        return jsonify({"status": "error", "message": "Cliente OKX não configurado. Verifique as API Keys."}), 500
    
    try:
        if keep_alive:
            keep_alive.start_keep_alive()
            logger.info("✅ Sistema de keep-alive iniciado.")
        
        trading_active = True
        trade_thread = threading.Thread(target=trading_loop, daemon=True)
        trade_thread.start()
        
        logger.info("🚀 BOT LIGADO via interface web!")
        return jsonify({
            "status": "success",
            "message": "Bot de trading iniciado com sucesso!",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    """Desliga o bot via interface web."""
    global trading_active
    
    try:
        trading_active = False
        
        if keep_alive:
            keep_alive.stop_keep_alive()
        
        if okx_client:
            okx_client.close_all_positions()
        
        logger.info("⏹️ BOT PARADO via interface web.")
        return jsonify({
            "status": "success",
            "message": "Bot parado e posições fechadas.",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"❌ Erro ao parar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Retorna status detalhado em JSON."""
    balance = okx_client.get_balance() if okx_client else 0
    price = okx_client.get_ticker_price() if okx_client else None
    
    return jsonify({
        "trading_active": trading_active,
        "balance_usdt": balance,
        "current_eth_price": price,
        "api_connected": okx_client is not None,
        "strategy_loaded": strategy is not None,
        "keep_alive_active": keep_alive is not None,
        "server_time": datetime.now().isoformat(),
        "service_url": request.host_url
    })

# ============================================================================
# 7. LÓGICA DE TRADING (EXECUTADA EM SEGUNDO PLANO)
# ============================================================================
def trading_loop():
    """Loop principal que executa a estratégia."""
    logger.info("🔄 Loop de trading iniciado.")
    
    cycle = 0
    while trading_active and okx_client and strategy:
        try:
            cycle += 1
            logger.info(f"📊 Ciclo #{cycle} - Obtendo dados...")
            
            candles = okx_client.get_candles(timeframe="45m", limit=100)
            
            if len(candles) < 30:
                logger.warning(f"Dados insuficientes ({len(candles)} candles)")
                time.sleep(300)
                continue
            
            signal = strategy.calculate_signals(candles)
            logger.info(f"Sinal calculado: {signal}")
            
            if signal.get("signal") in ["BUY", "SELL"] and signal.get("strength", 0) > 0:
                logger.info(f"🚨 SINAL: {signal['signal']} (Força: {signal['strength']})")
                
                position_size = okx_client.calculate_position_size(sl_points=2000)
                
                if position_size > 0:
                    success = okx_client.place_order(
                        side=signal["signal"],
                        quantity=position_size,
                        sl_points=2000,
                        tp_points=55
                    )
                    
                    if success:
                        logger.info(f"✅ Ordem executada: {signal['signal']} {position_size:.4f} ETH")
                    else:
                        logger.error("❌ Falha na ordem")
            
            time.sleep(300)
            
        except Exception as e:
            logger.error(f"💥 Erro no ciclo: {e}")
            time.sleep(60)

# ============================================================================
# 8. PONTO DE ENTRADA (PARA EXECUÇÃO LOCAL)
# ============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Iniciando servidor na porta {port}...")
    
    if keep_alive:
        keep_alive.start_keep_alive()
    
    app.run(host='0.0.0.0', port=port, debug=False)
