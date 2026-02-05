#!/usr/bin/env python3
"""
MAIN.PY ATUALIZADO - USANDO STRATEGY RUNNER EXATO
"""
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

# Configurar logging para Render - MAIS DETALHADO
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
    # Importar tudo de src - IMPORTANTE: Usar novo StrategyRunnerExact
    from src.okx_client import OKXClient
    from src.keep_alive import KeepAliveSystem
    from src.strategy_runner_exact import StrategyRunnerExact  # NOVO!
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
    
    # Inicializar NOVO strategy runner (100% TradingView)
    strategy_runner = StrategyRunnerExact(okx_client, trade_history)
    
    logger.info("✅ Sistema inicializado com sucesso (STRATEGY RUNNER EXACT)")
    
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
                last_bar_logged = None
                
                while trading_active and strategy_runner:
                    try:
                        # Executar estratégia
                        status = strategy_runner.run_strategy_realtime()
                        
                        # Log detalhado a cada nova barra
                        if strategy_runner.last_bar_timestamp and strategy_runner.last_bar_timestamp != last_bar_logged:
                            logger.info("=" * 60)
                            logger.info(f"📊 BARRA {strategy_runner.last_bar_timestamp.strftime('%H:%M')} PROCESSADA")
                            logger.info(f"   Preço: ${strategy_runner.current_price:.2f}")
                            
                            # Sinais pendentes do NOVO runner
                            pending_buy = getattr(strategy_runner, 'pending_buy', False)
                            pending_sell = getattr(strategy_runner, 'pending_sell', False)
                            
                            logger.info(f"   Sinal BUY pendente: {pending_buy}")
                            logger.info(f"   Sinal SELL pendente: {pending_sell}")
                            logger.info(f"   Posição: {strategy_runner.position_size:.4f} ETH")
                            logger.info(f"   Lado: {strategy_runner.position_side}")
                            
                            if strategy_runner.entry_price:
                                logger.info(f"   Entrada: ${strategy_runner.entry_price:.2f}")
                            
                            # Info do trailing stop
                            if strategy_runner.trailing_manager:
                                trailing_activated = getattr(strategy_runner.trailing_manager, 'trailing_activated', False)
                                current_stop = getattr(strategy_runner.trailing_manager, 'current_stop', None)
                                if trailing_activated:
                                    logger.info(f"   Trailing Stop: ${current_stop:.2f} (ATIVADO)")
                            
                            logger.info("=" * 60)
                            last_bar_logged = strategy_runner.last_bar_timestamp
                        
                        # Log periódico a cada 30 segundos
                        current_time = time.time()
                        if current_time - last_status_log > 30:
                            if strategy_runner.current_price:
                                position_str = f"{strategy_runner.position_side or 'FLAT'} {abs(strategy_runner.position_size):.4f} ETH"
                                logger.info(f"📈 Status: ${strategy_runner.current_price:.2f} | Posição: {position_str}")
                                
                                # Info do trailing
                                if strategy_runner.trailing_manager:
                                    trailing = strategy_runner.trailing_manager
                                    if trailing.trailing_activated:
                                        logger.info(f"   Trailing Stop: ${trailing.current_stop:.2f}")
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
    # Obter informações da posição atual
    position_info = {}
    if strategy_runner:
        # Para o NOVO runner, os atributos podem ser diferentes
        current_stop = None
        if strategy_runner.trailing_manager:
            current_stop = strategy_runner.trailing_manager.current_stop
        
        position_info = {
            'has_position': strategy_runner.position_size != 0,
            'position_side': strategy_runner.position_side,
            'position_size': abs(strategy_runner.position_size),
            'entry_price': strategy_runner.entry_price,
            'current_price': strategy_runner.current_price,
            'stop_loss': current_stop,  # Usa o trailing stop atual
            'take_profit': getattr(strategy_runner, 'take_profit_price', None),
            'trailing_activated': getattr(strategy_runner.trailing_manager, 'trailing_activated', False) if strategy_runner.trailing_manager else False
        }
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading ETH/USDT - ESTRATÉGIA EXATA</title>
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
            .force-close-btn { background: #ffaa00; color: #000; }
            .btn:disabled { opacity: 0.5; cursor: not-allowed; }
            .menu { margin: 30px 0; }
            .menu a { color: #00ff88; text-decoration: none; margin: 0 15px; font-size: 16px; }
            .menu a:hover { text-decoration: underline; }
            .info { color: #aaa; font-size: 14px; margin-top: 20px; }
            .position-info { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 10px; margin: 20px 0; text-align: left; }
            .position-info h3 { color: #00ff88; margin-top: 0; }
            .debug { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; margin: 20px 0; text-align: left; }
            .debug pre { overflow: auto; max-height: 300px; }
            .trailing-badge { background: #ffaa00; color: black; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
                <p><strong>Ambiente:</strong> """ + ("🌍 RENDER" if IS_RENDER else "💻 LOCAL") + """</p>
                <p><strong>Status:</strong> {{ '🟢 ATIVO' if trading_active else '🔴 INATIVO' }}</p>
                <p><strong>Versão:</strong> EXATA (100% TradingView)</p>
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Executando IDÊNTICO ao TradingView' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <!-- Informações da posição atual -->
            {% if position_info.has_position %}
            <div class="position-info">
                <h3>📊 Posição Atual</h3>
                <p><strong>Lado:</strong> {{ position_info.position_side|upper }}</p>
                <p><strong>Tamanho:</strong> {{ "%.4f"|format(position_info.position_size) }} ETH</p>
                <p><strong>Entrada:</strong> ${{ "%.2f"|format(position_info.entry_price) }}</p>
                <p><strong>Preço Atual:</strong> ${{ "%.2f"|format(position_info.current_price) }}</p>
                <p><strong>Stop Atual:</strong> ${{ "%.2f"|format(position_info.stop_loss) }} 
                    {% if position_info.trailing_activated %}
                    <span class="trailing-badge">TRAILING ATIVADO</span>
                    {% endif %}
                </p>
                <p><strong>PnL Estimado:</strong> 
                    {% if position_info.position_side == 'long' %}
                        ${{ "%.2f"|format((position_info.current_price - position_info.entry_price) * position_info.position_size) }}
                        ({{ "%.2f"|format(((position_info.current_price - position_info.entry_price) / position_info.entry_price * 100)) }}%)
                    {% else %}
                        ${{ "%.2f"|format((position_info.entry_price - position_info.current_price) * position_info.position_size) }}
                        ({{ "%.2f"|format(((position_info.entry_price - position_info.current_price) / position_info.entry_price * 100)) }}%)
                    {% endif %}
                </p>
            </div>
            {% endif %}
            
            <div>
                <button class="btn start-btn" onclick="controlBot('start')" {{ 'disabled' if trading_active else '' }}>
                    ⚡ Ligar Bot
                </button>
                <button class="btn stop-btn" onclick="controlBot('stop')" {{ 'disabled' if not trading_active else '' }}>
                    ⏹️ Parar Bot
                </button>
                {% if position_info.has_position %}
                <button class="btn force-close-btn" onclick="forceClosePosition()">
                    🔴 Fechar Posição Forçado
                </button>
                {% endif %}
            </div>
            
            <div class="menu">
                <a href="/status">📊 Status</a>
                <a href="/history">📜 Histórico</a>
                <a href="/debug">🐛 Debug</a>
                <a href="/health">❤️ Saúde</a>
                <a href="/test-auth">🔐 Testar OKX</a>
                <a href="/restart">🔄 Reiniciar</a>
                <a href="/force-close">🔴 Fechar Posição</a>
                <a href="/exact-status">🎯 Status Exato</a>
            </div>
            
            <div class="info">
                <strong>✅ ESTRATÉGIA 100% TRADINGVIEW:</strong> Algoritmo idêntico, timing exato, trailing stop real.<br>
                <strong>⏰ Timeframe:</strong> 30 minutos • <strong>Horário:</strong> Brasília (BRT)<br>
                <strong>🔧 Modo:</strong> {{ 'REAL' if okx_client and okx_client.has_credentials else 'SIMULAÇÃO' }}
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
        
        async function forceClosePosition() {
            if (confirm('⚠️ Tem certeza que deseja FORÇAR o fechamento da posição atual?')) {
                const response = await fetch('/force-close', {
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
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(html, 
                                 trading_active=trading_active, 
                                 IS_RENDER=IS_RENDER,
                                 position_info=position_info,
                                 okx_client=okx_client)

# ============================================================================
# 8. ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot - EXACT VERSION",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat(),
        "environment": "render" if IS_RENDER else "local",
        "uptime_seconds": round(time.time() - start_time, 2),
        "version": "exact_1.0"
    })

@app.route('/exact-status')
def exact_status():
    """Status detalhado do novo strategy runner"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        # Obter informações do engine
        engine_info = {}
        if hasattr(strategy_runner, 'engine'):
            engine = strategy_runner.engine
            engine_info = {
                "candle_count": getattr(engine, 'candle_count', 0),
                "period": getattr(engine, 'period', 20),
                "adaptive_method": getattr(engine, 'adaptive', 'Cos IFM'),
                "gain_limit": getattr(engine, 'gain_limit', 900),
                "threshold": getattr(engine, 'threshold', 0.0)
            }
        
        # Info do trailing stop
        trailing_info = {}
        if strategy_runner.trailing_manager:
            tm = strategy_runner.trailing_manager
            trailing_info = {
                "trailing_activated": tm.trailing_activated,
                "current_stop": tm.current_stop,
                "tp_trigger": tm.tp_trigger,
                "best_price": tm.best_price,
                "side": tm.side
            }
        
        return jsonify({
            "trading_active": trading_active,
            "strategy_runner": {
                "is_running": strategy_runner.is_running,
                "current_price": strategy_runner.current_price,
                "bar_count": strategy_runner.bar_count,
                "last_bar_timestamp": strategy_runner.last_bar_timestamp.isoformat() if strategy_runner.last_bar_timestamp else None,
                "pending_buy": getattr(strategy_runner, 'pending_buy', False),
                "pending_sell": getattr(strategy_runner, 'pending_sell', False),
                "position_size": strategy_runner.position_size,
                "position_side": strategy_runner.position_side,
                "entry_price": strategy_runner.entry_price,
                "trailing_manager": trailing_info
            },
            "engine": engine_info,
            "environment": "render" if IS_RENDER else "local",
            "simulation_mode": not (okx_client and okx_client.has_credentials)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug')
def debug_info():
    """Endpoint para diagnóstico DETALHADO"""
    try:
        strategy_status = {}
        if strategy_runner:
            # Para o novo runner
            engine_info = {}
            if hasattr(strategy_runner, 'engine'):
                engine = strategy_runner.engine
                engine_info = {
                    "candle_count": getattr(engine, 'candle_count', 0),
                    "params": getattr(engine, 'params', {})
                }
            
            strategy_status = {
                "is_running": strategy_runner.is_running,
                "current_price": strategy_runner.current_price,
                "bar_count": strategy_runner.bar_count,
                "pending_buy": getattr(strategy_runner, 'pending_buy', False),
                "pending_sell": getattr(strategy_runner, 'pending_sell', False),
                "position_size": strategy_runner.position_size,
                "position_side": strategy_runner.position_side,
                "entry_price": getattr(strategy_runner, 'entry_price', None),
                "engine_initialized": hasattr(strategy_runner, 'engine') and strategy_runner.engine is not None,
                "last_bar_timestamp": strategy_runner.last_bar_timestamp.isoformat() if strategy_runner.last_bar_timestamp else None,
                "engine_info": engine_info,
                "websocket_connected": getattr(strategy_runner, 'ws', None) and getattr(strategy_runner.ws, 'sock', None) and strategy_runner.ws.sock.connected
            }
        
        # Obtém trade_history_count de forma segura
        trade_history_count = 0
        if trade_history and hasattr(trade_history, 'trades'):
            trade_history_count = len(trade_history.trades)
        
        return jsonify({
            "trading_active": trading_active,
            "environment": "render" if IS_RENDER else "local",
            "simulation_mode": not (okx_client and okx_client.has_credentials),
            "strategy_runner": strategy_status,
            "okx_initialized": okx_client is not None,
            "okx_has_credentials": okx_client.has_credentials if okx_client else False,
            "trade_history_count": trade_history_count,
            "uptime_seconds": round(time.time() - start_time, 2),
            "current_time": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/restart')
def restart_strategy():
    """Reinicia a estratégia completamente"""
    global trading_active
    
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        # Parar estratégia atual
        if trading_active:
            strategy_runner.stop()
            trading_active = False
            time.sleep(2)
        
        # Resetar completamente o engine
        if hasattr(strategy_runner, 'engine') and strategy_runner.engine:
            # Chamar reset do engine se existir
            if hasattr(strategy_runner.engine, 'reset'):
                strategy_runner.engine.reset()
        
        # Reiniciar estratégia
        if strategy_runner.start():
            trading_active = True
            logger.info("🔄 Estratégia reiniciada com sucesso")
            return jsonify({"success": True, "message": "Estratégia reiniciada completamente"})
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
            last_bar_logged = None
            
            while trading_active and strategy_runner:
                try:
                    # Executar estratégia
                    status = strategy_runner.run_strategy_realtime()
                    
                    # Log detalhado a cada nova barra
                    if strategy_runner.last_bar_timestamp and strategy_runner.last_bar_timestamp != last_bar_logged:
                        logger.info("=" * 60)
                        logger.info(f"📊 BARRA {strategy_runner.last_bar_timestamp.strftime('%H:%M')} PROCESSADA")
                        logger.info(f"   Preço: ${strategy_runner.current_price:.2f}")
                        
                        # Sinais pendentes
                        pending_buy = getattr(strategy_runner, 'pending_buy', False)
                        pending_sell = getattr(strategy_runner, 'pending_sell', False)
                        
                        logger.info(f"   Sinal BUY pendente: {pending_buy}")
                        logger.info(f"   Sinal SELL pendente: {pending_sell}")
                        logger.info(f"   Posição: {strategy_runner.position_size:.4f} ETH")
                        logger.info(f"   Lado: {strategy_runner.position_side}")
                        
                        if strategy_runner.entry_price:
                            logger.info(f"   Entrada: ${strategy_runner.entry_price:.2f}")
                        
                        # Info do trailing
                        if strategy_runner.trailing_manager:
                            tm = strategy_runner.trailing_manager
                            if tm.trailing_activated:
                                logger.info(f"   Trailing Stop: ${tm.current_stop:.2f} (ATIVADO)")
                        
                        logger.info("=" * 60)
                        last_bar_logged = strategy_runner.last_bar_timestamp
                    
                    # Log periódico a cada 30 segundos
                    current_time = time.time()
                    if current_time - last_status_log > 30:
                        if strategy_runner.current_price:
                            position_str = f"{strategy_runner.position_side or 'FLAT'} {abs(strategy_runner.position_size):.4f} ETH"
                            logger.info(f"📈 Status: ${strategy_runner.current_price:.2f} | Posição: {position_str}")
                            
                            # Info do trailing
                            if strategy_runner.trailing_manager:
                                tm = strategy_runner.trailing_manager
                                if tm.trailing_activated:
                                    logger.info(f"   Trailing Stop: ${tm.current_stop:.2f}")
                        last_status_log = current_time
                    
                    time.sleep(1)
                    consecutive_errors = 0
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Erro no loop de trading ({consecutive_errors}): {e}")
                    if consecutive_errors > 10:
                        logger.error("🔴 Muitos erros consecutivos, parando loop...")
                        break
                    time.sleep(5)
        
        trade_thread = threading.Thread(target=trading_loop, daemon=True)
        trade_thread.start()
        
        mode = "REAL" if okx_client and okx_client.has_credentials else "SIMULAÇÃO"
        logger.info(f"⚡ BOT LIGADO em modo {mode} (100% TradingView)!")
        return jsonify({
            "status": "success", 
            "message": f"Bot iniciado em modo {mode} (100% TradingView)!"
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

@app.route('/force-close', methods=['GET', 'POST'])
def force_close_position():
    """Força o fechamento da posição atual"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        if request.method == 'GET':
            # Página HTML para fechamento manual
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Forçar Fechamento de Posição</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; text-align: center; padding: 50px; }
                    .container { max-width: 500px; margin: 0 auto; }
                    .warning { background: rgba(255, 68, 68, 0.2); border: 2px solid #ff4444; padding: 20px; border-radius: 10px; margin: 20px 0; }
                    .btn { padding: 12px 24px; margin: 10px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold; }
                    .force-btn { background: #ffaa00; color: #000; }
                    .back-btn { background: #555; color: white; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🔴 Forçar Fechamento de Posição</h1>
                    <div class="warning">
                        <h3>⚠️ ATENÇÃO</h3>
                        <p>Esta ação irá FORÇAR o fechamento da posição atual.</p>
                        <p><strong>Use apenas se a posição não estiver fechando automaticamente.</strong></p>
                    </div>
                    <div>
                        <button class="btn force-btn" onclick="forceClose()">🔴 FORÇAR FECHAMENTO</button>
                        <button class="btn back-btn" onclick="window.location.href='/'">↩️ Voltar</button>
                    </div>
                    <div id="result" style="margin-top: 20px;"></div>
                </div>
                
                <script>
                async function forceClose() {
                    if (!confirm('⚠️ TEM CERTEZA ABSOLUTA que deseja FORÇAR o fechamento?')) {
                        return;
                    }
                    
                    const response = await fetch('/force-close', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });
                    
                    const data = await response.json();
                    const resultDiv = document.getElementById('result');
                    
                    if (response.ok) {
                        resultDiv.innerHTML = '<div style="color: #00ff88; font-weight: bold;">✅ ' + data.message + '</div>';
                        setTimeout(() => window.location.href = '/', 2000);
                    } else {
                        resultDiv.innerHTML = '<div style="color: #ff4444; font-weight: bold;">❌ ' + data.message + '</div>';
                    }
                }
                </script>
            </body>
            </html>
            """
            return html
        
        # Método POST - Executar fechamento forçado
        result = strategy_runner.force_close_position()
        if result.get('success'):
            logger.info("🔴 POSIÇÃO FECHADA FORÇADAMENTE via API")
            return jsonify(result)
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"Erro ao forçar fechamento: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/status')
def status():
    price = strategy_runner.current_price if strategy_runner else None
    balance = okx_client.get_balance() if okx_client else 0
    
    # Obter sinais pendentes do NOVO runner
    pending_buy = getattr(strategy_runner, 'pending_buy', False) if strategy_runner else False
    pending_sell = getattr(strategy_runner, 'pending_sell', False) if strategy_runner else False
    entry_price = getattr(strategy_runner, 'entry_price', None) if strategy_runner else None
    position_side = getattr(strategy_runner, 'position_side', None) if strategy_runner else None
    position_size = getattr(strategy_runner, 'position_size', 0) if strategy_runner else 0
    last_bar = strategy_runner.last_bar_timestamp.strftime('%H:%M') if strategy_runner and strategy_runner.last_bar_timestamp else None
    
    # Info do trailing stop
    stop_loss = None
    trailing_activated = False
    if strategy_runner and strategy_runner.trailing_manager:
        stop_loss = strategy_runner.trailing_manager.current_stop
        trailing_activated = strategy_runner.trailing_manager.trailing_activated
    
    # Calcular PnL se houver posição
    pnl_usdt = 0
    pnl_percent = 0
    if position_side and entry_price and price:
        if position_side == 'long':
            pnl_usdt = (price - entry_price) * position_size
            pnl_percent = ((price - entry_price) / entry_price) * 100
        elif position_side == 'short':
            pnl_usdt = (entry_price - price) * abs(position_size)
            pnl_percent = ((entry_price - price) / entry_price) * 100
    
    return jsonify({
        "trading_active": trading_active,
        "current_price": price,
        "balance_usdt": balance,
        "pending_buy_signal": pending_buy,
        "pending_sell_signal": pending_sell,
        "position_size": position_size,
        "position_side": position_side,
        "entry_price": entry_price,
        "stop_loss_price": stop_loss,
        "trailing_activated": trailing_activated,
        "pnl_usdt": round(pnl_usdt, 2),
        "pnl_percent": round(pnl_percent, 2),
        "last_bar_timestamp": last_bar,
        "environment": "render" if IS_RENDER else "local",
        "simulation_mode": not (okx_client and okx_client.has_credentials),
        "version": "exact_tradingview"
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
                    <p>ETH/USDT - Estratégia 100% TradingView</p>
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
        
        # Determinar modo
        mode = "SIMULAÇÃO" if not (okx_client and okx_client.has_credentials) else "REAL"
        
        html += f"""
                <div class="footer">
                    <p><strong>🔧 MODO {mode}:</strong> Estratégia executada 100% igual ao TradingView</p>
                    <p>Horário de Brasília (BRT) • Timeframe: 30min</p>
                </div>
            </div>
            
            <script>
            function clearHistory() {{
                if (confirm('Tem certeza que deseja limpar todo o histórico?')) {{
                    fetch('/clear-history', {{ method: 'POST' }})
                        .then(r => r.json())
                        .then(data => {{
                            if (data.success) {{
                                alert('Histórico limpo!');
                                location.reload();
                            }} else {{
                                alert('Erro: ' + data.message);
                            }}
                        }});
                }}
            }}
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
            "auth": "success" if okx_client.has_credentials else "simulation",
            "balance_usdt": balance,
            "current_eth_price": price,
            "api_key_exists": bool(okx_client.api_key),
            "secret_key_exists": bool(okx_client.secret_key),
            "passphrase_exists": bool(okx_client.passphrase),
            "has_credentials": okx_client.has_credentials,
            "mode": "REAL" if okx_client.has_credentials else "SIMULAÇÃO"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# 9. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"✅ Usando StrategyRunnerExact (100% TradingView)")
    
    # Iniciar estratégia automaticamente se estiver em ambiente local
    if not IS_RENDER:
        logger.info("💻 Iniciando estratégia automaticamente (ambiente local)...")
        start_strategy_automatically()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
