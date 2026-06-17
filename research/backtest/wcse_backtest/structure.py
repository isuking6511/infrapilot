"""Structure module — pivots, ITH/ITL, BOS/CHoCH, neckline, ATH/ATL anchors.

Direct port of WCSE_engine_v3_19.pine sections M1 + BOS/CHoCH + macro anchors.

State machine driven bar-by-bar. Each bar advances state using only data
available at that bar's close — no look-ahead. Pine's ta.pivothigh(h, 8, 8)
confirms a pivot 8 bars *after* the pivot bar itself, so a pivot detected
at index i lives at bar index i-swLen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# pivot type constants (mirror Pine: 1=high, -1=low)
PT_HIGH = 1
PT_LOW = -1


@dataclass
class Pivot:
    price: float
    bar: int           # bar_index where the pivot lives
    ts: pd.Timestamp   # timestamp of that bar
    ptype: int         # 1=high, -1=low
    classified: int = 0  # pK in Pine: 1 if ITH/ITL


@dataclass
class FVG:
    """A fair-value gap (3-bar imbalance). Pine `type Fvg` (v4 line 448-452)."""
    top: float
    bot: float
    direction: int     # +1 = bullish gap (support below), -1 = bearish gap (resistance above)
    filled: bool = False
    bar_created: int = 0


@dataclass
class StructureState:
    """Running state of the structure engine — mirrors Pine's `var` globals."""
    pivots: list[Pivot] = field(default_factory=list)
    cur_ith: Optional[float] = None    # last classified ITH not yet broken
    cur_itl: Optional[float] = None
    ms_dir: int = 0                    # market-structure dir: 1 up, -1 down
    neckline: Optional[float] = None
    neck_dir: int = 0
    trend_dir: int = 0
    trend_start_ts: Optional[pd.Timestamp] = None

    # macro anchors (full history)
    ath_p: Optional[float] = None
    ath_b: Optional[int] = None
    ath_ts: Optional[pd.Timestamp] = None
    atl_p: Optional[float] = None
    atl_b: Optional[int] = None
    atl_ts: Optional[pd.Timestamp] = None
    # pkLow (lowest after ATH) + pkHi (highest after pkLow) — for pitchfork
    pk_low_p: Optional[float] = None
    pk_low_b: Optional[int] = None
    pk_low_ts: Optional[pd.Timestamp] = None
    pk_hi_p: Optional[float] = None
    pk_hi_b: Optional[int] = None
    pk_hi_ts: Optional[pd.Timestamp] = None

    # event flags for current bar (transient, reset each step)
    bos_event: int = 0         # 1 = BOS up, -1 = BOS down, 2 = CHoCH up, -2 = CHoCH down
    new_pivot_high: bool = False
    new_pivot_low: bool = False

    # --- v4 ICT additions ---
    # classified-pivot histories for LTH/LTL 3-tier classification
    ith_history: list[tuple[float, int]] = field(default_factory=list)   # (price, bar) of classified ITHs (max 12)
    itl_history: list[tuple[float, int]] = field(default_factory=list)
    last_conf_ith: Optional[float] = None    # most recent confirmed ITH (for SD target)
    last_conf_itl: Optional[float] = None

    # LTH/LTL price levels (price only — for confluence as wLT weight)
    lt_levels: list[float] = field(default_factory=list)

    # FVG tracking
    fvgs: list[FVG] = field(default_factory=list)

    # rebalance pivots — formed when FVG is filled by price
    reb_ith: Optional[float] = None
    reb_ith_bar: Optional[int] = None
    reb_itl: Optional[float] = None
    reb_itl_bar: Optional[int] = None

    # cooldown — set when sticky setup invalidated by structure flip / rebalance break
    cooldown_until_bar: int = -1
    last_inv_dir: int = 0


