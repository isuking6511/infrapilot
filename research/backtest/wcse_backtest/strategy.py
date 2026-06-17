"""Signal generation: bias + sticky reversion setup + breakout setup.

`generate_signal` is invoked at the close of bar i and produces a Signal that
the backtest engine will try to fill on bar i+1 (no look-ahead).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from structure import StructureState, last_classified, PT_HIGH, PT_LOW
from confluence import Cluster
from neely import neely_gate_ok


# ---------------------------------------------------------------------------
# Bias computation (Pine line 620-621)
# ---------------------------------------------------------------------------

def compute_bias(state: StructureState, close_now: float, htf_bull: bool, htf_bear: bool, req_align: bool) -> tuple[bool, bool]:
    """Local bias (msDir / last broken IT) AND optional HTF alignment."""
    ith_p, _, _ = last_classified(state, PT_HIGH)
    itl_p, _, _ = last_classified(state, PT_LOW)
    local_bull = state.ms_dir > 0 or (ith_p is not None and close_now > ith_p)
    local_bear = state.ms_dir < 0 or (itl_p is not None and close_now < itl_p)
    bull = local_bull and (not req_align or htf_bull)
    bear = local_bear and (not req_align or htf_bear)
    return bull, bear


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    mode: str          # 'reversion' or 'breakout'
    direction: int     # +1 long, -1 short
    entry: float
    sl: float
    tp: float
    score: float       # cluster wsum, or NaN for breakout
    cnt: int           # cluster members, or 0 for breakout
    src_text: str
    signal_bar: int    # bar at whose close we generated this signal
    rr: float
    size_mult: float = 1.0   # 0.5 for counter-HTF breakouts (optional)


# ---------------------------------------------------------------------------
# Reversion setup (Pine port, sticky)
# ---------------------------------------------------------------------------

def _build_reversion(
    i: int,
    close_now: float,
    state: StructureState,
    atr_i: float,
    clusters: list[Cluster],
    direction: int,
    cfg: dict,
) -> Optional[Signal]:
    """Pine line 766-816 — select best cluster on far side and build entry/SL/TP."""
    if direction == 0 or not clusters:
        return None
    min_plan_score = cfg["minPlanScore"]
    max_dist = atr_i * 10.0  # Pine `near = math.abs(midc - close) <= atr*10`

    best: Optional[Cluster] = None
    best_score = 0.0
    for cl in clusters:
        # okSide: short needs cluster above close; long needs below
        ok_side = (direction == -1 and cl.mid > close_now) or (direction == 1 and cl.mid < close_now)
        near = abs(cl.mid - close_now) <= max_dist
        if ok_side and near and cl.score >= min_plan_score and cl.score > best_score:
            best = cl
            best_score = cl.score

    if best is None:
        return None

    entry = best.mid
    cap_d = cfg["maxStopATR"] * atr_i

    # protective swing: nearest pivot on stop-side within cap_d
    prot: Optional[float] = None
    for p in state.pivots:
        if direction == -1 and p.ptype == PT_HIGH and p.price > entry and (p.price - entry) <= cap_d:
            if prot is None or p.price < prot:
                prot = p.price
        if direction == 1 and p.ptype == PT_LOW and p.price < entry and (entry - p.price) <= cap_d:
            if prot is None or p.price > prot:
                prot = p.price
    # v4: rebalance pivots can act as nearer protective levels (ICT line 947-950)
    if cfg.get("showReb", True):
        if direction == -1 and state.reb_ith is not None and state.reb_ith > entry and (state.reb_ith - entry) <= cap_d:
            if prot is None or state.reb_ith < prot:
                prot = state.reb_ith
        if direction == 1 and state.reb_itl is not None and state.reb_itl < entry and (entry - state.reb_itl) <= cap_d:
            if prot is None or state.reb_itl > prot:
                prot = state.reb_itl

    sl_buf = cfg["slBufATR"] * atr_i
    stop_n = cfg["stopN"]
    if prot is not None:
        sl_struct = prot + sl_buf if direction == -1 else prot - sl_buf
    else:
        sl_struct = entry + stop_n * atr_i if direction == -1 else entry - stop_n * atr_i

    min_stop = 0.5 * atr_i
    # clamp into [entry+min_stop, entry+cap_d] (or symmetric for longs)
    if direction == -1:
        slv = max(entry + min_stop, min(sl_struct, entry + cap_d))
    else:
        slv = min(entry - min_stop, max(sl_struct, entry - cap_d))
        # ensure positive
        if slv <= 0:
            slv = max(entry * 0.01, entry - cap_d)

    rsk = abs(entry - slv)
    if rsk <= 0:
        return None
    tpv = entry - rsk * cfg["rrMin"] if direction == -1 else entry + rsk * cfg["rrMin"]

    # next confluence beyond entry+risk — closest wins (Pine line 799-809)
    best_t: Optional[Cluster] = None
    best_td = float("inf")
    for cl in clusters:
        on_target = (direction == -1 and cl.mid < entry - rsk) or (direction == 1 and cl.mid > entry + rsk)
        if on_target:
            d = abs(cl.mid - entry)
            if d < best_td:
                best_td = d
                best_t = cl
    if best_t is not None:
        tpv = best_t.mid

    if tpv <= 0:
        return None
    rr = abs(tpv - entry) / max(rsk, 1e-9)

    return Signal(
        mode="reversion",
        direction=direction,
        entry=entry,
        sl=slv,
        tp=tpv,
        score=best.score,
        cnt=best.cnt,
        src_text=best.src_text,
        signal_bar=i,
        rr=rr,
    )


# ---------------------------------------------------------------------------
# Breakout setup (new — Turtle/LW variant Pine signals but never uses for entry)
# ---------------------------------------------------------------------------

def _build_breakout(
    i: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    atr_i: float,
    cfg: dict,
    htf_bull: bool,
    htf_bear: bool,
    vol_gate: bool,
) -> Optional[Signal]:
    """Detect Donchian breakout at close of bar i. Entry = next bar open (handled
    by the backtest engine). Direction picked by which side broke.
    """
    don_len = cfg["donLen"]
    rng_k = cfg["rngK"]
    vol_ma_len = cfg["volMA"]
    vol_x = cfg["volSpike_x"]
    if i < max(don_len, vol_ma_len):
        return None

    don_hi = float(np.max(highs[i - don_len: i]))     # [i-don_len .. i-1] → like Pine's highest()[1]
    don_lo = float(np.min(lows[i - don_len: i]))
    bar_range = highs[i] - lows[i]
    range_exp = bar_range > rng_k * atr_i
    vol_ma = float(np.mean(volumes[i + 1 - vol_ma_len: i + 1]))
    vol_spike = volumes[i] > vol_ma * vol_x

    if vol_gate and not vol_spike:
        return None

    brk_up = closes[i] > don_hi and range_exp
    brk_dn = closes[i] < don_lo and range_exp

    direction = 0
    if brk_up and not brk_dn:
        direction = 1
    elif brk_dn and not brk_up:
        direction = -1
    else:
        return None

    # Counter-HTF size discount (optional)
    size_mult = 1.0
    if direction == 1 and htf_bear:
        size_mult = 0.5
    if direction == -1 and htf_bull:
        size_mult = 0.5

    # Use close as the entry reference (actual fill = next bar open in backtest)
    entry_ref = closes[i]
    stop_n = cfg["stopN"]
    risk = stop_n * atr_i
    if direction == 1:
        sl = entry_ref - risk
        tp = entry_ref + cfg["rrMin"] * risk
    else:
        sl = entry_ref + risk
        tp = entry_ref - cfg["rrMin"] * risk
        if tp <= 0:
            return None

    return Signal(
        mode="breakout",
        direction=direction,
        entry=entry_ref,
        sl=sl,
        tp=tp,
        score=float("nan"),
        cnt=0,
        src_text=f"Donchian{don_len}+rngK{rng_k}" + ("+volSpike" if vol_gate else ""),
        signal_bar=i,
        rr=cfg["rrMin"],
        size_mult=size_mult,
    )


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

def generate_signal(
    i: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    state: StructureState,
    atr_i: float,
    clusters: list[Cluster],
    cfg: dict,
    htf_bull: bool,
    htf_bear: bool,
    entry_mode: str,
    neely_gate: bool,
    vol_gate: bool,
    btc_bull: Optional[bool] = None,
    btc_bear: Optional[bool] = None,
    use_btc_bias: bool = False,
) -> Optional[Signal]:
    """Try reversion then breakout based on entry_mode.

    entry_mode: 'reversion', 'breakout', or 'both'.

    Cross-asset gate (A): when use_btc_bias=True and BTC flags are provided,
    the alt direction must additionally align with BTC's 4h EMA50 bias.
    BTC self-test passes None for the BTC flags and bypasses this gate.
    """
    if not np.isfinite(atr_i) or atr_i <= 0:
        return None
    close_now = closes[i]

    # Neely directional gate (optional)
    if neely_gate and not neely_gate_ok(state, close_now):
        return None

    bull, bear = compute_bias(state, close_now, htf_bull, htf_bear, cfg.get("reqAlign", True))

    # (A) BTC cross-asset bias filter — alt must agree with BTC HTF
    if use_btc_bias and btc_bull is not None and btc_bear is not None:
        bull = bull and btc_bull
        bear = bear and btc_bear

    direction = 1 if bull else (-1 if bear else 0)

    # v4: cooldown — block reentry in the invalidated direction (line 918)
    if cfg.get("cooldownBars", 0) > 0 and direction != 0 and i <= state.cooldown_until_bar:
        if direction == state.last_inv_dir:
            return None

    if entry_mode in ("reversion", "both"):
        sig = _build_reversion(i, close_now, state, atr_i, clusters, direction, cfg)
        if sig is not None:
            return sig

    if entry_mode in ("breakout", "both"):
        sig = _build_breakout(i, opens, highs, lows, closes, volumes, atr_i, cfg, htf_bull, htf_bear, vol_gate)
        if sig is not None:
            return sig

    return None
