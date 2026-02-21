# main.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# BUG CORRIGIDO (causa dos 41 trades faltantes):
# ═══════════════════════════════════════════════════════════════════════════════
#
# ANTES (ERRADO):
#   BACKTEST_CANDLES = 4500
#   WARMUP_CANDLES   = 300
#   collector.fetch(limit=4500)   ← busca só 4500 candles
#   strategy(warmup_bars=300)     ← usa 300 do trading para warmup!
#   → efetivo de trading: 4200 candles (-300 = -18.8 trades perdidos)
#
# AGORA (CORRETO):
#   BACKTEST_CANDLES = 4500       ← período de trading (igual ao TradingView)
#   WARMUP_CANDLES   = 1000       ← pré-história para IFM/EC convergir
#   collector.fetch(limit=5500)   ← busca BACKTEST + WARMUP
#   strategy(warmup_bars=1000)    ← warmup sobre dados extras
#   → efetivo de trading: 4500 candles (IGUAL ao TradingView) ✅
#
# POR QUE 1000 WARMUP? Pine Script computa indicadores da história INTEIRA
# da OKX (anos de dados) antes de iniciar o trading. O IFM (Cosine) usa
# EMA com α=0.25, que converge em ~20 barras. EC/EMA do ZLEMA com α≈0.2
# converge em ~20 barras. 1000 barras = 20+ dias de pré-história → garante
# IFM e ZLEMA completamente convergidos antes do trading iniciar. ✅
#
# DURANTE WARMUP: IFM, ZLEMA, signals são computados normalmente.
# Pending flags também propagam (para o estado na 1ª barra de trading
# refletir corretamente o sinal da última barra do warmup — igual ao Pine).
# Apenas TRADES não são executados.
# ═══════════════════════════════════════════════════════════════════════════════

import os
import argparse
import traceback
from flask import Flask, jsonify
import pandas as pd

from strategy.adaptive_zero_lag_ema import AdaptiveZeroLagEMA
from data.collector import DataCollector
from backtest.engine import BacktestEngine
from backtest.reporter import BacktestReporter


# ─── Helpers ────────────────────────────────────────────────────────────────
def env(k, d=None):
    return os.environ.get(k, d)

def env_int(k, d=0):
    v = os.environ.get(k)
    if v is None: return d
    try: return int(v)
    except: return d

def env_float(k, d=0.0):
    v = os.environ.get(k)
    if v is None: return d
    try: return float(v)
    except: return d

def normalize_symbol(s: str) -> str:
    """Normaliza símbolo para formato OKX (ETH-USDT)."""
    s = s.strip().upper().replace('/', '-').replace('_', '-').replace(' ', '-')
    if '-' not in s and s.endswith('USDT'):
        s = s[:-4] + '-USDT'
    return s


# ─── Config ──────────────────────────────────────────────────────────────────
SYMBOL           = normalize_symbol(env("SYMBOL", "ETH-USDT"))
TIMEFRAME        = env("TIMEFRAME", "30m")
EXCHANGE         = env("EXCHANGE", "okx")       # OKX = mesma fonte do TradingView

# BACKTEST_CANDLES = candles de TRADING (deve ser igual ao período do TradingView)
# WARMUP_CANDLES   = candles EXTRAS para IFM/ZLEMA convergir antes do trading
# TOTAL buscado da exchange: BACKTEST_CANDLES + WARMUP_CANDLES
BACKTEST_CANDLES = env_int("BACKTEST_CANDLES", 4500)   # 93.75 dias de trading
WARMUP_CANDLES   = env_int("WARMUP_CANDLES",  1000)    # ~20.8 dias de pré-história

STRATEGY_CONFIG = {
    "adaptive_method": env("ADAPTIVE_METHOD", "Cos IFM"),
    "threshold":       env_float("THRESHOLD",    0.0),
    "fixed_sl_points": env_int("FIXED_SL",    2000),
    "fixed_tp_points": env_int("FIXED_TP",      55),
    "trail_offset":    env_int("TRAIL_OFFSET",   15),
    "risk_percent":    env_float("RISK_PERCENT", 0.01),
    "tick_size":       env_float("TICK_SIZE",    0.01),
    "initial_capital": env_float("INITIAL_CAPITAL", 1000.0),
    "max_lots":        env_int("MAX_LOTS",      100),
    "default_period":  env_int("DEFAULT_PERIOD", 20),
    "warmup_bars":     WARMUP_CANDLES,   # ← usa os candles extras de pré-história
}
if env("FORCE_PERIOD"):
    STRATEGY_CONFIG["force_period"] = env_int("FORCE_PERIOD", None)