def _is_pivot_high(highs: np.ndarray, i: int, left: int, right: int) -> bool:
    """True if highs[i-right] is strictly greater than its `left` + `right` neighbours."""
    center = i - right
    if center - left < 0 or i >= len(highs):
        return False
    pivot_val = highs[center]
    for j in range(center - left, center + right + 1):
        if j == center:
            continue
        if highs[j] >= pivot_val:
            return False
    return True


def _is_pivot_low(lows: np.ndarray, i: int, left: int, right: int) -> bool:
    center = i - right
    if center - left < 0 or i >= len(lows):
        return False
    pivot_val = lows[center]
    for j in range(center - left, center + right + 1):
        if j == center:
            continue
        if lows[j] <= pivot_val:
            return False
    return True


def _push_pivot(state: StructureState, p: Pivot, max_piv: int) -> None:
    state.pivots.append(p)
    if len(state.pivots) > max_piv:
        state.pivots.pop(0)


def _recent_idx(state: StructureState, ptype: int, k: int) -> int:
    """Return list index of the k-th (0=latest) pivot of given type, or -1."""
    cnt = 0
    for i in range(len(state.pivots) - 1, -1, -1):
        if state.pivots[i].ptype == ptype:
            if cnt == k:
                return i
            cnt += 1
    return -1


def _classify_3pivot(state: StructureState, ptype: int) -> Optional[float]:
    """Pine: among the 3 most-recent pivots of `ptype`, classify middle one
    as ITH/ITL if it strictly exceeds both flanks. Returns the classified
    price or None.
    """
    ai = _recent_idx(state, ptype, 2)  # oldest
    bi = _recent_idx(state, ptype, 1)  # middle (candidate)
    ci = _recent_idx(state, ptype, 0)  # newest
    if ai < 0 or bi < 0 or ci < 0:
        return None
    pa = state.pivots[ai].price
    pb = state.pivots[bi].price
    pc = state.pivots[ci].price
    if ptype == PT_HIGH and pa < pb and pc < pb:
        state.pivots[bi].classified = 1
        return pb
    if ptype == PT_LOW and pa > pb and pc > pb:
        state.pivots[bi].classified = 1
        return pb
    return None


def last_classified(state: StructureState, ptype: int) -> tuple[Optional[float], Optional[int], Optional[pd.Timestamp]]:
    """Return (price, bar, ts) of the most recent classified pivot of `ptype`."""
    for i in range(len(state.pivots) - 1, -1, -1):
        p = state.pivots[i]
        if p.ptype == ptype and p.classified >= 1:
            return p.price, p.bar, p.ts
    return None, None, None


def _push_history_and_check_lt(state: StructureState, price: float, bar: int, ptype: int) -> None:
    """Pine v4 line 249-263 / 291-305 — push classified ITH/ITL to history and
    classify LTH/LTL: if 3 consecutive classified ITHs (oldest → middle → newest)
    have middle strictly higher than flanks, the middle becomes LTH. LTL is the
    mirror condition on ITL history. Max history 12; LT levels capped at 10.
    """
    hist = state.ith_history if ptype == PT_HIGH else state.itl_history
    hist.append((price, bar))
    if len(hist) > 12:
        hist.pop(0)
    if len(hist) >= 3:
        a = hist[-3][0]
        m = hist[-2][0]
        c = hist[-1][0]
        is_lt = (m > a and m > c) if ptype == PT_HIGH else (m < a and m < c)
        if is_lt:
            state.lt_levels.append(m)
            if len(state.lt_levels) > 10:
                state.lt_levels.pop(0)


def _find_order_block_bullish(opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, i: int, lookback: int = 12) -> Optional[float]:
    """Pine v4 line 353-358 — at a bullish BOS, walk back from i-1 up to
    `lookback` bars; the FIRST bearish candle's LOW is the order block.
    Returns the low price (used as support level) or None.
    """
    for k in range(1, lookback + 1):
        idx = i - k
        if idx < 0:
            return None
        if closes[idx] < opens[idx]:
            return float(lows[idx])
    return None


