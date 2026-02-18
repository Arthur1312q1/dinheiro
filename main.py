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
# NORMALIZA SÍMBOLO
# ============================================================================
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

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================
FORCE_PERIOD = env_int("FORCE_PERIOD", None)

# ✅ WARMUP: passado para a estratégia, NÃO removido do DataFrame.
# A estratégia calcula indicadores no warmup mas NÃO abre posições.
# Isso replica o comportamento do TradingView (que usa todo histórico disponível).
# 300 candles de 30m ≈ 6 dias → suficiente para lenC convergir.
WARMUP_CANDLES = env_int("WARMUP_CANDLES", 300)

STRATEGY_CONFIG = {
    "adaptive_method": env("ADAPTIVE_METHOD", "Cos IFM"),
    "threshold": env_float("THRESHOLD", 0.0),
    "fixed_sl_points": env_int("FIXED_SL", 2000),
    "fixed_tp_points": env_int("FIXED_TP", 55),
    "trail_offset": env_int("TRAIL_OFFSET", 15),
    "risk_percent": env_float("RISK_PERCENT", 0.01),
    "tick_size": env_float("TICK_SIZE", 0.01),
    "initial_capital": env_float("INITIAL_CAPITAL", 1000.0),
    "max_lots": env_int("MAX_LOTS", 100),
    "force_period": FORCE_PERIOD,
    "warmup_bars": WARMUP_CANDLES,
}

# ============================================================================
# DADOS
# ============================================================================
RAW_SYMBOL = env("SYMBOL", "ETH-USDT")
SYMBOL = normalize_symbol(RAW_SYMBOL)
TIMEFRAME = env("TIMEFRAME", "30m")

# ✅ 4500 candles total:
#    - 300 warmup
#    - 4200 candles efetivos ≈ 2.9 meses de 30m
#    Isso é equivalente ao período que gera ~800 trades no TradingView.
BACKTEST_CANDLES = env_int("BACKTEST_CANDLES", 4500)

# ============================================================================
# FLASK
# ============================================================================
app = Flask(__name__)
app.register_blueprint(webhook_bp)

@app.route('/')
def root():
    return backtest_web()

@app.route('/backtest')
def backtest_web():
    try:
        print("📍 Executando backtest...")
        collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=BACKTEST_CANDLES)
        df = collector.fetch_ohlcv()

        if df.empty:
            return jsonify({"error": "Nenhum candle obtido", "status": "failed"}), 500

        df['index'] = df.index
        effective = len(df) - WARMUP_CANDLES
        print(f"📈 {len(df)} candles totais | warmup={WARMUP_CANDLES} | efetivos={effective}")

        strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        engine   = BacktestEngine(strategy, df)
        results  = engine.run()

        print(f"📊 {results['total_trades']} trades em {effective} candles efetivos")

        # Relatório mostra apenas candles pós-warmup
        df_report = df.iloc[WARMUP_CANDLES:].reset_index(drop=True)
        reporter  = BacktestReporter(results, df_report)
        html      = reporter.generate_html()

        return render_template_string(html)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"ERRO:\n{tb}")
        return jsonify({"error": str(e), "traceback": tb.split('\n'), "status": "failed"}), 500

# ============================================================================
# BACKTEST LOCAL
# ============================================================================
def run_backtest():
    print(f"🔍 Buscando {BACKTEST_CANDLES} candles de {SYMBOL} ({TIMEFRAME})...")
    collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=BACKTEST_CANDLES)
    df = collector.fetch_ohlcv()
    print(f"✅ {len(df)} candles obtidos")

    df['index'] = df.index
    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
    engine   = BacktestEngine(strategy, df)
    results  = engine.run()

    df_report = df.iloc[WARMUP_CANDLES:].reset_index(drop=True)
    reporter  = BacktestReporter(results, df_report)
    report_path = reporter.save_html('azlema_backtest_report.html')
    print(f"✅ {results['total_trades']} trades | Relatório: {report_path}")

# ============================================================================
# ENTRY POINT
# ============================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['backtest', 'server'], default='backtest')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest()
    else:
        port = env_int("PORT", 5000)
        app.run(host='0.0.0.0', port=port)
