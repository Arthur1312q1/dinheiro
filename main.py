# main.py
import os
import argparse
import traceback
from pathlib import Path
import pandas as pd
from flask import Flask, jsonify, render_template_string

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import OKXDataCollector
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter
from keepalive.pinger import KeepAlivePinger
from keepalive.webhook_receiver import webhook_bp
from utils.env_loader import env, env_int, env_float

# ============================================================================
# FUNÇÃO AUXILIAR: NORMALIZA SÍMBOLO PARA O FORMATO DA OKX (ETH-USDT)
# ============================================================================
def normalize_symbol(symbol: str) -> str:
    """
    Converte qualquer formato comum para o padrão da OKX: "ETH-USDT".
    Exemplos:
        "ETH/USDT"  -> "ETH-USDT"
        "ETHUSDT"   -> "ETH-USDT"
        "BTC-USD"   -> "BTC-USDT" (assume USDT como quote)
        "BTC-USDT"  -> "BTC-USDT"
    """
    # Remove espaços
    symbol = symbol.strip().upper()
    
    # Substitui separadores comuns por hífen
    symbol = symbol.replace('/', '-').replace('_', '-').replace(' ', '-')
    
    # Se não tiver hífen, insere antes do USDT (ex: ETHUSDT -> ETH-USDT)
    if '-' not in symbol and symbol.endswith('USDT'):
        base = symbol[:-4]  # remove 'USDT'
        symbol = f"{base}-USDT"
    
    # Garantir que a quote seja USDT (padrão para nosso backtest)
    if not symbol.endswith('-USDT'):
        if '-' in symbol:
            base, quote = symbol.split('-')
            if quote != 'USDT':
                symbol = f"{base}-USDT"
        else:
            symbol = f"{symbol}-USDT"
    
    return symbol

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

# ============================================================================
# CONFIGURAÇÕES DE COLETA DE DADOS
# ============================================================================
RAW_SYMBOL = env("SYMBOL", "ETH-USDT")  # ← AGORA O PADRÃO É "ETH-USDT" (correto!)
SYMBOL = normalize_symbol(RAW_SYMBOL)    # Garante formato OKX
TIMEFRAME = env("TIMEFRAME", "30m")
BACKTEST_CANDLES = env_int("BACKTEST_CANDLES", 150)  # ← 150 candles, não dias
CANDLE_LIMIT = BACKTEST_CANDLES

# ============================================================================
# CRIAÇÃO DA APLICAÇÃO FLASK
# ============================================================================
app = Flask(__name__)
app.register_blueprint(webhook_bp)

@app.route('/')
def root():
    """Página inicial: executa o backtest e mostra o relatório."""
    return backtest_web()

@app.route('/backtest')
def backtest_web():
    """Executa o backtest e retorna o relatório HTML."""
    try:
        # 1. Coleta dados da OKX
        collector = OKXDataCollector(
            symbol=SYMBOL,
            timeframe=TIMEFRAME,
            limit=CANDLE_LIMIT
        )
        df = collector.fetch_ohlcv()  # Agora busca os últimos 150 candles
        
        if df.empty:
            return jsonify({"error": "Nenhum candle obtido", "status": "failed"}), 500
        
        # 2. Inicializa estratégia
        strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        
        # 3. Executa backtest
        engine = BacktestEngine(strategy, df)
        results = engine.run()
        
        # 4. Gera HTML do relatório
        reporter = BacktestReporter(results, df)
        html_content = reporter.generate_html()
        
        return render_template_string(html_content)
    
    except Exception as e:
        tb = traceback.format_exc()
        print(f"ERRO NO BACKTEST:\n{tb}")
        return jsonify({
            "error": str(e),
            "traceback": tb.split('\n'),
            "status": "failed",
            "message": "Verifique os logs do servidor."
        }), 500

# ============================================================================
# FUNÇÕES DE BACKTEST LOCAL (COMPATIBILIDADE)
# ============================================================================
def run_backtest():
    print(f"🔍 Solicitando {CANDLE_LIMIT} candles de {SYMBOL}...")
    collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
    df = collector.fetch_ohlcv()
    print(f"✅ {len(df)} candles obtidos.")
    
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
