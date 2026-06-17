"""Unit tests for the structure module.

Run from the wcse_backtest/ directory:
    python3 -m pytest tests/ -v          (if pytest is installed)
    python3 tests/test_structure.py      (standalone)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# allow running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from structure import (
    StructureEngine,
    StructureState,
    compute_atr,
    last_classified,
    _is_pivot_high,
    _is_pivot_low,
    PT_HIGH,
    PT_LOW,
)
from confluence import _cluster


def _checks(label: str, results: list[tuple[str, bool]]) -> bool:
    all_ok = True
    print(f"\n--- {label} ---")
    for name, ok in results:
        status = "✓" if ok else "✗ FAIL"
        print(f"  {status}  {name}")
        if not ok:
            all_ok = False
    return all_ok


def test_pivot_detection() -> bool:
    """Pivot at center with strictly lower neighbours both sides."""
    highs = np.array([1, 2, 3, 4, 5, 4, 3, 2, 1, 0, 1, 2, 3, 4, 5, 4, 3], dtype=float)
    # at i=12 (i.e. center=4 with left=4,right=8), wait simpler:
    # use small left=right=3 — center is highs[i-3]; pivot at index 4 (value 5)
    # is_pivot_high(highs, i=7, 3, 3) → center=4, value=5, neighbours 1..6 all <5 ✓
    return _checks("pivot detection", [
        ("is_pivot_high at peak", _is_pivot_high(highs, 7, 3, 3) is True),
        ("not pivot on slope", _is_pivot_high(highs, 5, 3, 3) is False),
        ("is_pivot_low at trough", _is_pivot_low(np.array([5, 4, 3, 2, 1, 2, 3, 4, 5], dtype=float), 7, 3, 3) is True),
    ])


def test_ith_classification() -> bool:
    """Synthetic zigzag → known ITH/ITL labels."""
    # Build a series with clear swings. swLen=3 (smaller for tests)
    # peaks at high values, troughs at low values. We want sequence H1 L1 H2 L2 H3 L3 ...
    # then 3-pivot rule: middle of last 3 highs must be higher than both flanks → ITH.
    n = 100
    rng = np.linspace(0, 2 * np.pi * 4, n)
    base = np.sin(rng) * 10
    # Make the middle peak larger so middle high is the ITH.
    closes = base.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    timestamps = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")

    eng = StructureEngine(sw_len=3, max_piv=80)
    for i in range(n):
        eng.update(i, highs, lows, closes, timestamps)

    has_ith = any(p.ptype == PT_HIGH and p.classified for p in eng.state.pivots)
    has_itl = any(p.ptype == PT_LOW and p.classified for p in eng.state.pivots)
    return _checks("ITH/ITL classification", [
        ("found at least one ITH", has_ith),
        ("found at least one ITL", has_itl),
    ])


def test_atr_warmup() -> bool:
    closes = np.array([100, 101, 102, 99, 98, 100, 101, 103, 102, 100, 99, 98, 97, 96, 99], dtype=float)
    highs = closes + 1.0
    lows = closes - 1.0
    atr = compute_atr(highs, lows, closes, length=14)
    return _checks("ATR(14) warmup", [
        ("len matches input", len(atr) == len(closes)),
        ("nan before bar 13", np.isnan(atr[12])),
        ("finite at bar 13", np.isfinite(atr[13])),
        ("positive at bar 14", atr[14] > 0),
    ])


def test_cluster_basic() -> bool:
    """Cluster: anchor with cnt>=2 must include only same-or-higher prices around it."""
    prices = [100.0, 100.5, 101.0, 150.0, 150.2]
    weights = [1.0, 1.0, 1.0, 1.0, 1.0]
    srcs = [1, 1, 1, 1, 1]
    # tol=1 → 100 group has cnt=3 (100, 100.5, 101); 150 group has cnt=2.
    # isAnchor: lowest in cluster. 100 → anchor; 100.5/101 → not (100<them); 150 → anchor.
    clusters = _cluster(prices, weights, srcs, tol=1.0, min_conf=2)
    # Two anchors → two clusters
    return _checks("cluster anchor + min_conf", [
        ("two clusters", len(clusters) == 2),
        ("first cluster mid ~ 100.5", clusters and abs(clusters[0].mid - (100 + 100.5 + 101) / 3.0) < 0.01),
    ])


def test_no_lookahead() -> bool:
    """Pivot detected at bar i lives at bar i-swLen — never references future."""
    n = 50
    rng = np.random.default_rng(42)
    closes = 100 + rng.standard_normal(n).cumsum()
    highs = closes + 1; lows = closes - 1
    ts = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    eng = StructureEngine(sw_len=8, max_piv=80)
    for i in range(n):
        eng.update(i, highs, lows, closes, ts)
        # every pivot in state must have bar <= i - swLen
        for p in eng.state.pivots:
            if p.bar > i - eng.sw_len:
                return _checks("no look-ahead", [("pivot.bar <= i-swLen", False)])
    return _checks("no look-ahead", [("all pivots respect swLen delay", True)])


if __name__ == "__main__":
    results = [
        test_pivot_detection(),
        test_ith_classification(),
        test_atr_warmup(),
        test_cluster_basic(),
        test_no_lookahead(),
    ]
    if all(results):
        print("\n=== ALL TESTS PASSED ===")
        sys.exit(0)
    else:
        print("\n=== SOME TESTS FAILED ===")
        sys.exit(1)