# ─── Flask ───────────────────────────────────────────────────────────────────
app = Flask(__name__)


def run_full_backtest():
    total_candles = BACKTEST_CANDLES + WARMUP_CANDLES   # ← FIX: busca TOTAL

    print(f"═══════════════════════════════════════════")
    print(f"Exchange:  {EXCHANGE.upper()} | {SYMBOL} {TIMEFRAME}")
    print(f"Candles:   {total_candles} ({WARMUP_CANDLES} warmup + {BACKTEST_CANDLES} trading)")
    print(f"Período trading: {BACKTEST_CANDLES * 30 / 60 / 24:.1f} dias")
    print(f"Pré-história:    {WARMUP_CANDLES * 30 / 60 / 24:.1f} dias")
    print(f"═══════════════════════════════════════════")

    # Converte símbolo para formato da exchange
    if EXCHANGE == "okx":
        sym = SYMBOL   # já em formato ETH-USDT
    else:
        sym = SYMBOL.replace('-', '')   # Binance/Bybit: ETHUSDT

    collector = DataCollector(
        symbol    = sym,
        timeframe = TIMEFRAME,
        limit     = total_candles,   # ← FIX: BACKTEST + WARMUP
        exchange  = EXCHANGE,
    )
    df = collector.fetch_ohlcv()

    if df.empty:
        raise ValueError("Nenhum candle obtido da exchange")

    # Adiciona coluna index para logging interno
    df = df.reset_index(drop=True)
    df['index'] = df.index

    print(f"\n📅 Período completo:  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print(f"📅 Período trading:   {df['timestamp'].iloc[WARMUP_CANDLES]} → {df['timestamp'].iloc[-1]}")

    # Executa backtest
    strategy = AdaptiveZeroLagEMA(**STRATEGY_CONFIG)
    engine   = BacktestEngine(strategy, df)
    results  = engine.run()

    print(f"\n📊 Resultados:")
    print(f"   Trades: {results['total_trades']}")
    print(f"   Win Rate: {results['win_rate']:.1f}%")
    print(f"   PnL: {results['total_pnl_usdt']:.2f} USDT")
    print(f"   Balance: ${results['final_balance']:.2f}")
    print(f"   Profit Factor: {results.get('profit_factor', 0):.2f}")
    print(f"   Max Drawdown: {results.get('max_drawdown_pct', 0):.2f}%")

    # Reporter usa APENAS os candles de trading (sem o warmup)
    df_report = df.iloc[WARMUP_CANDLES:].reset_index(drop=True)
    reporter  = BacktestReporter(results, df_report)
    return reporter


@app.route('/')
@app.route('/backtest')
def backtest_web():
    try:
        reporter = run_full_backtest()
        return reporter.generate_html()
    except Exception as e:
        tb = traceback.format_exc()
        print(f"ERRO:\n{tb}")
        return jsonify({"error": str(e), "traceback": tb.split('\n')}), 500


@app.route('/ping')
def ping():
    return "pong", 200


@app.route('/health')
def health():
    return jsonify({"status": "healthy", "exchange": EXCHANGE,
                    "symbol": SYMBOL, "timeframe": TIMEFRAME}), 200


# ─── CLI ─────────────────────────────────────────────────────────────────────
def run_local_backtest():
    reporter    = run_full_backtest()
    report_path = reporter.save_html('azlema_backtest_report.html')
    print(f"\n✅ Relatório salvo: {report_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='AZLEMA Backtest')
    parser.add_argument('--mode', choices=['backtest', 'server'], default='backtest')
    args = parser.parse_args()

    if args.mode == 'backtest':
        run_local_backtest()
    else:
        port = env_int("PORT", 5000)
        print(f"🚀 Servidor na porta {port}")
        app.run(host='0.0.0.0', port=port, debug=False)
