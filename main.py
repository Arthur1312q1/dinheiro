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

# Configurar logging para Render
if os.getenv('RENDER', '').lower() == 'true':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
else:
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
start_time = time.time()

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
# 6. INICIAR ESTRATÉGIA AUTOMATICAMENTE NO RENDER
# ============================================================================
def start_strategy_automatically():
    """Inicia a estratégia automaticamente no Render"""
    global trading_active, trade_thread
    
    if not IS_RENDER:
        return
    
    if not strategy_runner:
        logger.error("❌ Strategy Runner não inicializado")
        return
    
    try:
        logger.info("🚀 Iniciando estratégia automaticamente no Render...")
        
        # Iniciar o strategy runner
        if strategy_runner.start():
            trading_active = True
            
            def trading_loop():
                logger.info("🔄 Loop de trading iniciado automaticamente")
                
                # Contador para logs periódicos
                last_status_log = time.time()
                consecutive_errors = 0
                
                while trading_active and strategy_runner:
                    try:
                        # Executar estratégia
                        status = strategy_runner.run_strategy_realtime()
                        
                        # Log periódico a cada 60 segundos
                        current_time = time.time()
                        if current_time - last_status_log > 60:
                            if strategy_runner.current_price:
                                logger.info(f"📈 Status Bot: Preço ${strategy_runner.current_price:.2f}")
                                logger.info(f"   Barras processadas: {strategy_runner.bar_count}")
                                logger.info(f"   Próxima barra - Sinal BUY: {strategy_runner.next_bar_buy_signal}")
                                logger.info(f"   Próxima barra - Sinal SELL: {strategy_runner.next_bar_sell_signal}")
                                logger.info(f"   Posição: {strategy_runner.position_size:.4f} ETH")
                                logger.info(f"   Lado: {strategy_runner.position_side}")
                                if strategy_runner.entry_price:
                                    logger.info(f"   Preço entrada: ${strategy_runner.entry_price:.2f}")
                            last_status_log = current_time
                        
                        time.sleep(1)  # Loop principal (1 segundo)
                        consecutive_errors = 0  # Resetar contador de erros
                        
                    except Exception as e:
                        consecutive_errors += 1
                        logger.error(f"💥 Erro no loop de trading ({consecutive_errors}): {e}")
                        if consecutive_errors > 10:
                            logger.error("🔴 Muitos erros consecutivos, parando loop...")
                            break
                        time.sleep(5)  # Aguarda 5 segundos em caso de erro
            
            # Iniciar thread de trading
            trade_thread = threading.Thread(target=trading_loop, daemon=True)
            trade_thread.start()
            logger.info("✅ Estratégia iniciada automaticamente no Render")
        else:
            logger.error("❌ Falha ao iniciar a estratégia automaticamente")
            
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar estratégia automaticamente: {e}")

# Iniciar automaticamente se estiver no Render
if IS_RENDER:
    # Aguardar 5 segundos para garantir que tudo está inicializado
    threading.Timer(5.0, start_strategy_automatically).start()

# ============================================================================
# 7. INTERFACE WEB
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
            .debug { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; margin: 20px 0; text-align: left; }
            .debug pre { overflow: auto; max-height: 300px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
                <p><strong>Ambiente:</strong> """ + ("🌍 RENDER" if IS_RENDER else "💻 LOCAL") + """</p>
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
                <a href="/debug">🐛 Debug</a>
                <a href="/health">❤️ Saúde</a>
                <a href="/test-auth">🔐 Testar OKX</a>
                <a href="/restart">🔄 Reiniciar Estratégia</a>
            </div>
            
            <div class="info">
                <strong>⚠️ MODO SIMULAÇÃO ATIVO:</strong> Nenhuma ordem real será enviada à OKX.
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
    return render_template_string(html, trading_active=trading_active, IS_RENDER=IS_RENDER)

# ============================================================================
# 8. ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat(),
        "environment": "render" if IS_RENDER else "local"
    })

