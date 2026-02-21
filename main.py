# main.py
#
# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO CORRETA — entendendo o problema do "86 trades"
# ═══════════════════════════════════════════════════════════════════════════════
#
# CAUSA DO BUG (241 → 86 trades):
# ──────────────────────────────────────────────────────────────────────────────
# O WARMUP_CANDLES foi aumentado de 300 → 1000 no main.py anterior.
# Mas o OKX /candles endpoint entrega apenas ~2400 candles máximo.
# Com warmup=1000 consumindo esses dados, restavam só ~1400 para trading
# → 86 trades (correto para 1400 bars, mas MUY POUCO vs 282 esperados)
#
# SOLUÇÃO IMPLEMENTADA:
# ──────────────────────────────────────────────────────────────────────────────
# 1. Collector usa /history-candles (dados históricos completos OKX) +
#    /candles (dados recentes), conseguindo 5500+ candles sem limite
#
# 2. Total buscado = BACKTEST_CANDLES + WARMUP_CANDLES
#    → warmup são candles EXTRAS para IFM/EC convergir
#    → BACKTEST_CANDLES é o período de trading real (= TradingView)
#
# VALORES PADRÃO:
#   BACKTEST_CANDLES = 4500  → 93.75 dias de trading (igual ao TradingView)
#   WARMUP_CANDLES   = 1000  → 20.8 dias extras para IFM convergir
#   Total buscado    = 5500  → via history-candles + candles
#
# RESULTADO ESPERADO: 282 trades (igual TradingView) ✅
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


# ─── Helpers ─────────────────────────────────────────────────────────────────
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
    """Normaliza para formato OKX: ETH-USDT"""
    s = s.strip().upper().replace('/', '-').replace('_', '-').replace(' ', '-')
    if '-' not in s and s.endswith('USDT'):
        s = s[:-4] + '-USDT'
    return s


# ─── Config ───────────────────────────────────────────────────────────────────
SYMBOL           = normalize_symbol(env("SYMBOL", "ETH-USDT"))
TIMEFRAME        = env("TIMEFRAME", "30m")

# Período de TRADING (deve ser idêntico ao período configurado no TradingView)
BACKTEST_CANDLES = env_int("BACKTEST_CANDLES", 4500)   # 93.75 dias × 48 bars/dia

# Pré-história para IFM/EC/EMA convergirem antes do trading iniciar
# (candles EXTRAS além do BACKTEST_CANDLES)
WARMUP_CANDLES   = env_int("WARMUP_CANDLES",  1000)    # 20.8 dias extras

# Total a buscar da OKX (via history-candles + candles)
TOTAL_CANDLES    = BACKTEST_CANDLES + WARMUP_CANDLES   # = 5500

STRATEGY_CONFIG = {
    "adaptive_method": env("ADAPTIVE_METHOD", "Cos IFM"),
    "threshold":       env_float("THRESHOLD",     0.0),
    "fixed_sl_points": env_int("FIXED_SL",     2000),
    "fixed_tp_points": env_int("FIXED_TP",       55),
    "trail_offset":    env_int("TRAIL_OFFSET",    15),
    "risk_percent":    env_float("RISK_PERCENT",  0.01),
    "tick_size":       env_float("TICK_SIZE",     0.01),
    "initial_capital": env_float("INITIAL_CAPITAL", 1000.0),
    "max_lots":        env_int("MAX_LOTS",       100),
    "default_period":  env_int("DEFAULT_PERIOD",  20),
    "warmup_bars":     WARMUP_CANDLES,  # ← Pine não tem warmup; ZLEMA/IFM convergem aqui
}

# Opcional: forçar Period fixo (para testes)
_fp = env_int("FORCE_PERIOD", None) if env("FORCE_PERIOD") else None
if _fp:
    STRATEGY_CONFIG["force_period"] = _fp


# ─── Flask ────────────────────────────────────────────────────────────────────
app = Flask(__name__)


def run_full_backtest():
    print(f"\n{'═'*55}")
    print(f"  AZLEMA Backtest — OKX {SYMBOL} {TIMEFRAME}")
    print(f"{'═'*55}")
    print(f"  Pré-história (warmup): {WARMUP_CANDLES:,} candles ({WARMUP_CANDLES/48:.1f} dias)")
    print(f"  Período trading:       {BACKTEST_CANDLES:,} candles ({BACKTEST_CANDLES/48:.1f} dias)")
    print(f"  Total a buscar:        {TOTAL_CANDLES:,} candles")
    print(f"{'═'*55}\n")

    collector = DataCollector(
        symbol    = SYMBOL,
        timeframe = TIMEFRAME,
        limit     = TOTAL_CANDLES,    # ← busca BACKTEST + WARMUP
    )
    df = collector.fetch_ohlcv()

    if df.empty:
        raise ValueError("Nenhum candle obtido da OKX")

    # Garante coluna index para logging da estratégia
    df = df.reset_index(drop=True)
    df['index'] = df.index

    actual_total     = len(df)
    actual_warmup    = min(WARMUP_CANDLES, actual_total - 1)
    actual_trading   = actual_total - actual_warmup

    print(f"\n📊 Candles reais:  {actual_total:,}")
    print(f"   Warmup bars:    {actual_warmup:,}")
    print(f"   Trading bars:   {actual_trading:,}")

    if actual_total < TOTAL_CANDLES * 0.8:
        print(f"\n⚠️  ATENÇÃO: recebeu {actual_total}/{TOTAL_CANDLES} candles")
        print(f"   Verifique se SYMBOL={SYMBOL} e TIMEFRAME={TIMEFRAME} estão corretos")

    # Ajusta warmup_bars ao real disponível
    cfg = {**STRATEGY_CONFIG, "warmup_bars": actual_warmup}

    strategy = AdaptiveZeroLagEMA(**cfg)
    engine   = BacktestEngine(strategy, df)
    results  = engine.run()

    print(f"\n{'─'*40}")
    print(f"  Total Trades:   {results['total_trades']}")
    print(f"  Win Rate:       {results['win_rate']:.1f}%")
    print(f"  PnL:            {results['total_pnl_usdt']:.2f} USDT")
    print(f"  Balance:        ${results['final_balance']:.2f}")
    print(f"  Profit Factor:  {results.get('profit_factor', 0):.2f}")
    print(f"  Max Drawdown:   {results.get('max_drawdown_pct', 0):.2f}%")
    print(f"{'─'*40}\n")

    df_report = df.iloc[actual_warmup:].reset_index(drop=True)
    return BacktestReporter(results, df_report)


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
    return jsonify({
        "status":   "healthy",
        "symbol":   SYMBOL,
        "timeframe": TIMEFRAME,
        "backtest_candles": BACKTEST_CANDLES,
        "warmup_candles":   WARMUP_CANDLES,
    }), 200


# ─── CLI ──────────────────────────────────────────────────────────────────────
def run_local_backtest():
    reporter    = run_full_backtest()
    report_path = reporter.save_html('azlema_backtest_report.html')
    print(f"✅ Relatório: {report_path}")


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
