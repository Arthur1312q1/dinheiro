#!/usr/bin/env python3
"""
MAIN.PY ATUALIZADO - COM SINCRONIZAÇÃO EXATA E LOOP RÁPIDO
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
if os.getenv('RENDER', '').lower() == 'true':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
else:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
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
# 3. IMPORTAR MÓDULOS DE src/ (ATUALIZADO)
# ============================================================================
try:
    from src.okx_client import OKXClient
    from src.keep_alive import KeepAliveSystem
    from src.strategy_runner_exact import StrategyRunnerExact  # VERSÃO REEESCRITA
    from src.trade_history import TradeHistory
    from src.time_sync import TimeSync  # VERSÃO PRECISA (50ms)
    from src.balance_manager import ExactBalanceManager  # VERSÃO EXATA
    from src.flag_system import FlagSystem  # NOVO
    from src.exact_execution_logger import ExactExecutionLogger  # NOVO
    from src.comparison_logger import ComparisonLogger  # Mantido
    
    logger.info("✅ Módulos importados com sucesso (SINCRONIZAÇÃO EXATA)")
    
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
    
    # Inicializar strategy runner (AGORA COM FLUXO TEMPORAL EXATO)
    strategy_runner = StrategyRunnerExact(okx_client, trade_history)
    
    logger.info("✅ Sistema inicializado com FLUXO TEMPORAL EXATO")
    
except Exception as e:
    logger.error(f"❌ Erro na inicialização: {e}")
    import traceback
    logger.error(traceback.format_exc())
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
            
            def trading_loop_exact():
                logger.info("🔄 Loop de trading EXATO iniciado (10ms)")
                
                # Contador para logs periódicos
                last_status_log = time.time()
                consecutive_errors = 0
                last_bar_logged = None
                
                while trading_active and strategy_runner:
                    try:
                        # Executar estratégia COM FLUXO EXATO (loop rápido)
                        status = strategy_runner.run_strategy_exact()
                        
                        # Log detalhado a cada nova barra
                        if strategy_runner.last_bar_timestamp and strategy_runner.last_bar_timestamp != last_bar_logged:
                            logger.info("=" * 60)
                            logger.info(f"📊 BARRA {strategy_runner.last_bar_timestamp.strftime('%H:%M:%S')} UTC PROCESSADA")
                            logger.info(f"   Preço: ${strategy_runner.current_price:.2f}")
                            
                            # Obter flags do sistema EXATO
                            if hasattr(strategy_runner, 'flag_system'):
                                flags = strategy_runner.flag_system.get_state()
                                logger.info(f"   Flags: pendingBuy={flags['pending_buy']}, pendingSell={flags['pending_sell']}")
                            
                            logger.info(f"   Posição: {strategy_runner.position_size:.4f} ETH")
                            logger.info(f"   Lado: {strategy_runner.position_side}")
                            
                            if strategy_runner.entry_price:
                                logger.info(f"   Entrada: ${strategy_runner.entry_price:.2f}")
                            
                            # BALANCE DINÂMICO EXATO
                            if hasattr(strategy_runner, 'balance_manager'):
                                balance = strategy_runner.balance_manager.get_balance()
                                logger.info(f"   Balance: ${balance:.2f}")
                            
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
                                balance_str = ""
                                if hasattr(strategy_runner, 'balance_manager'):
                                    balance = strategy_runner.balance_manager.get_balance()
                                    balance_str = f" | Balance: ${balance:.2f}"
                                
                                # Obter flags para status
                                flags_str = ""
                                if hasattr(strategy_runner, 'flag_system'):
                                    flags = strategy_runner.flag_system.get_state()
                                    flags_str = f" | Flags: B={flags['pending_buy']} S={flags['pending_sell']}"
                                
                                logger.info(f"📈 Status: ${strategy_runner.current_price:.2f} | Posição: {position_str}{balance_str}{flags_str}")
                                
                                # Info do trailing
                                if strategy_runner.trailing_manager:
                                    trailing = strategy_runner.trailing_manager
                                    if trailing.trailing_activated:
                                        logger.info(f"   Trailing Stop: ${trailing.current_stop:.2f}")
                            last_status_log = current_time
                        
                        # LOOP RÁPIDO (10ms) - MODIFICAÇÃO CRÍTICA
                        time.sleep(0.01)  # 10 milissegundos
                        consecutive_errors = 0  # Resetar contador de erros
                        
                    except Exception as e:
                        consecutive_errors += 1
                        logger.error(f"💥 Erro no loop de trading EXATO ({consecutive_errors}): {e}")
                        if consecutive_errors > 10:
                            logger.error("🔴 Muitos erros consecutivos, parando loop...")
                            break
                        time.sleep(1)  # Aguarda 1 segundo em caso de erro
            
            # Iniciar thread de trading EXATO
            trade_thread = threading.Thread(target=trading_loop_exact, daemon=True)
            trade_thread.start()
            logger.info("✅ Estratégia EXATA iniciada automaticamente no Render")
        else:
            logger.error("❌ Falha ao iniciar a estratégia EXATA automaticamente")
            
    except Exception as e:
        logger.error(f"❌ Erro ao iniciar estratégia EXATA automaticamente: {e}")

# Iniciar automaticamente se estiver no Render
if IS_RENDER:
    # Aguardar 5 segundos para garantir que tudo está inicializado
    threading.Timer(5.0, start_strategy_automatically).start()

# ============================================================================
# 7. INTERFACE WEB - ATUALIZADA COM FLUXO EXATO
# ============================================================================
@app.route('/')
def home():
    # Obter informações da posição atual
    position_info = {}
    balance_info = {}
    sync_info = {}
    flags_info = {}
    
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
        
        # Informações do balance EXATO
        if hasattr(strategy_runner, 'balance_manager'):
            balance_stats = strategy_runner.balance_manager.get_stats()
            balance_info = {
                'current_balance': balance_stats['current_balance'],
                'initial_capital': balance_stats['initial_capital'],
                'netprofit': balance_stats['netprofit'],
                'trade_count': balance_stats['trade_count'],
                'formula': balance_stats.get('formula', 'balance = initial_capital + netprofit')
            }
        
        # Informações de sincronização EXATA
        if hasattr(strategy_runner, 'time_sync'):
            sync_data = strategy_runner.time_sync.get_precise_bar_info()
            next_bar_in = sync_data.get('seconds_to_next_bar', 0)
            sync_info = {
                'next_bar_in': int(next_bar_in) if next_bar_in else 0,
                'current_time': sync_data['current_timestamp'].strftime('%H:%M:%S.%f')[:-3] if sync_data.get('current_timestamp') else 'N/A',
                'bar_count': strategy_runner.bar_count,
                'is_exact_close': sync_data.get('is_exact_close', False),
                'is_exact_open': sync_data.get('is_exact_open', False),
                'ms_to_next': int(sync_data.get('milliseconds_to_next', 0)),
                'ms_since_open': int(sync_data.get('milliseconds_since_open', 0))
            }
        
        # Informações das flags EXATAS
        if hasattr(strategy_runner, 'flag_system'):
            flags_state = strategy_runner.flag_system.get_state()
            flags_info = {
                'pending_buy': flags_state['pending_buy'],
                'pending_sell': flags_state['pending_sell'],
                'buy_signal_prev': flags_state['buy_signal_prev'],
                'sell_signal_prev': flags_state['sell_signal_prev']
            }
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading ETH/USDT - FLUXO EXATO</title>
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
            .ms-display { font-family: monospace; font-size: 14px; }
            .flow-diagram { background: rgba(0,0,0,0.3); padding: 15px; border-radius: 10px; margin: 20px 0; }
            .flow-step { display: inline-block; padding: 10px; margin: 5px; background: rgba(0, 136, 255, 0.2); border-radius: 5px; }
            .flow-arrow { display: inline-block; padding: 10px; color: #00ff88; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT - FLUXO EXATO</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Fluxo Temporal: FECHAMENTO→ABERTURA</p>
                <p><strong>Ambiente:</strong> """ + ("🌍 RENDER" if IS_RENDER else "💻 LOCAL") + """</p>
                <p><strong>Status:</strong> {{ '🟢 ATIVO' if trading_active else '🔴 INATIVO' }}</p>
                <p><strong>Versão:</strong> FLUXO TEMPORAL EXATO (50ms precisão)</p>
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Fluxo Exato: Fechamento → Processamento → Execução na Abertura' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <!-- Diagrama do fluxo -->
            <div class="flow-diagram">
                <h4>🔁 FLUXO TEMPORAL EXATO:</h4>
                <div class="flow-step">Barra N Fechando</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">Processar Sinais</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">Setar Flags</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">Barra N+1 Abrindo</div>
                <div class="flow-arrow">→</div>
                <div class="flow-step">Executar Ordens</div>
            </div>
            
            <!-- Grid de informações EXATAS -->
            <div class="grid">
                <!-- Card de Sincronização -->
                <div class="card">
                    <h4>⏰ Sincronização (50ms)</h4>
                    <p>Barra #{{ sync_info.bar_count }}</p>
                    <p>Horário UTC: {{ sync_info.current_time }}</p>
                    <p>Próxima barra em: <span class="ms-display">{{ sync_info.next_bar_in }}s ({{ sync_info.ms_to_next }}ms)</span></p>
                    <p>Fechamento exato: {{ '✅ SIM' if sync_info.is_exact_close else '❌ NÃO' }}</p>
                    <p>Abertura exata: {{ '✅ SIM' if sync_info.is_exact_open else '❌ NÃO' }}</p>
                    <span class="sync-badge">PRECISÃO 50ms</span>
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
                    ⚡ Iniciar Fluxo Exato
                </button>
                <button class="btn stop-btn" onclick="controlBot('stop')" {{ 'disabled' if not trading_active else '' }}>
                    ⏹️ Parar Fluxo
                </button>
                <button class="btn exact-btn" onclick="window.location.href='/validate-exact'">
                    🔬 Validar Fluxo Exato
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
                <a href="/debug">🐛 Debug</a>
                <a href="/health">❤️ Saúde</a>
                <a href="/exact-status">🎯 Status Exato</a>
            </div>
            
            <!-- Informações -->
            <div class="info">
                <strong>✅ FLUXO TEMPORAL EXATO IMPLEMENTADO:</strong><br>
                <strong>Fechamento (últimos 100ms):</strong> Processa candle completo, gera sinais<br>
                <strong>Transição:</strong> pendingBuy := nz(pendingBuy[1]); if (buy_signal[1]) pendingBuy := true<br>
                <strong>Abertura (primeiros 50ms):</strong> if (pendingBuy and position_size <= 0) → EXECUTA<br>
                <strong>Loop rápido:</strong> 10ms (100Hz) para precisão temporal<br>
                <strong>Balance:</strong> {{ balance_info.formula }} = ${{ "%.2f"|format(balance_info.current_balance) }}<br>
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
# 8. ENDPOINTS DA API - ATUALIZADOS
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot - FLUXO EXATO",
        "trading_active": trading_active,
        "timestamp": datetime.now().isoformat(),
        "environment": "render" if IS_RENDER else "local",
        "uptime_seconds": round(time.time() - start_time, 2),
        "version": "exact_flow_1.0",
        "flow_type": "fechamento→processamento→execução na abertura",
        "precision_ms": 50
    })

# ... (manter os endpoints existentes, mas atualizar para refletir o fluxo exato)

@app.route('/sync-status')
def sync_status():
    """Status da sincronização temporal EXATA"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        sync_info = strategy_runner.time_sync.get_precise_bar_info()
        return jsonify({
            "synchronized_time": sync_info['current_timestamp'].isoformat(),
            "current_bar": sync_info['current_bar_timestamp'].isoformat(),
            "next_bar_in": sync_info['seconds_to_next_bar'],
            "next_bar_in_ms": sync_info['milliseconds_to_next'],
            "time_since_open_ms": sync_info['milliseconds_since_open'],
            "time_offset": strategy_runner.time_sync.time_offset,
            "ntp_sync": strategy_runner.time_sync.last_sync.isoformat() if strategy_runner.time_sync.last_sync else None,
            "bar_count": strategy_runner.bar_count,
            "is_exact_close": sync_info['is_exact_close'],
            "is_exact_open": sync_info['is_exact_open'],
            "precision_ms": strategy_runner.time_sync.tolerance_ms,
            "flow_state": {
                "bar_close_processed": getattr(strategy_runner, 'bar_close_processed', False),
                "signals_for_next_bar": getattr(strategy_runner, 'signals_for_next_bar', None) is not None
            }
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/flags-status')
def flags_status():
    """Status das flags EXATAS"""
    if not strategy_runner:
        return jsonify({"error": "Strategy Runner não inicializado"}), 500
    
    try:
        if hasattr(strategy_runner, 'flag_system'):
            flags = strategy_runner.flag_system.get_state()
            return jsonify({
                "flags": flags,
                "interpretation": {
                    "pending_buy": "Flag para execução BUY na próxima abertura",
                    "pending_sell": "Flag para execução SELL na próxima abertura",
                    "buy_signal_prev": "buy_signal[1] (da barra anterior)",
                    "sell_signal_prev": "sell_signal[1] (da barra anterior)",
                    "pending_buy_prev": "pendingBuy[1] (valor anterior da flag)",
                    "pending_sell_prev": "pendingSell[1] (valor anterior da flag)"
                },
                "pine_logic": [
                    "pendingBuy := nz(pendingBuy[1])",
                    "pendingSell := nz(pendingSell[1])",
                    "if (buy_signal[1]) pendingBuy := true",
                    "if (sell_signal[1]) pendingSell := true"
                ],
                "execution_conditions": {
                    "buy_condition": "pendingBuy and strategy.position_size <= 0",
                    "sell_condition": "pendingSell and strategy.position_size >= 0"
                }
            })
        else:
            return jsonify({"error": "FlagSystem não inicializado"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/validate-exact')
def validate_exact_flow():
    """Página para validação do fluxo exato"""
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
            .flow-box { background: rgba(0, 136, 255, 0.1); padding: 15px; border-radius: 10px; margin: 20px 0; border: 1px solid #0088ff; }
            .flow-step { margin: 10px 0; padding: 10px; background: rgba(255,255,255,0.05); border-radius: 5px; }
            .step-number { display: inline-block; width: 25px; height: 25px; background: #0088ff; color: white; border-radius: 50%; text-align: center; margin-right: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔬 Validação do Fluxo Exato</h1>
                <p>Teste completo do fluxo temporal: Fechamento → Processamento → Execução</p>
            </div>
            
            <div class="flow-box">
                <h3>🔁 FLUXO TEMPORAL EXATO (Pine Script)</h3>
                <div class="flow-step">
                    <span class="step-number">1</span> <strong>FECHAMENTO da Barra N (últimos 100ms):</strong>
                    <ul>
                        <li>Completar candle com preço de fechamento</li>
                        <li>Processar candle completo no engine</li>
                        <li>Gerar buy_signal[N] e sell_signal[N]</li>
                        <li>Setar pendingBuy/pendingSell para próxima barra</li>
                    </ul>
                </div>
                <div class="flow-step">
                    <span class="step-number">2</span> <strong>TRANSFORMAÇÃO (instantâneo):</strong>
                    <ul>
                        <li>pendingBuy := nz(pendingBuy[1])</li>
                        <li>pendingSell := nz(pendingSell[1])</li>
                        <li>if (buy_signal[1]) pendingBuy := true</li>
                        <li>if (sell_signal[1]) pendingSell := true</li>
                    </ul>
                </div>
                <div class="flow-step">
                    <span class="step-number">3</span> <strong>ABERTURA da Barra N+1 (primeiros 50ms):</strong>
                    <ul>
                        <li>if (pendingBuy and strategy.position_size <= 0) → EXECUTA BUY</li>
                        <li>if (pendingSell and strategy.position_size >= 0) → EXECUTA SELL</li>
                        <li>Resetar flags após execução bem-sucedida</li>
                    </ul>
                </div>
            </div>
            
            <div>
                <button class="btn test-btn" onclick="testFullFlow()">Testar Fluxo Completo</button>
                <button class="btn test-btn" onclick="testSyncPrecision()">Testar Precisão 50ms</button>
                <button class="btn test-btn" onclick="testFlagSystem()">Testar Sistema de Flags</button>
                <button class="btn test-btn" onclick="testBalanceExact()">Testar Balance Exato</button>
                <button class="btn back-btn" onclick="window.location.href='/'">Voltar</button>
            </div>
            
            <div id="result" class="result"></div>
            
            <div>
                <h3>📁 Logs de Execução Exata:</h3>
                <p>Logs detalhados em: <code>exact_execution_logs/exact_execution_YYYY-MM-DD.log</code></p>
                <p>Formato: [TIMESTAMP] EVENTO com precisão de milissegundos</p>
                
                <h3>✅ Critérios de Validação:</h3>
                <ol>
                    <li>Timing correto: fechamento → abertura com 50ms de precisão</li>
                    <li>Flags exatas: pendingBuy/pendingSell como Pine Script</li>
                    <li>Balance exato: initial_capital + netprofit</li>
                    <li>Execução exata: condições idênticas ao TradingView</li>
                    <li>Logs consistentes: comparáveis barra-a-barra</li>
                </ol>
            </div>
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
                <p><strong>Flags:</strong> pendingBuy=${data.strategy_runner?.flags_state?.pending_buy || false}, pendingSell=${data.strategy_runner?.flags_state?.pending_sell || false}</p>
                <p><strong>Posição:</strong> ${data.strategy_runner?.position_side || 'FLAT'} ${Math.abs(data.strategy_runner?.position_size || 0).toFixed(4)} ETH</p>
                <p><strong>Balance:</strong> $${data.strategy_runner?.balance?.toFixed(2) || '0.00'}</p>
                <p><strong>Loop ativo:</strong> ${data.strategy_runner?.is_running ? '✅ SIM' : '❌ NÃO'}</p>
                <p><strong>Precisão temporal:</strong> ${data.strategy_runner?.time_sync?.precision_ms || 50}ms</p>
            </div>`;
            
            resultDiv.innerHTML = html;
        }
        
        async function testSyncPrecision() {
            const response = await fetch('/sync-status');
            const data = await response.json();
            
            const resultDiv = document.getElementById('result');
            
            if (data.error) {
                resultDiv.innerHTML = `<div class="error"><h4>❌ Erro:</h4><p>${data.error}</p></div>`;
                return;
            }
            
            const offsetOk = Math.abs(data.time_offset * 1000) < 50;
            const precisionOk = data.precision_ms <= 50;
            
            resultDiv.innerHTML = `
                <div class="${offsetOk && precisionOk ? 'success' : 'warning'}">
                    <h4>⏰ Teste de Precisão Temporal:</h4>
                    <p><strong>Offset NTP:</strong> ${(data.time_offset * 1000).toFixed(1)}ms ${offsetOk ? '✅' : '⚠️ (acima de 50ms)'}</p>
                    <p><strong>Precisão configurada:</strong> ${data.precision_ms}ms ${precisionOk ? '✅' : '⚠️'}</p>
                    <p><strong>Próxima barra em:</strong> ${data.next_bar_in_ms?.toFixed(1) || '0'}ms</p>
                    <p><strong>Fechamento exato detectado:</strong> ${data.is_exact_close ? '✅ SIM' : '❌ NÃO'}</p>
                    <p><strong>Abertura exata detectada:</strong> ${data.is_exact_open ? '✅ SIM' : '❌ NÃO'}</p>
                    <p><strong>Estado do fluxo:</strong> Barra processada=${data.flow_state?.bar_close_processed || false}, Sinais prontos=${data.flow_state?.signals_for_next_bar || false}</p>
                </div>
            `;
        }
        
        async function testFlagSystem() {
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
                    
                    <h5>Lógica Pine Script:</h5>
                    <ul>
                        ${data.pine_logic.map(logic => `<li><code>${logic}</code></li>`).join('')}
                    </ul>
                    
                    <h5>Condições de Execução:</h5>
                    <ul>
                        <li><code>${data.execution_conditions.buy_condition}</code></li>
                        <li><code>${data.execution_conditions.sell_condition}</code></li>
                    </ul>
                </div>
            `;
        }
        
        async function testBalanceExact() {
            const response = await fetch('/balance-status');
            const data = await response.json();
            
            const resultDiv = document.getElementById('result');
            
            if (data.error) {
                resultDiv.innerHTML = `<div class="error"><h4>❌ Erro:</h4><p>${data.error}</p></div>`;
                return;
            }
            
            const calculated = data.initial_capital + data.netprofit;
            const exactMatch = Math.abs(calculated - data.balance) < 0.01;
            
            resultDiv.innerHTML = `
                <div class="${exactMatch ? 'success' : 'error'}">
                    <h4>💰 Teste do Balance Exato:</h4>
                    <p><strong>Balance atual:</strong> $${data.balance?.toFixed(2) || '0.00'}</p>
                    <p><strong>Capital inicial:</strong> $${data.initial_capital?.toFixed(2) || '0.00'}</p>
                    <p><strong>Net Profit:</strong> $${data.netprofit?.toFixed(2) || '0.00'}</p>
                    <p><strong>Verificação:</strong> ${data.initial_capital?.toFixed(2)} + ${data.netprofit?.toFixed(2)} = $${calculated.toFixed(2)} ${exactMatch ? '✅ CORRETO' : '❌ ERRADO'}</p>
                    <p><strong>Fórmula:</strong> ${data.formula || 'N/A'}</p>
                    <p><strong>Trades realizados:</strong> ${data.trade_count || 0}</p>
                </div>
            `;
        }
        </script>
    </body>
    </html>
    """
    return html

# ... (manter os outros endpoints existentes: /balance-status, /exact-status, /debug, etc.)
# APENAS ATUALIZAR O CONTEÚDO PARA REFLETIR O FLUXO EXATO

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
            logger.info("🔄 Loop de trading EXATO iniciado manualmente")
            
            last_status_log = time.time()
            consecutive_errors = 0
            last_bar_logged = None
            
            while trading_active and strategy_runner:
                try:
                    # Executar estratégia COM FLUXO EXATO (loop rápido)
                    status = strategy_runner.run_strategy_exact()
                    
                    if strategy_runner.last_bar_timestamp and strategy_runner.last_bar_timestamp != last_bar_logged:
                        logger.info("=" * 60)
                        logger.info(f"📊 BARRA {strategy_runner.last_bar_timestamp.strftime('%H:%M:%S')} UTC PROCESSADA")
                        logger.info(f"   Preço: ${strategy_runner.current_price:.2f}")
                        
                        if hasattr(strategy_runner, 'flag_system'):
                            flags = strategy_runner.flag_system.get_state()
                            logger.info(f"   Flags: pendingBuy={flags['pending_buy']}, pendingSell={flags['pending_sell']}")
                        
                        logger.info(f"   Posição: {strategy_runner.position_size:.4f} ETH")
                        logger.info(f"   Lado: {strategy_runner.position_side}")
                        
                        if strategy_runner.entry_price:
                            logger.info(f"   Entrada: ${strategy_runner.entry_price:.2f}")
                        
                        if hasattr(strategy_runner, 'balance_manager'):
                            balance = strategy_runner.balance_manager.get_balance()
                            logger.info(f"   Balance: ${balance:.2f}")
                        
                        if strategy_runner.trailing_manager:
                            trailing = strategy_runner.trailing_manager
                            if trailing.trailing_activated:
                                logger.info(f"   Trailing Stop: ${trailing.current_stop:.2f} (ATIVADO)")
                        
                        logger.info("=" * 60)
                        last_bar_logged = strategy_runner.last_bar_timestamp
                    
                    current_time = time.time()
                    if current_time - last_status_log > 30:
                        if strategy_runner.current_price:
                            position_str = f"{strategy_runner.position_side or 'FLAT'} {abs(strategy_runner.position_size):.4f} ETH"
                            balance_str = ""
                            if hasattr(strategy_runner, 'balance_manager'):
                                balance = strategy_runner.balance_manager.get_balance()
                                balance_str = f" | Balance: ${balance:.2f}"
                            
                            flags_str = ""
                            if hasattr(strategy_runner, 'flag_system'):
                                flags = strategy_runner.flag_system.get_state()
                                flags_str = f" | Flags: B={flags['pending_buy']} S={flags['pending_sell']}"
                            
                            logger.info(f"📈 Status: ${strategy_runner.current_price:.2f} | Posição: {position_str}{balance_str}{flags_str}")
                            
                            if strategy_runner.trailing_manager:
                                trailing = strategy_runner.trailing_manager
                                if trailing.trailing_activated:
                                    logger.info(f"   Trailing Stop: ${trailing.current_stop:.2f}")
                        last_status_log = current_time
                    
                    # LOOP RÁPIDO (10ms) - MODIFICAÇÃO CRÍTICA
                    time.sleep(0.01)  # 10 milissegundos
                    consecutive_errors = 0
                    
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Erro no loop de trading EXATO ({consecutive_errors}): {e}")
                    if consecutive_errors > 10:
                        logger.error("🔴 Muitos erros consecutivos, parando loop...")
                        break
                    time.sleep(1)  # Aguarda 1 segundo em caso de erro
        
        trade_thread = threading.Thread(target=trading_loop_exact, daemon=True)
        trade_thread.start()
        
        mode = "REAL" if okx_client and okx_client.has_credentials else "SIMULAÇÃO"
        logger.info(f"⚡ BOT LIGADO em modo {mode} (FLUXO EXATO ATIVADO)!")
        return jsonify({
            "status": "success", 
            "message": f"Bot iniciado em modo {mode} (FLUXO EXATO ATIVADO)!",
            "flow_type": "fechamento→processamento→execução na abertura",
            "precision_ms": 50,
            "loop_speed_ms": 10
        })
        
    except Exception as e:
        logger.error(f"Erro ao iniciar: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ... (manter os outros endpoints: /stop, /force-close, /status, /history, etc.)

# ============================================================================
# 9. PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"✅ FLUXO TEMPORAL EXATO IMPLEMENTADO")
    logger.info(f"   - Precisão: 50ms")
    logger.info(f"   - Loop: 10ms (100Hz)")
    logger.info(f"   - Fluxo: FECHAMENTO → Processamento → Execução na ABERTURA")
    logger.info(f"   - Flags: pendingBuy/pendingSell exato como Pine Script")
    logger.info(f"   - Balance: initial_capital + netprofit (dinâmico)")
    logger.info(f"   - Logs: exact_execution_logs/ para validação")
    
    # Iniciar estratégia automaticamente se estiver em ambiente local
    if not IS_RENDER:
        logger.info("💻 Iniciando estratégia automaticamente (ambiente local)...")
        start_strategy_automatically()
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
