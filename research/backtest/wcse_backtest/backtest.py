"""Event-driven backtest loop with walk-forward split + grid runner.

Rules:
  - signals are generated at the close of bar i (full bar info)
  - fills happen at the OPEN of bar i+1 (no look-ahead)
  - SL/TP are checked intra-bar; conservative tie-break: SL wins if both hit
  - one position at a time per run (matches Pine's sticky setup)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd

from structure import StructureEngine, last_classified, PT_HIGH, PT_LOW, compute_atr
from confluence import LevelStore, compute_confluence_at
from strategy import compute_bias, generate_signal, Signal


@dataclass
class Trade:
    symbol: str
    timeframe: str
    direction: int
    mode: str
    signal_ts: pd.Timestamp
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry: float
    sl: float
    tp: float
    exit_price: float
    exit_reason: str       # 'tp', 'sl', 'flip', 'eod'
    score: float
    cnt: int
    src_text: str
    risk: float            # |entry - sl|
    r_multiple: float      # net PnL in R, post-fees
    pnl_pct: float
    bars_held: int


@dataclass
class RunResult:
    run_id: str
    cfg_snapshot: dict
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))


# ---------------------------------------------------------------------------
# HTF bias precompute
# ---------------------------------------------------------------------------

def build_htf_bias(ltf_index: pd.DatetimeIndex, htf_df: pd.DataFrame, htf_len: int) -> tuple[np.ndarray, np.ndarray]:
    """For each LTF timestamp, return (htf_bull, htf_bear) flags using the HTF
    bar that closed strictly before that timestamp (Pine `close[1]`)."""
    ema = htf_df["close"].ewm(span=htf_len, adjust=False).mean().to_numpy()
    closes = htf_df["close"].to_numpy()
    htf_ts = htf_df.index
    # For each LTF ts, find the index of the most-recently CLOSED HTF bar.
    # An HTF bar closes at its index + 1 period boundary, but we approximate
    # by "the HTF bar whose timestamp is <= ltf_ts" and then use the PREVIOUS
    # one to enforce close[1] semantics.
    pos = np.searchsorted(htf_ts.values, ltf_index.values, side="right") - 1  # last HTF bar with ts <= ltf_ts
    bull = np.zeros(len(ltf_index), dtype=bool)
    bear = np.zeros(len(ltf_index), dtype=bool)
    for i in range(len(ltf_index)):
        k = pos[i] - 1  # close[1] = the bar BEFORE the current/last-seen HTF bar
        if k >= 0 and k < len(htf_df):
            bull[i] = closes[k] > ema[k]
            bear[i] = closes[k] < ema[k]
    return bull, bear


# ---------------------------------------------------------------------------
# Single backtest run
# ---------------------------------------------------------------------------

def run_backtest(
    df: pd.DataFrame,
    htf_df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    cfg: dict,
    entry_mode: str = "reversion",
    neely_gate: bool = False,
    vol_gate: bool = True,
    fees_bps: float = 5.0,
    warmup: int = 100,
    run_id: str = "default",
    btc_htf_df: Optional[pd.DataFrame] = None,
    use_btc_bias: bool = False,
) -> RunResult:
    """Execute one configuration."""
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    ts = df.index
    N = len(df)

    atr = compute_atr(highs, lows, closes, cfg["atrLen"])
    htf_bull_arr, htf_bear_arr = build_htf_bias(ts, htf_df, cfg["htfLen"])
    if use_btc_bias and btc_htf_df is not None:
        btc_bull_arr, btc_bear_arr = build_htf_bias(ts, btc_htf_df, cfg["htfLen"])
    else:
        btc_bull_arr = btc_bear_arr = None

    eng = StructureEngine(sw_len=cfg["swLen"], max_piv=cfg["maxPiv"])
    store = LevelStore()

    trades: list[Trade] = []
    # equity tracked in R units (1 R = riskPct% of acctSize)
    equity_r = [0.0]
    equity_ts = [ts[0]]

    pending: Optional[Signal] = None    # waiting for fill
    open_pos: Optional[Signal] = None   # filled, awaiting exit
    open_entry_actual: float = 0.0      # actual fill price
    open_entry_ts: Optional[pd.Timestamp] = None
    open_entry_bar: int = -1

    fee_frac = fees_bps / 10_000.0  # per fill

    for i in range(N):
        bar_open = opens[i]; bar_high = highs[i]; bar_low = lows[i]; bar_close = closes[i]
        bar_ts = ts[i]

        # === A. Fill pending signal on this bar's open ===
        if pending is not None and open_pos is None:
            fill_ok = False
            fill_px = np.nan
            if pending.mode == "breakout":
                # Market on open
                fill_px = bar_open
                fill_ok = True
            else:  # reversion limit at entry
                if pending.direction == -1:
                    # short limit above market — fill if this bar trades up to entry
                    if bar_high >= pending.entry:
                        fill_px = max(pending.entry, bar_open)  # gap-through
                        fill_ok = True
                else:
                    if bar_low <= pending.entry:
                        fill_px = min(pending.entry, bar_open)
                        fill_ok = True

            if fill_ok:
                open_pos = pending
                open_entry_actual = fill_px
                open_entry_ts = bar_ts
                open_entry_bar = i
            pending = None  # consumed (filled or expired)

        # === B. Check exit on this bar's range (if position is open) ===
        if open_pos is not None:
            sl = open_pos.sl
            tp = open_pos.tp
            d = open_pos.direction
            hit_sl = (d == -1 and bar_high >= sl) or (d == 1 and bar_low <= sl)
            hit_tp = (d == -1 and bar_low <= tp) or (d == 1 and bar_high >= tp)
            # don't exit on the very same bar as entry unless gap-trigger
            same_bar_entry = (open_entry_bar == i)

            exit_px = np.nan
            exit_reason = ""

            if hit_sl and hit_tp:
                exit_px = sl
                exit_reason = "sl"
            elif hit_sl:
                exit_px = sl
                exit_reason = "sl"
            elif hit_tp:
                exit_px = tp
                exit_reason = "tp"

            # gap handling: if same-bar entry and gap-opened beyond SL/TP, use open
            if exit_reason and same_bar_entry:
                if exit_reason == "sl":
                    if d == -1 and bar_open >= sl: exit_px = bar_open
                    if d == 1 and bar_open <= sl: exit_px = bar_open
                if exit_reason == "tp":
                    if d == -1 and bar_open <= tp: exit_px = bar_open
                    if d == 1 and bar_open >= tp: exit_px = bar_open

            if exit_reason:
                _close_position(trades, symbol, timeframe, open_pos, open_entry_actual,
                                open_entry_ts, bar_ts, exit_px, exit_reason, fee_frac, i - open_entry_bar)
                # update equity in R
                r = trades[-1].r_multiple
                equity_r.append(equity_r[-1] + r)
                equity_ts.append(bar_ts)
                open_pos = None

        # === C. Update structure + levels using bar i (after entry/exit on this bar) ===
        cfg_for_struct = dict(cfg)
        cfg_for_struct["_atr_i"] = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        eng.update(i, highs, lows, closes, ts, opens=opens, level_store=store, cfg=cfg_for_struct)
        if np.isfinite(atr[i]):
            store.update(i, opens, highs, lows, closes, atr[i], cfg)

        # === D. Sticky-flip invalidation (Pine line 762-765 + v4 rebalance) ===
        if open_pos is not None and open_entry_bar >= 0:
            inv = False
            inv_reason = ""
            # ms_dir flip
            if (open_pos.direction == -1 and eng.state.ms_dir > 0) or \
               (open_pos.direction == 1 and eng.state.ms_dir < 0):
                inv = True
                inv_reason = "flip"
            # v4 rebalance pivot violated
            if not inv and cfg.get("showReb", True):
                s2 = eng.state
                if open_pos.direction == -1 and s2.reb_ith is not None and bar_close > s2.reb_ith:
                    inv = True; inv_reason = "reb"
                if open_pos.direction == 1 and s2.reb_itl is not None and bar_close < s2.reb_itl:
                    inv = True; inv_reason = "reb"
            if inv:
                _close_position(trades, symbol, timeframe, open_pos, open_entry_actual,
                                open_entry_ts, bar_ts, bar_close, inv_reason, fee_frac, i - open_entry_bar)
                r = trades[-1].r_multiple
                equity_r.append(equity_r[-1] + r)
                equity_ts.append(bar_ts)
                # v4: trigger cooldown — block same-direction reentry
                cd = cfg.get("cooldownBars", 0)
                if cd > 0:
                    eng.state.cooldown_until_bar = i + cd
                    eng.state.last_inv_dir = open_pos.direction
                open_pos = None

        # === E. Generate new signal at this bar's close (for next bar's fill) ===
        if open_pos is None and pending is None and i >= warmup and np.isfinite(atr[i]):
            htf_bull = bool(htf_bull_arr[i])
            htf_bear = bool(htf_bear_arr[i])
            bull, bear = compute_bias(eng.state, bar_close, htf_bull, htf_bear, cfg.get("reqAlign", True))
            clusters, _info = compute_confluence_at(
                i, opens, highs, lows, closes, eng.state, atr[i], store, cfg, bull, bear
            )
            btc_bull = bool(btc_bull_arr[i]) if btc_bull_arr is not None else None
            btc_bear = bool(btc_bear_arr[i]) if btc_bear_arr is not None else None
            sig = generate_signal(
                i, opens, highs, lows, closes, volumes,
                eng.state, atr[i], clusters, cfg,
                htf_bull, htf_bear,
                entry_mode=entry_mode,
                neely_gate=neely_gate,
                vol_gate=vol_gate,
                btc_bull=btc_bull,
                btc_bear=btc_bear,
                use_btc_bias=use_btc_bias,
            )
            if sig is not None:
                pending = sig

    # close any remaining position at last close ('eod')
    if open_pos is not None:
        _close_position(trades, symbol, timeframe, open_pos, open_entry_actual,
                        open_entry_ts, ts[-1], closes[-1], "eod", fee_frac, N - 1 - open_entry_bar)
        equity_r.append(equity_r[-1] + trades[-1].r_multiple)
        equity_ts.append(ts[-1])

    eq = pd.Series(equity_r, index=pd.DatetimeIndex(equity_ts))
    eq = eq[~eq.index.duplicated(keep="last")]

    return RunResult(
        run_id=run_id,
        cfg_snapshot={
            "symbol": symbol, "timeframe": timeframe,
            "entry_mode": entry_mode, "neely_gate": neely_gate, "vol_gate": vol_gate,
            "minPlanScore": cfg["minPlanScore"],
            "weights": cfg["weights"],
        },
        trades=trades,
        equity_curve=eq,
    )


def _close_position(trades, symbol, timeframe, sig: Signal,
                    entry_actual: float, entry_ts, exit_ts, exit_px: float,
                    reason: str, fee_frac: float, bars_held: int) -> None:
    risk = abs(entry_actual - sig.sl)
    if risk <= 0:
        return
    if sig.direction == 1:
        gross = exit_px - entry_actual
    else:
        gross = entry_actual - exit_px
    # subtract two-sided fees (taker on both fills)
    fees_value = fee_frac * (entry_actual + exit_px)
    net = gross - fees_value
    r = (net / risk) * sig.size_mult
    pnl_pct = (net / entry_actual) * 100.0 * sig.size_mult
    trades.append(Trade(
        symbol=symbol, timeframe=timeframe,
        direction=sig.direction, mode=sig.mode,
        signal_ts=None, entry_ts=entry_ts, exit_ts=exit_ts,
        entry=entry_actual, sl=sig.sl, tp=sig.tp,
        exit_price=exit_px, exit_reason=reason,
        score=sig.score, cnt=sig.cnt, src_text=sig.src_text,
        risk=risk, r_multiple=r, pnl_pct=pnl_pct, bars_held=bars_held,
    ))


# ---------------------------------------------------------------------------
# Walk-forward split helper
# ---------------------------------------------------------------------------

def wf_split(trades: list[Trade], split_ts: pd.Timestamp) -> tuple[list[Trade], list[Trade]]:
    is_t = [t for t in trades if t.entry_ts <= split_ts]
    oos_t = [t for t in trades if t.entry_ts > split_ts]
    return is_t, oos_t


def split_timestamp(index: pd.DatetimeIndex, oos_pct: float) -> pd.Timestamp:
    n = len(index)
    split_i = int(n * (1.0 - oos_pct))
    return index[split_i]
