# Adicione estas importações no início do arquivo:
from trade_history import TradeHistory

# Após inicializar os componentes (por volta da linha 70), adicione:
# ============================================================================
# 5. INICIALIZAR HISTÓRICO DE TRADES
# ============================================================================
trade_history = TradeHistory() if 'TradeHistory' in globals() else None

# Adicione este novo endpoint após os endpoints existentes (aprox linha 200):
@app.route('/trade-history', methods=['GET'])
def get_trade_history():
    """Retorna o histórico completo de trades"""
    try:
        if not strategy_runner:
            return jsonify({"error": "Strategy Runner não inicializado"}), 500
        
        limit = request.args.get('limit', default=50, type=int)
        history_data = strategy_runner.get_trade_history(limit=limit)
        
        return jsonify({
            "success": True,
            "history": history_data,
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Erro ao obter histórico: {e}")
        return jsonify({"error": str(e)}), 500

# Adicione este endpoint para página web do histórico:
@app.route('/history', methods=['GET'])
def history_page():
    """Página web com histórico de trades"""
    try:
        if not strategy_runner:
            return "Strategy Runner não inicializado", 500
        
        # Obter histórico
        history_data = strategy_runner.get_trade_history(limit=100)
        trades = history_data.get('trades', [])
        stats = history_data.get('stats', {})
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>📊 Histórico de Operações - Bot Trading</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }
                
                body {
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                    color: #fff;
                    padding: 20px;
                    min-height: 100vh;
                }
                
                .container {
                    max-width: 1400px;
                    margin: 0 auto;
                }
                
                .header {
                    text-align: center;
                    margin-bottom: 30px;
                    padding: 20px;
                    background: rgba(255, 255, 255, 0.05);
                    border-radius: 15px;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                }
                
                h1 {
                    color: #00ff88;
                    font-size: 32px;
                    margin-bottom: 10px;
                }
                
                .subtitle {
                    color: #a0a0c0;
                    font-size: 16px;
                }
                
                .stats-grid {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                    gap: 20px;
                    margin-bottom: 30px;
                }
                
                .stat-card {
                    background: rgba(255, 255, 255, 0.05);
                    padding: 20px;
                    border-radius: 12px;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    text-align: center;
                }
                
                .stat-value {
                    font-size: 28px;
                    font-weight: bold;
                    margin: 10px 0;
                }
                
                .positive { color: #00ff88; }
                .negative { color: #ff4444; }
                .neutral { color: #a0a0c0; }
                
                .stat-label {
                    font-size: 14px;
                    color: #888;
                    text-transform: uppercase;
                }
                
                .controls {
                    display: flex;
                    gap: 15px;
                    justify-content: center;
                    margin-bottom: 30px;
                }
                
                .btn {
                    padding: 12px 25px;
                    background: rgba(0, 255, 136, 0.2);
                    border: 1px solid #00ff88;
                    color: #00ff88;
                    border-radius: 8px;
                    cursor: pointer;
                    font-weight: bold;
                    transition: all 0.3s;
                }
                
                .btn:hover {
                    background: rgba(0, 255, 136, 0.3);
                    transform: translateY(-2px);
                }
                
                .btn-danger {
                    background: rgba(255, 68, 68, 0.2);
                    border: 1px solid #ff4444;
                    color: #ff4444;
                }
                
                .btn-danger:hover {
                    background: rgba(255, 68, 68, 0.3);
                }
                
                .trades-table {
                    width: 100%;
                    background: rgba(0, 0, 0, 0.3);
                    border-radius: 12px;
                    overflow: hidden;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                }
                
                table {
                    width: 100%;
                    border-collapse: collapse;
                }
                
                th {
                    background: rgba(0, 0, 0, 0.5);
                    padding: 15px;
                    text-align: left;
                    color: #00ff88;
                    font-weight: bold;
                    border-bottom: 2px solid rgba(0, 255, 136, 0.3);
                }
                
                td {
                    padding: 15px;
                    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                }
                
                tr:hover {
                    background: rgba(255, 255, 255, 0.03);
                }
                
                .trade-side {
                    padding: 5px 10px;
                    border-radius: 5px;
                    font-weight: bold;
                    font-size: 12px;
                }
                
                .side-buy {
                    background: rgba(0, 255, 136, 0.2);
                    color: #00ff88;
                }
                
                .side-sell {
                    background: rgba(255, 68, 68, 0.2);
                    color: #ff4444;
                }
                
                .status-open {
                    color: #ffa500;
                }
                
                .status-closed {
                    color: #00ff88;
                }
                
                .empty-state {
                    text-align: center;
                    padding: 60px 20px;
                    color: #888;
                    font-size: 18px;
                }
                
                .footer {
                    text-align: center;
                    margin-top: 40px;
                    padding-top: 20px;
                    border-top: 1px solid rgba(255, 255, 255, 0.1);
                    color: #888;
                }
                
                .footer a {
                    color: #00ff88;
                    text-decoration: none;
                    margin: 0 15px;
                }
                
                .footer a:hover {
                    text-decoration: underline;
                }
                
                .price {
                    font-family: monospace;
                    font-size: 16px;
                }
                
                @media (max-width: 768px) {
                    .stats-grid {
                        grid-template-columns: 1fr;
                    }
                    
                    table {
                        display: block;
                        overflow-x: auto;
                    }
                    
                    th, td {
                        padding: 10px;
                        font-size: 14px;
                    }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📊 Histórico de Operações</h1>
                    <p class="subtitle">ETH/USDT - Estratégia: Adaptive Zero Lag EMA v2 • Modo: SIMULAÇÃO</p>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-label">Total de Trades</div>
                        <div class="stat-value">""" + str(stats.get('total_trades', 0)) + """</div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-label">Trades Lucrativas</div>
                        <div class="stat-value positive">""" + str(stats.get('winning_trades', 0)) + """</div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-label">Trades Prejudiciais</div>
                        <div class="stat-value negative">""" + str(stats.get('losing_trades', 0)) + """</div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-label">Taxa de Acerto</div>
                        <div class="stat-value """ + ("positive" if stats.get('win_rate', 0) > 50 else "negative") + """">
                            """ + f"{stats.get('win_rate', 0):.1f}%" + """
                        </div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-label">Lucro Total %</div>
                        <div class="stat-value """ + ("positive" if stats.get('total_pnl_percent', 0) > 0 else "negative") + """">
                            """ + f"{stats.get('total_pnl_percent', 0):.4f}%" + """
                        </div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-label">Lucro Total USDT</div>
                        <div class="stat-value """ + ("positive" if stats.get('total_pnl_usdt', 0) > 0 else "negative") + """">
                            $""" + f"{stats.get('total_pnl_usdt', 0):.2f}" + """
                        </div>
                    </div>
                </div>
                
                <div class="controls">
                    <button class="btn" onclick="refreshHistory()">🔄 Atualizar Histórico</button>
                    <button class="btn" onclick="exportHistory()">📥 Exportar CSV</button>
                    <button class="btn btn-danger" onclick="clearHistory()">🗑️ Limpar Histórico</button>
                    <button class="btn" onclick="window.location.href='/'">🏠 Voltar ao Bot</button>
                </div>
        """
        
        if not trades:
            html += """
                <div class="empty-state">
                    <div style="font-size: 60px; margin-bottom: 20px;">📭</div>
                    <h3>Nenhuma operação registrada ainda</h3>
                    <p>Quando o bot executar trades em modo simulação, elas aparecerão aqui.</p>
                </div>
            """
        else:
            html += """
                <div class="trades-table">
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
                # Determinar classe CSS baseada no resultado
                pnl_class = "positive" if trade.get('pnl_percent', 0) > 0 else "negative" if trade.get('pnl_percent', 0) < 0 else "neutral"
                side_class = "side-buy" if trade['side'] == 'buy' else "side-sell"
                status_class = "status-open" if trade['status'] == 'open' else "status-closed"
                
                # Formatar valores
                entry_price = f"${trade['entry_price']:.2f}"
                exit_price = f"${trade['exit_price']:.2f}" if trade.get('exit_price') else "-"
                quantity = f"{trade['quantity']:.4f} ETH"
                pnl_percent = f"{trade.get('pnl_percent', 0):.4f}%" if trade.get('pnl_percent') is not None else "-"
                pnl_usdt = f"${trade.get('pnl_usdt', 0):.2f}" if trade.get('pnl_usdt') is not None else "-"
                
                # Horário
                time_str = trade.get('entry_time_str', '-')
                duration = trade.get('duration', '-')
                
                html += f"""
                            <tr>
                                <td><strong>#{trade['id']}</strong></td>
                                <td>{time_str}</td>
                                <td><span class="trade-side {side_class}">{trade['side'].upper()}</span></td>
                                <td class="price">{entry_price}</td>
                                <td class="price">{exit_price}</td>
                                <td class="price">{quantity}</td>
                                <td class="{pnl_class}"><strong>{pnl_percent}</strong></td>
                                <td class="{pnl_class}"><strong>{pnl_usdt}</strong></td>
                                <td>{duration}</td>
                                <td class="{status_class}"><strong>{trade['status'].upper()}</strong></td>
                            </tr>
                """
            
            html += """
                        </tbody>
                    </table>
                </div>
            """
        
        html += """
                <div class="footer">
                    <p>
                        <a href="/">🏠 Página Principal</a> | 
                        <a href="/status">📊 Status do Bot</a> | 
                        <a href="/strategy-status">📈 Status da Estratégia</a>
                    </p>
                    <p style="margin-top: 10px; font-size: 14px;">
                        <strong>⚠️ MODO SIMULAÇÃO:</strong> Estas operações não foram executadas na exchange real.
                    </p>
                </div>
            </div>
            
            <script>
                function refreshHistory() {
                    window.location.reload();
                }
                
                function exportHistory() {
                    alert('Funcionalidade de exportação em desenvolvimento!');
                }
                
                function clearHistory() {
                    if (confirm('Tem certeza que deseja limpar todo o histórico? Esta ação não pode ser desfeita.')) {
                        fetch('/clear-history', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json'
                            }
                        })
                        .then(response => response.json())
                        .then(data => {
                            if (data.success) {
                                alert('Histórico limpo com sucesso!');
                                refreshHistory();
                            } else {
                                alert('Erro ao limpar histórico: ' + data.message);
                            }
                        })
                        .catch(error => {
                            alert('Erro de conexão: ' + error);
                        });
                    }
                }
                
                // Auto-refresh a cada 30 segundos se houver trades abertas
                setTimeout(() => {
                    const hasOpenTrades = document.querySelectorAll('.status-open').length > 0;
                    if (hasOpenTrades) {
                        refreshHistory();
                    }
                }, 30000);
            </script>
        </body>
        </html>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Erro na página de histórico: {e}")
        return f"<h1>Erro: {str(e)}</h1>"

# Adicione este endpoint para limpar histórico:
@app.route('/clear-history', methods=['POST'])
def clear_history():
    """Limpa todo o histórico de trades"""
    try:
        if not strategy_runner or not strategy_runner.trade_history:
            return jsonify({"success": False, "message": "Strategy Runner não inicializado"}), 500
        
        strategy_runner.trade_history.clear_history()
        logger.info("🗑️ Histórico de trades limpo por solicitação do usuário")
        
        return jsonify({
            "success": True,
            "message": "Histórico limpo com sucesso",
            "timestamp": datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Erro ao limpar histórico: {e}")
        return jsonify({"success": False, "message": str(e)}), 500

# Adicione no menu da página principal (no HTML) este link:
# <a href="/history" target="_blank">📜 Histórico de Trades</a>
