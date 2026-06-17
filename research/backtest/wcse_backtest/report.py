"""Trade log + equity curve + leaderboard."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from metrics import Metrics


def write_trades_csv(trades, path: Path) -> None:
    if not trades:
        pd.DataFrame().to_csv(path, index=False)
        return
    rows = []
    for t in trades:
        rows.append({
            "symbol": t.symbol, "timeframe": t.timeframe,
            "direction": "long" if t.direction == 1 else "short",
            "mode": t.mode,
            "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
            "entry": t.entry, "sl": t.sl, "tp": t.tp,
            "exit_price": t.exit_price, "exit_reason": t.exit_reason,
            "score": t.score, "cnt": t.cnt, "src": t.src_text,
            "risk": t.risk, "r": t.r_multiple, "pnl_pct": t.pnl_pct,
            "bars_held": t.bars_held,
        })
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)


def plot_equity(equity: pd.Series, title: str, path: Path) -> None:
    if equity.empty:
        return
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(equity.index, equity.values, color="#2962ff")
    ax.set_title(title)
    ax.set_ylabel("Equity (R)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def metrics_to_dict(m: Metrics, prefix: str = "") -> dict:
    return {
        f"{prefix}n_trades": m.n_trades,
        f"{prefix}win_rate": round(m.win_rate, 4),
        f"{prefix}profit_factor": round(m.profit_factor, 3) if m.profit_factor != float("inf") else 999.0,
        f"{prefix}expectancy_r": round(m.expectancy_r, 4),
        f"{prefix}avg_win_r": round(m.avg_win_r, 4),
        f"{prefix}avg_loss_r": round(m.avg_loss_r, 4),
        f"{prefix}total_r": round(m.total_r, 3),
        f"{prefix}max_dd_r": round(m.max_dd_r, 3),
        f"{prefix}max_dd_pct": round(m.max_dd_pct_of_peak, 4),
        f"{prefix}max_consec_l": m.max_consec_losses,
        f"{prefix}cagr_r": round(m.cagr_r_per_year, 3),
        f"{prefix}exposure": round(m.exposure_bars / max(1, m.total_bars), 4),
        f"{prefix}sample_warn": m.sample_warning,
    }


def print_metrics(m: Metrics, label: str = "") -> None:
    print(f"\n--- {label} ---")
    print(f"  trades={m.n_trades}  win_rate={m.win_rate:.1%}  PF={m.profit_factor:.2f}")
    print(f"  expectancy={m.expectancy_r:+.3f}R  total={m.total_r:+.2f}R  CAGR={m.cagr_r_per_year:+.2f}R/yr")
    print(f"  avg_win={m.avg_win_r:+.2f}R  avg_loss={m.avg_loss_r:+.2f}R")
    print(f"  max_DD={m.max_dd_r:.2f}R ({m.max_dd_pct_of_peak:.1%})  max_consec_losses={m.max_consec_losses}")
    print(f"  exposure={m.exposure_bars}/{m.total_bars} bars ({m.exposure_bars / max(1, m.total_bars):.1%})")
    if m.by_mode:
        for k, v in m.by_mode.items():
            print(f"  mode[{k}]: n={v['n']} wr={v['win_rate']:.1%} PF={v['pf']:.2f} exp={v['expectancy_r']:+.3f}R")
    if m.by_dir:
        for k, v in m.by_dir.items():
            print(f"  dir[{k}]:  n={v['n']} wr={v['win_rate']:.1%} PF={v['pf']:.2f} exp={v['expectancy_r']:+.3f}R")
    if m.sample_warning:
        print("  ⚠️  표본 부족 (n<30)")


def leaderboard_row(run_id: str, cfg_snap: dict, is_m: Metrics, oos_m: Metrics) -> dict:
    row = {"run_id": run_id, **cfg_snap}
    row.pop("weights", None)
    row.update(metrics_to_dict(is_m, "IS_"))
    row.update(metrics_to_dict(oos_m, "OOS_"))
    # overfitting flag
    row["overfit_flag"] = (is_m.expectancy_r > 0 and oos_m.expectancy_r < 0)
    return row
