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
# Adicionar src ao path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, 'src')
sys.path.insert(0, src_path)

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
# 3. IMPORTAR MÓDULOS DE src/
# ============================================================================
try:
    # Importar tudo de src
    from src.okx_client import OKXClient
    from src.keep_alive import KeepAliveSystem
    from src.strategy_runner import StrategyRunner
    from src.trade_history import TradeHistory
    from src.web_socket_manager import OKXWebSocketManager
    
    logger.info("✅ Módulos importados com sucesso")
    
    # Inicializar componentes
    okx_client = OKXClient()
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
    
except Exception as e:
    logger.error(f"❌ Erro na inicialização: {e}")
    okx_client = None
    trade_history = None
    keep_alive = None
    strategy_runner = None

# ============================================================================
# 4. VARIÁVEIS DE ESTADO
# ============================================================================
trading_active = False
trade_thread = None

# ============================================================================
# 5. INICIAR KEEP-ALIVE AUTOMÁTICO NO RENDER
# ============================================================================
if IS_RENDER and keep_alive:
    try:
        keep_alive.start_keep_alive()
        logger.info("✅ Keep-alive automático iniciado")
    except Exception as e:
        logger.error(f"❌ Erro no keep-alive: {e}")

# ============================================================================
# 6. INTERFACE WEB
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
            .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
            .status-card { background: rgba(255, 255, 255, 0.05); padding: 15px; border-radius: 8px; text-align: left; }
            .status-card h3 { margin-top: 0; color: #00ff88; }
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
            
            <div class="status-grid">
                <div class="status-card">
                    <h3>📈 Preço Atual</h3>
                    <div id="current-price">Carregando...</div>
                </div>
                <div class="status-card">
                    <h3>💰 Saldo</h3>
                    <div id="balance">Carregando...</div>
                </div>
                <div class="status-card">
                    <h3>📊 Barras Processadas</h3>
                    <div id="bars-processed">0</div>
                </div>
                <div class="status-card">
                    <h3>🔄 Próxima Barra</h3>
                    <div id="next-bar">--:--:--</div>
                </div>
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
        
        // Atualizar status em tempo real
        async function updateStatus() {
            try {
                const response = await fetch('/status');
                const data = await response.json();
                
                if (data.current_price) {
                    document.getElementById('current-price').innerHTML = `$${data.current_price.toFixed(2)}`;
                }
                
                if (data.balance_usdt) {
                    document.getElementById('balance').innerHTML = `$${data.balance_usdt.toFixed(2)}`;
                }
                
                if (data.next_bar_at) {
                    document.getElementById('next-bar').innerHTML = data.next_bar_at;
                }
                
                if (data.bars_processed !== undefined) {
                    document.getElementById('bars-processed').innerHTML = data.bars_processed;
                }
                
                // Atualizar status do bot
                const statusDiv = document.querySelector('.status');
                if (data.trading_active) {
                    statusDiv.className = 'status active';
                    statusDiv.innerHTML = '🟢 ATIVO - Simulando (sem ordens reais)';
                } else {
                    statusDiv.className = 'status inactive';
                    statusDiv.innerHTML = '🔴 INATIVO - Aguardando ativação';
                }
                
            } catch (error) {
                console.error('Erro ao atualizar status:', error);
            }
        }
        
        // Atualizar a cada 5 segundos
        setInterval(updateStatus, 5000);
        updateStatus(); // Executar imediatamente
        </script>
    </body>
    </html>
    """
    return render_template_string(html, trading_active=trading_active)

# ============================================================================
# 7. ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot",
        "trading_active": trading_active,
        "environment": "render" if IS_RENDER else "local",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/ping-internal-1')
def ping1():
    return jsonify({"status": "pong1", "time": datetime.now().isoformat()})

@app.route('/ping-internal-2')
def ping2():
    return jsonify({"status": "pong2", "time": datetime.now().isoformat()})

@app.route('/start', methods=['POST'])
def start_trading():
    global trading_active, trade_thread
    
    if trading_active:
        return jsonify({"status": "error", "message": "Bot já está ativo!"}), 400
    
    if not strategy_runner:
        return jsonify({"status": "error", "message": "Strategy Runner não inicializado."}), 500
    
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
            "error": "Strategy Runner não inicializado"
        })
    
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
        "ws_connected": strategy_status.get("ws_connected", False),
        "price_fresh": strategy_status.get("price_fresh", False)
    })

@app.route('/history')
def history_page():
    try:
        if not trade_history:
            return "Sistema de histórico não inicializado", 500
        
        trades = trade_history.get_all_trades(limit=100)
        stats = trade_history.get_stats()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>📜 Histórico de Operações</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .header { text-align: center; margin-bottom: 30px; }
                h1 { color: #00ff88; margin-bottom: 10px; }
                .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
                .stat-card { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; text-align: center; }
                .stat-value { font-size: 24px; font-weight: bold; margin: 5px 0; }
                .positive { color: #00ff88; }
                .negative { color: #ff4444; }
                .neutral { color: #888; }
                .controls { text-align: center; margin: 20px 0; }
                .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; }
                .btn-refresh { background: #00ff88; color: black; }
                .btn-clear { background: #ff4444; color: white; }
                .btn-back { background: #555; color: white; }
                .btn-export { background: #3a86ff; color: white; }
                table { width: 100%; border-collapse: collapse; margin: 20px 0; background: rgba(0,0,0,0.3); }
                th { background: rgba(0,0,0,0.5); padding: 12px; text-align: left; color: #00ff88; }
                td { padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.1); }
                tr:hover { background: rgba(255,255,255,0.05); }
                .side-buy { color: #00ff88; font-weight: bold; }
                .side-sell { color: #ff4444; font-weight: bold; }
                .pnl-positive { color: #00ff88; }
                .pnl-negative { color: #ff4444; }
                .pnl-neutral { color: #888; }
                .empty { text-align: center; padding: 50px; color: #888; }
                .footer { text-align: center; margin-top: 30px; color: #888; font-size: 14px; }
                .filter { margin: 20px 0; text-align: center; }
                .filter select { padding: 8px; background: #333; color: white; border: 1px solid #555; border-radius: 4px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📜 Histórico de Operações</h1>
                    <p>ETH/USDT - Modo SIMULAÇÃO • Timeframe: 30 minutos</p>
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
                        <div class="stat-value """ + ("positive" if stats['win_rate'] > 50 else "negative" if stats['win_rate'] < 50 else "neutral") + """">
                            """ + f"{stats['win_rate']:.1f}%" + """
                        </div>
                    </div>
                    <div class="stat-card">
                        <div>Lucro Total %</div>
                        <div class="stat-value """ + ("positive" if stats['total_pnl_percent'] > 0 else "negative" if stats['total_pnl_percent'] < 0 else "neutral") + """">
                            """ + f"{stats['total_pnl_percent']:.4f}%" + """
                        </div>
                    </div>
                    <div class="stat-card">
                        <div>Lucro Total USDT</div>
                        <div class="stat-value """ + ("positive" if stats['total_pnl_usdt'] > 0 else "negative" if stats['total_pnl_usdt'] < 0 else "neutral") + """">
                            $""" + f"{stats['total_pnl_usdt']:.2f}" + """
                        </div>
                    </div>
                </div>
                
                <div class="controls">
                    <button class="btn btn-refresh" onclick="location.reload()">🔄 Atualizar</button>
                    <button class="btn btn-export" onclick="exportHistory()">📥 Exportar CSV</button>
                    <button class="btn btn-clear" onclick="clearHistory()">🗑️ Limpar Histórico</button>
                    <button class="btn btn-back" onclick="location.href='/'">🏠 Voltar</button>
                </div>
                
                <div class="filter">
                    <label for="status-filter">Filtrar por status: </label>
                    <select id="status-filter" onchange="filterTable()">
                        <option value="all">Todas as trades</option>
                        <option value="open">Abertas</option>
                        <option value="closed">Fechadas</option>
                        <option value="profit">Lucrativas</option>
                        <option value="loss">Prejudiciais</option>
                    </select>
                </div>
        """
        
        if not trades:
            html += """
                <div class="empty">
                    <div style="font-size: 50px; margin-bottom: 20px;">📭</div>
                    <h3>Nenhuma operação registrada</h3>
                    <p>Quando o bot executar trades, elas aparecerão aqui.</p>
                    <p><small>Lembre-se: O bot opera em barras de 30 minutos e executa com delay de 1 barra.</small></p>
                </div>
            """
        else:
            html += """
                <table id="trades-table">
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
                
                # Determinar classe do PnL
                pnl_percent = trade.get('pnl_percent', 0)
                if pnl_percent > 0:
                    pnl_class = "pnl-positive"
                elif pnl_percent < 0:
                    pnl_class = "pnl-negative"
                else:
                    pnl_class = "pnl-neutral"
                
                # Status icon
                status_icon = '🟢' if trade['status'] == 'open' else '✅' if pnl_percent > 0 else '❌' if pnl_percent < 0 else '➖'
                
                html += f"""
                        <tr class="trade-row" data-status="{trade['status']}" data-pnl="{pnl_percent}">
                            <td><strong>#{trade['id']}</strong></td>
                            <td>{trade['entry_time_str']}</td>
                            <td class="{side_class}">{trade['side'].upper()}</td>
                            <td>${trade['entry_price']:.2f}</td>
                            <td>{'$' + str(trade['exit_price']) if trade['exit_price'] else '-'}</td>
                            <td>{trade['quantity']:.4f} ETH</td>
                            <td class="{pnl_class}">{trade['pnl_percent']:.4f}%</td>
                            <td class="{pnl_class}">${trade['pnl_usdt']:.2f}</td>
                            <td>{trade['duration'] or '-'}</td>
                            <td>{status_icon}</td>
                        </tr>
                """
            
            html += """
                    </tbody>
                </table>
            """
        
        html += """
                <div class="footer">
                    <p><strong>⚠️ MODO SIMULAÇÃO:</strong> Nenhuma ordem real foi executada na OKX.</p>
                    <p>Horário de Brasília (BRT) • Delay de execução: 1 barra (30 minutos)</p>
                </div>
            </div>
            
            <script>
            function filterTable() {
                const filter = document.getElementById('status-filter').value;
                const rows = document.querySelectorAll('.trade-row');
                
                rows.forEach(row => {
                    const status = row.getAttribute('data-status');
                    const pnl = parseFloat(row.getAttribute('data-pnl'));
                    let show = true;
                    
                    switch(filter) {
                        case 'open':
                            show = status === 'open';
                            break;
                        case 'closed':
                            show = status === 'closed';
                            break;
                        case 'profit':
                            show = status === 'closed' && pnl > 0;
                            break;
                        case 'loss':
                            show = status === 'closed' && pnl < 0;
                            break;
                        case 'all':
                        default:
                            show = true;
                    }
                    
                    row.style.display = show ? '' : 'none';
                });
            }
            
            function clearHistory() {
                if (confirm('Tem certeza que deseja limpar todo o histórico? Esta ação não pode ser desfeita.')) {
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
            
            function exportHistory() {
                fetch('/export-history')
                    .then(response => response.blob())
                    .then(blob => {
                        const url = window.URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'trade_history.csv';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        window.URL.revokeObjectURL(url);
                    })
                    .catch(error => {
                        alert('Erro ao exportar histórico: ' + error);
                    });
            }
            </script>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Erro na página de histórico: {e}")
        return f"<h1>Erro: {str(e)}</h1>"

@app.route('/export-history')
def export_history():
    if not trade_history:
        return "Sistema de histórico não inicializado", 500
    
    try:
        trades = trade_history.get_all_trades(limit=1000)
        
        # Criar CSV
        import csv
        from io import StringIO
        
        output = StringIO()
        writer = csv.writer(output)
        
        # Cabeçalho
        writer.writerow([
            'ID', 'Data/Hora', 'Operação', 'Preço Entrada', 'Preço Saída',
            'Quantidade (ETH)', 'Variação %', 'Lucro USDT', 'Duração', 'Status'
        ])
        
        # Dados
        for trade in trades:
            writer.writerow([
                trade['id'],
                trade['entry_time_str'],
                trade['side'].upper(),
                f"{trade['entry_price']:.2f}",
                f"{trade['exit_price']:.2f}" if trade['exit_price'] else '',
                f"{trade['quantity']:.4f}",
                f"{trade['pnl_percent']:.4f}",
                f"{trade['pnl_usdt']:.2f}",
                trade['duration'] or '',
                trade['status']
            ])
        
        # Retornar arquivo
        from flask import Response
        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": "attachment; filename=trade_history.csv"}
        )
        
    except Exception as e:
        logger.error(f"Erro ao exportar histórico: {e}")
        return jsonify({"error": str(e)}), 500

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
        balance = okx_client.get_balance()
        price = okx_client.get_ticker_price()
        
        return jsonify({
            "auth": "success" if balance else "failed",
            "balance_usdt": balance,
            "current_eth_price": price,
            "api_key_exists": bool(okx_client.api_key),
            "secret_key_exists": bool(okx_client.secret_key),
            "passphrase_exists": bool(okx_client.passphrase)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/logs')
def logs_page():
    """Página para visualizar logs recentes"""
    try:
        import io
        from datetime import datetime, timedelta
        
        # Ler últimos 1000 caracteres do log (ajuste conforme necessário)
        log_content = ""
        
        # Tentar ler arquivo de log se existir
        log_files = ['app.log', 'debug.log', 'logs/app.log']
        log_found = False
        
        for log_file in log_files:
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # Pegar últimas 100 linhas
                    log_content = ''.join(lines[-100:])
                    log_found = True
                    break
        
        if not log_found:
            log_content = "Nenhum arquivo de log encontrado."
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>📝 Logs do Sistema</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: monospace; background: #1a1a2e; color: #00ff88; padding: 20px; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .header {{ margin-bottom: 20px; text-align: center; }}
                h1 {{ color: #00ff88; }}
                .log-container {{ background: rgba(0,0,0,0.7); padding: 20px; border-radius: 8px; overflow-x: auto; }}
                .log-line {{ margin: 2px 0; white-space: pre-wrap; }}
                .log-error {{ color: #ff4444; }}
                .log-warning {{ color: #ffaa00; }}
                .log-info {{ color: #00ff88; }}
                .log-debug {{ color: #888; }}
                .controls {{ margin: 20px 0; text-align: center; }}
                .btn {{ padding: 10px 20px; margin: 5px; background: #00ff88; color: black; border: none; border-radius: 4px; cursor: pointer; }}
                .btn-back {{ background: #555; color: white; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📝 Logs do Sistema</h1>
                    <p>Últimas 100 linhas • Horário: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
                </div>
                
                <div class="controls">
                    <button class="btn" onclick="location.reload()">🔄 Atualizar Logs</button>
                    <button class="btn btn-back" onclick="location.href='/'">🏠 Voltar</button>
                </div>
                
                <div class="log-container">
                    <pre>{log_content}</pre>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        return f"<h1>Erro ao carregar logs: {str(e)}</h1>"

@app.route('/restart', methods=['POST'])
def restart_bot():
    """Reinicia o bot (para debugging)"""
    global trading_active
    
    try:
        # Parar se estiver rodando
        if trading_active and strategy_runner:
            strategy_runner.stop()
            trading_active = False
        
        # Pequena pausa
        time.sleep(2)
        
        return jsonify({
            "success": True,
            "message": "Bot reiniciado. Use /start para iniciar novamente."
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ============================================================================
# 8. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"📊 Estratégia: Adaptive Zero Lag EMA v2")
    logger.info(f"⏰ Timeframe: 30 minutos")
    logger.info(f"🎯 Modo: SIMULAÇÃO (sem ordens reais)")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
