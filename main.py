import os
import logging
from flask import Flask, jsonify, render_template_string
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

IS_RENDER = os.getenv('RENDER', '').lower() == 'true'
PORT = int(os.environ.get('PORT', 10000))

trading_active = False

@app.route('/')
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading ETH/USDT - SIMULAÇÃO</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; text-align: center; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; }
            .header { background: rgba(0, 255, 136, 0.1); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #00ff88; }
            .status { padding: 15px; border-radius: 10px; margin: 15px 0; font-weight: bold; }
            .active { background: rgba(0, 255, 136, 0.2); border: 2px solid #00ff88; }
            .inactive { background: rgba(255, 68, 68, 0.2); border: 2px solid #ff4444; }
            .btn { padding: 12px 24px; margin: 10px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold; }
            .start-btn { background: #00ff88; color: #000; }
            .stop-btn { background: #ff4444; color: white; }
            .btn:disabled { opacity: 0.5; cursor: not-allowed; }
            .menu { margin: 30px 0; }
            .menu a { color: #00ff88; text-decoration: none; margin: 0 15px; font-size: 16px; }
            .menu a:hover { text-decoration: underline; }
            .info { color: #aaa; font-size: 14px; margin-top: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Simulando (sem ordens reais)' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <div>
                <button class="btn start-btn" onclick="startBot()" {{ 'disabled' if trading_active else '' }}>
                    ⚡ Ligar Bot
                </button>
                <button class="btn stop-btn" onclick="stopBot()" {{ 'disabled' if not trading_active else '' }}>
                    ⏹️ Parar Bot
                </button>
            </div>
            
            <div class="menu">
                <a href="/status">📊 Status</a>
                <a href="/health">❤️ Saúde</a>
            </div>
            
            <div class="info">
                <strong>⚠️ MODO SIMULAÇÃO ATIVO:</strong> Nenhuma ordem real será enviada à OKX.
                <p>Sistema em manutenção - versão simplificada</p>
            </div>
        </div>
        
        <script>
        async function startBot() {
            try {
                const response = await fetch('/start', { method: 'POST' });
                const data = await response.json();
                alert(data.message);
                location.reload();
            } catch (error) {
                alert('Erro: ' + error);
            }
        }
        
        async function stopBot() {
            try {
                const response = await fetch('/stop', { method: 'POST' });
                const data = await response.json();
                alert(data.message);
                location.reload();
            } catch (error) {
                alert('Erro: ' + error);
            }
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, trading_active=trading_active)

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/start', methods=['POST'])
def start_trading():
    global trading_active
    trading_active = True
    logger.info("⚡ Bot SIMULAÇÃO iniciado")
    return jsonify({"status": "success", "message": "Bot iniciado em modo SIMULAÇÃO!"})

@app.route('/stop', methods=['POST'])
def stop_trading():
    global trading_active
    trading_active = False
    logger.info("⏹️ Bot SIMULAÇÃO parado")
    return jsonify({"status": "success", "message": "Bot parado."})

@app.route('/status')
def status():
    return jsonify({
        "trading_active": trading_active,
        "current_price": 2500.0,
        "balance_usdt": 1000.0,
        "environment": "render" if IS_RENDER else "local",
        "simulation_mode": True,
        "bars_processed": 0,
        "next_bar_at": "00:00:00"
    })

if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info("📊 Estratégia: Adaptive Zero Lag EMA v2")
    logger.info("⏰ Timeframe: 30 minutos")
    logger.info("🎯 Modo: SIMULAÇÃO")
    app.run(host='0.0.0.0', port=PORT, debug=False)
