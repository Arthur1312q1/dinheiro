#!/usr/bin/env python3
"""
MAIN.PY - Bot Trading ETH/USDT com execução TOTALMENTE por tick
Replicação EXATA da estratégia Pine Script do TradingView
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
    
    # Inicializar strategy runner (AGORA COM EXECUÇÃO TOTAL POR TICK)
    strategy_runner = StrategyRunner(okx_client, trade_history)
    
    logger.info("✅ Sistema inicializado com sucesso")
    logger.info("⚡ Modo: EXECUÇÃO TOTALMENTE POR TICK")
    logger.info("🎯 Entradas/Saídas a qualquer momento (intra-bar)")
    logger.info("📊 Replicação EXATA do Pine Script")
    
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
        logger.info("⚡ Modo: EXECUÇÃO TOTAL POR TICK (igual TradingView)")
        
        # Iniciar o strategy runner
        if strategy_runner.start():
            trading_active = True
            
            def monitoring_loop():
                """Loop de monitoramento LEVE (processamento é feito no WebSocket)"""
                logger.info("👁️  Loop de monitoramento iniciado")
                
                last_status_log = time.time()
                last_bar_log = time.time()
                
                while trading_active and strategy_runner:
                    try:
                        current_time = time.time()
                        
                        # Log de status a cada 10 segundos
                        if current_time - last_status_log > 10:
                            if strategy_runner.current_price:
                                status_msg = f"📊 Tick: ${strategy_runner.current_price:.2f}"
                                
                                if strategy_runner.position_side:
                                    status_msg += f" | {strategy_runner.position_side.upper()} {abs(strategy_runner.position_size):.4f} ETH"
                                    
                                    # Mostrar trailing stop
                                    trailing_price = strategy_runner.trailing_stop.get_stop_price()
                                    if trailing_price:
                                        if strategy_runner.position_side == 'long':
                                            distance = strategy_runner.current_price - trailing_price
                                        else:
                                            distance = trailing_price - strategy_runner.current_price
                                        status_msg += f" | Trail: ${trailing_price:.2f} (dist: ${distance:.2f})"
                                    
                                    # Mostrar sinais pendentes
                                    if strategy_runner.pending_buy_signal:
                                        status_msg += " | 🟢 BUY PENDENTE"
                                    if strategy_runner.pending_sell_signal:
                                        status_msg += " | 🔴 SELL PENDENTE"
                                
                                logger.info(status_msg)
                            last_status_log = current_time
                        
                        # Log de barra a cada 30 segundos (apenas para informação)
                        if current_time - last_bar_log > 30:
                            if strategy_runner.last_bar_timestamp:
                                now_brazil = datetime.now(strategy_runner.tz_brazil)
                                next_bar = strategy_runner.last_bar_timestamp + strategy_runner.timedelta(minutes=30)
                                time_to_next = (next_bar - now_brazil).total_seconds()
                                
                                if time_to_next > 0:
                                    logger.info(f"⏰ Próxima barra em {int(time_to_next/60)}:{int(time_to_next%60):02d} minutos")
                                
                                last_bar_log = current_time
                        
                        time.sleep(0.5)  # Loop leve
                        
                    except Exception as e:
                        logger.error(f"💥 Erro no loop de monitoramento: {e}")
                        time.sleep(2)
            
            # Iniciar thread de monitoramento
            trade_thread = threading.Thread(target=monitoring_loop, daemon=True)
            trade_thread.start()
            logger.info("✅ Estratégia iniciada automaticamente no Render")
            logger.info("📈 Cada tick do WebSocket é processado em tempo real")
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
    
    if strategy_runner:
        position_info = {
            'has_position': strategy_runner.position_size != 0,
            'position_side': strategy_runner.position_side,
            'position_size': abs(strategy_runner.position_size),
            'entry_price': strategy_runner.entry_price,
            'current_price': strategy_runner.current_price,
            'stop_loss': strategy_runner.stop_loss_price,
            'take_profit': strategy_runner.take_profit_price,
            'pending_buy': strategy_runner.pending_buy_signal,
            'pending_sell': strategy_runner.pending_sell_signal
        }
        
        # Obter informações do trailing stop
        if strategy_runner.position_size != 0:
            trailing_status = strategy_runner.trailing_stop.get_status()
            trailing_info = {
                'activated': trailing_status['activated'],
                'current_stop': trailing_status['current_stop'],
                'best_price': trailing_status['best_price'],
                'trail_points': trailing_status['trail_points'],
                'trail_offset': trailing_status['trail_offset']
            }
    
    # Calcular tempo até próxima barra
    next_bar_info = ""
    if strategy_runner and strategy_runner.last_bar_timestamp:
        try:
            now_brazil = datetime.now(strategy_runner.tz_brazil)
            next_bar = strategy_runner.last_bar_timestamp + strategy_runner.timedelta(minutes=30)
            time_to_next = (next_bar - now_brazil).total_seconds()
            
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
        <title>Bot Trading ETH/USDT - EXECUÇÃO POR TICK TOTAL</title>
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
            .debug { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; margin: 20px 0; text-align: left; }
            .debug pre { overflow: auto; max-height: 300px; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
            .signal-active { color: #00ff88; font-weight: bold; }
            .signal-inactive { color: #888; }
            .next-bar { background: rgba(100,100,255,0.1); padding: 10px; border-radius: 5px; margin: 10px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT - EXECUÇÃO POR TICK TOTAL</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
                <p><strong>Ambiente:</strong> """ + ("🌍 RENDER" if IS_RENDER else "💻 LOCAL") + """</p>
                <p><strong>Status:</strong> {{ '🟢 ATIVO' if trading_active else '🔴 INATIVO' }}</p>
                <p><strong>Modo:</strong> Processamento TOTAL por Tick (Entradas/Saídas intra-bar)</p>
                {% if next_bar_info %}
                <div class="next-bar">
                    ⏰ {{ next_bar_info }}
                </div>
                {% endif %}
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Processando CADA TICK em tempo real' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <div class="signals-info">
                <h3>📡 SINAIS ATUAIS</h3>
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
                        {{ position_info.position_side|upper }} {{ position_info.position_size }} ETH
                    {% else %}
                        FLAT (sem posição)
                    {% endif %}
                </p>
            </div>
            
            <div class="grid">
                <!-- Informações da posição atual -->
                {% if position_info.has_position %}
                <div class="position-info">
                    <h3>📊 Posição Atual</h3>
                    <p><strong>Lado:</strong> {{ position_info.position_side|upper }}</p>
                    <p><strong>Tamanho:</strong> {{ "%.4f"|format(position_info.position_size) }} ETH</p>
                    <p><strong>Entrada:</strong> ${{ "%.2f"|format(position_info.entry_price) }}</p>
                    <p><strong>Preço Atual:</strong> ${{ "%.2f"|format(position_info.current_price) }}</p>
                    <p><strong>Stop Loss Estático:</strong> ${{ "%.2f"|format(position_info.stop_loss) }}</p>
                    <p><strong>Take Profit Estático:</strong> ${{ "%.2f"|format(position_info.take_profit) }}</p>
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
                    <h3>🎯 Trailing Stop Dinâmico</h3>
                    <p><strong>Status:</strong> {{ '🟢 ATIVADO' if trailing_info.activated else '🟡 PENDENTE' }}</p>
                    <p><strong>Stop Atual:</strong> ${{ "%.2f"|format(trailing_info.current_stop) }}</p>
                    <p><strong>Melhor Preço:</strong> ${{ "%.2f"|format(trailing_info.best_price) }}</p>
                    <p><strong>Trail Points:</strong> {{ trailing_info.trail_points }}p</p>
                    <p><strong>Trail Offset:</strong> {{ trailing_info.trail_offset }}p</p>
                    <p><strong>Distância até Stop:</strong> 
                        {% if position_info.position_side == 'long' %}
                            ${{ "%.2f"|format(position_info.current_price - trailing_info.current_stop) }}
                            ({{ "%.1f"|format((position_info.current_price - trailing_info.current_stop) / position_info.current_price * 100) }}%)
                        {% else %}
                            ${{ "%.2f"|format(trailing_info.current_stop - position_info.current_price) }}
                            ({{ "%.1f"|format((trailing_info.current_stop - position_info.current_price) / position_info.current_price * 100) }}%)
                        {% endif %}
                    </p>
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
                <a href="/trailing-status">🎯 Trailing Status</a>
                <a href="/tick-log">⚡ Últimos Ticks</a>
            </div>
            
            <div class="info">
                <strong>⚡ MODO TICK TOTAL:</strong> Cada preço do WebSocket é processado IMEDIATAMENTE<br>
                <strong>🎯 TRAILING STOP ATIVO:</strong> Offset=15p, Trail=55p (exato Pine Script)<br>
                <strong>⏰ Timeframe:</strong> 30 minutos (apenas referência)<br>
                <strong>📈 Entradas:</strong> A qualquer momento (sinal confirmado)<br>
                <strong>📉 Saídas:</strong> A qualquer momento (trailing stop intra-bar)<br>
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
                                 next_bar_info=next_bar_info)

# ============================================================================
# 8. ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot (Tick Mode)",
        "trading_active": trading_active,
        "mode": "full_tick_processing",
        "trailing_stop": True,
        "intra_bar_trading": True,
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
            # Obter informações do interpretador
            interpreter_info = {}
            if strategy_runner.interpreter:
                interpreter_info = {
                    'period': strategy_runner.interpreter.period,
                    'gain_limit': strategy_runner.interpreter.gain_limit,
                    'threshold': strategy_runner.interpreter.threshold,
                    'adaptive': strategy_runner.interpreter.adaptive
                }
            
            # Obter status do trailing stop
            trailing_status = {}
            if strategy_runner.position_size != 0:
                trailing_status = strategy_runner.trailing_stop.get_status()
            
            # Verificar se deve fechar posição
            should_close = False
            if strategy_runner.position_size != 0 and strategy_runner.current_price:
                should_close = strategy_runner.trailing_stop.should_close(strategy_runner.current_price)
            
            strategy_status = {
                "is_running": strategy_runner.is_running,
                "current_price": strategy_runner.current_price,
                "bar_count": strategy_runner.bar_count,
                "pending_buy_signal": strategy_runner.pending_buy_signal,
                "pending_sell_signal": strategy_runner.pending_sell_signal,
                "position_size": strategy_runner.position_size,
                "position_side": strategy_runner.position_side,
                "entry_price": strategy_runner.entry_price,
                "stop_loss_price": strategy_runner.stop_loss_price,
                "take_profit_price": strategy_runner.take_profit_price,
                "interpreter_info": interpreter_info,
                "last_bar_timestamp": strategy_runner.last_bar_timestamp.isoformat() if strategy_runner.last_bar_timestamp else None,
                "websocket_connected": getattr(strategy_runner, 'ws', None) and getattr(strategy_runner.ws, 'sock', None) and strategy_runner.ws.sock.connected,
                "should_close_position": should_close,
                "trailing_stop": trailing_status,
                "trailing_stop_price": strategy_runner.trailing_stop.get_stop_price() if strategy_runner.position_size else None,
                "processing_mode": "full_tick_realtime"
            }
        
        # Obter trade_history_count
        trade_history_count = 0
        if trade_history and hasattr(trade_history, 'trades'):
            trade_history_count = len(trade_history.trades)
        
        return jsonify({
            "trading_active": trading_active,
            "environment": "render" if IS_RENDER else "local",
            "simulation_mode": True,
            "processing_mode": "full_tick_realtime",
            "intra_bar_execution": True,
            "strategy_runner": strategy_status,
            "okx_initialized": okx_client is not None,
            "trade_history_count": trade_history_count,
            "uptime_seconds": round(time.time() - start_time, 2),
            "current_time": datetime.now().isoformat(),
            "brasilia_time": datetime.now().astimezone(strategy_runner.tz_brazil if strategy_runner else None).isoformat() if strategy_runner else None
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/trailing-status')
def trailing_status():
    """Endpoint específico para status do trailing stop"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        if strategy_runner.position_size == 0:
            return jsonify({
                "has_position": False,
                "message": "Sem posição ativa"
            })
        
        trailing_status = strategy_runner.trailing_stop.get_status()
        current_price = strategy_runner.current_price
        
        # Calcular distância
        distance_to_stop = 0
        if trailing_status['current_stop'] and current_price:
            if strategy_runner.position_side == 'long':
                distance_to_stop = current_price - trailing_status['current_stop']
            else:
                distance_to_stop = trailing_status['current_stop'] - current_price
        
        return jsonify({
            "has_position": True,
            "position_side": strategy_runner.position_side,
            "position_size": abs(strategy_runner.position_size),
            "entry_price": strategy_runner.entry_price,
            "current_price": current_price,
            "trailing_stop": trailing_status,
            "distance_to_stop": distance_to_stop,
            "distance_percent": (distance_to_stop / current_price * 100) if current_price > 0 else 0,
            "should_close": strategy_runner.trailing_stop.should_close(current_price),
            "time_since_entry": time.time() - start_time if strategy_runner.entry_price else 0
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/tick-log')
def tick_log():
    """Endpoint para ver últimos ticks processados"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Últimos Ticks Processados</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; padding: 20px; }
            .container { max-width: 1200px; margin: 0 auto; }
            .header { text-align: center; margin-bottom: 30px; }
            h1 { color: #00ff88; margin-bottom: 10px; }
            .back-btn { padding: 10px 20px; background: #555; color: white; border: none; border-radius: 5px; cursor: pointer; margin: 20px 0; }
            .info { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Últimos Ticks Processados</h1>
                <p>Modo: Processamento TOTAL por Tick</p>
            </div>
            
            <div class="info">
                <h3>Informações do Sistema</h3>
                <p><strong>Preço Atual:</strong> $""" + (f"{strategy_runner.current_price:.2f}" if strategy_runner and strategy_runner.current_price else "N/A") + """</p>
                <p><strong>Último Processamento:</strong> """ + (f"{datetime.now().strftime('%H:%M:%S')}" if strategy_runner else "N/A") + """</p>
                <p><strong>WebSocket Conectado:</strong> """ + ("✅ Sim" if strategy_runner and strategy_runner.ws and strategy_runner.ws.sock and strategy_runner.ws.sock.connected else "❌ Não") + """</p>
                <p><strong>Modo:</strong> Cada tick é processado em tempo real</p>
            </div>
            
            <button class="back-btn" onclick="window.location.href='/'">🏠 Voltar</button>
            
            <div class="info">
                <h3>Como funciona:</h3>
                <p>1. Cada novo preço do WebSocket é processado IMEDIATAMENTE</p>
                <p>2. O indicador EC/EMA é calculado a cada tick</p>
                <p>3. Sinais são detectados no momento que ocorrem</p>
                <p>4. Entradas ocorrem no PRIMEIRO TICK após confirmação do sinal</p>
                <p>5. Saídas ocorrem a QUALQUER MOMENTO via trailing stop</p>
                <p>6. NÃO há espera pelo fechamento da barra de 30 minutos</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html

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
        
        # Resetar completamente
        if strategy_runner.interpreter:
            strategy_runner.interpreter.reset()
        
        # Reiniciar estratégia
        if strategy_runner.start():
            trading_active = True
            logger.info("🔄 Estratégia reiniciada com sucesso (MODO TICK TOTAL)")
            return jsonify({"success": True, "message": "Estratégia reiniciada completamente (MODO TICK TOTAL)"})
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
            logger.info("👁️  Loop de monitoramento iniciado manualmente")
            
            last_status_log = time.time()
            
            while trading_active and strategy_runner:
                try:
                    current_time = time.time()
                    
                    # Log de status a cada 10 segundos
                    if current_time - last_status_log > 10:
                        if strategy_runner.current_price:
                            status_msg = f"📊 Tick: ${strategy_runner.current_price:.2f}"
                            
                            if strategy_runner.position_side:
                                status_msg += f" | {strategy_runner.position_side.upper()} {abs(strategy_runner.position_size):.4f} ETH"
                                
                                # Mostrar trailing stop
                                trailing_price = strategy_runner.trailing_stop.get_stop_price()
                                if trailing_price:
                                    if strategy_runner.position_side == 'long':
                                        distance = strategy_runner.current_price - trailing_price
                                    else:
                                        distance = trailing_price - strategy_runner.current_price
                                    status_msg += f" | Trail: ${trailing_price:.2f} (dist: ${distance:.2f})"
                                
                                # Mostrar sinais pendentes
                                if strategy_runner.pending_buy_signal:
                                    status_msg += " | 🟢 BUY PENDENTE"
                                if strategy_runner.pending_sell_signal:
                                    status_msg += " | 🔴 SELL PENDENTE"
                            
                            logger.info(status_msg)
                        last_status_log = current_time
                    
                    time.sleep(0.5)  # Loop leve
                    
                except Exception as e:
                    logger.error(f"💥 Erro no loop de monitoramento: {e}")
                    time.sleep(2)
        
        # Iniciar thread de monitoramento
        trade_thread = threading.Thread(target=monitoring_loop, daemon=True)
        trade_thread.start()
        
        logger.info("⚡ BOT LIGADO em modo TICK TOTAL!")
        logger.info("📈 Cada tick do WebSocket é processado em tempo real")
        logger.info("🎯 Entradas/Saídas ocorrem a qualquer momento (intra-bar)")
        return jsonify({
            "status": "success", 
            "message": "Bot iniciado em modo TICK TOTAL com execução intra-bar!"
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
    
    logger.info("⏹️ BOT PARADO (modo tick total)")
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
                        <p><strong>Ignorará trailing stop e fechará IMEDIATAMENTE.</strong></p>
                        <p><strong>Modo TICK:</strong> Fechamento ocorre no preço atual.</p>
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
        result = strategy_runner.force_close_current_position()
        if result.get('success'):
            logger.info("🔴 POSIÇÃO FECHADA FORÇADAMENTE via API (ignorando trailing stop)")
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
        pending_buy = strategy_runner.pending_buy_signal
        pending_sell = strategy_runner.pending_sell_signal
        entry_price = strategy_runner.entry_price
        stop_loss = strategy_runner.stop_loss_price
        take_profit = strategy_runner.take_profit_price
        position_side = strategy_runner.position_side
        position_size = strategy_runner.position_size
        last_bar = strategy_runner.last_bar_timestamp.strftime('%H:%M') if strategy_runner.last_bar_timestamp else None
        bar_count = strategy_runner.bar_count
    else:
        pending_buy = pending_sell = False
        entry_price = stop_loss = take_profit = None
        position_side = None
        position_size = 0
        last_bar = None
        bar_count = 0
    
    # Obter informações do trailing stop
    trailing_stop_price = None
    trailing_activated = False
    best_price = None
    if strategy_runner and strategy_runner.position_size != 0:
        trailing_status = strategy_runner.trailing_stop.get_status()
        trailing_stop_price = trailing_status.get('current_stop')
        trailing_activated = trailing_status.get('activated', False)
        best_price = trailing_status.get('best_price')
    
    # Calcular PnL se houver posição
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
        "stop_loss_price": stop_loss,
        "take_profit_price": take_profit,
        "trailing_stop_price": trailing_stop_price,
        "trailing_activated": trailing_activated,
        "trailing_best_price": best_price,
        "pnl_usdt": round(pnl_usdt, 2),
        "pnl_percent": round(pnl_percent, 2),
        "last_bar_timestamp": last_bar,
        "bar_count": bar_count,
        "environment": "render" if IS_RENDER else "local",
        "simulation_mode": True,
        "processing_mode": "full_tick_realtime",
        "intra_bar_trading": True
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
            <title>📜 Histórico de Operações - Modo Tick Total</title>
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
                    <h1>📜 Histórico de Operações - MODO TICK TOTAL</h1>
                    <p>ETH/USDT - Processamento TOTAL por Tick • Entradas/Saídas intra-bar</p>
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
                    <p><em>Modo: Execução TOTAL por Tick com Trailing Stop</em></p>
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
                reason_icon = '⚡'  # Padrão para execução por tick
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
                    <p><strong>⚡ MODO TICK TOTAL:</strong> Entradas e saídas ocorrem a QUALQUER MOMENTO</p>
                    <p><strong>🎯 TRAILING STOP ATIVO:</strong> Fechamentos intra-bar via trailing stop dinâmico</p>
                    <p><strong>⚠️ MODO SIMULAÇÃO:</strong> Nenhuma ordem real foi executada na OKX.</p>
                    <p>Horário de Brasília (BRT) • Processamento em tempo real por tick</p>
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
            "mode": "full_tick_processing",
            "trailing_stop": True,
            "intra_bar_trading": True
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# 9. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"⚡ Modo de operação: EXECUÇÃO TOTAL POR TICK")
    logger.info(f"🎯 Trailing Stop: Ativado (execução intra-bar)")
    logger.info(f"📈 Entradas/Saídas: A QUALQUER MOMENTO (igual TradingView)")
    
    # Iniciar estratégia automaticamente se estiver em ambiente local
    if not IS_RENDER:
        logger.info("💻 Iniciando estratégia automaticamente (ambiente local)...")
        start_strategy_automatically()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
