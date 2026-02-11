# main.py
import os
import argparse
from pathlib import Path
import pandas as pd

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import OKXDataCollector
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter
from keepalive.pinger import KeepAlivePinger
from keepalive.webhook_receiver import webhook_bp
from utils.env_loader import env, env_int, env_float

# Flask para webhook (modo servidor)
from flask import Flask

# ============================================================================
# CONFIGURAÇÕES DA ESTRATÉGIA – ALTERE AQUI OS PARÂMETROS DESEJADOS
# ============================================================================
# Estas são as mesmas do script Pine. Modifique à vontade.
# Caso queira sobrescrever via variáveis de ambiente no Render,
# utilize os nomes indicados nos comentários.
# ============================================================================

STRATEGY_CONFIG = {
    "adaptive_method": env("ADAPTIVE_METHOD", "Cos IFM"),        # "Cos IFM", "I-Q IFM", "Average", "Off"
    "threshold": env_float("THRESHOLD", 0.0),                   # Filtro de erro percentual
    "fixed_sl_points": env_int("FIXED_SL", 2000),               # Stop loss em pontos
    "fixed_tp_points": env_int("FIXED_TP", 55),                 # Pontos para ativar trailing
    "trail_offset": env_int("TRAIL_OFFSET", 15),                # Distância do trailing
    "risk_percent": env_float("RISK_PERCENT", 0.01),            # 1% de risco por trade
    "tick_size": env_float("TICK_SIZE", 0.01),                  # ETH/USDT = 0.01, BTC/USDT = 0.1
    "initial_capital": env_float("INITIAL_CAPITAL", 1000.0),    # Capital inicial
    "max_lots": env_int("MAX_LOTS", 100)                        # Limite máximo de contratos
}

# ============================================================================
# CONFIGURAÇÕES DE COLETA DE DADOS
# ============================================================================
SYMBOL = env("SYMBOL", "ETH/USDT")
TIMEFRAME = env("TIMEFRAME", "30m")
BACKTEST_DAYS = env_int("BACKTEST_DAYS", 30)
CANDLE_LIMIT = env_int("CANDLE_LIMIT", 1000)

# ============================================================================
# FLUXO DE BACKTESTING
# ============================================================================
def run_backtest():
    print("🔍 Iniciando coleta de dados da OKX...")
    collector = OKXDataCollector(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        limit=CANDLE_LIMIT
    )
    df = collector.fetch_recent(days=BACKTEST_DAYS)
    print(f"✅ {len(df)} candles baixados.")

    print("⚙️  Inicializando estratégia...")
    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)

    print("📊 Executando backtest...")
    engine = BacktestEngine(strategy, df)
    results = engine.run()

    print("📈 Gerando relatório visual...")
    reporter = BacktestReporter(results, df)
    report_path = reporter.generate('azlema_backtest_report.html')
    print(f"✅ Relatório salvo: {report_path}")

    print("\n========== RESUMO ==========")
    print(f"Total Trades: {results['total_trades']}")
    print(f"Win Rate: {results['win_rate']:.2f}%")
    print(f"Total PnL: ${results['total_pnl_usdt']:.2f}")
    print(f"Final Balance: ${results['final_balance']:.2f}")
    print(f"Max Drawdown: {results['max_drawdown']:.2f}%")
    print("============================\n")

# ============================================================================
# MODO SERVIDOR (WEBHOOK + KEEPALIVE)
# ============================================================================
def start_server():
    app = Flask(__name__)
    app.register_blueprint(webhook_bp)

    # Inicia pings internos se não estiver em debug
    if not app.debug:
        base_url = env("SELF_URL", "http://localhost:5000")
        pinger = KeepAlivePinger(base_url=base_url)
        pinger.start(intervals=[13, 23, 30])

    port = env_int("PORT", 5000)
    app.run(host='0.0.0.0', port=port)

# ============================================================================
# PONTO DE ENTRADA
# ============================================================================
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AZLEMA Backtesting System')
    parser.add_argument('--mode', choices=['backtest', 'server'], default='backtest',
                        help='backtest (gera relatório) ou server (webhook + keepalive)')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_backtest()
    else:
        start_server()
