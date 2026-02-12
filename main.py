# main.py
import os
import argparse
from pathlib import Path
import pandas as pd
from flask import Flask, jsonify, send_file, render_template_string

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import OKXDataCollector
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter
from keepalive.pinger import KeepAlivePinger
from keepalive.webhook_receiver import webhook_bp
from utils.env_loader import env, env_int, env_float

# ============================================================================
# CONFIGURAÇÕES DA ESTRATÉGIA
# ============================================================================
STRATEGY_CONFIG = {
    "adaptive_method": env("ADAPTIVE_METHOD", "Cos IFM"),
    "threshold": env_float("THRESHOLD", 0.0),
    "fixed_sl_points": env_int("FIXED_SL", 2000),
    "fixed_tp_points": env_int("FIXED_TP", 55),
    "trail_offset": env_int("TRAIL_OFFSET", 15),
    "risk_percent": env_float("RISK_PERCENT", 0.01),
    "tick_size": env_float("TICK_SIZE", 0.01),
    "initial_capital": env_float("INITIAL_CAPITAL", 1000.0),
    "max_lots": env_int("MAX_LOTS", 100)
}

SYMBOL = env("SYMBOL", "ETH/USDT")
TIMEFRAME = env("TIMEFRAME", "30m")
BACKTEST_DAYS = env_int("BACKTEST_DAYS", 30)
CANDLE_LIMIT = env_int("CANDLE_LIMIT", 1000)

# ============================================================================
# CRIAÇÃO DA APLICAÇÃO FLASK
# ============================================================================
app = Flask(__name__)
app.register_blueprint(webhook_bp)

@app.route('/')
def home():
    return jsonify({
        "service": "AZLEMA Backtest Engine",
        "status": "running",
        "endpoints": ["/", "/ping", "/health", "/uptimerobot", "/backtest"],
        "docs": "https://github.com/Arthur1312q1/dinheiro"
    }), 200

# ============================================================================
# ENDPOINT PRINCIPAL: BACKTEST VIA NAVEGADOR
# ============================================================================
@app.route('/backtest')
def backtest_web():
    """Executa o backtest e retorna o relatório HTML completo."""
    try:
        # 1. Coleta dados
        collector = OKXDataCollector(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            limit=CANDLE_LIMIT
        )
        df = collector.fetch_recent(days=BACKTEST_DAYS)
        
        # 2. Inicializa estratégia
        strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        
        # 3. Executa backtest
        engine = BacktestEngine(strategy, df)
        results = engine.run()
        
        # 4. Gera HTML do relatório
        reporter = BacktestReporter(results, df)
        html_content = reporter.generate_html()
        
        # 5. Retorna como página web
        return render_template_string(html_content)
    
    except Exception as e:
        return jsonify({"error": str(e), "status": "failed"}), 500

# ============================================================================
# FUNÇÕES DE BACKTEST LOCAL (MANTIDAS PARA COMPATIBILIDADE)
# ============================================================================
def run_backtest():
    print("🔍 Iniciando coleta de dados da OKX...")
    collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
    df = collector.fetch_recent(days=BACKTEST_DAYS)
    print(f"✅ {len(df)} candles baixados.")
    
    print("⚙️  Inicializando estratégia...")
    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
    
    print("📊 Executando backtest...")
    engine = BacktestEngine(strategy, df)
    results = engine.run()
    
    print("📈 Gerando relatório visual...")
    reporter = BacktestReporter(results, df)
    report_path = reporter.save_html('azlema_backtest_report.html')
    print(f"✅ Relatório salvo: {report_path}")

# ============================================================================
# PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AZLEMA Backtesting System')
    parser.add_argument('--mode', choices=['backtest', 'server'], default='backtest')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest()
    else:
        port = env_int("PORT", 5000)
        app.run(host='0.0.0.0', port=port)
