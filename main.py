import os
import sys
import logging
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

# ============================================================================
# 1. CONFIGURAÇÃO INICIAL
# ============================================================================
# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================================
# 2. DETECTAR AMBIENTE
# ============================================================================
IS_RENDER = os.getenv('RENDER', '').lower() == 'true'
PORT = int(os.environ.get('PORT', 10000))

if IS_RENDER:
    logger.info("🌍 AMBIENTE RENDER DETECTADO")
else:
    logger.info("💻 Ambiente local detectado")

# ============================================================================
# 3. TRY TO IMPORT MODULES WITH ERROR HANDLING
# ============================================================================
# Variáveis globais
okx_client = None
trade_history = None
keep_alive = None
strategy_runner = None
trading_active = False

def initialize_modules():
    """Tenta inicializar os módulos com tratamento de erro"""
    global okx_client, trade_history, keep_alive, strategy_runner
    
    try:
        # Adicionar diretório atual ao path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, current_dir)
        
        # Tentar importar de src/ ou direto
        try:
            from src.okx_client import OKXClient
            from src.keep_alive import KeepAliveSystem
            from src.strategy_runner import StrategyRunner
            from src.trade_history import TradeHistory
            logger.info("✅ Módulos importados de src/")
        except ImportError:
            # Tentar importar direto
            from okx_client import OKXClient
            from keep_alive import KeepAliveSystem
            from strategy_runner import StrategyRunner
            from trade_history import TradeHistory
            logger.info("✅ Módulos importados direto")
        
        # Verificar credenciais OKX
        api_key = os.getenv('OKX_API_KEY', '')
        secret_key = os.getenv('OKX_SECRET_KEY', '')
        passphrase = os.getenv('OKX_PASSPHRASE', '')
        
        if not api_key or not secret_key or not passphrase:
            logger.warning("⚠️  Credenciais OKX não encontradas. Modo apenas simulação.")
            # Criar cliente mock para simulação
            class MockOKXClient:
                def get_balance(self):
                    return 1000.0
                def get_ticker_price(self, symbol="ETH-USDT-SWAP"):
                    return 2500.0
                def calculate_position_size(self):
                    return 0.1
                def get_candles(self, symbol="ETH-USDT-SWAP", timeframe="30m", limit=100):
                    # Retorna dados mock
                    import random
                    candles = []
                    base_price = 2500.0
                    for i in range(100):
                        candles.append({
                            "timestamp": int((datetime.now().timestamp() - i * 1800) * 1000),
                            "open": base_price + random.uniform(-50, 50),
                            "high": base_price + random.uniform(-30, 70),
                            "low": base_price + random.uniform(-70, 30),
                            "close": base_price + random.uniform(-50, 50),
                            "volume": random.uniform(100, 1000)
                        })
                    return candles
            
            okx_client = MockOKXClient()
        else:
            okx_client = OKXClient()
            logger.info("✅ OKX Client inicializado com credenciais")
        
        # Inicializar histórico
        if IS_RENDER and os.path.exists('/data'):
            trade_history = TradeHistory(file_path="/data/trade_history.json")
        else:
            trade_history = TradeHistory()
        
        # Inicializar keep-alive
        if IS_RENDER:
            SERVICE_NAME = os.getenv('RENDER_SERVICE_NAME', 'okx-eth-trading-bot')
            base_url = f"https://{SERVICE_NAME}.onrender.com"
        else:
            base_url = f"http://localhost:{PORT}"
        
        keep_alive = KeepAliveSystem(base_url=base_url)
        
        # Inicializar strategy runner
        strategy_runner = StrategyRunner(okx_client, trade_history)
        
        logger.info("✅ Sistema inicializado com sucesso")
        return True
        
    except Exception as e:
        logger.error(f"❌ Erro na inicialização: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

# Inicializar módulos
initialize_modules()

# ============================================================================
# 4. INICIAR KEEP-ALIVE AUTOMÁTICO NO RENDER
# ============================================================================
if IS_RENDER and keep_alive:
    try:
        keep_alive.start_keep_alive()
        logger.info("✅ Keep-alive automático iniciado")
    except Exception as e:
        logger.error(f"❌ Erro no keep-alive: {e}")

# ============================================================================
# 5. INTERFACE WEB SIMPLIFICADA
# ============================================================================
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
                <button class="btn start-btn" onclick="controlBot('start')" {{ 'disabled' if trading_active else '' }}>
                    ⚡ Ligar Bot
                </button>
                <button class="btn stop-btn" onclick="controlBot('stop')" {{ 'disabled' if not trading_active else '' }}>
                    ⏹️ Parar Bot
                </button>
            </div>
            
            <div class="menu">
                <a href="/status">📊 Status</a>
                <a href="/history">📜 Histórico</a>
                <a href="/health">❤️ Saúde</a>
                <a href="/test-auth">🔐 Testar OKX</a>
                <a href="/logs">📝 Logs</a>
            </div>
            
            <div class="info">
                <strong>⚠️ MODO SIMULAÇÃO ATIVO:</strong> Nenhuma ordem real será enviada à OKX.
                <p>As trades são executadas com delay de 1 barra (30 minutos), conforme estratégia Pine Script.</p>
            </div>
        </div>
        
        <script>
        async function controlBot(action) {
            const response = await fetch('/' + action, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            const data = await response.json();
            
            if (response.ok) {
                alert('✅ ' + data.message);
                setTimeout(() => location.reload(), 1000);
            } else {
                alert('❌ ' + data.message);
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
    
    if trading_active:
        return jsonify({"status": "error", "message": "Bot já está ativo!"}), 400
    
    if not strategy_runner:
        # Tentar reinicializar
        if not initialize_modules():
            return jsonify({"status": "error", "message": "Strategy Runner não inicializado. Verifique os logs."}), 500
    
    try:
        # Iniciar o strategy runner
        if not strategy_runner.start():
            return jsonify({"status": "error", "message": "Falha ao iniciar o Strategy Runner."}), 500
        
        trading_active = True
        
        logger.info("⚡ BOT LIGADO em modo SIMULAÇÃO!")
        
        return jsonify({
            "status": "success", 
            "message": "Bot iniciado em modo SIMULAÇÃO!",
            "details": "Estratégia: Adaptive Zero Lag EMA v2 | Timeframe: 30 minutos | Delay: 1 barra"
        })
        
    except Exception as e:
        logger.error(f"Erro ao iniciar bot: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    global trading_active
    
    if not strategy_runner:
        return jsonify({"status": "error", "message": "Strategy Runner não inicializado"}), 500
    
    strategy_runner.stop()
    trading_active = False
    
    logger.info("⏹️ BOT PARADO")
    return jsonify({"status": "success", "message": "Bot parado."})

@app.route('/status')
def status():
    if not strategy_runner:
        return jsonify({
            "trading_active": trading_active,
            "error": "Strategy Runner não inicializado",
            "initialized": False
        })
    
    try:
        # Obter status detalhado
        strategy_status = strategy_runner.get_strategy_status()
        
        # Obter saldo
        balance = okx_client.get_balance() if okx_client else 0
        
        return jsonify({
            "trading_active": trading_active,
            "current_price": strategy_status.get("current_price"),
            "balance_usdt": balance,
            "environment": "render" if IS_RENDER else "local",
            "simulation_mode": True,
            "next_bar_at": strategy_status.get("next_bar_at"),
            "bars_processed": strategy_status.get("bars_processed", 0),
            "pending_buy": strategy_status.get("pending_buy", False),
            "pending_sell": strategy_status.get("pending_sell", False),
            "position_size": strategy_status.get("position_size", 0),
            "position_side": strategy_status.get("position_side"),
            "initialized": True
        })
    except Exception as e:
        return jsonify({
            "trading_active": trading_active,
            "error": str(e),
            "initialized": False
        })

@app.route('/history')
def history_page():
    try:
        if not trade_history:
            return "Sistema de histórico não inicializado", 500
        
        trades = trade_history.get_all_trades(limit=50)
        stats = trade_history.get_stats()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>📜 Histórico de Operações</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .header { text-align: center; margin-bottom: 30px; }
                h1 { color: #00ff88; margin-bottom: 10px; }
                .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
                .stat-card { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; text-align: center; }
                .stat-value { font-size: 24px; font-weight: bold; margin: 5px 0; }
                .positive { color: #00ff88; }
                .negative { color: #ff4444; }
                .controls { text-align: center; margin: 20px 0; }
                .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; }
                .btn-refresh { background: #00ff88; color: black; }
                .btn-clear { background: #ff4444; color: white; }
                .btn-back { background: #555; color: white; }
                table { width: 100%; border-collapse: collapse; margin: 20px 0; background: rgba(0,0,0,0.3); }
                th { background: rgba(0,0,0,0.5); padding: 12px; text-align: left; color: #00ff88; }
                td { padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); }
                tr:hover { background: rgba(255,255,255,0.05); }
                .side-buy { color: #00ff88; font-weight: bold; }
                .side-sell { color: #ff4444; font-weight: bold; }
                .empty { text-align: center; padding: 50px; color: #888; }
                .footer { text-align: center; margin-top: 30px; color: #888; font-size: 14px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📜 Histórico de Operações</h1>
                    <p>ETH/USDT - Modo SIMULAÇÃO</p>
                </div>
                
                <div class="stats">
                    <div class="stat-card">
                        <div>Total de Trades</div>
                        <div class="stat-value">""" + str(stats['total_trades']) + """</div>
                    </div>
                    <div class="stat-card">
                        <div>Trades Lucrativas</div>
                        <div class="stat-value positive">""" + str(stats['winning_trades']) + """</div>
                    </div>
                    <div class="stat-card">
                        <div>Trades Prejudiciais</div>
                        <div class="stat-value negative">""" + str(stats['losing_trades']) + """</div>
                    </div>
                    <div class="stat-card">
                        <div>Taxa de Acerto</div>
                        <div class="stat-value """ + ("positive" if stats['win_rate'] > 50 else "negative") + """">
                            """ + f"{stats['win_rate']:.1f}%" + """
                        </div>
                    </div>
                    <div class="stat-card">
                        <div>Lucro Total %</div>
                        <div class="stat-value """ + ("positive" if stats['total_pnl_percent'] > 0 else "negative") + """">
                            """ + f"{stats['total_pnl_percent']:.4f}%" + """
                        </div>
                    </div>
                </div>
                
                <div class="controls">
                    <button class="btn btn-refresh" onclick="location.reload()">🔄 Atualizar</button>
                    <button class="btn btn-clear" onclick="clearHistory()">🗑️ Limpar Histórico</button>
                    <button class="btn btn-back" onclick="location.href='/'">🏠 Voltar</button>
                </div>
        """
        
        if not trades:
            html += """
                <div class="empty">
                    <div style="font-size: 50px; margin-bottom: 20px;">📭</div>
                    <h3>Nenhuma operação registrada</h3>
                    <p>Quando o bot executar trades, elas aparecerão aqui.</p>
                </div>
            """
        else:
            html += """
                <table>
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Data/Hora</th>
                            <th>Operação</th>
                            <th>Preço Entrada</th>
                            <th>Preço Saída</th>
                            <th>Quantidade</th>
                            <th>Variação %</th>
                            <th>Lucro USDT</th>
                            <th>Duração</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            for trade in trades:
                side_class = "side-buy" if trade['side'] == 'buy' else "side-sell"
                pnl_class = "positive" if trade['pnl_percent'] > 0 else "negative" if trade['pnl_percent'] < 0 else ""
                
                html += f"""
                        <tr>
                            <td><strong>#{trade['id']}</strong></td>
                            <td>{trade['entry_time_str']}</td>
                            <td class="{side_class}">{trade['side'].upper()}</td>
                            <td>${trade['entry_price']:.2f}</td>
                            <td>{'$' + str(trade['exit_price']) if trade['exit_price'] else '-'}</td>
                            <td>{trade['quantity']:.4f} ETH</td>
                            <td class="{pnl_class}">{trade['pnl_percent']:.4f}%</td>
                            <td class="{pnl_class}">${trade['pnl_usdt']:.2f}</td>
                            <td>{trade['duration'] or '-'}</td>
                            <td>{'🟢' if trade['status'] == 'open' else '✅' if trade['pnl_percent'] > 0 else '❌'}</td>
                        </tr>
                """
            
            html += """
                    </tbody>
                </table>
            """
        
        html += """
                <div class="footer">
                    <p><strong>⚠️ MODO SIMULAÇÃO:</strong> Nenhuma ordem real foi executada na OKX.</p>
                    <p>Horário de Brasília (BRT)</p>
                </div>
            </div>
            
            <script>
            function clearHistory() {
                if (confirm('Tem certeza que deseja limpar todo o histórico?')) {
                    fetch('/clear-history', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            if (data.success) {
                                alert('Histórico limpo!');
                                location.reload();
                            } else {
                                alert('Erro: ' + data.message);
                            }
                        });
                }
            }
            </script>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Erro na página de histórico: {e}")
        return f"<h1>Erro: {str(e)}</h1>"

@app.route('/clear-history', methods=['POST'])
def clear_history():
    if not trade_history:
        return jsonify({"success": False, "message": "Sistema de histórico não inicializado"}), 500
    
    trade_history.clear_history()
    return jsonify({"success": True, "message": "Histórico limpo."})

@app.route('/test-auth')
def test_auth():
    if not okx_client:
        return jsonify({"error": "OKX Client não configurado"}), 500
    
    try:
        # Verificar se é mock client
        if hasattr(okx_client, '__class__') and okx_client.__class__.__name__ == 'MockOKXClient':
            return jsonify({
                "auth": "mock",
                "balance_usdt": okx_client.get_balance(),
                "current_eth_price": okx_client.get_ticker_price(),
                "message": "Usando cliente mock para simulação"
            })
        
        balance = okx_client.get_balance()
        price = okx_client.get_ticker_price()
        
        return jsonify({
            "auth": "success" if balance else "failed",
            "balance_usdt": balance,
            "current_eth_price": price,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/logs')
def logs_page():
    """Página para visualizar logs recentes"""
    try:
        # Logs simples
        logs = [
            "Logs do sistema OKX Trading Bot",
            f"Horário: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}",
            f"Ambiente: {'RENDER' if IS_RENDER else 'Local'}",
            f"Bot ativo: {'Sim' if trading_active else 'Não'}",
            f"Strategy Runner inicializado: {'Sim' if strategy_runner else 'Não'}",
            f"Trade History inicializado: {'Sim' if trade_history else 'Não'}",
        ]
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>📝 Status do Sistema</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: monospace; background: #1a1a2e; color: #00ff88; padding: 20px; }}
                .container {{ max-width: 800px; margin: 0 auto; }}
                .header {{ margin-bottom: 20px; text-align: center; }}
                h1 {{ color: #00ff88; }}
                .log-container {{ background: rgba(0,0,0,0.7); padding: 20px; border-radius: 8px; }}
                .log-line {{ margin: 10px 0; }}
                .controls {{ margin: 20px 0; text-align: center; }}
                .btn {{ padding: 10px 20px; margin: 5px; background: #00ff88; color: black; border: none; border-radius: 4px; cursor: pointer; }}
                .btn-back {{ background: #555; color: white; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📝 Status do Sistema</h1>
                    <p>OKX ETH Trading Bot</p>
                </div>
                
                <div class="controls">
                    <button class="btn" onclick="location.reload()">🔄 Atualizar</button>
                    <button class="btn btn-back" onclick="location.href='/'">🏠 Voltar</button>
                </div>
                
                <div class="log-container">
                    {"<br>".join(logs)}
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        return f"<h1>Erro ao carregar logs: {str(e)}</h1>"

# ============================================================================
# 7. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"📊 Estratégia: Adaptive Zero Lag EMA v2")
    logger.info(f"⏰ Timeframe: 30 minutos")
    logger.info(f"🎯 Modo: SIMULAÇÃO")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
