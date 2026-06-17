"""risk.position_size 검증 — v8 사이징 1:1 + 0나누기 방어."""
from __future__ import annotations

import math
import random

from infrapilot.analysis_core.models import Setup
from infrapilot.analysis_core.risk import position_size


def _setup(entry, stop):
    return Setup(direction=1 if stop < entry else -1, entry=entry, stop=stop,
                 target=entry, rr=1.0, score=5.0)


def test_position_size_known():
    """알려진 입력 → v8 공식 수량."""
    # equity 10000, risk 1% → 100 risk dollars; |100-98|=2 → 50 units
    assert position_size(_setup(100.0, 98.0), 10000.0, 1.0) == 50.0
    # risk 2% → 100 units
    assert position_size(_setup(100.0, 98.0), 10000.0, 2.0) == 100.0
    # 숏: |100-103|=3 → 10000*1/100/3
    assert abs(position_size(_setup(100.0, 103.0), 10000.0, 1.0) - (100.0 / 3.0)) < 1e-12


def test_position_size_matches_v8_formula():
    """랜덤 입력 → v8 인라인 공식과 1:1."""
    rng = random.Random(7)
    for _ in range(500):
        entry = rng.uniform(1, 1000)
        stop = entry + rng.choice([-1, 1]) * rng.uniform(0.01, 50)
        eq = rng.uniform(100, 1_000_000)
        rp = rng.uniform(0.1, 5.0)
        expect = eq * rp / 100.0 / abs(entry - stop)
        assert abs(position_size(_setup(entry, stop), eq, rp) - expect) < 1e-9


def test_position_size_zero_division_defense():
    """손절폭 0/비정상 → 0.0 (0나누기 방지)."""
    assert position_size(_setup(100.0, 100.0), 10000.0, 1.0) == 0.0          # stop==entry
    assert position_size(_setup(100.0, float("nan")), 10000.0, 1.0) == 0.0   # NaN
    assert position_size(_setup(float("inf"), 98.0), 10000.0, 1.0) == 0.0    # inf


def test_position_size_bad_equity_or_risk():
    """equity·risk_pct 비정상/비양수 → 0.0."""
    assert position_size(_setup(100.0, 98.0), 0.0, 1.0) == 0.0
    assert position_size(_setup(100.0, 98.0), -5.0, 1.0) == 0.0
    assert position_size(_setup(100.0, 98.0), 10000.0, 0.0) == 0.0
    assert position_size(_setup(100.0, 98.0), float("nan"), 1.0) == 0.0
