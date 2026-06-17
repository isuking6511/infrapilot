"""Entry point: MVP run + full grid sweep.

Usage:
  python3 run.py mvp          # quick BTC/USDT 1d sanity (default)
  python3 run.py grid         # full experiment matrix
  python3 run.py mvp 4h       # BTC/USDT 4h sanity
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

from data import fetch_ohlcv
from backtest import run_backtest, wf_split, split_timestamp
from metrics import compute_metrics, bars_per_year_for
from report import (
    write_trades_csv,
    plot_equity,
    print_metrics,
    leaderboard_row,
)


HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
REPORTS.mkdir(exist_ok=True)


def load_cfg() -> dict:
    return yaml.safe_load(open(HERE / "config.yaml"))


def merge_pine_with_weights(pine: dict, weight_preset: str, all_presets: dict) -> dict:
    cfg = {**pine}
    cfg["weights"] = all_presets[weight_preset]
    return cfg


def fetch_pair(exchange: str, symbol: str, timeframe: str, since: str, cache_dir: str) -> pd.DataFrame:
    return fetch_ohlcv(exchange, symbol, timeframe, since, cache_dir=cache_dir)


def _run_one(
    cfg_all: dict,
    symbol: str,
    timeframe: str,
    entry_mode: str,
    neely_gate: bool,
    vol_gate: bool,
    min_plan_score: float,
    weight_preset: str,
    label_extra: str = "",
    use_btc_bias: bool = False,
):
    data_cfg = cfg_all["data"]
    pine = dict(cfg_all["pine_defaults"])
    pine["minPlanScore"] = min_plan_score
    cfg = merge_pine_with_weights(pine, weight_preset, cfg_all["weight_presets"])

    df = fetch_pair(data_cfg["exchange"], symbol, timeframe, data_cfg["since"], data_cfg["cache_dir"])
    htf_df = fetch_pair(data_cfg["exchange"], symbol, pine["biasTF"], data_cfg["since"], data_cfg["cache_dir"])

    btc_htf_df = None
    apply_btc_bias = use_btc_bias and symbol != "BTC/USDT"   # BTC self-test bypasses
    if apply_btc_bias:
        btc_htf_df = fetch_pair(data_cfg["exchange"], "BTC/USDT", pine["biasTF"], data_cfg["since"], data_cfg["cache_dir"])

    safe_sym = symbol.replace("/", "")
    btc_tag = "_btcb" if apply_btc_bias else ""
    run_id = f"{safe_sym}_{timeframe}_{entry_mode}_neely-{'on' if neely_gate else 'off'}_vol-{'on' if vol_gate else 'off'}_mps{min_plan_score}_{weight_preset}{btc_tag}{label_extra}"

    res = run_backtest(
        df=df, htf_df=htf_df,
        symbol=symbol, timeframe=timeframe, cfg=cfg,
        entry_mode=entry_mode, neely_gate=neely_gate, vol_gate=vol_gate,
        fees_bps=cfg_all["fees"]["taker_bps"],
        warmup=100, run_id=run_id,
        btc_htf_df=btc_htf_df,
        use_btc_bias=apply_btc_bias,
    )

    # WF split
    split_ts = split_timestamp(df.index, cfg_all["walk_forward"]["oos_pct"])
    is_trades, oos_trades = wf_split(res.trades, split_ts)

    bars_year = bars_per_year_for(timeframe)
    n_total = len(df)
    n_is = int(n_total * (1.0 - cfg_all["walk_forward"]["oos_pct"]))
    n_oos = n_total - n_is
    is_equity = [0.0] + [sum(t.r_multiple for t in is_trades[:k]) for k in range(1, len(is_trades) + 1)]
    oos_equity = [0.0] + [sum(t.r_multiple for t in oos_trades[:k]) for k in range(1, len(oos_trades) + 1)]
    full_equity_r = res.equity_curve.values.tolist()
    is_m = compute_metrics(is_trades, is_equity, n_is, bars_year)
    oos_m = compute_metrics(oos_trades, oos_equity, n_oos, bars_year)
    full_m = compute_metrics(res.trades, full_equity_r, n_total, bars_year)

    return res, is_m, oos_m, full_m, split_ts


def cmd_mvp(cfg_all: dict, timeframe: str | None = None):
    mvp = cfg_all["mvp"]
    if timeframe:
        mvp = {**mvp, "timeframe": timeframe}
    print(f"=== MVP RUN ===")
    print(f"  symbol={mvp['symbol']}  TF={mvp['timeframe']}  mode={mvp['entry_mode']}")
    print(f"  neely_gate={mvp['neely_gate']}  vol_gate={mvp['vol_gate']}  minPlanScore={mvp['minPlanScore']}")

    res, is_m, oos_m, full_m, split_ts = _run_one(
        cfg_all,
        symbol=mvp["symbol"], timeframe=mvp["timeframe"],
        entry_mode=mvp["entry_mode"],
        neely_gate=(mvp["neely_gate"] == "on" or mvp["neely_gate"] is True),
        vol_gate=(mvp["vol_gate"] == "on" or mvp["vol_gate"] is True),
        min_plan_score=mvp["minPlanScore"],
        weight_preset=mvp["weight_preset"],
        label_extra="_mvp",
    )

    print_metrics(full_m, label="FULL")
    print_metrics(is_m, label=f"IN-SAMPLE (≤ {split_ts.date()})")
    print_metrics(oos_m, label=f"OUT-OF-SAMPLE (> {split_ts.date()})")

    if is_m.expectancy_r > 0 and oos_m.expectancy_r < 0:
        print("\n⚠️  과최적화 의심: IS expectancy>0 인데 OOS<0")

    # outputs
    trades_path = REPORTS / f"{res.run_id}_trades.csv"
    eq_path = REPORTS / f"{res.run_id}_equity.png"
    write_trades_csv(res.trades, trades_path)
    plot_equity(res.equity_curve, f"{res.run_id}  ({full_m.total_r:+.1f}R)", eq_path)
    print(f"\n  trades  → {trades_path}")
    print(f"  equity  → {eq_path}")


def cmd_grid(cfg_all: dict):
    grid = cfg_all["grid"]
    symbols = grid.get("symbols", cfg_all["data"]["symbols"])
    timeframes = grid.get("timeframes", cfg_all["data"]["timeframes"])
    rows = []
    print(f"=== GRID RUN ===")
    print(f"  symbols={symbols}  TFs={timeframes}")
    print(f"  entry_modes={grid['entry_modes']}  neely={grid['neely_gate']}  vol={grid['vol_gate']}")
    print(f"  minPlanScore={grid['minPlanScore']}  weight_presets={grid['weight_preset']}")

    total = (len(symbols) * len(timeframes) * len(grid["entry_modes"])
             * len(grid["neely_gate"]) * len(grid["vol_gate"])
             * len(grid["minPlanScore"]) * len(grid["weight_preset"])
             * len(grid.get("use_btc_bias", [False])))
    print(f"  total runs: {total}")
    done = 0

    btc_bias_grid = grid.get("use_btc_bias", [False])
    for sym in symbols:
        for tf in timeframes:
            for em in grid["entry_modes"]:
                for ng in grid["neely_gate"]:
                    for vg in grid["vol_gate"]:
                        for mps in grid["minPlanScore"]:
                            for wp in grid["weight_preset"]:
                                for btcb in btc_bias_grid:
                                    done += 1
                                    try:
                                        res, is_m, oos_m, full_m, _split = _run_one(
                                            cfg_all, sym, tf, em,
                                            neely_gate=(ng is True or str(ng).lower() == "on"),
                                            vol_gate=(vg is True or str(vg).lower() == "on"),
                                            min_plan_score=mps,
                                            weight_preset=wp,
                                            use_btc_bias=(btcb is True or str(btcb).lower() == "on"),
                                        )
                                    except Exception as e:
                                        print(f"  [{done}/{total}] ERROR {sym} {tf} {em} {ng} {vg} {mps} {wp} btcb={btcb}: {e}")
                                        continue
                                    row = leaderboard_row(res.run_id, res.cfg_snapshot, is_m, oos_m)
                                    row["use_btc_bias"] = bool(btcb is True or str(btcb).lower() == "on")
                                    rows.append(row)
                                    tag = "⚠️ " if row.get("overfit_flag") else ""
                                    print(f"  [{done}/{total}] {tag}{res.run_id}  IS={is_m.expectancy_r:+.2f}R(n={is_m.n_trades})  OOS={oos_m.expectancy_r:+.2f}R(n={oos_m.n_trades})  OOS_PF={oos_m.profit_factor:.2f}")

    if not rows:
        print("no completed runs")
        return
    lb = pd.DataFrame(rows)
    lb_path = REPORTS / "leaderboard.csv"
    lb.to_csv(lb_path, index=False)

    # rank by OOS expectancy & PF, with min trades threshold
    qual = lb[(lb["OOS_n_trades"] >= 30) & (lb["OOS_profit_factor"] >= 1.0)].copy()
    if qual.empty:
        qual = lb.copy()
        print("\n  (no runs cleared OOS≥30 trades + PF≥1; showing all)")
    qual["rank_score"] = qual["OOS_expectancy_r"] * (qual["OOS_profit_factor"].clip(0, 5))
    top = qual.sort_values("rank_score", ascending=False).head(10)
    cols_show = ["run_id", "OOS_n_trades", "OOS_win_rate", "OOS_profit_factor",
                 "OOS_expectancy_r", "OOS_total_r", "OOS_max_dd_pct",
                 "IS_expectancy_r", "overfit_flag"]
    print("\n=== TOP 10 OOS LEADERBOARD ===")
    print(top[cols_show].to_string(index=False))
    print(f"\n  full leaderboard → {lb_path}")


if __name__ == "__main__":
    cfg_all = load_cfg()
    mode = sys.argv[1] if len(sys.argv) > 1 else "mvp"
    if mode == "mvp":
        tf = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_mvp(cfg_all, tf)
    elif mode == "grid":
        cmd_grid(cfg_all)
    else:
        print(f"unknown mode: {mode}. use 'mvp' or 'grid'.")
        sys.exit(1)