@app.route('/debug')
def debug_info():
    """Endpoint para diagnóstico"""
    try:
        strategy_status = {}
        if strategy_runner:
            # Obtém candle_count de forma segura
            candle_count_value = 0
            if strategy_runner.interpreter:
                try:
                    if hasattr(strategy_runner.interpreter, 'candle_count'):
                        candle_count_value = strategy_runner.interpreter.candle_count
                except:
                    candle_count_value = 0
            
            strategy_status = {
                "is_running": strategy_runner.is_running,
                "current_price": strategy_runner.current_price,
                "bar_count": strategy_runner.bar_count,
                "next_bar_buy_signal": getattr(strategy_runner, 'next_bar_buy_signal', False),
                "next_bar_sell_signal": getattr(strategy_runner, 'next_bar_sell_signal', False),
                "position_size": strategy_runner.position_size,
                "position_side": strategy_runner.position_side,
                "entry_price": getattr(strategy_runner, 'entry_price', None),
                "interpreter_initialized": strategy_runner.interpreter is not None,
                "candle_count": candle_count_value,
                "last_bar_timestamp": strategy_runner.last_bar_timestamp.isoformat() if hasattr(strategy_runner, 'last_bar_timestamp') and strategy_runner.last_bar_timestamp else None
            }
        
        # Obtém trade_history_count de forma segura
        trade_history_count = 0
        if trade_history and hasattr(trade_history, 'trades'):
            trade_history_count = len(trade_history.trades)
        
        return jsonify({
            "trading_active": trading_active,
            "environment": "render" if IS_RENDER else "local",
            "simulation_mode": True,
            "strategy_runner": strategy_status,
            "okx_initialized": okx_client is not None,
            "trade_history_count": trade_history_count,
            "uptime_seconds": round(time.time() - start_time, 2),
            "current_time": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/restart')
def restart_strategy():
    """Reinicia a estratégia"""
    global trading_active
    
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        # Parar estratégia atual
        if trading_active:
            strategy_runner.stop()
            trading_active = False
            time.sleep(2)
        
        # Resetar interpretador
        if strategy_runner.interpreter:
            strategy_runner.interpreter.reset()
        
        # Reiniciar estratégia
        if strategy_runner.start():
            trading_active = True
            logger.info("🔄 Estratégia reiniciada com sucesso")
            return jsonify({"success": True, "message": "Estratégia reiniciada"})
        else:
            return jsonify({"error": "Falha ao reiniciar estratégia"}), 500
            
    except Exception as e:
        logger.error(f"Erro ao reiniciar estratégia: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/start', methods=['POST'])
def start_trading():
    global trading_active, trade_thread
    
    if trading_active:
        return jsonify({"status": "error", "message": "Bot já está ativo!"}), 400
    
    if not strategy_runner:
        return jsonify({"status": "error", "message": "Strategy Runner não inicializado."}), 500
    
    try:
        if not strategy_runner.start():
            return jsonify({"status": "error", "message": "Falha ao iniciar WebSocket."}), 500
        
        trading_active = True
        
        def trading_loop():
            logger.info("🔄 Loop de trading iniciado manualmente")
            
            # Contador para logs periódicos
            last_status_log = time.time()
            consecutive_errors = 0
            
            while trading_active and strategy_runner:
                try:
                    # Executar estratégia
                    status = strategy_runner.run_strategy_realtime()
                    
                    # Log periódico a cada 60 segundos
                    current_time = time.time()
                    if current_time - last_status_log > 60:
                        if strategy_runner.current_price:
                            logger.info(f"📈 Status Bot: Preço ${strategy_runner.current_price:.2f}")
                            logger.info(f"   Barras processadas: {strategy_runner.bar_count}")
                            logger.info(f"   Próxima barra - Sinal BUY: {strategy_runner.next_bar_buy_signal}")
                            logger.info(f"   Próxima barra - Sinal SELL: {strategy_runner.next_bar_sell_signal}")
                            logger.info(f"   Posição: {strategy_runner.position_size:.4f} ETH")
                            logger.info(f"   Lado: {strategy_runner.position_side}")
                            if strategy_runner.entry_price:
                                logger.info(f"   Preço entrada: ${strategy_runner.entry_price:.2f}")
                        last_status_log = current_time
                    
                    time.sleep(1)  # Loop principal (1 segundo)
                    consecutive_errors = 0  # Resetar contador de erros
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Erro no loop de trading ({consecutive_errors}): {e}")
                    if consecutive_errors > 10:
                        logger.error("🔴 Muitos erros consecutivos, parando loop...")
                        break
                    time.sleep(5)  # Aguarda 5 segundos em caso de erro
        
        trade_thread = threading.Thread(target=trading_loop, daemon=True)
        trade_thread.start()
        
        logger.info("⚡ BOT LIGADO em modo SIMULAÇÃO!")
        return jsonify({
            "status": "success", 
            "message": "Bot iniciado em modo SIMULAÇÃO!"
        })
        
    except Exception as e:
        logger.error(f"Erro ao iniciar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    global trading_active
    
    if strategy_runner:
        strategy_runner.stop()
    
    trading_active = False
    
    logger.info("⏹️ BOT PARADO")
    return jsonify({"status": "success", "message": "Bot parado."})

@app.route('/status')
def status():
    price = strategy_runner.current_price if strategy_runner else None
    balance = okx_client.get_balance() if okx_client else 0
    
    # Obter sinais da próxima barra
    next_buy = getattr(strategy_runner, 'next_bar_buy_signal', False) if strategy_runner else False
    next_sell = getattr(strategy_runner, 'next_bar_sell_signal', False) if strategy_runner else False
    entry_price = getattr(strategy_runner, 'entry_price', None) if strategy_runner else None
    
    return jsonify({
        "trading_active": trading_active,
        "current_price": price,
        "balance_usdt": balance,
        "next_bar_buy_signal": next_buy,
        "next_bar_sell_signal": next_sell,
        "position_size": strategy_runner.position_size if strategy_runner else 0,
        "position_side": strategy_runner.position_side if strategy_runner else None,
        "entry_price": entry_price,
        "environment": "render" if IS_RENDER else "local",
        "simulation_mode": True
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
                    <div class="stat-card">
                        <div>Lucro Total USDT</div>
                        <div class="stat-value """ + ("positive" if stats['total_pnl_usdt'] > 0 else "negative") + """">
                            $""" + f"{stats['total_pnl_usdt']:.2f}" + """
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

# ============================================================================
# 9. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    
    # Iniciar estratégia automaticamente se estiver em ambiente local
    if not IS_RENDER:
        logger.info("💻 Iniciando estratégia automaticamente (ambiente local)...")
        start_strategy_automatically()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
