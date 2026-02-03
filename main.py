#!/usr/bin/env python3
"""
MAIN.PY - Bot Trading ETH/USDT - REPLICAÇÃO EXATA DO TRADINGVIEW
Modo: calc_on_every_tick=false (execução apenas no fechamento das barras)
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

# Configurar logging
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
    # Importar módulos - IMPORTANTE: Usar a nova implementação
    from src.okx_client import OKXClient
    from src.keep_alive import KeepAliveSystem
    from src.trade_history import TradeHistory
    
    # Importar a NOVA implementação que replica exatamente o TradingView
    try:
        from src.strategy_runner_correct import StrategyRunnerCorrect as StrategyRunner
        logger.info("✅ Usando StrategyRunnerCorrect (replicação exata TradingView)")
    except ImportError:
        # Fallback para compatibilidade
        from src.strategy_runner import StrategyRunner
        logger.info("⚠️ Usando StrategyRunner padrão (modo compatibilidade)")
    
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
    
    # Inicializar strategy runner COM REPLICAÇÃO EXATA
    strategy_runner = StrategyRunner(okx_client, trade_history)
    
    logger.info("✅ Sistema inicializado com sucesso")
    logger.info("🎯 Modo: calc_on_every_tick=false (EXATO TradingView)")
    logger.info("⏰ Entradas: Apenas no FECHAMENTO das barras de 30min")
    logger.info("📈 Saídas: A qualquer momento (trailing stop ativo)")
    
except Exception as e:
    logger.error(f"❌ Erro na inicialização: {e}", exc_info=True)
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
        logger.info("🎯 Modo: calc_on_every_tick=false (igual TradingView)")
        
        # Iniciar o strategy runner
        if strategy_runner.start():
            trading_active = True
            
            def monitoring_loop():
                """Loop de monitoramento LEVE"""
                logger.info("👁️  Monitoramento iniciado")
                
                last_status_log = time.time()
                last_bar_log = time.time()
                
                while trading_active and strategy_runner:
                    try:
                        current_time = time.time()
                        
                        # Log de status a cada 15 segundos
                        if current_time - last_status_log > 15:
                            if strategy_runner.current_price:
                                status_msg = f"📊 Preço: ${strategy_runner.current_price:.2f}"
                                
                                if strategy_runner.position_side:
                                    status_msg += f" | Posição: {strategy_runner.position_side.upper()} {abs(strategy_runner.position_size):.4f} ETH"
                                    
                                    # Mostrar trailing stop se ativado
                                    trailing_status = strategy_runner.trailing_stop.get_status()
                                    if trailing_status and trailing_status.get('activated'):
                                        stop_price = trailing_status.get('current_stop')
                                        if stop_price:
                                            if strategy_runner.position_side == 'long':
                                                distance = strategy_runner.current_price - stop_price
                                            else:
                                                distance = stop_price - strategy_runner.current_price
                                            status_msg += f" | Trail: ${stop_price:.2f} (dist: ${distance:.2f})"
                                
                                # Mostrar sinais pendentes do TV Engine
                                tv_status = strategy_runner.get_status().get('tv_engine', {})
                                if tv_status:
                                    if tv_status.get('pending_buy'):
                                        status_msg += " | 🟢 BUY PENDENTE"
                                    if tv_status.get('pending_sell'):
                                        status_msg += " | 🔴 SELL PENDENTE"
                                
                                logger.info(status_msg)
                            last_status_log = current_time
                        
                        # Informações de barra a cada 60 segundos
                        if current_time - last_bar_log > 60:
                            tv_status = strategy_runner.get_status().get('tv_engine', {})
                            if tv_status:
                                bar_start = tv_status.get('current_bar_start')
                                bar_count = tv_status.get('bar_count', 0)
                                if bar_start:
                                    logger.info(f"📊 Barra atual: {bar_start} | Total: {bar_count} barras")
                            last_bar_log = current_time
                        
                        time.sleep(1)  # Loop leve
                        
                    except Exception as e:
                        logger.error(f"💥 Erro no monitoramento: {e}")
                        time.sleep(5)
            
            # Iniciar thread de monitoramento
            trade_thread = threading.Thread(target=monitoring_loop, daemon=True)
            trade_thread.start()
            
            logger.info("✅ Estratégia iniciada automaticamente")
            logger.info("📊 Modo TradingView: Entradas apenas no fechamento das barras")
            logger.info("🎯 Trailing stop: Monitorado a cada tick")
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
    trailing_info = {}
    tv_info = {}
    
    if strategy_runner:
        status = strategy_runner.get_status()
        
        position_info = {
            'has_position': strategy_runner.position_size != 0,
            'position_side': strategy_runner.position_side,
            'position_size': abs(strategy_runner.position_size) if strategy_runner.position_size else 0,
            'entry_price': strategy_runner.entry_price,
            'current_price': strategy_runner.current_price,
            'pending_buy': False,
            'pending_sell': False
        }
        
        # Obter informações do TV Engine
        tv_info = status.get('tv_engine', {})
        if tv_info:
            position_info['pending_buy'] = tv_info.get('pending_buy', False)
            position_info['pending_sell'] = tv_info.get('pending_sell', False)
        
        # Obter informações do trailing stop
        if strategy_runner.position_size != 0:
            trailing_status = status.get('trailing_stop', {})
            if trailing_status:
                trailing_info = {
                    'activated': trailing_status.get('activated', False),
                    'current_stop': trailing_status.get('current_stop'),
                    'best_price': trailing_status.get('best_price'),
                    'trail_points': trailing_status.get('trail_points', 55),
                    'trail_offset': trailing_status.get('trail_offset', 15)
                }
    
    # Calcular tempo até próxima barra
    next_bar_info = ""
    if tv_info and tv_info.get('current_bar_start'):
        try:
            bar_start = tv_info['current_bar_start']
            if ':' in bar_start:
                # Converter para datetime
                from datetime import datetime
                now = datetime.now()
                bar_hour, bar_minute = map(int, bar_start.split(':'))
                bar_time = now.replace(hour=bar_hour, minute=bar_minute, second=0, microsecond=0)
                
                # Adicionar 30 minutos para próxima barra
                from datetime import timedelta
                next_bar = bar_time + timedelta(minutes=30)
                time_to_next = (next_bar - now).total_seconds()
                
                if time_to_next > 0:
                    minutes = int(time_to_next / 60)
                    seconds = int(time_to_next % 60)
                    next_bar_info = f"Próxima barra em: {minutes}:{seconds:02d}"
        except:
            pass
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading ETH/USDT - REPLICAÇÃO EXATA TRADINGVIEW</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; text-align: center; padding: 20px; }
            .container { max-width: 900px; margin: 0 auto; }
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
            .trailing-info { background: rgba(255,215,0,0.1); padding: 15px; border-radius: 10px; margin: 20px 0; text-align: left; border: 1px solid #ffd700; }
            .trailing-info h3 { color: #ffd700; margin-top: 0; }
            .signals-info { background: rgba(0,150,255,0.1); padding: 15px; border-radius: 10px; margin: 20px 0; text-align: left; border: 1px solid #0096ff; }
            .signals-info h3 { color: #0096ff; margin-top: 0; }
            .tv-info { background: rgba(100,255,100,0.1); padding: 15px; border-radius: 10px; margin: 20px 0; text-align: left; border: 1px solid #64ff64; }
            .tv-info h3 { color: #64ff64; margin-top: 0; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
            .signal-active { color: #00ff88; font-weight: bold; }
            .signal-inactive { color: #888; }
            .next-bar { background: rgba(100,100,255,0.1); padding: 10px; border-radius: 5px; margin: 10px 0; }
            .warning { color: #ffaa00; font-weight: bold; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT - REPLICAÇÃO EXATA TRADINGVIEW</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
                <p><strong>Ambiente:</strong> """ + ("🌍 RENDER" if IS_RENDER else "💻 LOCAL") + """</p>
                <p><strong>Status:</strong> {{ '🟢 ATIVO' if trading_active else '🔴 INATIVO' }}</p>
                <p><strong>Modo:</strong> calc_on_every_tick=false (EXATO TradingView)</p>
                {% if next_bar_info %}
                <div class="next-bar">
                    ⏰ {{ next_bar_info }}
                </div>
                {% endif %}
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Modo TradingView (entradas apenas no fechamento da barra)' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <div class="tv-info">
                <h3>📊 TRADINGVIEW ENGINE</h3>
                <p><strong>Barra atual:</strong> {{ tv_info.current_bar_start if tv_info else 'N/A' }}</p>
                <p><strong>Barras processadas:</strong> {{ tv_info.bar_count if tv_info else '0' }}</p>
                <p><strong>Modo:</strong> calc_on_every_tick=false</p>
                <p class="warning">⚠️ Entradas executadas APENAS no fechamento das barras de 30min</p>
            </div>
            
            <div class="signals-info">
                <h3>📡 SINAIS PENDENTES</h3>
                <p><strong>Sinal BUY pendente:</strong> 
                    <span class="{{ 'signal-active' if position_info.pending_buy else 'signal-inactive' }}">
                        {{ '🟢 ATIVO' if position_info.pending_buy else '⚪ INATIVO' }}
                    </span>
                </p>
                <p><strong>Sinal SELL pendente:</strong> 
                    <span class="{{ 'signal-active' if position_info.pending_sell else 'signal-inactive' }}">
                        {{ '🔴 ATIVO' if position_info.pending_sell else '⚪ INATIVO' }}
                    </span>
                </p>
                <p><strong>Posição atual:</strong> 
                    {% if position_info.has_position %}
                        {{ position_info.position_side|upper }} {{ "%.4f"|format(position_info.position_size) }} ETH
                    {% else %}
                        FLAT (sem posição)
                    {% endif %}
                </p>
            </div>
            
            <div class="grid">
                <!-- Informações da posição atual -->
                {% if position_info.has_position %}
                <div class="position-info">
                    <h3>💰 POSIÇÃO ATUAL</h3>
                    <p><strong>Lado:</strong> {{ position_info.position_side|upper }}</p>
                    <p><strong>Tamanho:</strong> {{ "%.4f"|format(position_info.position_size) }} ETH</p>
                    <p><strong>Entrada:</strong> ${{ "%.2f"|format(position_info.entry_price) }}</p>
                    <p><strong>Preço Atual:</strong> ${{ "%.2f"|format(position_info.current_price) }}</p>
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
                
                <!-- Informações do Trailing Stop -->
                <div class="trailing-info">
                    <h3>🎯 TRAILING STOP DINÂMICO</h3>
                    <p><strong>Status:</strong> {{ '🟢 ATIVADO' if trailing_info.activated else '🟡 PENDENTE' }}</p>
                    <p><strong>Stop Atual:</strong> ${{ "%.2f"|format(trailing_info.current_stop) }}</p>
                    <p><strong>Melhor Preço:</strong> ${{ "%.2f"|format(trailing_info.best_price) }}</p>
                    <p><strong>Trail Points:</strong> {{ trailing_info.trail_points }}p</p>
                    <p><strong>Trail Offset:</strong> {{ trailing_info.trail_offset }}p</p>
                    <p><strong>Distância até Stop:</strong> 
                        {% if position_info.position_side == 'long' %}
                            ${{ "%.2f"|format(position_info.current_price - trailing_info.current_stop) }}
                        {% else %}
                            ${{ "%.2f"|format(trailing_info.current_stop - position_info.current_price) }}
                        {% endif %}
                    </p>
                    <p class="warning">⚠️ Saídas ocorrem a QUALQUER MOMENTO (monitorado a cada tick)</p>
                </div>
                {% endif %}
            </div>
            
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
                <a href="/tv-status">🎯 TradingView Status</a>
            </div>
            
            <div class="info">
                <strong>🎯 MODO TRADINGVIEW EXATO:</strong> calc_on_every_tick=false<br>
                <strong>⏰ ENTRADAS:</strong> Apenas no FECHAMENTO das barras de 30min<br>
                <strong>📈 SAÍDAS:</strong> A qualquer momento (trailing stop monitorado por tick)<br>
                <strong>⚙️ Parâmetros:</strong> fixedSL=2000p, fixedTP=55p, trail_offset=15p<br>
                <strong>⚠️ MODO SIMULAÇÃO:</strong> Nenhuma ordem real será enviada à OKX
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
        
        // Auto-refresh a cada 30 segundos
        setTimeout(() => {
            location.reload();
        }, 30000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html, 
                                 trading_active=trading_active, 
                                 IS_RENDER=IS_RENDER,
                                 position_info=position_info,
                                 trailing_info=trailing_info,
                                 tv_info=tv_info,
                                 next_bar_info=next_bar_info)

# ============================================================================
# 8. ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot (TradingView Exact Mode)",
        "trading_active": trading_active,
        "mode": "calc_on_every_tick_false",
        "replication": "exact_tradingview",
        "timestamp": datetime.now().isoformat(),
        "environment": "render" if IS_RENDER else "local",
        "uptime_seconds": round(time.time() - start_time, 2)
    })

@app.route('/debug')
def debug_info():
    """Endpoint para diagnóstico DETALHADO"""
    try:
        strategy_status = {}
        if strategy_runner:
            status = strategy_runner.get_status()
            
            strategy_status = {
                "is_running": strategy_runner.is_running,
                "current_price": strategy_runner.current_price,
                "position_size": strategy_runner.position_size,
                "position_side": strategy_runner.position_side,
                "entry_price": strategy_runner.entry_price,
                "tv_engine": status.get('tv_engine', {}),
                "trailing_stop": status.get('trailing_stop'),
                "trailing_stop_price": strategy_runner.trailing_stop.get_stop_price() if strategy_runner.position_size else None,
                "processing_mode": "tradingview_exact",
                "calc_on_every_tick": False
            }
        
        # Obter trade_history_count
        trade_history_count = 0
        if trade_history and hasattr(trade_history, 'trades'):
            trade_history_count = len(trade_history.trades)
        
        return jsonify({
            "trading_active": trading_active,
            "environment": "render" if IS_RENDER else "local",
            "simulation_mode": True,
            "processing_mode": "tradingview_exact_replication",
            "calc_on_every_tick": False,
            "entry_timing": "on_bar_close_only",
            "exit_timing": "any_time_trailing_stop",
            "strategy_runner": strategy_status,
            "okx_initialized": okx_client is not None,
            "trade_history_count": trade_history_count,
            "uptime_seconds": round(time.time() - start_time, 2),
            "current_time": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/tv-status')
def tv_status():
    """Endpoint específico para status do TradingView Engine"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        status = strategy_runner.get_status()
        tv_engine = status.get('tv_engine', {})
        
        return jsonify({
            "tradingview_mode": True,
            "calc_on_every_tick": False,
            "timeframe_minutes": 30,
            "tv_engine": tv_engine,
            "current_price": strategy_runner.current_price,
            "has_position": strategy_runner.position_size != 0,
            "position_side": strategy_runner.position_side,
            "pending_signals": {
                "buy": tv_engine.get('pending_buy', False),
                "sell": tv_engine.get('pending_sell', False)
            },
            "bar_information": {
                "current_start": tv_engine.get('current_bar_start'),
                "total_count": tv_engine.get('bar_count', 0)
            }
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
        
        # Reiniciar estratégia
        if strategy_runner.start():
            trading_active = True
            logger.info("🔄 Estratégia reiniciada (modo TradingView exato)")
            return jsonify({"success": True, "message": "Estratégia reiniciada (modo TradingView exato)"})
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
        
        def monitoring_loop():
            """Loop de monitoramento LEVE"""
            logger.info("👁️  Monitoramento iniciado manualmente")
            
            last_status_log = time.time()
            
            while trading_active and strategy_runner:
                try:
                    current_time = time.time()
                    
                    # Log de status a cada 15 segundos
                    if current_time - last_status_log > 15:
                        if strategy_runner.current_price:
                            status_msg = f"📊 Preço: ${strategy_runner.current_price:.2f}"
                            
                            if strategy_runner.position_side:
                                status_msg += f" | Posição: {strategy_runner.position_side.upper()} {abs(strategy_runner.position_size):.4f} ETH"
                                
                                # Mostrar trailing stop
                                trailing_status = strategy_runner.trailing_stop.get_status()
                                if trailing_status and trailing_status.get('activated'):
                                    stop_price = trailing_status.get('current_stop')
                                    if stop_price:
                                        if strategy_runner.position_side == 'long':
                                            distance = strategy_runner.current_price - stop_price
                                        else:
                                            distance = stop_price - strategy_runner.current_price
                                        status_msg += f" | Trail: ${stop_price:.2f} (dist: ${distance:.2f})"
                            
                            # Mostrar sinais pendentes
                            tv_status = strategy_runner.get_status().get('tv_engine', {})
                            if tv_status:
                                if tv_status.get('pending_buy'):
                                    status_msg += " | 🟢 BUY PENDENTE"
                                if tv_status.get('pending_sell'):
                                    status_msg += " | 🔴 SELL PENDENTE"
                            
                            logger.info(status_msg)
                        last_status_log = current_time
                    
                    time.sleep(1)  # Loop leve
                    
                except Exception as e:
                    logger.error(f"💥 Erro no monitoramento: {e}")
                    time.sleep(5)
        
        # Iniciar thread de monitoramento
        trade_thread = threading.Thread(target=monitoring_loop, daemon=True)
        trade_thread.start()
        
        logger.info("⚡ BOT LIGADO em modo TRADINGVIEW EXATO!")
        logger.info("🎯 calc_on_every_tick=false (entradas apenas no fechamento da barra)")
        logger.info("📈 Trailing stop monitorado a cada tick")
        return jsonify({
            "status": "success", 
            "message": "Bot iniciado em modo TradingView exato (calc_on_every_tick=false)!"
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
    
    logger.info("⏹️ BOT PARADO (modo TradingView)")
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
                        <p><strong>Modo TradingView:</strong> Ignorará trailing stop e fechará IMEDIATAMENTE.</p>
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
    
    # Obter informações da estratégia
    if strategy_runner:
        status_data = strategy_runner.get_status()
        tv_engine = status_data.get('tv_engine', {})
        
        pending_buy = tv_engine.get('pending_buy', False)
        pending_sell = tv_engine.get('pending_sell', False)
        entry_price = strategy_runner.entry_price
        position_side = strategy_runner.position_side
        position_size = strategy_runner.position_size
        bar_start = tv_engine.get('current_bar_start')
        bar_count = tv_engine.get('bar_count', 0)
        
        # Trailing stop info
        trailing_info = status_data.get('trailing_stop', {})
        trailing_price = trailing_info.get('current_stop') if trailing_info else None
        trailing_activated = trailing_info.get('activated', False) if trailing_info else False
    else:
        pending_buy = pending_sell = False
        entry_price = position_side = None
        position_size = 0
        bar_start = None
        bar_count = 0
        trailing_price = None
        trailing_activated = False
    
    # Calcular PnL
    pnl_usdt = 0
    pnl_percent = 0
    if position_side and entry_price and price:
        if position_side == 'long':
            pnl_usdt = (price - entry_price) * abs(position_size)
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
        "trailing_stop_price": trailing_price,
        "trailing_activated": trailing_activated,
        "pnl_usdt": round(pnl_usdt, 2),
        "pnl_percent": round(pnl_percent, 2),
        "bar_information": {
            "current_start": bar_start,
            "total_count": bar_count
        },
        "environment": "render" if IS_RENDER else "local",
        "simulation_mode": True,
        "processing_mode": "tradingview_exact",
        "calc_on_every_tick": False,
        "entry_timing": "on_bar_close_only"
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
            <title>📜 Histórico de Operações - Modo TradingView</title>
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
                    <h1>📜 Histórico de Operações - MODO TRADINGVIEW</h1>
                    <p>ETH/USDT - calc_on_every_tick=false • Entradas no fechamento da barra</p>
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
                    <p><em>Modo: calc_on_every_tick=false (entradas no fechamento da barra)</em></p>
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
                
                # Identificar motivo do fechamento
                reason_icon = '⚡'
                if trade.get('status') == 'closed':
                    if 'trailing' in trade.get('reason', '').lower():
                        reason_icon = '🎯'
                    elif 'stop' in trade.get('reason', '').lower():
                        reason_icon = '🛑'
                    elif 'profit' in trade.get('reason', '').lower():
                        reason_icon = '💰'
                    elif 'inversao' in trade.get('reason', '').lower():
                        reason_icon = '🔄'
                
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
                            <td>{reason_icon}</td>
                        </tr>
                """
            
            html += """
                    </tbody>
                </table>
            """
        
        html += """
                <div class="footer">
                    <p><strong>🎯 MODO TRADINGVIEW:</strong> calc_on_every_tick=false (entradas apenas no fechamento da barra)</p>
                    <p><strong>⏰ TIMING:</strong> Entradas às 00:00, 00:30, 01:00... (fechamento das barras de 30min)</p>
                    <p><strong>📈 SAÍDAS:</strong> A qualquer momento via trailing stop dinâmico</p>
                    <p><strong>⚠️ MODO SIMULAÇÃO:</strong> Nenhuma ordem real foi executada na OKX.</p>
                    <p>Horário de Brasília (BRT) • Replicação exata do Pine Script</p>
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
            "passphrase_exists": bool(okx_client.passphrase),
            "mode": "tradingview_exact",
            "calc_on_every_tick": False,
            "trailing_stop": True
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# 9. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"🎯 Modo: REPLICAÇÃO EXATA DO TRADINGVIEW")
    logger.info(f"⚙️ Parâmetro: calc_on_every_tick=false")
    logger.info(f"⏰ Entradas: Apenas no FECHAMENTO das barras de 30min")
    logger.info(f"📈 Saídas: A qualquer momento (trailing stop ativo)")
    
    # Iniciar estratégia automaticamente se estiver em ambiente local
    if not IS_RENDER:
        logger.info("💻 Iniciando estratégia automaticamente (ambiente local)...")
        start_strategy_automatically()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
