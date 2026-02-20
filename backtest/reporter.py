# backtest/reporter.py
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
import pandas as pd


class BacktestReporter:
    def __init__(self, results: Dict[str, Any], df_report: pd.DataFrame):
        self.results = results
        self.df      = df_report

    def generate_html(self) -> str:
        stats   = self._build_stats()
        trades  = self.results.get("trades", [])
        equity  = self.results.get("equity_curve", [])
        ts_list = self.results.get("timestamps", [])

        ts_str = [str(t) for t in ts_list]

        candles_js = []
        for _, row in self.df.iterrows():
            candles_js.append({
                "time":  str(row.get("timestamp", "")),
                "open":  float(row.get("open",  0)),
                "high":  float(row.get("high",  0)),
                "low":   float(row.get("low",   0)),
                "close": float(row.get("close", 0)),
            })

        markers_js = []
        for t in trades:
            if t.get("entry_time"):
                markers_js.append({
                    "time":  str(t["entry_time"]),
                    "price": float(t.get("entry_price", 0)),
                    "type":  t.get("action", ""),
                    "label": "B" if t.get("action") == "BUY" else "S",
                })

        equity_js = [
            {"time": ts_str[i], "value": float(v)}
            for i, v in enumerate(equity)
            if i < len(ts_str)
        ]

        ultimo_candle = candles_js[-1] if candles_js else None
        return self._render(stats, trades, candles_js, markers_js, equity_js, ultimo_candle)

    def _build_stats(self) -> Dict:
        r      = self.results
        trades = r.get("trades", [])

        # FIX: filtra apenas trades fechados (pnl_usdt não None)
        closed = [t for t in trades if t.get("pnl_usdt") is not None]

        gross_win  = sum(t["pnl_usdt"] for t in closed if t["pnl_usdt"] > 0)
        gross_loss = abs(sum(t["pnl_usdt"] for t in closed if t["pnl_usdt"] < 0))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

        wins   = [t["pnl_usdt"] for t in closed if t["pnl_usdt"] > 0]
        losses = [t["pnl_usdt"] for t in closed if t["pnl_usdt"] < 0]
        avg_win  = sum(wins)   / len(wins)   if wins   else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        return {
            "total_pnl_usdt": r.get("total_pnl_usdt", 0),
            "final_balance":  r.get("final_balance",  0),
            "win_rate":       r.get("win_rate",        0),
            "total_trades":   r.get("total_trades",    0),
            "max_drawdown":   r.get("max_drawdown",    0),
            "sharpe":         r.get("sharpe",          0),
            "profit_factor":  pf,
            "avg_win":        avg_win,
            "avg_loss":       avg_loss,
        }

    def _render(self, stats, trades, candles_js, markers_js, equity_js, ultimo_candle) -> str:
        now    = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        pf_str = f"{stats['profit_factor']:.2f}" if stats['profit_factor'] != float("inf") else "∞"

        rows_html = ""
        for t in trades:
            pnl       = t.get("pnl_usdt")
            pnl_str   = f"{pnl:.2f}" if pnl is not None else "--"
            pnl_class = ("positive" if (pnl or 0) > 0
                         else "negative" if (pnl or 0) < 0 else "")
            ep        = t.get("exit_price")
            ep_str    = f"{ep:.2f}" if ep is not None else "--"
            badge     = "buy" if t.get("action") == "BUY" else "sell"
            label     = "LONG" if t.get("action") == "BUY" else "SHORT"
            reason    = t.get("exit_comment") or t.get("exit_reason") or "--"
            rows_html += f"""
            <tr>
                <td>{t.get('entry_time','--')}</td>
                <td>{t.get('exit_time','--')}</td>
                <td><span class="badge {badge}">{label}</span></td>
                <td>{t.get('qty', 0):.4f}</td>
                <td>{t.get('entry_price', 0):.2f}</td>
                <td>{ep_str}</td>
                <td class="{pnl_class}">{pnl_str}</td>
                <td style="font-size:11px;color:#888">{reason}</td>
            </tr>"""

        uc_html = ""
        if ultimo_candle:
            uc_html = (f"Time: {ultimo_candle['time']} | "
                       f"Open: {ultimo_candle['open']:.2f} | "
                       f"High: {ultimo_candle['high']:.2f} | "
                       f"Low: {ultimo_candle['low']:.2f} | "
                       f"Close: {ultimo_candle['close']:.2f}")

        pnl_color_class = "positive" if stats["total_pnl_usdt"] >= 0 else "negative"
        pf_color_class  = "positive" if stats["profit_factor"] > 1   else "negative"

        return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AZLEMA Backtest Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3.4.4/build/global/luxon.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1.3.1/dist/chartjs-adapter-luxon.min.js"></script>
