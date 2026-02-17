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
# FUNÇÃO AUXILIAR: NORMALIZA SÍMBOLO
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
# CONFIGURAÇÕES DA ESTRATÉGIA
# ============================================================================
FORCE_PERIOD = env_int("FORCE_PERIOD", None)

# ✅ WARMUP_CANDLES: passado para a estratégia, NÃO removido do dataframe.
# A estratégia processa esses candles para aquecer os indicadores
# mas não abre posições neles — igual ao TradingView que usa todo o histórico.
WARMUP_CANDLES = env_int("WARMUP_CANDLES", 200)

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
    "warmup_bars": WARMUP_CANDLES,   # ← estratégia gerencia o warmup internamente
}

# ============================================================================
# CONFIGURAÇÕES DE COLETA DE DADOS
# ============================================================================
RAW_SYMBOL = env("SYMBOL", "ETH-USDT")
SYMBOL = normalize_symbol(RAW_SYMBOL)
TIMEFRAME = env("TIMEFRAME", "30m")

# Total de candles = warmup + candles de backtest real
# Ex: 200 warmup + 1300 reais = 1500 total
BACKTEST_CANDLES = env_int("BACKTEST_CANDLES", 1500)
CANDLE_LIMIT = BACKTEST_CANDLES

# ============================================================================
# CRIAÇÃO DA APLICAÇÃO FLASK
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

        collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
        df = collector.fetch_ohlcv()

        if df.empty:
            return jsonify({"error": "Nenhum candle obtido", "status": "failed"}), 500

        # ✅ NÃO removemos candles do df. O df completo vai para o engine.
        # A estratégia usa warmup_bars internamente para não operar nas
        # primeiras N barras, mas ainda processa os indicadores nelas.
        df['index'] = df.index

        print(f"📈 {len(df)} candles totais ({WARMUP_CANDLES} warmup + {len(df)-WARMUP_CANDLES} backtest)")

        strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
        engine = BacktestEngine(strategy, df)
        results = engine.run()

        print(f"📊 Relatório: {len(df)} candles, {results['total_trades']} trades")

        # Para o relatório, mostramos apenas os candles pós-warmup
        df_report = df.iloc[WARMUP_CANDLES:].reset_index(drop=True)
        reporter = BacktestReporter(results, df_report)
        html_content = reporter.generate_html()

        print("✅ Backtest concluído")
        return render_template_string(html_content)

    except Exception as e:
        tb = traceback.format_exc()
        print(f"ERRO NO BACKTEST:\n{tb}")
        return jsonify({"error": str(e), "traceback": tb.split('\n'), "status": "failed"}), 500

# ============================================================================
# BACKTEST LOCAL
# ============================================================================
def run_backtest():
    print(f"🔍 Buscando {CANDLE_LIMIT} candles de {SYMBOL} ({TIMEFRAME})...")
    collector = OKXDataCollector(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
    df = collector.fetch_ohlcv()
    print(f"✅ {len(df)} candles obtidos")

    df['index'] = df.index
    print(f"📈 {WARMUP_CANDLES} candles de warmup interno, {len(df)-WARMUP_CANDLES} para backtest")

    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
    engine = BacktestEngine(strategy, df)
    results = engine.run()

    df_report = df.iloc[WARMUP_CANDLES:].reset_index(drop=True)
    reporter = BacktestReporter(results, df_report)
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
