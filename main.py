#!/usr/bin/env python3
"""
MAIN.PY - VERSÃO FINAL COM CONFIGURAÇÃO EXATA DO PINE SCRIPT
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
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, 'src')
sys.path.insert(0, src_path)

# Configurar logging detalhado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================================
# 2. CONFIGURAÇÃO EXATA DO PINE SCRIPT
# ============================================================================
PINE_CONFIG_EXACT = {
    'timeframe': '30m',           # ⚠️ ALTERE PARA O TIMEFRAME QUE VOCÊ USA NO TRADINGVIEW
    'period': 20,
    'adaptive': 'Cos IFM',        # 'Cos IFM', 'I-Q IFM', 'Average', ou 'Off'
    'gain_limit': 900,
    'threshold': 0.0,
    'fixed_sl': 2000,             # pontos
    'fixed_tp': 55,               # pontos
    'risk': 0.01,                 # 1%
    'limit': 100,                 # max lots
    'initial_capital': 1000.0,    # igual ao initial_capital no Pine
    'mintick': 0.01,              # syminfo.mintick para ETH/USDT
    'symbol': 'ETH-USDT-SWAP'
}

# ============================================================================
# 3. DETECTAR AMBIENTE
# ============================================================================
IS_RENDER = os.getenv('RENDER', '').lower() == 'true'
PORT = int(os.environ.get('PORT', 10000))

if IS_RENDER:
    logger.info("🌍 AMBIENTE RENDER DETECTADO")
else:
    logger.info("💻 Ambiente local detectado")

# ============================================================================
# 4. IMPORTAR MÓDULOS
# ============================================================================
try:
    from src.okx_client import OKXClient
    from src.keep_alive import KeepAliveSystem
    from src.strategy_runner_exact import StrategyRunnerExact
    from src.trade_history import TradeHistory
    from src.balance_manager import ExactBalanceManager
    from src.flag_system import FlagSystem
    
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
    
    # Inicializar strategy runner COM CONFIG EXATA
    strategy_runner = StrategyRunnerExact(
        okx_client=okx_client,
        trade_history=trade_history,
        config=PINE_CONFIG_EXACT
    )
    
    logger.info("✅ Sistema inicializado com configuração EXATA do Pine Script")
    
except Exception as e:
    logger.error(f"❌ Erro na inicialização: {e}")
    import traceback
    logger.error(traceback.format_exc())
    okx_client = None
    trade_history = None
    keep_alive = None
    strategy_runner = None

# ============================================================================
# 5. VARIÁVEIS DE ESTADO
# ============================================================================
trading_active = False
trade_thread = None
start_time = time.time()

# ============================================================================
# 6. INICIAR KEEP-ALIVE AUTOMÁTICO NO RENDER
# ============================================================================
if IS_RENDER and keep_alive:
    try:
        keep_alive.start_keep_alive()
        logger.info("✅ Keep-alive automático iniciado")
    except Exception as e:
        logger.error(f"❌ Erro no keep-alive: {e}")

# ============================================================================
# 7. INICIAR ESTRATÉGIA AUTOMATICAMENTE
# ============================================================================
def start_strategy_automatically():
    """Inicia a estratégia automaticamente"""
    global trading_active, trade_thread
    
    if not strategy_runner:
        logger.error("❌ Strategy Runner não inicializado")
        return
    
    try:
        logger.info("🚀 Iniciando estratégia automaticamente...")
        
        # Iniciar o strategy runner
        if strategy_runner.start():
            trading_active = True
            
            def trading_loop_exact():
                logger.info("🔄 Loop de trading EXATO iniciado (10ms)")
                
                last_status_log = time.time()
                consecutive_errors = 0
                
                while trading_active and strategy_runner:
                    try:
                        # Executar estratégia
                        status = strategy_runner.run_strategy_exact()
                        
                        # Log periódico a cada 30 segundos
                        current_time = time.time()
                        if current_time - last_status_log > 30:
                            if strategy_runner.current_price:
                                position_str = f"{strategy_runner.position_side or 'FLAT'} {abs(strategy_runner.position_size):.4f} ETH"
                                balance = strategy_runner.balance_manager.get_balance()
                                
                                flags = strategy_runner.flag_system.get_state()
                                flags_str = f" | Flags: B={flags['pending_buy']} S={flags['pending_sell']}"
                                
                                logger.info(f"📈 Status: ${strategy_runner.current_price:.2f} | Posição: {position_str} | Balance: ${balance:.2f}{flags_str}")
                                
                            last_status_log = current_time
                        
                        # LOOP RÁPIDO (10ms)
                        time.sleep(0.01)
                        consecutive_errors = 0
                        
                    except Exception as e:
                        consecutive_errors += 1
                        logger.error(f"💥 Erro no loop de trading ({consecutive_errors}): {e}")
                        if consecutive_errors > 10:
                            logger.error("🔴 Muitos erros consecutivos, parando loop...")
                            break
                        time.sleep(1)
            
            # Iniciar thread de trading
            trade_thread = threading.Thread(target=trading_loop_exact, daemon=True)
            trade_thread.start()
            logger.info("✅ Estratégia iniciada automaticamente")
        else:
            logger.error("❌ Falha ao iniciar a estratégia automaticamente")
            
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar estratégia automaticamente: {e}")

# Iniciar automaticamente se estiver no Render
if IS_RENDER:
    # Aguardar 5 segundos para garantir que tudo está inicializado
    threading.Timer(5.0, start_strategy_automatically).start()

# ============================================================================
# 8. INTERFACE WEB - ATUALIZADA
# ============================================================================
@app.route('/')
def home():
    # Obter informações atuais
    position_info = {}
    balance_info = {}
    flags_info = {}
    sync_info = {}
    
    if strategy_runner:
        # Informações da posição
        current_stop = None
        trailing_activated = False
        if strategy_runner.trailing_manager:
            current_stop = strategy_runner.trailing_manager.current_stop
            trailing_activated = strategy_runner.trailing_manager.trailing_activated
        
        position_info = {
            'has_position': strategy_runner.position_size != 0,
            'position_side': strategy_runner.position_side,
            'position_size': abs(strategy_runner.position_size),
            'entry_price': strategy_runner.entry_price,
            'current_price': strategy_runner.current_price,
            'stop_loss': current_stop,
            'trailing_activated': trailing_activated
        }
        
        # Informações do balance
        if hasattr(strategy_runner, 'balance_manager'):
            balance_stats = strategy_runner.balance_manager.get_stats()
            balance_info = {
                'current_balance': balance_stats['current_balance'],
                'initial_capital': balance_stats['initial_capital'],
                'netprofit': balance_stats['netprofit'],
                'trade_count': balance_stats['trade_count'],
                'formula': balance_stats.get('formula', 'balance = initial_capital + netprofit')
            }
        
        # Informações das flags
        if hasattr(strategy_runner, 'flag_system'):
            flags_state = strategy_runner.flag_system.get_state()
            flags_info = {
                'pending_buy': flags_state['pending_buy'],
                'pending_sell': flags_state['pending_sell'],
                'buy_signal_prev': flags_state['buy_signal_prev'],
                'sell_signal_prev': flags_state['sell_signal_prev']
            }
        
        # Informações de sincronização
        if hasattr(strategy_runner, 'time_sync'):
            sync_data = strategy_runner.time_sync.get_precise_bar_info()
            sync_info = {
                'next_bar_in': int(sync_data.get('seconds_to_next_bar', 0)),
                'current_time': sync_data['current_timestamp'].strftime('%H:%M:%S.%f')[:-3] if sync_data.get('current_timestamp') else 'N/A',
                'bar_count': strategy_runner.bar_count,
                'timeframe': PINE_CONFIG_EXACT['timeframe']
            }
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading ETH/USDT - IDÊNTICO AO TRADINGVIEW</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; text-align: center; padding: 20px; }
            .container { max-width: 1000px; margin: 0 auto; }
            .header { background: rgba(0, 255, 136, 0.1); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #00ff88; }
            .status { padding: 15px; border-radius: 10px; margin: 15px 0; font-weight: bold; }
            .active { background: rgba(0, 255, 136, 0.2); border: 2px solid #00ff88; }
            .inactive { background: rgba(255, 68, 68, 0.2); border: 2px solid #ff4444; }
            .btn { padding: 12px 24px; margin: 10px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold; }
            .start-btn { background: #00ff88; color: #000; }
            .stop-btn { background: #ff4444; color: white; }
            .force-close-btn { background: #ffaa00; color: #000; }
            .sync-btn { background: #0088ff; color: white; }
            .exact-btn { background: #9d4edd; color: white; }
            .btn:disabled { opacity: 0.5; cursor: not-allowed; }
            .menu { margin: 30px 0; }
            .menu a { color: #00ff88; text-decoration: none; margin: 0 15px; font-size: 16px; }
            .menu a:hover { text-decoration: underline; }
            .info { color: #aaa; font-size: 14px; margin-top: 20px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 15px; margin: 20px 0; }
            .card { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 10px; text-align: left; }
            .card h4 { margin-top: 0; color: #00ff88; }
            .value { font-size: 24px; font-weight: bold; margin: 10px 0; }
            .positive { color: #00ff88; }
            .negative { color: #ff4444; }
            .neutral { color: #aaa; }
            .trailing-badge { background: #ffaa00; color: black; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }
            .sync-badge { background: #0088ff; color: white; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }
            .flag-badge { background: #9d4edd; color: white; padding: 3px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; }
            .flag-true { color: #00ff88; }
            .flag-false { color: #ff4444; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT - IDÊNTICO AO TRADINGVIEW</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: """ + PINE_CONFIG_EXACT['timeframe'] + """ • Configuração EXATA</p>
                <p><strong>Ambiente:</strong> """ + ("🌍 RENDER" if IS_RENDER else "💻 LOCAL") + """</p>
                <p><strong>Status:</strong> {{ '🟢 ATIVO' if trading_active else '🔴 INATIVO' }}</p>
                <p><strong>Versão:</strong> FLUXO IDÊNTICO AO PINE SCRIPT</p>
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Executando com fluxo idêntico ao TradingView' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <!-- Grid de informações -->
            <div class="grid">
                <!-- Card de Configuração -->
                <div class="card">
                    <h4>⚙️ Configuração Exata</h4>
                    <p>Timeframe: """ + PINE_CONFIG_EXACT['timeframe'] + """</p>
                    <p>Período: """ + str(PINE_CONFIG_EXACT['period']) + """</p>
                    <p>Adaptive: """ + PINE_CONFIG_EXACT['adaptive'] + """</p>
                    <p>Risk: """ + str(PINE_CONFIG_EXACT['risk']*100) + """%</p>
                    <p>SL: """ + str(PINE_CONFIG_EXACT['fixed_sl']) + """p | TP: """ + str(PINE_CONFIG_EXACT['fixed_tp']) + """p</p>
                </div>
                
                <!-- Card de Balance -->
                <div class="card">
                    <h4>💰 Balance Exato</h4>
                    <div class="value {{ 'positive' if balance_info.netprofit > 0 else 'negative' if balance_info.netprofit < 0 else 'neutral' }}">
                        ${{ "%.2f"|format(balance_info.current_balance) }}
                    </div>
                    <p>Fórmula: {{ balance_info.formula }}</p>
                    <p>Capital: ${{ "%.2f"|format(balance_info.initial_capital) }}</p>
                    <p>Net Profit: ${{ "%.2f"|format(balance_info.netprofit) }}</p>
                    <p>Trades: {{ balance_info.trade_count }}</p>
                </div>
                
                <!-- Card de Flags -->
                <div class="card">
                    <h4>🏁 Flags do Pine Script</h4>
                    <p>pendingBuy: <span class="{{ 'flag-true' if flags_info.pending_buy else 'flag-false' }}">{{ '✅ TRUE' if flags_info.pending_buy else '❌ FALSE' }}</span></p>
                    <p>pendingSell: <span class="{{ 'flag-true' if flags_info.pending_sell else 'flag-false' }}">{{ '✅ TRUE' if flags_info.pending_sell else '❌ FALSE' }}</span></p>
                    <p>buy_signal[1]: <span class="{{ 'flag-true' if flags_info.buy_signal_prev else 'flag-false' }}">{{ '✅ TRUE' if flags_info.buy_signal_prev else '❌ FALSE' }}</span></p>
                    <p>sell_signal[1]: <span class="{{ 'flag-true' if flags_info.sell_signal_prev else 'flag-false' }}">{{ '✅ TRUE' if flags_info.sell_signal_prev else '❌ FALSE' }}</span></p>
                    <span class="flag-badge">SISTEMA EXATO</span>
                </div>
                
                <!-- Card de Posição -->
                <div class="card">
                    <h4>📊 Posição</h4>
                    {% if position_info.has_position %}
                        <div class="value {{ 'positive' if position_info.position_side == 'long' else 'negative' }}">
                            {{ position_info.position_side|upper }} {{ "%.4f"|format(position_info.position_size) }} ETH
                        </div>
                        <p>Entrada: ${{ "%.2f"|format(position_info.entry_price) }}</p>
                        <p>Atual: ${{ "%.2f"|format(position_info.current_price) }}</p>
                        <p>Stop: ${{ "%.2f"|format(position_info.stop_loss) }}</p>
                        {% if position_info.trailing_activated %}
                        <span class="trailing-badge">TRAILING ATIVADO</span>
                        {% endif %}
                    {% else %}
                        <div class="value neutral">FLAT</div>
                        <p>Sem posição aberta</p>
                    {% endif %}
                </div>
            </div>
            
            <!-- Botões de controle -->
            <div>
                <button class="btn start-btn" onclick="controlBot('start')" {{ 'disabled' if trading_active else '' }}>
                    ⚡ Iniciar Bot
                </button>
                <button class="btn stop-btn" onclick="controlBot('stop')" {{ 'disabled' if not trading_active else '' }}>
                    ⏹️ Parar Bot
                </button>
                <button class="btn exact-btn" onclick="window.location.href='/validate-exact'">
                    🔬 Validar Fluxo
                </button>
                <button class="btn sync-btn" onclick="window.location.href='/sync-status'">
                    ⏰ Status Sincronização
                </button>
                {% if position_info.has_position %}
                <button class="btn force-close-btn" onclick="forceClosePosition()">
                    🔴 Fechar Posição Forçado
                </button>
                {% endif %}
            </div>
            
            <!-- Menu -->
            <div class="menu">
                <a href="/status">📊 Status</a>
                <a href="/history">📜 Histórico</a>
                <a href="/balance-status">💰 Balance</a>
                <a href="/flags-status">🏁 Flags</a>
                <a href="/sync-status">⏰ Sincronização</a>
                <a href="/validate-exact">🔬 Validação Exata</a>
                <a href="/exact-status">🎯 Status Exato</a>
                <a href="/health">❤️ Saúde</a>
            </div>
            
            <!-- Informações -->
            <div class="info">
                <strong>✅ CONFIGURAÇÃO EXATA DO PINE SCRIPT:</strong><br>
                <strong>Timeframe:</strong> """ + PINE_CONFIG_EXACT['timeframe'] + """<br>
                <strong>Adaptive Method:</strong> """ + PINE_CONFIG_EXACT['adaptive'] + """<br>
                <strong>Period:</strong> """ + str(PINE_CONFIG_EXACT['period']) + """<br>
                <strong>Risk:</strong> """ + str(PINE_CONFIG_EXACT['risk']*100) + """%<br>
                <strong>SL/TP:</strong> """ + str(PINE_CONFIG_EXACT['fixed_sl']) + """p/""" + str(PINE_CONFIG_EXACT['fixed_tp']) + """p<br>
                <strong>Initial Capital:</strong> $""" + str(PINE_CONFIG_EXACT['initial_capital']) + """<br>
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
                                 balance_info=balance_info,
                                 sync_info=sync_info,
                                 flags_info=flags_info,
                                 okx_client=okx_client)

# ============================================================================
# 9. ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot - IDÊNTICO AO TRADINGVIEW",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat(),
        "environment": "render" if IS_RENDER else "local",
        "uptime_seconds": round(time.time() - start_time, 2),
        "version": "exact_pine_script_1.0",
        "pine_config": PINE_CONFIG_EXACT
    })

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
        
        def trading_loop_exact():
            logger.info("🔄 Loop de trading iniciado manualmente")
            
            last_status_log = time.time()
            consecutive_errors = 0
            
            while trading_active and strategy_runner:
                try:
                    # Executar estratégia
                    status = strategy_runner.run_strategy_exact()
                    
                    current_time = time.time()
                    if current_time - last_status_log > 30:
                        if strategy_runner.current_price:
                            position_str = f"{strategy_runner.position_side or 'FLAT'} {abs(strategy_runner.position_size):.4f} ETH"
                            balance = strategy_runner.balance_manager.get_balance()
                            
                            flags = strategy_runner.flag_system.get_state()
                            flags_str = f" | Flags: B={flags['pending_buy']} S={flags['pending_sell']}"
                            
                            logger.info(f"📈 Status: ${strategy_runner.current_price:.2f} | Posição: {position_str} | Balance: ${balance:.2f}{flags_str}")
                            
                        last_status_log = current_time
                    
                    time.sleep(0.01)
                    consecutive_errors = 0
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Erro no loop de trading ({consecutive_errors}): {e}")
                    if consecutive_errors > 10:
                        logger.error("🔴 Muitos erros consecutivos, parando loop...")
                        break
                    time.sleep(1)
        
        trade_thread = threading.Thread(target=trading_loop_exact, daemon=True)
        trade_thread.start()
        
        mode = "REAL" if okx_client and okx_client.has_credentials else "SIMULAÇÃO"
        logger.info(f"⚡ BOT LIGADO em modo {mode}!")
        return jsonify({
            "status": "success", 
            "message": f"Bot iniciado em modo {mode}!",
            "pine_config": PINE_CONFIG_EXACT
        })
        
    except Exception as e:
        logger.error(f"Erro ao iniciar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/stop', methods=['POST'])
def stop_trading():
    global trading_active
    
    if not trading_active:
        return jsonify({"status": "error", "message": "Bot já está parado!"}), 400
    
    if strategy_runner:
        strategy_runner.stop()
    
    trading_active = False
    logger.info("⏹️ Bot parado manualmente")
    
    return jsonify({
        "status": "success",
        "message": "Bot parado com sucesso!"
    })

@app.route('/force-close', methods=['POST'])
def force_close():
    if not strategy_runner:
        return jsonify({"status": "error", "message": "Strategy Runner não inicializado."}), 500
    
    try:
        result = strategy_runner.force_close_position()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": f"Erro: {str(e)}"}), 500

@app.route('/status')
def status():
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        status_info = strategy_runner.get_status()
        return jsonify({
            "trading_active": trading_active,
            "strategy_runner": status_info,
            "pine_config": PINE_CONFIG_EXACT,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/history')
def history():
    """Retorna histórico de trades"""
    if not trade_history:
        return jsonify({"error": "Trade History não inicializado"}), 500
    
    try:
        trades = trade_history.get_all_trades(limit=100)
        stats = trade_history.get_stats()
        
        return jsonify({
            "trades": trades,
            "stats": stats,
            "total_trades": len(trades),
            "server_time": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/balance-status')
def balance_status():
    """Status do balance exato"""
    if not strategy_runner or not hasattr(strategy_runner, 'balance_manager'):
        return jsonify({"error": "Balance Manager não inicializado"}), 500
    
    try:
        balance_stats = strategy_runner.balance_manager.get_stats()
        return jsonify(balance_stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/flags-status')
def flags_status():
    """Status das flags"""
    if not strategy_runner or not hasattr(strategy_runner, 'flag_system'):
        return jsonify({"error": "Flag System não inicializado"}), 500
    
    try:
        flags = strategy_runner.flag_system.get_state()
        return jsonify({
            "flags": flags,
            "interpretation": {
                "pending_buy": "Flag para execução BUY",
                "pending_sell": "Flag para execução SELL",
                "buy_signal_prev": "buy_signal[1] (da barra anterior)",
                "sell_signal_prev": "sell_signal[1] (da barra anterior)"
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/sync-status')
def sync_status():
    """Status da sincronização"""
    if not strategy_runner or not hasattr(strategy_runner, 'time_sync'):
        return jsonify({"error": "Time Sync não inicializado"}), 500
    
    try:
        sync_info = strategy_runner.time_sync.get_precise_bar_info()
        return jsonify({
            "synchronized_time": sync_info['current_timestamp'].isoformat(),
            "current_bar": sync_info['current_bar_timestamp'].isoformat(),
            "next_bar_in": sync_info['seconds_to_next_bar'],
            "next_bar_in_ms": sync_info['milliseconds_to_next'],
            "time_since_open_ms": sync_info['milliseconds_since_open'],
            "timeframe": sync_info['timeframe_str'],
            "timeframe_minutes": sync_info['timeframe_minutes']
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/exact-status')
def exact_status():
    """Status completo do sistema exato"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        return jsonify({
            "trading_active": trading_active,
            "strategy_runner": strategy_runner.get_status(),
            "pine_config": PINE_CONFIG_EXACT,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/validate-exact')