<style>
  body {{ background:#0e1219; color:#e0e0e0; font-family:'Segoe UI',sans-serif; padding:20px; margin:0; }}
  .container {{ max-width:1400px; margin:0 auto; }}
  h1 {{ color:#f0b90b; font-weight:400; border-bottom:1px solid #2c3137; padding-bottom:10px; }}
  h2 {{ color:#f0b90b; font-weight:400; margin-top:0; }}
  .stats-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:15px; margin-bottom:30px; }}
  .stat-card {{ background:#1e2329; border-radius:8px; padding:20px; border-left:5px solid #f0b90b; }}
  .stat-label {{ font-size:13px; color:#a0a8b5; margin-bottom:6px; }}
  .stat-value {{ font-size:26px; font-weight:bold; color:#f0b90b; }}
  .stat-value.positive {{ color:#00c864; }}
  .stat-value.negative {{ color:#f04c4c; }}
  .debug-box {{ background:#1e2329; border-radius:8px; padding:12px 15px; margin-bottom:25px;
                font-family:'Courier New',monospace; font-size:13px; border-left:5px solid #3b82f6; }}
  .debug-box strong {{ color:#f0b90b; }}
  .chart-container {{ background:#1e2329; border-radius:8px; padding:20px; margin-bottom:25px; height:320px; }}
  .trades-section {{ background:#1e2329; border-radius:8px; padding:20px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ background:#2c3137; color:#f0b90b; padding:11px 12px; text-align:left; font-weight:600; font-size:13px; }}
  td {{ padding:9px 12px; border-bottom:1px solid #2c3137; font-size:13px; }}
  tr:hover {{ background:#2a2f36; }}
  .positive {{ color:#00c864; }}
  .negative {{ color:#f04c4c; }}
  .badge {{ display:inline-block; padding:3px 8px; border-radius:4px; font-size:11px; font-weight:bold; }}
  .badge.buy  {{ background:rgba(0,200,100,.2); color:#00c864; border:1px solid #00c864; }}
  .badge.sell {{ background:rgba(240,76,76,.2);  color:#f04c4c; border:1px solid #f04c4c; }}
  .footer {{ margin-top:25px; text-align:center; color:#6c757d; font-size:12px; }}
</style>
</head>
<body>
<div class="container">
  <h1>📈 Adaptive Zero Lag EMA v2 – Backtest Report</h1>

  <div class="debug-box">
    <strong>🔍 Último candle:</strong> {uc_html or "N/D"}
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-label">Total PnL (USDT)</div>
      <div class="stat-value {pnl_color_class}">{stats['total_pnl_usdt']:.2f}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Saldo Final</div>
      <div class="stat-value">{stats['final_balance']:.2f}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Win Rate</div>
      <div class="stat-value">{stats['win_rate']:.1f}%</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Trades</div>
      <div class="stat-value">{stats['total_trades']}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Max Drawdown</div>
      <div class="stat-value negative">{stats['max_drawdown']:.2f}%</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Sharpe (anual)</div>
      <div class="stat-value">{stats['sharpe']:.2f}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Profit Factor</div>
      <div class="stat-value {pf_color_class}">{pf_str}</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Avg Win / Avg Loss</div>
      <div class="stat-value" style="font-size:18px">
        <span class="positive">{stats['avg_win']:.2f}</span>
        &nbsp;/&nbsp;
        <span class="negative">{stats['avg_loss']:.2f}</span>
      </div>
    </div>
  </div>

  <div class="chart-container">
    <canvas id="equityChart"></canvas>
  </div>

  <div class="chart-container">
    <canvas id="priceChart"></canvas>
  </div>

  <div class="trades-section">
    <h2>📋 Histórico de Trades</h2>
    <table>
      <thead>
        <tr>
          <th>Entrada</th><th>Saída</th><th>Dir</th>
          <th>Qtd</th><th>Preço Entrada</th><th>Preço Saída</th>
          <th>PnL (USDT)</th><th>Motivo</th>
        </tr>
      </thead>
      <tbody>
        {rows_html if rows_html else '<tr><td colspan="8" style="text-align:center">Nenhum trade realizado</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="footer">
    Gerado em {now} &bull; {len(candles_js)} candles no relatório
  </div>
</div>

<script>
const equity  = {json.dumps(equity_js)};
const candles = {json.dumps(candles_js)};

const ctxE = document.getElementById('equityChart').getContext('2d');
new Chart(ctxE, {{
  type: 'line',
  data: {{
    labels: equity.map(e => e.time),
    datasets: [{{ label: 'Equity (USDT)', data: equity.map(e => e.value),
      borderColor: '#f0b90b', backgroundColor: 'rgba(240,185,11,0.08)',
      borderWidth: 2, pointRadius: 0, tension: 0.1 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color:'#e0e0e0' }} }} }},
    scales: {{
      x: {{ type:'time', time: {{ unit:'day', tooltipFormat:'yyyy-MM-dd HH:mm',
            displayFormats:{{day:'dd/MM'}} }}, grid:{{color:'rgba(255,255,255,0.07)'}},
            ticks:{{color:'#a0a8b5'}} }},
      y: {{ position:'right', grid:{{color:'rgba(255,255,255,0.07)'}}, ticks:{{color:'#a0a8b5'}} }}
    }}
  }}
}});

const ctxP = document.getElementById('priceChart').getContext('2d');
new Chart(ctxP, {{
  type: 'line',
  data: {{
    labels: candles.map(c => c.time),
    datasets: [{{ label: 'Preço de Fechamento', data: candles.map(c => c.close),
      borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.08)',
      borderWidth: 1.5, pointRadius: 0, tension: 0.1 }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color:'#e0e0e0' }} }} }},
    scales: {{
      x: {{ type:'time', time: {{ unit:'day', tooltipFormat:'yyyy-MM-dd HH:mm',
            displayFormats:{{day:'dd/MM'}} }}, grid:{{color:'rgba(255,255,255,0.07)'}},
            ticks:{{color:'#a0a8b5'}} }},
      y: {{ position:'right', grid:{{color:'rgba(255,255,255,0.07)'}}, ticks:{{color:'#a0a8b5'}} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    def save_html(self, filepath: str = "azlema_backtest_report.html") -> str:
        html = self.generate_html()
        Path(filepath).write_text(html, encoding="utf-8")
        print(f"📄 Relatório salvo: {filepath}")
        return filepath
