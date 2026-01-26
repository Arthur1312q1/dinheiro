import os
import sys
import logging
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response
import csv
import io

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
# DETECTAR AMBIENTE
# ============================================================================
IS_RENDER = os.getenv('RENDER', '').lower() == 'true'
PORT = int(os.environ.get('PORT', 10000))

# ============================================================================
# SISTEMA DE HISTÓRICO SIMPLES (sem dependências)
# ============================================================================
class SimpleTradeHistory:
    def __init__(self, file_path=None):
        try:
            import pytz
            self.tz_brazil = pytz.timezone('America/Sao_Paulo')
        except:
            # Fallback se pytz não estiver disponível
            class FakeTZ:
                def localize(self, dt):
                    return dt
            self.tz_brazil = FakeTZ()
        
        if file_path is None:
            if IS_RENDER and os.path.exists('/data'):
                self.file_path = "/data/trade_history.json"
                logger.info("📁 Usando disco persistente /data do Render")
            else:
                self.file_path = "trade_history.json"
                logger.info("📁 Usando arquivo local")
        else:
            self.file_path = file_path
        
        self.trades = []
        self.load_trades()
        logger.info(f"📊 Histórico inicializado: {len(self.trades)} trades")
    
    def get_brazil_time(self):
        return datetime.now(self.tz_brazil)
    
    def add_trade(self, side, entry_price, quantity):
        try:
            trade_id = len(self.trades) + 1
            entry_time = self.get_brazil_time()
            
            trade = {
                'id': trade_id,
                'side': side,
                'entry_price': entry_price,
                'quantity': quantity,
                'entry_time': entry_time.isoformat(),
                'entry_time_str': entry_time.strftime('%d/%m/%Y %H:%M:%S'),
                'exit_price': None,
                'exit_time': None,
                'exit_time_str': None,
                'pnl_percent': 0.0,
                'pnl_usdt': 0.0,
                'status': 'open',
                'duration': None
            }
            
            self.trades.append(trade)
            self.save_trades()
            logger.info(f"📝 Trade #{trade_id} registrada: {side.upper()} {quantity:.4f} ETH @ ${entry_price:.2f}")
            return trade_id
            
        except Exception as e:
            logger.error(f"❌ Erro ao registrar trade: {e}")
            return None
    
    def close_trade(self, trade_id, exit_price):
        try:
            for trade in self.trades:
                if trade['id'] == trade_id and trade['status'] == 'open':
                    exit_time = self.get_brazil_time()
                    entry_price = trade['entry_price']
                    
                    # Calcular PnL
                    if trade['side'] == 'buy':
                        pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                    else:  # sell (short)
                        pnl_percent = ((entry_price - exit_price) / entry_price) * 100
                    
                    pnl_usdt = (entry_price * trade['quantity'] * pnl_percent) / 100
                    
                    # Calcular duração
                    try:
                        entry_time_obj = datetime.fromisoformat(trade['entry_time'].replace('Z', '+00:00'))
                    except:
                        try:
                            entry_time_obj = datetime.fromisoformat(trade['entry_time'])
                        except:
                            entry_time_obj = datetime.now()
                    
                    duration_seconds = (exit_time - entry_time_obj).total_seconds()
                    
                    # Formatar duração
                    if duration_seconds < 60:
                        duration = f"{duration_seconds:.0f}s"
                    elif duration_seconds < 3600:
                        duration = f"{duration_seconds/60:.1f}m"
                    else:
                        duration = f"{duration_seconds/3600:.2f}h"
                    
                    # Atualizar trade
                    trade['exit_price'] = exit_price
                    trade['exit_time'] = exit_time.isoformat()
                    trade['exit_time_str'] = exit_time.strftime('%d/%m/%Y %H:%M:%S')
                    trade['pnl_percent'] = round(pnl_percent, 4)
                    trade['pnl_usdt'] = round(pnl_usdt, 2)
                    trade['status'] = 'closed'
                    trade['duration'] = duration
                    
                    self.save_trades()
                    
                    emoji = "✅" if pnl_percent > 0 else "❌" if pnl_percent < 0 else "➖"
                    logger.info(f"{emoji} Trade #{trade_id} fechada: PnL {pnl_percent:.4f}% (${pnl_usdt:.2f})")
                    return True
            
            return False
        except Exception as e:
            logger.error(f"❌ Erro ao fechar trade #{trade_id}: {e}")
            return False
    
    def get_all_trades(self, limit=100):
        return sorted(self.trades, key=lambda x: x['id'], reverse=True)[:limit]
    
    def get_stats(self):
        closed_trades = [t for t in self.trades if t['status'] == 'closed']
        
        if not closed_trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'total_pnl_percent': 0,
                'total_pnl_usdt': 0,
                'avg_pnl_percent': 0,
                'avg_pnl_usdt': 0
            }
        
        winning = [t for t in closed_trades if t.get('pnl_percent', 0) > 0]
        losing = [t for t in closed_trades if t.get('pnl_percent', 0) < 0]
        
        total_pnl_percent = sum(t.get('pnl_percent', 0) for t in closed_trades)
        total_pnl_usdt = sum(t.get('pnl_usdt', 0) for t in closed_trades)
        
        avg_pnl_percent = total_pnl_percent / len(closed_trades) if closed_trades else 0
        avg_pnl_usdt = total_pnl_usdt / len(closed_trades) if closed_trades else 0
        
        return {
            'total_trades': len(closed_trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': (len(winning) / len(closed_trades)) * 100 if closed_trades else 0,
            'total_pnl_percent': round(total_pnl_percent, 4),
            'total_pnl_usdt': round(total_pnl_usdt, 2),
            'avg_pnl_percent': round(avg_pnl_percent, 4),
            'avg_pnl_usdt': round(avg_pnl_usdt, 2)
        }
    
    def save_trades(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                import json
                json.dump(self.trades, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"❌ Erro ao salvar histórico: {e}")
    
    def load_trades(self):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    import json
                    self.trades = json.load(f)
        except Exception as e:
            logger.error(f"❌ Erro ao carregar histórico: {e}")
            self.trades = []
    
    def clear_history(self):
        self.trades = []
        if os.path.exists(self.file_path):
            os.remove(self.file_path)
        logger.info("🗑️ Histórico limpo")

# Inicializar histórico
trade_history = SimpleTradeHistory()

# ============================================================================
# VARIÁVEIS DE ESTADO
# ============================================================================
trading_active = False
current_price = 2500.0  # Preço mock
position_size = 0
position_side = None
current_trade_id = None
bars_processed = 0

# ============================================================================
# FUNÇÕES DE SIMULAÇÃO
# ============================================================================
def execute_simulation_trade(side):
    """Executa uma trade de simulação"""
    global current_trade_id, position_size, position_side
    
    try:
        # Fechar trade anterior se existir
        if current_trade_id and trading_active:
            trade_history.close_trade(current_trade_id, current_price)
        
        # Criar nova trade
        quantity = 0.1  # Quantidade fixa para simulação
        
        trade_id = trade_history.add_trade(
            side=side,
            entry_price=current_price,
            quantity=quantity
        )
        
        if trade_id:
            current_trade_id = trade_id
            position_size = quantity if side == 'buy' else -quantity
            position_side = side
            logger.info(f"✅ Trade de simulação #{trade_id}: {side.upper()} {quantity} ETH @ ${current_price:.2f}")
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"❌ Erro na simulação: {e}")
        return False

# ============================================================================
# INTERFACE WEB
# ============================================================================
@app.route('/')
def home():
    stats = trade_history.get_stats()
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Trading ETH/USDT - SIMULAÇÃO</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; text-align: center; padding: 20px; }
            .container { max-width: 1000px; margin: 0 auto; }
            .header { background: rgba(0, 255, 136, 0.1); padding: 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid #00ff88; }
            .status { padding: 15px; border-radius: 10px; margin: 15px 0; font-weight: bold; }
            .active { background: rgba(0, 255, 136, 0.2); border: 2px solid #00ff88; }
            .inactive { background: rgba(255, 68, 68, 0.2); border: 2px solid #ff4444; }
            .btn { padding: 12px 24px; margin: 5px; border: none; border-radius: 5px; font-size: 16px; cursor: pointer; font-weight: bold; }
            .btn-green { background: #00ff88; color: #000; }
            .btn-red { background: #ff4444; color: white; }
            .btn-blue { background: #3a86ff; color: white; }
            .btn-orange { background: #ff9e00; color: black; }
            .btn:disabled { opacity: 0.5; cursor: not-allowed; }
            .menu { margin: 20px 0; }
            .menu a { color: #00ff88; text-decoration: none; margin: 0 10px; font-size: 16px; padding: 8px 16px; border-radius: 5px; }
            .menu a:hover { background: rgba(0, 255, 136, 0.1); }
            .info { color: #aaa; font-size: 14px; margin-top: 20px; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
            .stat-card { background: rgba(255, 255, 255, 0.05); padding: 15px; border-radius: 8px; text-align: center; }
            .stat-value { font-size: 24px; font-weight: bold; margin: 5px 0; }
            .positive { color: #00ff88; }
            .negative { color: #ff4444; }
            .controls { margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚡ Bot Trading ETH/USDT - SIMULAÇÃO</h1>
                <p>Estratégia: Adaptive Zero Lag EMA v2 • Timeframe: 30m • Modo: SIMULAÇÃO</p>
            </div>
            
            <div class="status {{ 'active' if trading_active else 'inactive' }}">
                {{ '🟢 ATIVO - Simulando (sem ordens reais)' if trading_active else '🔴 INATIVO - Aguardando ativação' }}
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>📈 Preço Atual</h3>
                    <div class="stat-value" id="current-price">$""" + f"{current_price:.2f}" + """</div>
                </div>
                <div class="stat-card">
                    <h3>💰 Posição</h3>
                    <div class="stat-value" id="position">
    """
    
    if position_side == 'buy':
        html += f"""<span class="positive">LONG {position_size:.4f} ETH</span>"""
    elif position_side == 'sell':
        html += f"""<span class="negative">SHORT {-position_size:.4f} ETH</span>"""
    else:
        html += """Nenhuma"""
    
    html += """
                    </div>
                </div>
                <div class="stat-card">
                    <h3>📊 Trades</h3>
                    <div class="stat-value" id="total-trades">""" + str(len(trade_history.trades)) + """</div>
                </div>
                <div class="stat-card">
                    <h3>🎯 Win Rate</h3>
                    <div class="stat-value """ + ("positive" if stats['win_rate'] > 50 else "negative") + """" 
                         id="win-rate">""" + f"{stats['win_rate']:.1f}%" + """</div>
                </div>
            </div>
            
            <div class="controls">
                <button class="btn btn-green" onclick="controlBot('start')" """ + ("disabled" if trading_active else "") + """>
                    ⚡ Iniciar Bot
                </button>
                <button class="btn btn-red" onclick="controlBot('stop')" """ + ("disabled" if not trading_active else "") + """>
                    ⏹️ Parar Bot
                </button>
                <button class="btn btn-blue" onclick="executeTrade('buy')">
                    📈 Simular BUY
                </button>
                <button class="btn btn-orange" onclick="executeTrade('sell')">
                    📉 Simular SELL
                </button>
            </div>
            
            <div class="menu">
                <a href="/history">📜 Histórico Completo</a>
                <a href="/stats">📊 Estatísticas</a>
                <a href="/health">❤️ Saúde</a>
                <a href="/export">📥 Exportar CSV</a>
            </div>
            
            <div class="info">
                <strong>⚠️ MODO SIMULAÇÃO ATIVO:</strong> Nenhuma ordem real será enviada à OKX.
                <p>Clique em "Simular BUY/SELL" para testar o registro de trades no histórico.</p>
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
                location.reload();
            } else {
                alert('❌ ' + data.message);
            }
        }
        
        async function executeTrade(side) {
            if (!confirm(`Deseja executar uma trade de ${side.toUpperCase()} de simulação?`)) {
                return;
            }
            
            const response = await fetch('/simulate-trade', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ side: side })
            });
            
            const data = await response.json();
            
            if (response.ok) {
                alert('✅ ' + data.message);
                location.reload();
            } else {
                alert('❌ ' + data.message);
            }
        }
        
        // Atualizar preço a cada 10 segundos
        setInterval(async () => {
            try {
                const response = await fetch('/status');
                const data = await response.json();
                document.getElementById('current-price').innerHTML = '$' + data.current_price.toFixed(2);
            } catch (error) {
                console.error('Erro ao atualizar:', error);
            }
        }, 10000);
        </script>
    </body>
    </html>
    """
    return html

# ============================================================================
# ENDPOINTS DA API
# ============================================================================
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "service": "OKX ETH Trading Bot",
        "trading_active": trading_active,
        "environment": "render" if IS_RENDER else "local",
        "timestamp": datetime.now().isoformat(),
        "trades_count": len(trade_history.trades)
    })

@app.route('/start', methods=['POST'])
def start_trading():
    global trading_active
    
    if trading_active:
        return jsonify({"status": "error", "message": "Bot já está ativo!"}), 400
    
    trading_active = True
    logger.info("⚡ Bot de SIMULAÇÃO iniciado")
    
    return jsonify({
        "status": "success", 
        "message": "Bot de simulação iniciado!",
        "details": "Estratégia: Adaptive Zero Lag EMA v2 | Timeframe: 30 minutos | Modo: SIMULAÇÃO"
    })

@app.route('/stop', methods=['POST'])
def stop_trading():
    global trading_active
    
    if not trading_active:
        return jsonify({"status": "error", "message": "Bot já está parado!"}), 400
    
    trading_active = False
    
    # Fechar trade aberta se existir
    global current_trade_id
    if current_trade_id:
        trade_history.close_trade(current_trade_id, current_price)
        current_trade_id = None
    
    logger.info("⏹️ Bot de simulação parado")
    return jsonify({"status": "success", "message": "Bot parado."})

@app.route('/status')
def status():
    stats = trade_history.get_stats()
    
    return jsonify({
        "trading_active": trading_active,
        "current_price": current_price,
        "position_size": position_size,
        "position_side": position_side,
        "current_trade_id": current_trade_id,
        "bars_processed": bars_processed,
        "environment": "render" if IS_RENDER else "local",
        "simulation_mode": True,
        "stats": stats,
        "total_trades": len(trade_history.trades)
    })

@app.route('/history')
def history_page():
    try:
        trades = trade_history.get_all_trades(limit=100)
        stats = trade_history.get_stats()
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>📜 Histórico Completo de Operações</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: Arial, sans-serif; background: #1a1a2e; color: white; padding: 20px; }
                .container { max-width: 1400px; margin: 0 auto; }
                .header { text-align: center; margin-bottom: 30px; }
                h1 { color: #00ff88; margin-bottom: 10px; }
                h2 { color: #00ccff; margin: 20px 0 10px 0; }
                .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
                .stat-card { background: rgba(255,255,255,0.05); padding: 15px; border-radius: 8px; text-align: center; }
                .stat-value { font-size: 24px; font-weight: bold; margin: 5px 0; }
                .positive { color: #00ff88; }
                .negative { color: #ff4444; }
                .neutral { color: #888; }
                .controls { text-align: center; margin: 20px 0; }
                .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; }
                .btn-green { background: #00ff88; color: black; }
                .btn-red { background: #ff4444; color: white; }
                .btn-blue { background: #3a86ff; color: white; }
                .btn-back { background: #555; color: white; }
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
                .trade-open { background: rgba(0, 255, 136, 0.05); }
                .trade-closed { }
                .pagination { margin: 20px 0; text-align: center; }
                .pagination button { padding: 8px 16px; margin: 0 5px; background: #333; color: white; border: none; border-radius: 4px; cursor: pointer; }
                .pagination button.active { background: #00ff88; color: black; }
                .pagination button:hover:not(.active) { background: #555; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📜 Histórico Completo de Operações</h1>
                    <p>ETH/USDT - Modo SIMULAÇÃO • Total: """ + str(len(trade_history.trades)) + """ trades</p>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div>Trades Totais</div>
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
                        <div>Lucro Total $</div>
                        <div class="stat-value """ + ("positive" if stats['total_pnl_usdt'] > 0 else "negative" if stats['total_pnl_usdt'] < 0 else "neutral") + """">
                            $""" + f"{stats['total_pnl_usdt']:.2f}" + """
                        </div>
                    </div>
                    <div class="stat-card">
                        <div>Lucro Médio %</div>
                        <div class="stat-value """ + ("positive" if stats['avg_pnl_percent'] > 0 else "negative" if stats['avg_pnl_percent'] < 0 else "neutral") + """">
                            """ + f"{stats['avg_pnl_percent']:.4f}%" + """
                        </div>
                    </div>
                    <div class="stat-card">
                        <div>Lucro Médio $</div>
                        <div class="stat-value """ + ("positive" if stats['avg_pnl_usdt'] > 0 else "negative" if stats['avg_pnl_usdt'] < 0 else "neutral") + """">
                            $""" + f"{stats['avg_pnl_usdt']:.2f}" + """
                        </div>
                    </div>
                </div>
                
                <div class="controls">
                    <button class="btn btn-green" onclick="location.reload()">🔄 Atualizar</button>
                    <button class="btn btn-blue" onclick="exportHistory()">📥 Exportar CSV</button>
                    <button class="btn btn-red" onclick="clearHistory()">🗑️ Limpar Histórico</button>
                    <button class="btn btn-back" onclick="location.href='/'">🏠 Voltar</button>
                </div>
        """
        
        if not trades:
            html += """
                <div class="empty">
                    <div style="font-size: 50px; margin-bottom: 20px;">📭</div>
                    <h3>Nenhuma operação registrada</h3>
                    <p>Quando você executar trades de simulação, elas aparecerão aqui.</p>
                    <p>Use os botões "Simular BUY" e "Simular SELL" na página principal para testar.</p>
                </div>
            """
        else:
            html += """
                <div class="filter">
                    <label for="status-filter">Filtrar por: </label>
                    <select id="status-filter" onchange="filterTable()">
                        <option value="all">Todas as trades</option>
                        <option value="open">Abertas</option>
                        <option value="closed">Fechadas</option>
                        <option value="profit">Lucrativas</option>
                        <option value="loss">Prejudiciais</option>
                    </select>
                    
                    <label for="side-filter" style="margin-left: 20px;">Lado: </label>
                    <select id="side-filter" onchange="filterTable()">
                        <option value="all">Todos</option>
                        <option value="buy">BUY (Long)</option>
                        <option value="sell">SELL (Short)</option>
                    </select>
                </div>
                
                <table id="trades-table">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Data/Hora Entrada</th>
                            <th>Data/Hora Saída</th>
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
                
                pnl_percent = trade.get('pnl_percent', 0)
                if pnl_percent > 0:
                    pnl_class = "pnl-positive"
                elif pnl_percent < 0:
                    pnl_class = "pnl-negative"
                else:
                    pnl_class = "pnl-neutral"
                
                status_icon = '🟢' if trade['status'] == 'open' else '✅' if pnl_percent > 0 else '❌' if pnl_percent < 0 else '➖'
                row_class = "trade-open" if trade['status'] == 'open' else "trade-closed"
                
                # Usar .get() para evitar erros com chaves ausentes
                exit_time_str = trade.get('exit_time_str', '-')
                exit_price = trade.get('exit_price')
                pnl_usdt = trade.get('pnl_usdt')
                duration = trade.get('duration', '-')
                
                html += f"""
                        <tr class="trade-row {row_class}" data-status="{trade['status']}" data-pnl="{pnl_percent}" data-side="{trade['side']}">
                            <td><strong>#{trade['id']}</strong></td>
                            <td>{trade['entry_time_str']}</td>
                            <td>{exit_time_str}</td>
                            <td class="{side_class}">{trade['side'].upper()}</td>
                            <td>${trade['entry_price']:.2f}</td>
                            <td>{'$' + f"{exit_price:.2f}" if exit_price else '-'}</td>
                            <td>{trade['quantity']:.4f} ETH</td>
                            <td class="{pnl_class}">{pnl_percent:.4f}%</td>
                            <td class="{pnl_class}">{'$' + f"{pnl_usdt:.2f}" if pnl_usdt else '-'}</td>
                            <td>{duration}</td>
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
                    <p>Horário de Brasília (BRT) • Última atualização: """ + datetime.now().strftime('%d/%m/%Y %H:%M:%S') + """</p>
                </div>
            </div>
            
            <script>
            function filterTable() {
                const statusFilter = document.getElementById('status-filter').value;
                const sideFilter = document.getElementById('side-filter').value;
                const rows = document.querySelectorAll('.trade-row');
                
                rows.forEach(row => {
                    const status = row.getAttribute('data-status');
                    const pnl = parseFloat(row.getAttribute('data-pnl'));
                    const side = row.getAttribute('data-side');
                    
                    let showStatus = true;
                    let showSide = true;
                    
                    // Filtro por status
                    switch(statusFilter) {
                        case 'open':
                            showStatus = status === 'open';
                            break;
                        case 'closed':
                            showStatus = status === 'closed';
                            break;
                        case 'profit':
                            showStatus = status === 'closed' && pnl > 0;
                            break;
                        case 'loss':
                            showStatus = status === 'closed' && pnl < 0;
                            break;
                        case 'all':
                        default:
                            showStatus = true;
                    }
                    
                    // Filtro por lado
                    if (sideFilter !== 'all') {
                        showSide = side === sideFilter;
                    }
                    
                    row.style.display = (showStatus && showSide) ? '' : 'none';
                });
            }
            
            function clearHistory() {
                if (confirm('⚠️ ATENÇÃO: Tem certeza que deseja limpar TODO o histórico?\n\nEsta ação NÃO pode ser desfeita.')) {
                    fetch('/clear-history', { method: 'POST' })
                        .then(r => r.json())
                        .then(data => {
                            if (data.success) {
                                alert('✅ Histórico limpo com sucesso!');
                                location.reload();
                            } else {
                                alert('❌ Erro: ' + data.message);
                            }
                        });
                }
            }
            
            function exportHistory() {
                window.open('/export', '_blank');
            }
            
            // Inicializar filtros
            document.addEventListener('DOMContentLoaded', function() {
                filterTable();
            });
            </script>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Erro na página de histórico: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return f"<h1>Erro: {str(e)}</h1>"

@app.route('/stats')
def stats_page():
    """Página de estatísticas detalhadas"""
    stats = trade_history.get_stats()
    trades = trade_history.get_all_trades(limit=100)
    
    # Calcular estatísticas adicionais
    open_trades = [t for t in trades if t['status'] == 'open']
    closed_trades = [t for t in trades if t['status'] == 'closed']
    
    # Melhor e pior trade
    best_trade = None
    worst_trade = None
    if closed_trades:
        best_trade = max(closed_trades, key=lambda x: x.get('pnl_percent', 0))
        worst_trade = min(closed_trades, key=lambda x: x.get('pnl_percent', 0))
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📊 Estatísticas Detalhadas</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; background: #1a1a2e; color: white; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            h1 {{ color: #00ff88; margin-bottom: 10px; }}
            h2 {{ color: #00ccff; margin: 25px 0 15px 0; border-bottom: 1px solid #444; padding-bottom: 10px; }}
            .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin: 20px 0; }}
            .stat-card {{ background: rgba(255,255,255,0.05); padding: 20px; border-radius: 10px; }}
            .stat-title {{ color: #aaa; font-size: 14px; margin-bottom: 5px; }}
            .stat-value {{ font-size: 28px; font-weight: bold; margin: 5px 0; }}
            .positive {{ color: #00ff88; }}
            .negative {{ color: #ff4444; }}
            .neutral {{ color: #888; }}
            .controls {{ text-align: center; margin: 30px 0; }}
            .btn {{ padding: 10px 20px; margin: 5px; border: none; border-radius: 5px; cursor: pointer; }}
            .btn-green {{ background: #00ff88; color: black; }}
            .btn-back {{ background: #555; color: white; }}
            .highlight {{ background: rgba(0, 255, 136, 0.1); border-left: 4px solid #00ff88; padding: 15px; margin: 15px 0; }}
            .highlight-negative {{ background: rgba(255, 68, 68, 0.1); border-left: 4px solid #ff4444; }}
            .footer {{ text-align: center; margin-top: 30px; color: #888; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 Estatísticas Detalhadas</h1>
                <p>Análise de Performance - Modo SIMULAÇÃO</p>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-title">Total de Operações</div>
                    <div class="stat-value">{len(trades)}</div>
                    <div>({len(open_trades)} abertas, {len(closed_trades)} fechadas)</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-title">Taxa de Acerto</div>
                    <div class="stat-value {'positive' if stats['win_rate'] > 50 else 'negative' if stats['win_rate'] < 50 else 'neutral'}">
                        {stats['win_rate']:.1f}%
                    </div>
                    <div>({stats['winning_trades']} lucrativas / {stats['losing_trades']} prejudiciais)</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-title">Lucro Total</div>
                    <div class="stat-value {'positive' if stats['total_pnl_usdt'] > 0 else 'negative' if stats['total_pnl_usdt'] < 0 else 'neutral'}">
                        ${stats['total_pnl_usdt']:.2f}
                    </div>
                    <div>{stats['total_pnl_percent']:.4f}%</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-title">Lucro Médio por Trade</div>
                    <div class="stat-value {'positive' if stats['avg_pnl_usdt'] > 0 else 'negative' if stats['avg_pnl_usdt'] < 0 else 'neutral'}">
                        ${stats['avg_pnl_usdt']:.2f}
                    </div>
                    <div>{stats['avg_pnl_percent']:.4f}%</div>
                </div>
            </div>
            
            <h2>📈 Melhor e Pior Performances</h2>
            <div class="stats-grid">
    """
    
    if best_trade:
        html += f"""
                <div class="stat-card highlight">
                    <div class="stat-title">🏆 MELHOR TRADE</div>
                    <div class="stat-value positive">#{best_trade['id']} - {best_trade['side'].upper()}</div>
                    <div>Lucro: {best_trade.get('pnl_percent', 0):.4f}% (${best_trade.get('pnl_usdt', 0):.2f})</div>
                    <div>Entrada: ${best_trade['entry_price']:.2f}</div>
                    <div>Saída: ${best_trade.get('exit_price', 0):.2f}</div>
                    <div>Duração: {best_trade.get('duration', '-')}</div>
                </div>
        """
    
    if worst_trade:
        html += f"""
                <div class="stat-card highlight-negative">
                    <div class="stat-title">📉 PIOR TRADE</div>
                    <div class="stat-value negative">#{worst_trade['id']} - {worst_trade['side'].upper()}</div>
                    <div>Prejuízo: {worst_trade.get('pnl_percent', 0):.4f}% (${worst_trade.get('pnl_usdt', 0):.2f})</div>
                    <div>Entrada: ${worst_trade['entry_price']:.2f}</div>
                    <div>Saída: ${worst_trade.get('exit_price', 0):.2f}</div>
                    <div>Duração: {worst_trade.get('duration', '-')}</div>
                </div>
        """
    
    html += """
            </div>
            
            <h2>📋 Distribuição de Operações</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-title">Operações LONG (BUY)</div>
                    <div class="stat-value">
    """
    
    buy_trades = [t for t in trades if t['side'] == 'buy']
    buy_closed = [t for t in buy_trades if t['status'] == 'closed']
    buy_winning = [t for t in buy_closed if t.get('pnl_percent', 0) > 0]
    
    html += f"""
                        {len(buy_trades)} trades
                    </div>
                    <div>{len(buy_closed)} fechadas</div>
                    <div>Win Rate: {(len(buy_winning)/len(buy_closed)*100 if buy_closed else 0):.1f}%</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-title">Operações SHORT (SELL)</div>
                    <div class="stat-value">
    """
    
    sell_trades = [t for t in trades if t['side'] == 'sell']
    sell_closed = [t for t in sell_trades if t['status'] == 'closed']
    sell_winning = [t for t in sell_closed if t.get('pnl_percent', 0) > 0]
    
    html += f"""
                        {len(sell_trades)} trades
                    </div>
                    <div>{len(sell_closed)} fechadas</div>
                    <div>Win Rate: {(len(sell_winning)/len(sell_closed)*100 if sell_closed else 0):.1f}%</div>
                </div>
                
                <div class="stat-card">
                    <div class="stat-title">Operações Abertas</div>
                    <div class="stat-value">{len(open_trades)}</div>
                    <div>Valor total em risco</div>
    """
    
    if open_trades:
        total_risk = sum(t['quantity'] * current_price for t in open_trades)
        html += f"<div>${total_risk:.2f}</div>"
    
    html += """
                </div>
            </div>
            
            <div class="controls">
                <button class="btn btn-green" onclick="location.href='/history'">📜 Ver Histórico Completo</button>
                <button class="btn btn-back" onclick="location.href='/'">🏠 Voltar</button>
            </div>
            
            <div class="footer">
                <p><strong>⚠️ MODO SIMULAÇÃO:</strong> Estas estatísticas são baseadas em dados de simulação.</p>
                <p>Última atualização: """ + datetime.now().strftime('%d/%m/%Y %H:%M:%S') + """</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html

@app.route('/simulate-trade', methods=['POST'])
def simulate_trade():
    """Endpoint para simular uma trade manualmente"""
    global trading_active
    
    try:
        data = request.get_json()
        side = data.get('side', 'buy').lower()
        
        if side not in ['buy', 'sell']:
            return jsonify({"status": "error", "message": "Lado inválido. Use 'buy' ou 'sell'."}), 400
        
        if execute_simulation_trade(side):
            return jsonify({
                "status": "success", 
                "message": f"Trade de {side.upper()} simulada com sucesso!",
                "trade_id": current_trade_id,
                "price": current_price,
                "side": side
            })
        else:
            return jsonify({"status": "error", "message": "Falha ao executar trade de simulação."}), 500
            
    except Exception as e:
        logger.error(f"Erro na simulação de trade: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/export')
def export_history():
    """Exporta o histórico como CSV"""
    try:
        trades = trade_history.get_all_trades(limit=1000)
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            'ID', 'Data/Hora Entrada', 'Data/Hora Saída', 'Operação',
            'Preço Entrada (USDT)', 'Preço Saída (USDT)', 'Quantidade (ETH)',
            'Variação (%)', 'Lucro/Prejuízo (USDT)', 'Duração', 'Status'
        ])
        
        for trade in trades:
            writer.writerow([
                trade['id'],
                trade['entry_time_str'],
                trade.get('exit_time_str', ''),
                trade['side'].upper(),
                f"{trade['entry_price']:.2f}",
                f"{trade.get('exit_price', 0):.2f}" if trade.get('exit_price') else '',
                f"{trade['quantity']:.4f}",
                f"{trade.get('pnl_percent', 0):.4f}",
                f"{trade.get('pnl_usdt', 0):.2f}",
                trade.get('duration', ''),
                trade['status']
            ])
        
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
    """Limpa todo o histórico"""
    try:
        trade_history.clear_history()
        global current_trade_id, position_size, position_side
        current_trade_id = None
        position_size = 0
        position_side = None
        
        return jsonify({
            "success": True, 
            "message": "Histórico limpo com sucesso!",
            "trades_count": 0
        })
    except Exception as e:
        logger.error(f"Erro ao limpar histórico: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# ============================================================================
# INICIAR SERVIDOR
# ============================================================================
if __name__ == '__main__':
    logger.info(f"🚀 Iniciando servidor na porta {PORT}...")
    logger.info(f"📊 Sistema de Histórico: {len(trade_history.trades)} trades carregadas")
    logger.info(f"🎯 Modo: SIMULAÇÃO COMPLETA")
    logger.info(f"🌍 Ambiente: {'RENDER' if IS_RENDER else 'Local'}")
    
    # Adicionar algumas trades de exemplo se o histórico estiver vazio
    if len(trade_history.trades) == 0:
        logger.info("➕ Adicionando trades de exemplo...")
        
        # Trades de exemplo
        example_trades = [
            ('buy', 2400.0, 0.1, 2450.0),
            ('sell', 2500.0, 0.1, 2480.0),
            ('buy', 2450.0, 0.15, 2475.0),
            ('sell', 2550.0, 0.12, 2530.0),
            ('buy', 2480.0, 0.1, None),  # Trade aberta
        ]
        
        for side, entry, qty, exit_price in example_trades:
            trade_id = trade_history.add_trade(side, entry, qty)
            if exit_price and trade_id:
                trade_history.close_trade(trade_id, exit_price)
        
        logger.info(f"✅ {len(example_trades)} trades de exemplo adicionadas")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
