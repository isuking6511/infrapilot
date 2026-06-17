"""Performance metrics computed in R units (1R = riskPct of acctSize)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    expectancy_r: float        # average R per trade
    total_r: float
    max_dd_r: float            # peak-to-trough in R
    max_dd_pct_of_peak: float
    max_consec_losses: int
    cagr_r_per_year: float
    exposure_bars: int
    total_bars: int
    by_mode: dict
    by_dir: dict
    sample_warning: bool       # n_trades < 30


def _basic_counts(rs: list[float]) -> tuple[int, int, int, float, float]:
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    nw = len(wins); nl = len(losses)
    avg_w = float(np.mean(wins)) if wins else 0.0
    avg_l = float(np.mean(losses)) if losses else 0.0
    wr = nw / max(1, len(rs))
    return nw, nl, len(rs), wr, avg_w


def _equity_dd(equity_r: list[float]) -> tuple[float, float]:
    if not equity_r:
        return 0.0, 0.0
    arr = np.array(equity_r, dtype=float)
    peak = -1e18
    dd = 0.0
    dd_pct = 0.0
    for v in arr:
        if v > peak:
            peak = v
        cur_dd = peak - v
        if cur_dd > dd:
            dd = cur_dd
            if peak > 0:
                dd_pct = cur_dd / peak
    return float(dd), float(dd_pct)


def _max_consec_losses(rs: list[float]) -> int:
    m = c = 0
    for r in rs:
        if r <= 0:
            c += 1
            if c > m:
                m = c
        else:
            c = 0
    return m


def compute_metrics(trades, equity_r: list[float], total_bars: int, bars_per_year: float) -> Metrics:
    if not trades:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, total_bars, {}, {}, True)
    rs = [t.r_multiple for t in trades]
    nw, nl, n, wr, avg_w = _basic_counts(rs)
    losses = [r for r in rs if r <= 0]
    avg_l = float(np.mean(losses)) if losses else 0.0
    gross_w = sum(r for r in rs if r > 0)
    gross_l = -sum(r for r in rs if r < 0)
    pf = (gross_w / gross_l) if gross_l > 0 else float("inf") if gross_w > 0 else 0.0
    expectancy = float(np.mean(rs))
    total_r = float(np.sum(rs))
    dd, dd_pct = _equity_dd(equity_r)
    consec_l = _max_consec_losses(rs)

    # exposure: sum of bars_held / total_bars
    exposure_bars = sum(t.bars_held for t in trades)

    # CAGR in R/year (linear additive — equity is in R)
    years = total_bars / bars_per_year if bars_per_year > 0 else 0.0
    cagr = total_r / years if years > 0 else 0.0

    def _agg(group_trades):
        rs_g = [t.r_multiple for t in group_trades]
        if not rs_g:
            return None
        nw_g = sum(1 for r in rs_g if r > 0)
        gross_w_g = sum(r for r in rs_g if r > 0)
        gross_l_g = -sum(r for r in rs_g if r < 0)
        pf_g = (gross_w_g / gross_l_g) if gross_l_g > 0 else float("inf") if gross_w_g > 0 else 0.0
        return {
            "n": len(rs_g),
            "win_rate": nw_g / len(rs_g),
            "expectancy_r": float(np.mean(rs_g)),
            "pf": pf_g,
            "total_r": float(np.sum(rs_g)),
        }

    by_mode = {m: _agg([t for t in trades if t.mode == m]) for m in {t.mode for t in trades}}
    by_dir = {("long" if d == 1 else "short"): _agg([t for t in trades if t.direction == d])
              for d in {t.direction for t in trades}}

    return Metrics(
        n_trades=n, n_wins=nw, n_losses=nl,
        win_rate=wr, avg_win_r=avg_w, avg_loss_r=avg_l,
        profit_factor=pf, expectancy_r=expectancy,
        total_r=total_r,
        max_dd_r=dd, max_dd_pct_of_peak=dd_pct,
        max_consec_losses=consec_l,
        cagr_r_per_year=cagr,
        exposure_bars=exposure_bars, total_bars=total_bars,
        by_mode={k: v for k, v in by_mode.items() if v is not None},
        by_dir={k: v for k, v in by_dir.items() if v is not None},
        sample_warning=n < 30,
    )


def bars_per_year_for(timeframe: str) -> float:
    table = {
        "1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040, "30m": 17520,
        "1h": 8760, "2h": 4380, "4h": 2190, "6h": 1460, "12h": 730,
        "1d": 365, "1w": 52,
    }
    return float(table.get(timeframe, 365))
