"""
research/analysis/tearsheet.py — HTML performance tearsheet generator.

Reads ml_equity.csv, ml_trades.csv, and ml_metrics.csv produced by the C++
backtester and generates a single self-contained HTML file with 7 panels:

  1. Summary statistics table
  2. Equity curve (strategy vs benchmark)
  3. Underwater / drawdown plot
  4. Rolling 60-day Sharpe
  5. Monthly returns heatmap
  6. Trade P&L distribution
  7. Per-symbol P&L contribution

Usage:
    python research/analysis/tearsheet.py \\
        --equity  output/ml_equity.csv  \\
        --trades  output/ml_trades.csv  \\
        --metrics output/ml_metrics.csv \\
        --config  backtest_config.yaml  \\
        --output  output/tearsheet.html
"""

from __future__ import annotations

import argparse
import math
import os
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from jinja2 import Template


# ---------------------------------------------------------------------------
# Pure-function data helpers (all testable without file I/O)
# ---------------------------------------------------------------------------

def compute_drawdown_series(equity: pd.Series) -> pd.Series:
    """Return the drawdown from peak at every bar (non-positive values)."""
    peak = equity.cummax()
    return (equity - peak) / peak


def compute_rolling_sharpe(equity: pd.Series, window: int = 60) -> pd.Series:
    """Annualised rolling Sharpe over `window` bars (NaN for first window-1 bars)."""
    returns = equity.pct_change()
    rolling_mean = returns.rolling(window).mean()
    rolling_std = returns.rolling(window).std(ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        sharpe = rolling_mean / rolling_std * math.sqrt(252)
    return sharpe


def compute_monthly_returns(equity: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot monthly returns into a year × month matrix.

    Parameters
    ----------
    equity : DataFrame with 'timestamp' and 'equity' columns

    Returns
    -------
    DataFrame indexed by year, columns 0..11 (Jan=0 … Dec=11), values are
    monthly return fractions.
    """
    df = equity[["timestamp", "equity"]].copy()
    df["date"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()

    monthly = df["equity"].resample("ME").last()
    monthly_ret = monthly.pct_change().dropna()

    result = pd.DataFrame({
        "year": monthly_ret.index.year,
        "month": monthly_ret.index.month - 1,  # 0-based for heatmap x-axis
        "return": monthly_ret.values,
    })
    if result.empty:
        return pd.DataFrame()
    return result.pivot(index="year", columns="month", values="return")


def compute_per_symbol_pnl(trades: pd.DataFrame) -> pd.Series:
    """Sum realised P&L per symbol from SELL trades."""
    if trades.empty or "direction" not in trades.columns or "pnl" not in trades.columns:
        return pd.Series(dtype=float)
    sells = trades[trades["direction"] == "SELL"]
    if sells.empty:
        return pd.Series(dtype=float)
    return sells.groupby("symbol")["pnl"].sum().sort_values()


# ---------------------------------------------------------------------------
# Tearsheet class
# ---------------------------------------------------------------------------

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class Tearsheet:
    """Generate a self-contained HTML performance tearsheet."""

    def __init__(
        self,
        equity_csv: str,
        trades_csv: str,
        metrics_csv: str,
        config_path: str,
        output_path: str,
    ) -> None:
        self.equity_csv = equity_csv
        self.trades_csv = trades_csv
        self.metrics_csv = metrics_csv
        self.config_path = config_path
        self.output_path = output_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self) -> None:
        equity = self._load_equity()
        trades = self._load_trades()
        metrics = self._load_metrics()

        figs = [
            self._panel_equity_curve(equity),
            self._panel_drawdown(equity),
            self._panel_rolling_sharpe(equity),
            self._panel_monthly_heatmap(equity),
            self._panel_trade_distribution(trades),
            self._panel_symbol_contribution(trades),
        ]

        os.makedirs(os.path.dirname(self.output_path) or ".", exist_ok=True)
        html = self._render(figs, metrics)
        with open(self.output_path, "w", encoding="utf-8") as f:
            f.write(html)

    # ------------------------------------------------------------------
    # Panel builders
    # ------------------------------------------------------------------

    def _panel_equity_curve(self, equity: pd.DataFrame) -> go.Figure:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity["timestamp"], y=equity["equity"],
            mode="lines", name="Strategy",
            line=dict(color="#2196F3", width=2),
        ))
        if "benchmark_equity" in equity.columns:
            fig.add_trace(go.Scatter(
                x=equity["timestamp"], y=equity["benchmark_equity"],
                mode="lines", name="Benchmark (buy & hold)",
                line=dict(color="#9E9E9E", width=1.5, dash="dash"),
            ))
        fig.update_layout(
            title="Equity Curve", xaxis_title="Date", yaxis_title="Portfolio Value ($)",
            legend=dict(x=0, y=1), **self._layout_defaults(),
        )
        return fig

    def _panel_drawdown(self, equity: pd.DataFrame) -> go.Figure:
        dd = compute_drawdown_series(equity["equity"]) * 100.0
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity["timestamp"], y=dd,
            fill="tozeroy", mode="lines",
            name="Drawdown",
            line=dict(color="#F44336", width=1),
            fillcolor="rgba(244,67,54,0.3)",
        ))
        fig.update_layout(
            title="Drawdown", xaxis_title="Date", yaxis_title="Drawdown (%)",
            **self._layout_defaults(),
        )
        return fig

    def _panel_rolling_sharpe(self, equity: pd.DataFrame) -> go.Figure:
        rs = compute_rolling_sharpe(equity["equity"], window=60)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=equity["timestamp"], y=rs,
            mode="lines", name="Rolling 60-Day Sharpe",
            line=dict(color="#4CAF50", width=1.5),
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="rgba(0,0,0,0.3)")
        fig.update_layout(
            title="Rolling 60-Day Sharpe Ratio",
            xaxis_title="Date", yaxis_title="Sharpe",
            **self._layout_defaults(),
        )
        return fig

    def _panel_monthly_heatmap(self, equity: pd.DataFrame) -> go.Figure:
        pivot = compute_monthly_returns(equity)
        if pivot.empty:
            fig = go.Figure()
            fig.update_layout(title="Monthly Returns (insufficient data)",
                              **self._layout_defaults())
            return fig

        years = [str(y) for y in pivot.index.tolist()]
        months_present = sorted(pivot.columns.tolist())
        month_labels = [MONTH_ABBR[m] for m in months_present]
        z = (pivot[months_present].values * 100.0).tolist()

        fig = go.Figure(go.Heatmap(
            z=z, x=month_labels, y=years,
            colorscale="RdYlGn",
            zmid=0,
            text=[[f"{v:.1f}%" if not math.isnan(v) else "" for v in row] for row in z],
            texttemplate="%{text}",
            colorbar=dict(title="Return %"),
        ))
        fig.update_layout(
            title="Monthly Returns (%)",
            xaxis_title="Month", yaxis_title="Year",
            **self._layout_defaults(),
        )
        return fig

    def _panel_trade_distribution(self, trades: pd.DataFrame) -> go.Figure:
        fig = go.Figure()
        sells = trades[trades["direction"] == "SELL"] if not trades.empty else pd.DataFrame()

        if not sells.empty and "pnl" in sells.columns:
            pnl = sells["pnl"].dropna()
            fig.add_trace(go.Histogram(
                x=pnl, nbinsx=30, name="Trade P&L",
                marker_color="#2196F3", opacity=0.75,
            ))
            if not pnl.empty:
                mean_pnl = pnl.mean()
                median_pnl = pnl.median()
                fig.add_vline(x=mean_pnl, line_dash="dash",
                              line_color="#F44336",
                              annotation_text=f"Mean ${mean_pnl:.0f}",
                              annotation_position="top right")
                fig.add_vline(x=median_pnl, line_dash="dot",
                              line_color="#FF9800",
                              annotation_text=f"Median ${median_pnl:.0f}",
                              annotation_position="top left")

        fig.update_layout(
            title="Trade P&L Distribution",
            xaxis_title="Realised P&L ($)", yaxis_title="Count",
            **self._layout_defaults(),
        )
        return fig

    def _panel_symbol_contribution(self, trades: pd.DataFrame) -> go.Figure:
        pnl_by_sym = compute_per_symbol_pnl(trades)
        fig = go.Figure()
        if not pnl_by_sym.empty:
            colors = ["#F44336" if v < 0 else "#4CAF50" for v in pnl_by_sym.values]
            fig.add_trace(go.Bar(
                x=pnl_by_sym.index.tolist(),
                y=pnl_by_sym.values.tolist(),
                marker_color=colors,
                name="Realised P&L",
            ))
        fig.update_layout(
            title="Per-Symbol Realised P&L",
            xaxis_title="Symbol", yaxis_title="Total P&L ($)",
            **self._layout_defaults(),
        )
        return fig

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render(self, figs: list[go.Figure], metrics: dict) -> str:
        # First figure includes plotly.js (self-contained); rest are divs only
        div_parts = []
        for i, fig in enumerate(figs):
            div_parts.append(fig.to_html(
                full_html=False,
                include_plotlyjs=(i == 0),
                div_id=f"panel-{i}",
                config={"displayModeBar": False},
            ))

        run_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        template = Template(_HTML_TEMPLATE)
        return template.render(
            run_timestamp=run_ts,
            config_path=self.config_path,
            metrics=metrics,
            panels=div_parts,
        )

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _load_equity(self) -> pd.DataFrame:
        return pd.read_csv(self.equity_csv)

    def _load_trades(self) -> pd.DataFrame:
        if not os.path.exists(self.trades_csv):
            return pd.DataFrame(columns=["timestamp", "symbol", "price",
                                         "quantity", "direction", "profit", "pnl"])
        return pd.read_csv(self.trades_csv)

    def _load_metrics(self) -> dict:
        if not os.path.exists(self.metrics_csv):
            return {}
        df = pd.read_csv(self.metrics_csv)
        return dict(zip(df["metric"], df["value"]))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _layout_defaults() -> dict:
        return dict(
            template="plotly_white",
            height=400,
            margin=dict(l=60, r=30, t=50, b=50),
            font=dict(family="Inter, Arial, sans-serif", size=13),
        )


# ---------------------------------------------------------------------------
# Jinja2 HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Backtest Tearsheet</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: Inter, Arial, sans-serif; background: #f5f5f5; color: #212121; }
    header {
      background: #1565C0; color: white;
      padding: 16px 24px;
      display: flex; justify-content: space-between; align-items: center;
    }
    header h1 { font-size: 1.3rem; font-weight: 600; }
    header span { font-size: 0.85rem; opacity: 0.85; }
    .container { max-width: 1280px; margin: 0 auto; padding: 24px 16px; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 12px; margin-bottom: 24px;
    }
    .metric-card {
      background: white; border-radius: 8px;
      padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.1);
      text-align: center;
    }
    .metric-card .label { font-size: 0.75rem; color: #757575; text-transform: uppercase; }
    .metric-card .value { font-size: 1.5rem; font-weight: 700; margin-top: 4px; }
    .panel {
      background: white; border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.1);
      margin-bottom: 20px; padding: 8px;
      overflow: hidden;
    }
    .green { color: #2E7D32; }
    .red   { color: #C62828; }
  </style>
</head>
<body>
<header>
  <h1>Distributed-Backtesting-Engine &mdash; Performance Tearsheet</h1>
  <span>Run: {{ run_timestamp }} &nbsp;|&nbsp; Config: {{ config_path }}</span>
</header>

<div class="container">

  <section id="summary" class="summary-grid">
    {% set fmt = {
        'total_return':      ('Total Return',     '%', 2),
        'benchmark_return':  ('Benchmark Return', '%', 2),
        'alpha':             ('Alpha',            '%', 2),
        'annualised_return': ('Ann. Return',      '%', 2),
        'sharpe_ratio':      ('Sharpe',           '',  3),
        'information_ratio': ('Info. Ratio',      '',  3),
        'max_drawdown':      ('Max Drawdown',     '%', 2),
        'trading_days':      ('Trading Days',     '',  0),
    } %}
    {% for key, (label, suffix, decimals) in fmt.items() %}
      {% if key in metrics %}
        {% set val = metrics[key] | float %}
        <div class="metric-card">
          <div class="label">{{ label }}</div>
          <div class="value {% if val > 0 %}green{% elif val < 0 %}red{% endif %}">
            {{ '%.{}f'.format(decimals) | format(val) }}{{ suffix }}
          </div>
        </div>
      {% endif %}
    {% endfor %}
  </section>

  {% for panel in panels %}
  <section class="panel" id="panel-{{ loop.index0 }}">
    {{ panel }}
  </section>
  {% endfor %}

</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate HTML performance tearsheet")
    p.add_argument("--equity",  required=True, help="Path to ml_equity.csv")
    p.add_argument("--trades",  required=True, help="Path to ml_trades.csv")
    p.add_argument("--metrics", required=True, help="Path to ml_metrics.csv")
    p.add_argument("--config",  default="backtest_config.yaml",
                   help="Path to backtest_config.yaml (for header display)")
    p.add_argument("--output",  required=True, help="Output HTML path")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    ts = Tearsheet(
        equity_csv=args.equity,
        trades_csv=args.trades,
        metrics_csv=args.metrics,
        config_path=args.config,
        output_path=args.output,
    )
    ts.generate()
    print(f"Tearsheet written to {args.output}")


if __name__ == "__main__":
    main()
