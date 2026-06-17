"""Confluence engine — level sources + weighted clustering.

Direct port of WCSE_engine_v3_19.pine sections:
  - soft levels: wick + FVG, with consume on touch (line 338-381)
  - diagonal containing lines from ATH/ATL (line 541-568)
  - pitchfork median (line 584-601)
  - neckline (line 604-607)
  - Donchian channel (line 609-618)
  - dominant-leg Fibonacci + dualFib (line 640-677)
  - zone = prior classified pivots (line 700-708)
  - cluster: tol = atr * confTolA, isAnchor + cnt >= minConf (line 710-752)

Two clear phases per bar i:
  1. LevelStore.update(i, df, atr_i, cfg)       — accumulate persistent soft levels
  2. compute_confluence_at(i, df, state, atr_i, level_store, cfg) — gather all
     level sources at this instant and cluster them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from structure import (
    Pivot,
    StructureState,
    PT_HIGH,
    PT_LOW,
    last_classified,
)

# source codes (Pine srcTxt mapping + v4 additions)
SRC_WICK = 1
SRC_FIB = 2
SRC_NECK = 3
SRC_DON = 4
SRC_DIAG = 5
SRC_FVG = 6
SRC_FORK = 7
SRC_ZONE = 8
SRC_LIQ = 9
SRC_OB = 10   # v4: Order block (BOS-adjacent opposite-color candle)

SRC_NAMES = {
    SRC_WICK: "꼬리", SRC_FIB: "피보", SRC_NECK: "넥라인", SRC_DON: "돌파선",
    SRC_DIAG: "빗각", SRC_FVG: "FVG", SRC_FORK: "포크", SRC_ZONE: "가격대",
    SRC_LIQ: "유동성", SRC_OB: "OB",
}


@dataclass
class SoftLevel:
    price: float
    wt: float
    active: bool
    src: int


@dataclass
class LevelStore:
    """Persistent wick + FVG + neckline levels.

    Mirrors Pine's `levels` array. Accumulated bar-by-bar. Consumed when the
    bar's range crosses the level.
    """
    levels: list[SoftLevel] = field(default_factory=list)

    def _add(self, price: float, wt: float, src: int, tol: float, max_levels: int = 200) -> None:
        if not np.isfinite(price) or price <= 0:
            return
        # merge if within tol*0.4 of existing active level
        merge_dist = tol * 0.4
        for l in self.levels:
            if l.active and abs(l.price - price) < merge_dist:
                l.wt += wt
                return
        self.levels.append(SoftLevel(price=price, wt=wt, active=True, src=src))
        if len(self.levels) > max_levels:
            self.levels.pop(0)

    def update(
        self,
        i: int,
        opens: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        atr_i: float,
        cfg: dict,
    ) -> None:
        """Pine wick/FVG accumulation + consume on bar i."""
        if not np.isfinite(atr_i) or atr_i <= 0:
            return
        tol = atr_i * cfg["confTolA"]
        wick_ratio = cfg["wickRatio"]
        w = cfg["weights"]

        o = opens[i]; h = highs[i]; l = lows[i]; c = closes[i]
        rng = h - l
        if rng > 0 and cfg.get("showWick", True):
            up_wick = (h - max(o, c)) / rng
            dn_wick = (min(o, c) - l) / rng
            if up_wick >= wick_ratio:
                self._add(max(o, c), w["wWick"], SRC_WICK, tol)
            if dn_wick >= wick_ratio:
                self._add(min(o, c), w["wWick"], SRC_WICK, tol)

        # FVG needs i >= 2
        if i >= 2 and cfg.get("showFVG", True):
            if l > highs[i - 2]:
                self._add(highs[i - 2], w["wFvg"], SRC_FVG, tol)
            if h < lows[i - 2]:
                self._add(lows[i - 2], w["wFvg"], SRC_FVG, tol)

        # consume on touch
        if cfg.get("consume", True):
            for ll in self.levels:
                if ll.active and h >= ll.price and l <= ll.price:
                    ll.active = False

    def active_in_range(self, center: float, half_width: float) -> list[SoftLevel]:
        return [l for l in self.levels if l.active and abs(l.price - center) <= half_width]


# ---------------------------------------------------------------------------
# Diagonal / pitchfork helpers
# ---------------------------------------------------------------------------

def _find_containing_line(state: StructureState, anchor_bar: int, anchor_p: float, ptype: int, buf: float) -> Optional[Pivot]:
    """Pine f_contain: find the most-recent pivot of `ptype` after anchor_bar such
    that the line from (anchor_bar, anchor_p) through it contains all other
    same-type pivots after anchor_bar.

    For ptype=1 (resistance): all same-type pivots must be <= line + buf.
    For ptype=-1 (support):   all same-type pivots must be >= line - buf.
    """
    cands = [p for p in state.pivots if p.bar > anchor_bar and p.ptype == ptype]
    if not cands:
        return None
    for cand in reversed(cands):
        dt = max(1, cand.bar - anchor_bar)
        slope = (cand.price - anchor_p) / dt
        ok = True
        for p in cands:
            lv = anchor_p + slope * (p.bar - anchor_bar)
            if ptype == PT_HIGH and p.price > lv + buf:
                ok = False; break
            if ptype == PT_LOW and p.price < lv - buf:
                ok = False; break
        if ok:
            return cand
    return None


def _first_classified_after(state: StructureState, ptype: int, after_bar: int) -> Optional[Pivot]:
    """Earliest classified pivot of `ptype` whose bar > after_bar."""
    for p in state.pivots:
        if p.ptype == ptype and p.classified >= 1 and p.bar > after_bar:
            return p
    return None


# ---------------------------------------------------------------------------
# Confluence computation at one bar
# ---------------------------------------------------------------------------

@dataclass
class Cluster:
    mid: float
    score: float        # wsum
    cnt: int
    src_text: str


def _src_text(srcs: set[int]) -> str:
    parts = []
    for s in [SRC_FIB, SRC_DIAG, SRC_FORK, SRC_ZONE, SRC_OB, SRC_NECK, SRC_DON, SRC_WICK, SRC_FVG, SRC_LIQ]:
        if s in srcs:
            parts.append(SRC_NAMES[s])
    return " ".join(parts)


def compute_confluence_at(
    i: int,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    state: StructureState,
    atr_i: float,
    level_store: LevelStore,
    cfg: dict,
    bull: bool,
    bear: bool,
) -> tuple[list[Cluster], dict]:
    """Compute all level candidates at bar i and cluster them.

    Returns (clusters, debug_info). bull/bear flags drive the dominant leg
    selection for Fibonacci.

    Important: every input that depends on future bars must NOT be referenced
    here. We only use bars [0..i] inclusive — i.e. arr[: i+1].
    """
    if not np.isfinite(atr_i) or atr_i <= 0:
        return [], {}
    w = cfg["weights"]
    tol = atr_i * cfg["confTolA"]
    min_conf = cfg["minConf"]
    near_band = cfg["nearBandATR"] * atr_i

    c_now = closes[i]
    info: dict = {}

    cand_prices: list[float] = []
    cand_w: list[float] = []
    cand_src: list[int] = []
    # also collect diag-only and fib mids for debugging
    diag_vals: list[tuple[float, float, int]] = []  # (price, weight, src)

    # ---- (1) soft levels from LevelStore (wick / FVG / neckline as added) ----
    horizon = atr_i * 25.0
    for l in level_store.levels:
        if l.active and abs(l.price - c_now) < horizon:
            cand_prices.append(l.price)
            cand_w.append(l.wt)
            cand_src.append(l.src)

    # ---- (2) neckline (active structural neckline persists each bar) ----
    if state.neckline is not None and np.isfinite(state.neckline):
        cand_prices.append(state.neckline)
        cand_w.append(w["wNeck"])
        cand_src.append(SRC_NECK)

    # ---- (3) Donchian (current bar's highest/lowest over donLen) ----
    don_len = cfg["donLen"]
    if i + 1 >= don_len:
        dH = float(np.max(highs[i + 1 - don_len: i + 1]))
        dL = float(np.min(lows[i + 1 - don_len: i + 1]))
        if np.isfinite(dH) and abs(dH - c_now) < horizon:
            cand_prices.append(dH); cand_w.append(w["wDon"]); cand_src.append(SRC_DON)
        if np.isfinite(dL) and abs(dL - c_now) < horizon:
            cand_prices.append(dL); cand_w.append(w["wDon"]); cand_src.append(SRC_DON)

    # ---- (4) Diagonal containing lines (resistance from ATH, support from earliest low) ----
    if state.ath_b is not None and state.atl_b is not None:
        last_ith_p, last_ith_b, _ = last_classified(state, PT_HIGH)
        last_itl_p, last_itl_b, _ = last_classified(state, PT_LOW)
        # resistance: ATH -> containing high
        r_pivot = _find_containing_line(state, state.ath_b, state.ath_p, PT_HIGH, tol)
        r_bar = r_pivot.bar if r_pivot is not None else last_ith_b
        r_pri = r_pivot.price if r_pivot is not None else last_ith_p
        if r_bar is not None and r_pri is not None and r_bar > state.ath_b:
            sR = (r_pri - state.ath_p) / max(1, r_bar - state.ath_b)
            vR = state.ath_p + sR * (i - state.ath_b)
            if np.isfinite(vR) and vR > 0 and abs(vR - c_now) <= near_band:
                cand_prices.append(vR); cand_w.append(w["wDiag"]); cand_src.append(SRC_DIAG)
                diag_vals.append((vR, w["wDiag"], SRC_DIAG))
        # support: earliest classified ITL after bar 0 -> containing low
        oldest_itl = _first_classified_after(state, PT_LOW, -1)
        sup_bar = oldest_itl.bar if oldest_itl is not None else state.atl_b
        sup_pri = oldest_itl.price if oldest_itl is not None else state.atl_p
        s_pivot = _find_containing_line(state, sup_bar, sup_pri, PT_LOW, tol) if sup_bar is not None else None
        s_bar = s_pivot.bar if s_pivot is not None else last_itl_b
        s_pri = s_pivot.price if s_pivot is not None else last_itl_p
        if sup_bar is not None and s_bar is not None and s_pri is not None and s_bar > sup_bar:
            sS = (s_pri - sup_pri) / max(1, s_bar - sup_bar)
            vS = sup_pri + sS * (i - sup_bar)
            if np.isfinite(vS) and vS > 0 and abs(vS - c_now) <= near_band:
                cand_prices.append(vS); cand_w.append(w["wDiag"]); cand_src.append(SRC_DIAG)
                diag_vals.append((vS, w["wDiag"], SRC_DIAG))

    # ---- (5) Pitchfork median line + tines + bands (v4 line 690-729) ----
    if state.ath_b is not None:
        f_low = _first_classified_after(state, PT_LOW, state.ath_b)
        if f_low is not None:
            f_hi = _first_classified_after(state, PT_HIGH, f_low.bar)
            if f_hi is not None:
                mx = (f_low.bar + f_hi.bar) / 2.0
                my = (f_low.price + f_hi.price) / 2.0
                if mx > state.ath_b:
                    msl = (my - state.ath_p) / max(1, mx - state.ath_b)
                    vM_now = state.ath_p + msl * (i - state.ath_b)
                    if np.isfinite(vM_now) and vM_now > 0 and abs(vM_now - c_now) < horizon:
                        cand_prices.append(vM_now); cand_w.append(w["wFork"]); cand_src.append(SRC_FORK)
                        diag_vals.append((vM_now, w["wFork"], SRC_FORK))
                    # tines: offsets from median at the two anchors (line 699-700)
                    off_low = f_low.price - (state.ath_p + msl * (f_low.bar - state.ath_b))
                    off_high = f_hi.price - (state.ath_p + msl * (f_hi.bar - state.ath_b))
                    for t_off in (off_low, off_high):
                        vT = vM_now + t_off
                        if np.isfinite(vT) and vT > 0 and abs(vT - c_now) <= near_band:
                            cand_prices.append(vT); cand_w.append(w["wFork"]); cand_src.append(SRC_FORK)
                            diag_vals.append((vT, w["wFork"], SRC_FORK))
                    # bands: 0.5 inner + 1.5/2.0 warning (line 717-729)
                    if cfg.get("forkBands", True):
                        for m in (0.5, 1.5, 2.0):
                            for t_off in (off_low, off_high):
                                vB = vM_now + m * t_off
                                if np.isfinite(vB) and vB > 0 and abs(vB - c_now) <= near_band:
                                    cand_prices.append(vB); cand_w.append(w["wFork"] * 0.6); cand_src.append(SRC_FORK)
                                    diag_vals.append((vB, w["wFork"] * 0.6, SRC_FORK))

    # ---- (5b) ATH fan — N lines from ATH through subsequent classified pivots (v4 line 657-674) ----
    fan_n = cfg.get("fanN", 4)
    if fan_n > 0 and state.ath_b is not None and state.ath_b < i:
        cnt_f = 0
        for p in state.pivots:
            if cnt_f >= fan_n:
                break
            if p.classified >= 1 and p.bar > state.ath_b:
                slope_f = (p.price - state.ath_p) / max(1, p.bar - state.ath_b)
                vF = state.ath_p + slope_f * (i - state.ath_b)
                if np.isfinite(vF) and vF > 0 and abs(vF - c_now) <= near_band:
                    cand_prices.append(vF); cand_w.append(w["wDiag"] * 0.8); cand_src.append(SRC_DIAG)
                    diag_vals.append((vF, w["wDiag"] * 0.8, SRC_DIAG))
                cnt_f += 1

    # ---- (6) Dominant-leg Fibonacci ----
    leg_dir, leg_low, leg_high, leg_ok = _dominant_leg(state, atr_i, cfg, bull, bear)
    if leg_ok:
        diff = leg_high - leg_low
        ext_mul = cfg["ext_multiples"]
        for m in ext_mul:
            py = leg_low + m * diff if leg_dir == 1 else leg_high - m * diff
            if np.isfinite(py) and py > 0 and abs(py - c_now) < horizon:
                cand_prices.append(py); cand_w.append(w["wFib"]); cand_src.append(SRC_FIB)

        # dualFib: alternate anchor = 2nd most recent opposite-type pivot
        if cfg.get("dualFib", True):
            alt_type = PT_LOW if leg_dir == 1 else PT_HIGH
            alt_pivot = _kth_recent_pivot(state, alt_type, 1)
            if alt_pivot is not None:
                alt_p = alt_pivot.price
                span2 = leg_high - alt_p if leg_dir == 1 else alt_p - leg_low
                if span2 > 0:
                    for m in ext_mul:
                        py2 = alt_p + m * span2 if leg_dir == 1 else alt_p - m * span2
                        if np.isfinite(py2) and py2 > 0 and abs(py2 - c_now) < horizon:
                            cand_prices.append(py2); cand_w.append(w["wFib"] * 0.7); cand_src.append(SRC_FIB)

    # ---- (7) Zone — classified pivots (prior ITH/ITL horizontals) ----
    inc_unc = cfg.get("includeUnclassifiedZone", False)
    unc_factor = cfg.get("uncZoneFactor", 0.5)
    for p in state.pivots:
        if not (np.isfinite(p.price) and p.price > 0):
            continue
        if abs(p.price - c_now) >= horizon:
            continue
        if p.classified >= 1:
            cand_prices.append(p.price); cand_w.append(w["wZone"]); cand_src.append(SRC_ZONE)
        elif inc_unc:
            cand_prices.append(p.price); cand_w.append(w["wZone"] * unc_factor); cand_src.append(SRC_ZONE)

    # ---- (7a) v4: LTH/LTL price levels (strongest horizontal S/R) ----
    w_lt = w.get("wLT", 0.0)
    if w_lt > 0:
        for lt_px in state.lt_levels:
            if np.isfinite(lt_px) and lt_px > 0 and abs(lt_px - c_now) < horizon:
                cand_prices.append(lt_px); cand_w.append(w_lt); cand_src.append(SRC_ZONE)

    # ---- (7b) Liquidity (BSL/SSL) — Pine line 911-926 lifted into confluence.
    # A pivot whose price is within `tol` of ≥1 other same-type pivot represents
    # a liquidity pool. Pine only logged this for the info panel; we promote it
    # to a real confluence level with weight wLiq.
    w_liq = w.get("wLiq", 0.0)
    if w_liq > 0 and len(state.pivots) > 1:
        prices_arr = [p.price for p in state.pivots]
        types_arr = [p.ptype for p in state.pivots]
        for i_p in range(len(state.pivots)):
            pv = prices_arr[i_p]
            ty = types_arr[i_p]
            if not (np.isfinite(pv) and pv > 0):
                continue
            if abs(pv - c_now) >= horizon:
                continue
            cnt_eq = 0
            for j_p in range(len(state.pivots)):
                if j_p == i_p:
                    continue
                if types_arr[j_p] == ty and abs(prices_arr[j_p] - pv) <= tol:
                    cnt_eq += 1
            if cnt_eq >= 1:
                # BSL (highs above close) or SSL (lows below close)
                if (ty == PT_HIGH and pv > c_now) or (ty == PT_LOW and pv < c_now):
                    cand_prices.append(pv); cand_w.append(w_liq); cand_src.append(SRC_LIQ)

    # ---- (8) Cluster ----
    clusters = _cluster(cand_prices, cand_w, cand_src, tol, min_conf)

    info["leg_dir"] = leg_dir
    info["leg_low"] = leg_low
    info["leg_high"] = leg_high
    info["leg_ok"] = leg_ok
    info["n_candidates"] = len(cand_prices)
    return clusters, info


def _kth_recent_pivot(state: StructureState, ptype: int, k: int) -> Optional[Pivot]:
    """k-th most-recent pivot of given type (k=0 latest, k=1 second latest)."""
    cnt = 0
    for i in range(len(state.pivots) - 1, -1, -1):
        if state.pivots[i].ptype == ptype:
            if cnt == k:
                return state.pivots[i]
            cnt += 1
    return None


def _dominant_leg(state: StructureState, atr_i: float, cfg: dict, bull: bool, bear: bool):
    """Pine line 626-637 — dominant leg between last classified ITH and ITL.

    Returns (dir, leg_low, leg_high, ok). Direction is +1 for bull leg
    (ITH after ITL), -1 for bear leg (ITL after ITH).
    """
    min_leg_atr = cfg["minLegATR"]
    max_leg_atr = cfg.get("maxLegATR", 0.0)

    ith_p, ith_b, _ = last_classified(state, PT_HIGH)
    itl_p, itl_b, _ = last_classified(state, PT_LOW)
    if ith_p is None or itl_p is None:
        return 0, np.nan, np.nan, False

    leg_low = itl_p
    leg_high = ith_p
    span = leg_high - leg_low
    if span <= 0:
        return 0, np.nan, np.nan, False
    if span < min_leg_atr * atr_i:
        return 0, np.nan, np.nan, False
    if max_leg_atr > 0 and span > max_leg_atr * atr_i:
        return 0, np.nan, np.nan, False

    leg_dir = 0
    if bull and ith_b > itl_b:
        leg_dir = 1
    elif bear and itl_b > ith_b:
        leg_dir = -1
    else:
        return 0, np.nan, np.nan, False
    return leg_dir, leg_low, leg_high, True


def _cluster(prices: list[float], weights: list[float], srcs: list[int], tol: float, min_conf: int) -> list[Cluster]:
    """Pine line 710-752 — for each candidate price, count others within tol.
    Accept only `isAnchor` clusters (no candidate within tol strictly below).
    """
    out: list[Cluster] = []
    n = len(prices)
    for i in range(n):
        cp = prices[i]
        cnt = 0
        wsum = 0.0
        pacc = 0.0
        is_anchor = True
        srcset: set[int] = set()
        for j in range(n):
            cj = prices[j]
            if abs(cj - cp) <= tol:
                cnt += 1
                wsum += weights[j]
                pacc += cj * weights[j]
                srcset.add(srcs[j])
                if cj < cp:
                    is_anchor = False
        if is_anchor and cnt >= min_conf and wsum > 0:
            mid = pacc / wsum
            out.append(Cluster(mid=mid, score=wsum, cnt=cnt, src_text=_src_text(srcset)))
    return out


if __name__ == "__main__":
    import yaml
    from data import fetch_ohlcv
    from structure import run_structure, last_classified

    cfg_all = yaml.safe_load(open("config.yaml"))
    cfg = cfg_all["pine_defaults"]

    df = fetch_ohlcv("binance", "BTC/USDT", "1d", "2022-01-01T00:00:00Z")
    eng, atr = run_structure(df, sw_len=cfg["swLen"], max_piv=cfg["maxPiv"], atr_len=cfg["atrLen"])

    # Now redo bar-by-bar to also build LevelStore + evaluate confluence at last bar
    from structure import StructureEngine
    eng2 = StructureEngine(sw_len=cfg["swLen"], max_piv=cfg["maxPiv"])
    store = LevelStore()
    opens = df["open"].to_numpy(); highs = df["high"].to_numpy()
    lows = df["low"].to_numpy(); closes = df["close"].to_numpy(); ts = df.index
    bull = bear = False
    for i in range(len(df)):
        eng2.update(i, highs, lows, closes, ts)
        if np.isfinite(atr[i]):
            store.update(i, opens, highs, lows, closes, atr[i], cfg)
    s = eng2.state
    ith_p, _, _ = last_classified(s, PT_HIGH)
    itl_p, _, _ = last_classified(s, PT_LOW)
    eq = (ith_p + itl_p) / 2.0 if (ith_p and itl_p) else np.nan
    c_last = float(closes[-1])
    bull = s.ms_dir > 0 or (ith_p is not None and c_last > ith_p)
    bear = s.ms_dir < 0 or (itl_p is not None and c_last < itl_p)
    clusters, info = compute_confluence_at(len(df) - 1, opens, highs, lows, closes, s, atr[-1], store, cfg, bull, bear)

    print(f"close={c_last:.2f}  atr={atr[-1]:.2f}  EQ={eq:.2f}  bull={bull}  bear={bear}")
    print(f"leg_dir={info['leg_dir']}  leg=[{info['leg_low']:.2f}, {info['leg_high']:.2f}]  ok={info['leg_ok']}")
    print(f"candidates={info['n_candidates']}  clusters={len(clusters)}")
    print(f"active soft levels: {sum(1 for l in store.levels if l.active)}/{len(store.levels)}")
    print("\nclusters near price (sorted by score):")
    near = sorted(clusters, key=lambda c: -c.score)[:12]
    for cl in near:
        side = "above" if cl.mid > c_last else "below"
        dist_atr = (cl.mid - c_last) / atr[-1]
        print(f"  {cl.mid:>10.2f}  score={cl.score:>4.1f}  cnt={cl.cnt}  {side}  ({dist_atr:+.2f} ATR)  {cl.src_text}")