def _find_order_block_bearish(opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, i: int, lookback: int = 12) -> Optional[float]:
    """Pine v4 line 347-352 — mirror: first bullish candle's HIGH within lookback."""
    for k in range(1, lookback + 1):
        idx = i - k
        if idx < 0:
            return None
        if closes[idx] > opens[idx]:
            return float(highs[idx])
    return None


@dataclass
class StructureEngine:
    """Stateful structure tracker. Feed bars one at a time via `update(i, df)`."""
    sw_len: int = 8
    max_piv: int = 80
    state: StructureState = field(default_factory=StructureState)

    def reset(self) -> None:
        self.state = StructureState()

    def update(
        self,
        i: int,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        timestamps: pd.DatetimeIndex,
        opens: Optional[np.ndarray] = None,
        level_store=None,         # type LevelStore — avoids circular import
        cfg: Optional[dict] = None,
    ) -> None:
        """Advance state after observing bar `i` (0-based index into the arrays).

        Pine bar-close order (v4):
          1. detect pivot high/low at bar (i - sw_len)
          2. classify ITH/ITL on the 3-pivot rule → push to history → LTH/LTL check
          3. BOS/CHoCH on close vs cur_ith / cur_itl → on event push OB + SD targets
          4. update macro anchors (ATH/ATL + pk_low/pk_hi)
          5. FVG tracking (form + fill detection → rebITH/rebITL + LT level)
        """
        s = self.state
        s.bos_event = 0
        s.new_pivot_high = False
        s.new_pivot_low = False

        # (1) + (2) pivot detection + 3-pivot classification (+ LTH/LTL on new classifications)
        if _is_pivot_high(highs, i, self.sw_len, self.sw_len):
            piv_bar = i - self.sw_len
            p = Pivot(price=highs[piv_bar], bar=piv_bar, ts=timestamps[piv_bar], ptype=PT_HIGH)
            _push_pivot(s, p, self.max_piv)
            s.new_pivot_high = True
            v = _classify_3pivot(s, PT_HIGH)
            if v is not None:
                s.cur_ith = v
                s.last_conf_ith = v
                _push_history_and_check_lt(s, v, piv_bar, PT_HIGH)
        if _is_pivot_low(lows, i, self.sw_len, self.sw_len):
            piv_bar = i - self.sw_len
            p = Pivot(price=lows[piv_bar], bar=piv_bar, ts=timestamps[piv_bar], ptype=PT_LOW)
            _push_pivot(s, p, self.max_piv)
            s.new_pivot_low = True
            v = _classify_3pivot(s, PT_LOW)
            if v is not None:
                s.cur_itl = v
                s.last_conf_itl = v
                _push_history_and_check_lt(s, v, piv_bar, PT_LOW)

        # (3) BOS / CHoCH on close of bar i
        c = closes[i]
        if s.cur_ith is not None and c > s.cur_ith:
            broken_ith = s.cur_ith
            choch = s.ms_dir < 0
            s.ms_dir = 1
            s.neckline = broken_ith
            s.neck_dir = 1
            if choch:
                s.trend_dir = 1
                s.trend_start_ts = timestamps[i]
                s.bos_event = 2
            else:
                s.bos_event = 1
            # (v4) Order block: nearest prior bearish candle (high pushed to store at wOB)
            # SD target: project from broken ITH using last confirmed leg
            if level_store is not None and cfg is not None and opens is not None:
                from confluence import SRC_OB, SRC_FIB
                w = cfg.get("weights", {})
                atr_i = cfg.get("_atr_i", 0.0)
                tol = atr_i * cfg.get("confTolA", 0.5)
                if cfg.get("showOB", True) and tol > 0:
                    ob_p = _find_order_block_bullish(opens, closes, highs, lows, i, lookback=12)
                    if ob_p is not None:
                        level_store._add(ob_p, w.get("wOB", 1.2), SRC_OB, tol)
                if cfg.get("showSD", True) and s.last_conf_itl is not None and tol > 0:
                    sw_len = broken_ith - s.last_conf_itl
                    if sw_len > 0:
                        level_store._add(broken_ith + 1.0 * sw_len, w.get("wFib", 1.5) * 0.8, SRC_FIB, tol)
                        level_store._add(broken_ith + 2.0 * sw_len, w.get("wFib", 1.5) * 0.8, SRC_FIB, tol)
                        level_store._add(broken_ith + 2.5 * sw_len, w.get("wFib", 1.5) * 0.6, SRC_FIB, tol)
            s.cur_ith = None
        if s.cur_itl is not None and c < s.cur_itl:
            broken_itl = s.cur_itl
            choch = s.ms_dir > 0
            s.ms_dir = -1
            s.neckline = broken_itl
            s.neck_dir = -1
            if choch:
                s.trend_dir = -1
                s.trend_start_ts = timestamps[i]
                s.bos_event = -2
            else:
                s.bos_event = -1
            if level_store is not None and cfg is not None and opens is not None:
                from confluence import SRC_OB, SRC_FIB
                w = cfg.get("weights", {})
                atr_i = cfg.get("_atr_i", 0.0)
                tol = atr_i * cfg.get("confTolA", 0.5)
                if cfg.get("showOB", True) and tol > 0:
                    ob_p = _find_order_block_bearish(opens, closes, highs, lows, i, lookback=12)
                    if ob_p is not None:
                        level_store._add(ob_p, w.get("wOB", 1.2), SRC_OB, tol)
                if cfg.get("showSD", True) and s.last_conf_ith is not None and tol > 0:
                    sw_len = s.last_conf_ith - broken_itl
                    if sw_len > 0:
                        sd1 = broken_itl - 1.0 * sw_len
                        sd2 = broken_itl - 2.0 * sw_len
                        sd25 = broken_itl - 2.5 * sw_len
                        if sd1 > 0:
                            level_store._add(sd1, w.get("wFib", 1.5) * 0.8, SRC_FIB, tol)
                        if sd2 > 0:
                            level_store._add(sd2, w.get("wFib", 1.5) * 0.8, SRC_FIB, tol)
                        if sd25 > 0:
                            level_store._add(sd25, w.get("wFib", 1.5) * 0.6, SRC_FIB, tol)
            s.cur_itl = None

        # (4) macro anchors
        h = highs[i]
        l = lows[i]
        if s.ath_p is None or h > s.ath_p:
            s.ath_p = h
            s.ath_b = i
            s.ath_ts = timestamps[i]
        if s.atl_p is None or l < s.atl_p:
            s.atl_p = l
            s.atl_b = i
            s.atl_ts = timestamps[i]
        if s.ath_b is not None and i > s.ath_b:
            if s.pk_low_p is None or l < s.pk_low_p:
                s.pk_low_p = l
                s.pk_low_b = i
                s.pk_low_ts = timestamps[i]
                s.pk_hi_p = None
                s.pk_hi_b = None
                s.pk_hi_ts = None
        if s.pk_low_b is not None and i > s.pk_low_b:
            if s.pk_hi_p is None or h > s.pk_hi_p:
                s.pk_hi_p = h
                s.pk_hi_b = i
                s.pk_hi_ts = timestamps[i]

        # (5) FVG tracking — form new gaps + check fills (v4 line 460-494)
        if cfg is not None and cfg.get("showFVG", True) and i >= 2:
            if l > highs[i - 2]:
                s.fvgs.append(FVG(top=l, bot=highs[i - 2], direction=1, filled=False, bar_created=i))
            if h < lows[i - 2]:
                s.fvgs.append(FVG(top=lows[i - 2], bot=h, direction=-1, filled=False, bar_created=i))
            if len(s.fvgs) > 40:
                s.fvgs.pop(0)
        if level_store is not None and cfg is not None and cfg.get("showReb", True):
            from confluence import SRC_ZONE
            w = cfg.get("weights", {})
            atr_i = cfg.get("_atr_i", 0.0)
            tol = atr_i * cfg.get("confTolA", 0.5)
            for f in s.fvgs:
                if f.filled:
                    continue
                if f.direction == -1 and h >= f.bot:    # bearish FVG filled by upmove → reb_ith
                    f.filled = True
                    s.reb_ith = h
                    s.reb_ith_bar = i
                    if tol > 0:
                        level_store._add(h, w.get("wLT", 1.8), SRC_ZONE, tol)
                if f.direction == 1 and l <= f.top:     # bullish FVG filled by downmove → reb_itl
                    f.filled = True
                    s.reb_itl = l
                    s.reb_itl_bar = i
                    if tol > 0:
                        level_store._add(l, w.get("wLT", 1.8), SRC_ZONE, tol)


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, length: int = 14) -> np.ndarray:
    """Wilder's ATR — matches Pine's ta.atr(14).

    Pine's ta.atr uses ta.rma (Wilder smoothing). We seed with the SMA of the
    first `length` TRs, then apply rma recursion.
    """
    n = len(closes)
    tr = np.full(n, np.nan)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        a = highs[i] - lows[i]
        b = abs(highs[i] - closes[i - 1])
        c = abs(lows[i] - closes[i - 1])
        tr[i] = max(a, b, c)
    atr = np.full(n, np.nan)
    if n < length:
        return atr
    seed = np.nanmean(tr[:length])
    atr[length - 1] = seed
    alpha = 1.0 / length
    for i in range(length, n):
        atr[i] = atr[i - 1] + alpha * (tr[i] - atr[i - 1])
    return atr