def validate_exact():
    """Página de validação"""
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Validação do Fluxo Exato</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; padding: 20px; }
            .container { max-width: 800px; margin: 0 auto; }
            .header { background: rgba(0, 255, 136, 0.1); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #00ff88; }
            .btn { padding: 12px 24px; margin: 10px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold; }
            .test-btn { background: #9d4edd; color: white; }
            .back-btn { background: #555; color: white; }
            .result { margin: 20px 0; padding: 15px; background: rgba(255,255,255,0.05); border-radius: 8px; }
            .success { color: #00ff88; }
            .warning { color: #ffaa00; }
            .error { color: #ff4444; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔬 Validação do Fluxo Exato</h1>
                <p>Teste completo do fluxo temporal idêntico ao TradingView</p>
            </div>
            
            <div>
                <button class="btn test-btn" onclick="testFullFlow()">Testar Fluxo Completo</button>
                <button class="btn test-btn" onclick="testFlags()">Testar Sistema de Flags</button>
                <button class="btn test-btn" onclick="testBalance()">Testar Balance Exato</button>
                <button class="btn test-btn" onclick="testSync()">Testar Sincronização</button>
                <button class="btn back-btn" onclick="window.location.href='/'">Voltar</button>
            </div>
            
            <div id="result" class="result"></div>
        </div>
        
        <script>
        async function testFullFlow() {
            const response = await fetch('/exact-status');
            const data = await response.json();
            
            const resultDiv = document.getElementById('result');
            
            if (data.error) {
                resultDiv.innerHTML = `<div class="error"><h4>❌ Erro:</h4><p>${data.error}</p></div>`;
                return;
            }
            
            let html = `<div class="success">
                <h4>🔬 Status do Fluxo Exato:</h4>
                <p><strong>Barra atual:</strong> #${data.strategy_runner?.bar_count || 0}</p>
                <p><strong>Preço atual:</strong> $${data.strategy_runner?.current_price?.toFixed(2) || '0.00'}</p>
                <p><strong>Flags:</strong> pendingBuy=${data.strategy_runner?.pending_buy || false}, pendingSell=${data.strategy_runner?.pending_sell || false}</p>
                <p><strong>Posição:</strong> ${data.strategy_runner?.position_side || 'FLAT'} ${Math.abs(data.strategy_runner?.position_size || 0).toFixed(4)} ETH</p>
                <p><strong>Balance:</strong> $${data.strategy_runner?.balance?.toFixed(2) || '0.00'}</p>
                <p><strong>Execução ativa:</strong> ${data.trading_active ? '✅ SIM' : '❌ NÃO'}</p>
            </div>`;
            
            resultDiv.innerHTML = html;
        }
        
        async function testFlags() {
            const response = await fetch('/flags-status');
            const data = await response.json();
            
            const resultDiv = document.getElementById('result');
            
            if (data.error) {
                resultDiv.innerHTML = `<div class="error"><h4>❌ Erro:</h4><p>${data.error}</p></div>`;
                return;
            }
            
            resultDiv.innerHTML = `
                <div class="success">
                    <h4>🏁 Teste do Sistema de Flags:</h4>
                    <p><strong>pendingBuy:</strong> ${data.flags.pending_buy ? '✅ TRUE' : '❌ FALSE'}</p>
                    <p><strong>pendingSell:</strong> ${data.flags.pending_sell ? '✅ TRUE' : '❌ FALSE'}</p>
                    <p><strong>buy_signal[1]:</strong> ${data.flags.buy_signal_prev ? '✅ TRUE' : '❌ FALSE'}</p>
                    <p><strong>sell_signal[1]:</strong> ${data.flags.sell_signal_prev ? '✅ TRUE' : '❌ FALSE'}</p>
                </div>
            `;
        }
        
        async function testBalance() {
            const response = await fetch('/balance-status');
            const data = await response.json();
            
            const resultDiv = document.getElementById('result');
            
            if (data.error) {
                resultDiv.innerHTML = `<div class="error"><h4>❌ Erro:</h4><p>${data.error}</p></div>`;
                return;
            }
            
            const calculated = data.initial_capital + data.netprofit;
            const exactMatch = Math.abs(calculated - data.current_balance) < 0.01;
            
            resultDiv.innerHTML = `
                <div class="${exactMatch ? 'success' : 'error'}">
                    <h4>💰 Teste do Balance Exato:</h4>
                    <p><strong>Balance atual:</strong> $${data.current_balance?.toFixed(2) || '0.00'}</p>
                    <p><strong>Capital inicial:</strong> $${data.initial_capital?.toFixed(2) || '0.00'}</p>
                    <p><strong>Net Profit:</strong> $${data.netprofit?.toFixed(2) || '0.00'}</p>
                    <p><strong>Verificação:</strong> ${data.initial_capital?.toFixed(2)} + ${data.netprofit?.toFixed(2)} = $${calculated.toFixed(2)} ${exactMatch ? '✅ CORRETO' : '❌ ERRADO'}</p>
                </div>
            `;
        }
        
        async function testSync() {
            const response = await fetch('/sync-status');
            const data = await response.json();
            
            const resultDiv = document.getElementById('result');
            
            if (data.error) {
                resultDiv.innerHTML = `<div class="error"><h4>❌ Erro:</h4><p>${data.error}</p></div>`;
                return;
            }
            
            resultDiv.innerHTML = `
                <div class="success">
                    <h4>⏰ Teste de Sincronização:</h4>
                    <p><strong>Timeframe:</strong> ${data.timeframe}</p>
                    <p><strong>Próxima barra em:</strong> ${data.next_bar_in_ms?.toFixed(1) || '0'}ms</p>
                    <p><strong>Horário UTC:</strong> ${data.synchronized_time?.split('T')[1]?.split('.')[0] || 'N/A'}</p>
                </div>
            `;
        }
        </script>
    </body>
    </html>
    """
    return html

# ============================================================================
# 10. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info("=" * 70)
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"✅ CONFIGURAÇÃO EXATA DO PINE SCRIPT CARREGADA")
    logger.info(f"   Timeframe: {PINE_CONFIG_EXACT['timeframe']}")
    logger.info(f"   Period: {PINE_CONFIG_EXACT['period']}")
    logger.info(f"   Adaptive: {PINE_CONFIG_EXACT['adaptive']}")
    logger.info(f"   Risk: {PINE_CONFIG_EXACT['risk']*100}%")
    logger.info(f"   SL/TP: {PINE_CONFIG_EXACT['fixed_sl']}p/{PINE_CONFIG_EXACT['fixed_tp']}p")
    logger.info(f"   Initial Capital: ${PINE_CONFIG_EXACT['initial_capital']}")
    logger.info("=" * 70)
    
    # Iniciar estratégia automaticamente se estiver em ambiente local
    if not IS_RENDER:
        logger.info("💻 Iniciando estratégia automaticamente (ambiente local)...")
        start_strategy_automatically()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
