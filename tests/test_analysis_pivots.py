"""pivots.py 검증 — lookahead 없음(전체 vs 누적 일치) + 원본 1:1 대조.

왜 이 두 가지: analysis_core의 생명줄은 결정성과 포팅 충실성. 전자는 '봉 하나씩'
재현으로, 후자는 research 원본 엔진과의 산출 비교로 증명한다.
"""
from __future__ import annotations

import math
import random

from infrapilot.analysis_core.models import Candle, PT_HIGH, PT_LOW
from infrapilot.analysis_core.pivots import PivotEngine, detect_pivots


def _synthetic_candles(n: int = 400, seed: int = 7) -> list[Candle]:
    """합성 OHLCV. 시드 고정 → 데이터 자체는 결정적(테스트 입력 안정)."""
    rng = random.Random(seed)
    candles: list[Candle] = []
    price = 100.0
    ts = 1_700_000_000_000  # 고정 epoch ms 시작점
    for _ in range(n):
        drift = math.sin(len(candles) / 13.0) * 1.5      # 스윙 생성용 결정적 파동
        step = drift + rng.uniform(-1.0, 1.0)
        o = price
        c = price + step
        hi = max(o, c) + rng.uniform(0.0, 1.2)
        lo = min(o, c) - rng.uniform(0.0, 1.2)
        candles.append(Candle(ts=ts, open=o, high=hi, low=lo, close=c, volume=1.0))
        price = c
        ts += 900_000  # 15m
    return candles


def _key(pivots):
    """비교용 정규화 — (bar, ptype, price, classified) 튜플 리스트."""
    return [(p.bar, p.ptype, round(p.price, 8), p.classified) for p in pivots]


def test_pivots_no_lookahead_batch_equals_incremental():
    """전체 한번에 vs 봉 하나씩 누적 → 결과 동일(=lookahead 없음)."""
    candles = _synthetic_candles()

    # (a) 전체 한번에
    batch = detect_pivots(candles, sw_len=8)

    # (b) 봉을 하나씩만 '공개'하며 누적: 엔진은 매 시점 prefix만 본다.
    eng = PivotEngine(sw_len=8)
    for i in range(len(candles)):
        eng.update(i, candles[: i + 1])   # 미래 봉을 아예 넘기지 않음
    incremental = eng.pivots

    assert _key(batch) == _key(incremental)


def test_pivots_deterministic_repeat():
    """같은 입력 2회 → 완전히 동일한 출력."""
    candles = _synthetic_candles()
    assert _key(detect_pivots(candles)) == _key(detect_pivots(candles))


def test_pivot_confirmation_delay():
    """피벗은 좌우 swLen봉이 다 생긴 뒤에야 확정 — bar+swLen 이전엔 안 보인다."""
    candles = _synthetic_candles(n=120)
    sw = 8
    full = detect_pivots(candles, sw_len=sw)
    assert full, "합성 데이터에서 피벗이 하나는 나와야 함"
    first = min(full, key=lambda p: p.bar)
    # first.bar 봉의 피벗은 first.bar+sw 시점에야 확정 → 그 직전까지 공개하면 안 잡힘
    eng = PivotEngine(sw_len=sw)
    for i in range(first.bar + sw):          # i = first.bar+sw-1 까지만 공개
        eng.update(i, candles[: i + 1])
    assert all(p.bar != first.bar for p in eng.pivots)


def _v8_reference_pivots(candles, sw=8, max_piv=80):
    """정본 research/pine/wcse_v8/engine.py 의 피벗 블록(366-398)+confirm_it를 그대로
    복제한 참조 구현. v8은 피벗을 run() 내부 지역변수로 들고 있어 사후 추출이 안 되므로,
    검증용으로 동일 코드를 떼어 둔다. numpy로 v8과 같은 max/min·count 비교를 재현."""
    import numpy as np
    h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles])
    pivP, pivB, pivT, pivK = [], [], [], []

    def idx_recent(ty, k):
        cnt = 0
        for ii in range(len(pivT) - 1, -1, -1):
            if pivT[ii] == ty:
                if cnt == k:
                    return ii
                cnt += 1
        return -1

    def confirm_it(ty):
        ai, bi, ci = idx_recent(ty, 2), idx_recent(ty, 1), idx_recent(ty, 0)
        if ai < 0 or bi < 0 or ci < 0:
            return
        pa, pb_, pc = pivP[ai], pivP[bi], pivP[ci]
        if (pa < pb_ > pc) if ty == 1 else (pa > pb_ < pc):
            pivK[bi] = 1

    for i in range(len(candles)):
        j = i - sw
        if j >= sw:
            win_h = h[j - sw:i + 1]
            win_l = l[j - sw:i + 1]
            if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                pivP.append(h[j]); pivB.append(j); pivT.append(1); pivK.append(0); confirm_it(1)
            if l[j] == win_l.min() and (win_l == l[j]).sum() == 1:
                pivP.append(l[j]); pivB.append(j); pivT.append(-1); pivK.append(0); confirm_it(-1)
            while len(pivP) > max_piv:
                pivP.pop(0); pivB.pop(0); pivT.pop(0); pivK.pop(0)
    return [(pivB[i], pivT[i], round(float(pivP[i]), 8), pivK[i]) for i in range(len(pivP))]


def test_matches_wcse_v8_engine():
    """정본 v8 엔진 피벗/분류와 1:1 일치하는지 대조 (numpy 없으면 스킵)."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    candles = _synthetic_candles()
    assert _key(detect_pivots(candles, sw_len=8, max_piv=80)) == _v8_reference_pivots(candles)