def run_structure(df: pd.DataFrame, sw_len: int = 8, max_piv: int = 80, atr_len: int = 14) -> tuple[StructureEngine, np.ndarray]:
    """Run the structure engine across `df` and also return the ATR series.
    The returned engine has been advanced through the LAST bar in df."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    ts = df.index
    atr = compute_atr(highs, lows, closes, atr_len)
    eng = StructureEngine(sw_len=sw_len, max_piv=max_piv)
    for i in range(len(df)):
        eng.update(i, highs, lows, closes, ts)
    return eng, atr


if __name__ == "__main__":
    from data import fetch_ohlcv
    df = fetch_ohlcv("binance", "BTC/USDT", "1d", "2022-01-01T00:00:00Z")
    eng, atr = run_structure(df, sw_len=8)
    s = eng.state
    print(f"pivots tracked: {len(s.pivots)}")
    print(f"ms_dir: {s.ms_dir}  trend_dir: {s.trend_dir}")
    print(f"cur_ITH: {s.cur_ith}  cur_ITL: {s.cur_itl}  neckline: {s.neckline}")
    ith_p, ith_b, ith_ts = last_classified(s, PT_HIGH)
    itl_p, itl_b, itl_ts = last_classified(s, PT_LOW)
    print(f"last classified ITH: {ith_p} at {ith_ts}")
    print(f"last classified ITL: {itl_p} at {itl_ts}")
    print(f"ATH: {s.ath_p} at {s.ath_ts}  ATL: {s.atl_p} at {s.atl_ts}")
    print(f"latest ATR(14): {atr[-1]:.2f}")
    # show last few pivots
    print("\nlast 10 pivots:")
    for p in s.pivots[-10:]:
        kind = "ITH" if (p.ptype == PT_HIGH and p.classified) else \
               "ITL" if (p.ptype == PT_LOW and p.classified) else \
               ("H" if p.ptype == PT_HIGH else "L")
        print(f"  {p.ts.date()}  bar={p.bar}  {kind}  px={p.price:.2f}")
