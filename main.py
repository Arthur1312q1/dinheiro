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

def normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    symbol = symbol.replace('/', '-').replace('_', '-').replace(' ', '-')
    if '-' not in symbol and symbol.endswith('USDT'):
        base = symbol[:-4]
        symbol = f"{base}-USDT"
    if not symbol.endswith('-USDT'):
        if '-' in symbol:
            base, quote = symbol.split('-')
            if quote != 'USDT':
                symbol = f"{base}-USDT"
        else:
            symbol = f"{symbol}-USDT"
    return symbol

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

RAW_SYMBOL = env("SYMBOL", "ETH-USDT")
SYMBOL = normalize_symbol(RAW_SYMBOL)
TIMEFRAME = env("TIMEFRAME", "30m")
CANDLE_LIMIT = 1000   # FORÇADO

app = Flask(__name__)
app.register_blueprint(webhook_bp)

@app.route('/')
def root():
    return backtest_web()

@app.route('/backtest')
def backtest_web():
    try:
        collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = collector.fetch_ohlcv()
        if df.empty:
            return jsonify({"error": "Nenhum candle obtido", "status": "failed"}), 500

        # Warm-up: remove primeiros 100 candles
        df = df.iloc[100:].reset_index(drop=True)

        strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        engine = BacktestEngine(strategy, df)
        results = engine.run()

        reporter = BacktestReporter(results, df)
        html_content = reporter.generate_html()
        return render_template_string(html_content)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"ERRO NO BACKTEST:\n{tb}")
        return jsonify({"error": str(e), "traceback": tb.split('\n'), "status": "failed"}), 500

def run_backtest():
    print(f"🔍 Solicitando {CANDLE_LIMIT} candles de {SYMBOL}...")
    collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
    df = collector.fetch_ohlcv()
    df = df.iloc[100:].reset_index(drop=True)
    print(f"✅ {len(df)} candles após warm-up.")

    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
    engine = BacktestEngine(strategy, df)
    results = engine.run()

    reporter = BacktestReporter(results, df)
    report_path = reporter.save_html('azlema_backtest_report.html')
    print(f"✅ Relatório salvo: {report_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AZLEMA Backtesting System')
    parser.add_argument('--mode', choices=['backtest', 'server'], default='backtest')
    args = parser.parse_args()
    if args.mode == 'backtest':
        run_backtest()
    else:
        port = env_int("PORT", 5000)
        app.run(host='0.0.0.0', port=port)
