"""setup._reversion_v4 검증 — 골든 2종(섞지 않음).

1) 포팅 정확성: 동일 클러스터/상태 입력 → wcse_backtest/strategy.py _build_reversion과
   entry/stop/target/rr/score/src_text 1:1 (랜덤 시나리오로 손절·RR 경계까지).
2) 통합 동작: 우리 v8 confluence 클러스터 입력 → 에러 없이 Setup/None, 결정성·커버리지만
   (1:1 아님 — v8클러스터+v4진입은 운용문서 비재현).
"""
from __future__ import annotations

import math
import random

from infrapilot.analysis_core.models import LevelCluster, LevelSource, Pivot, PT_HIGH, PT_LOW
from infrapilot.analysis_core.ict import StructureState, StructureConfig, StructureEngine
from infrapilot.analysis_core.setup import (
    SetupConfig, make_setup, _reversion_v4, compute_bias, resolve_direction,
)


# ───────────────────── 골든 1: v4 _build_reversion 1:1 ─────────────────────

def _load_v4_build_reversion():
    """wcse_backtest/strategy.py _build_reversion + Cluster + structure.Pivot/State 로드."""
    import sys
    from pathlib import Path
    bt = Path(__file__).resolve().parents[1] / "research" / "backtest" / "wcse_backtest"
    if not bt.exists():
        return None
    sys.path.insert(0, str(bt))
    try:
        import strategy as v4strat          # type: ignore
        from confluence import Cluster       # type: ignore
        from structure import Pivot as V4Pivot, StructureState as V4State  # type: ignore
    except Exception:
        return None
    return v4strat, Cluster, V4Pivot, V4State


def _build_inputs(spec):
    """spec → (our clusters/state, v4 clusters/state). 동일 수치·src_text('피보')."""
    close, atr, direction, cl_specs, piv_specs, reb_ith, reb_itl, mps = spec
    v4 = _load_v4_build_reversion()
    _, Cluster, V4Pivot, V4State = v4

    our_clusters = [LevelCluster(mid=m, score=s, cnt=c, sources=frozenset({LevelSource.FIB}))
                    for (m, s, c) in cl_specs]
    v4_clusters = [Cluster(mid=m, score=s, cnt=c, src_text="피보") for (m, s, c) in cl_specs]

    our_state = StructureState()
    our_state.pivots = [Pivot(bar=i, ts=i, price=pr, ptype=pt, classified=1)
                        for i, (pt, pr) in enumerate(piv_specs)]
    our_state.reb_ith = reb_ith
    our_state.reb_itl = reb_itl

    v4_state = V4State()
    v4_state.pivots = [V4Pivot(price=pr, bar=i, ts=i, ptype=pt, classified=1)
                       for i, (pt, pr) in enumerate(piv_specs)]
    v4_state.reb_ith = reb_ith
    v4_state.reb_itl = reb_itl

    our_cfg = SetupConfig(min_plan_score=mps, rr_min=2.0, sl_buf_atr=0.5, stop_n=2.0,
                          max_stop_atr=4.0, near_dist_atr=10.0)
    v4_cfg = {"minPlanScore": mps, "rrMin": 2.0, "slBufATR": 0.5, "stopN": 2.0,
              "maxStopATR": 4.0, "showReb": True}
    return our_clusters, our_state, our_cfg, v4_clusters, v4_state, v4_cfg


def _rand_spec(rng):
    close = rng.uniform(50, 200)
    atr = rng.uniform(0.5, 5.0)
    direction = rng.choice([1, -1])
    cl_specs = []
    for _ in range(rng.randint(1, 6)):
        cl_specs.append((close + rng.uniform(-15, 15) * atr,
                         round(rng.uniform(0.5, 8.0), 3), rng.randint(1, 5)))
    piv_specs = []
    for _ in range(rng.randint(0, 6)):
        piv_specs.append((rng.choice([PT_HIGH, PT_LOW]), close + rng.uniform(-12, 12) * atr))
    reb_ith = close + rng.uniform(0, 10) * atr if rng.random() < 0.4 else None
    reb_itl = close - rng.uniform(0, 10) * atr if rng.random() < 0.4 else None
    mps = rng.choice([3.0, 4.0])
    return (close, atr, direction, cl_specs, piv_specs, reb_ith, reb_itl, mps)


def _norm(sig_or_setup, is_v4):
    if sig_or_setup is None:
        return None
    o = sig_or_setup
    return (o.direction, round(o.entry, 6),
            round(o.sl if is_v4 else o.stop, 6),
            round(o.tp if is_v4 else o.target, 6),
            round(o.rr, 6), round(o.score, 6), o.src_text)


def test_reversion_v4_matches_build_reversion():
    """랜덤 400 시나리오 + 경계 케이스: _reversion_v4 == _build_reversion (None 포함)."""
    v4 = _load_v4_build_reversion()
    if v4 is None:
        import pytest
        pytest.skip("wcse_backtest(strategy.py) 로드 불가")
    v4strat = v4[0]
    rng = random.Random(20260617)
    n_setup = 0
    for _ in range(400):
        spec = _rand_spec(rng)
        oc, os_, ocfg, vc, vs, vcfg = _build_inputs(spec)
        close, atr, direction = spec[0], spec[1], spec[2]
        mine = _reversion_v4(oc, os_, close, atr, direction, ocfg)
        ref = v4strat._build_reversion(0, close, vs, atr, vc, direction, vcfg)
        assert _norm(mine, False) == _norm(ref, True), f"불일치 spec={spec}"
        n_setup += mine is not None
    assert n_setup > 50, f"셋업 생성 케이스가 충분해야(손절/RR 경로 커버): {n_setup}"


def test_reversion_v4_boundary_cases():
    """경계: (a) 클러스터 목표 없음 → rr=rr_min, (b) 보호스윙 클램프(cap_d)."""
    v4 = _load_v4_build_reversion()
    if v4 is None:
        import pytest
        pytest.skip("wcse_backtest 로드 불가")
    v4strat = v4[0]
    # (a) 롱, 아래 클러스터 1개(목표 클러스터 없음) → tp = entry + rsk*rr_min
    specA = (100.0, 2.0, 1, [(96.0, 5.0, 2)], [], None, None, 3.0)
    oc, os_, ocfg, vc, vs, vcfg = _build_inputs(specA)
    mine = _reversion_v4(oc, os_, 100.0, 2.0, 1, ocfg)
    ref = v4strat._build_reversion(0, 100.0, vs, 2.0, vc, 1, vcfg)
    assert _norm(mine, False) == _norm(ref, True)
    assert mine is not None and abs(mine.rr - 2.0) < 1e-9, "목표 클러스터 없으면 rr=rr_min"
    # (b) 숏, 보호 피벗이 cap_d 밖 → stopN 폴백 후 클램프
    specB = (100.0, 2.0, -1, [(104.0, 5.0, 2)], [(PT_HIGH, 130.0)], None, None, 3.0)
    oc, os_, ocfg, vc, vs, vcfg = _build_inputs(specB)
    mine = _reversion_v4(oc, os_, 100.0, 2.0, -1, ocfg)
    ref = v4strat._build_reversion(0, 100.0, vs, 2.0, vc, -1, vcfg)
    assert _norm(mine, False) == _norm(ref, True)


# ───────────────────── 골든 2: v8 클러스터 통합 동작 ─────────────────────

def _choppy(n=1500, seed=2):
    rng = random.Random(seed)
    out, ts = [], 1_700_000_000_000
    from infrapilot.analysis_core.models import Candle
    for i in range(n):
        c = 100 + 3.0 * math.sin(i / 9.0) + 5.0 * math.sin(i / 23.0) + rng.uniform(-1.5, 1.5)
        o = 100 + 3.0 * math.sin((i - 1) / 9.0) + 5.0 * math.sin((i - 1) / 23.0)
        out.append(Candle(ts=ts, open=o, high=max(o, c) + rng.uniform(0, 1),
                          low=min(o, c) - rng.uniform(0, 1), close=c, volume=1.0))
        ts += 900_000
    return out


def _atr(candles, n=14):
    tr = []
    pc = candles[0].close
    for cd in candles:
        tr.append(max(cd.high - cd.low, abs(cd.high - pc), abs(cd.low - pc)))
        pc = cd.close
    out, a = [], tr[0]
    al = 1.0 / n
    for i, x in enumerate(tr):
        a = x if i == 0 else a + al * (x - a)
        out.append(a)
    return out


def _run_engine(candles):
    from infrapilot.analysis_core.confluence import compute_clusters
    cfg = StructureConfig()
    eng = StructureEngine(cfg)
    atr = _atr(candles)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i])
    bar = len(candles) - 1
    clusters = compute_clusters(eng.state, candles, bar, atr[bar], cfg)
    return eng.state, clusters, candles[bar].close, atr[bar]


def test_v8_clusters_integration_runs():
    """v8 클러스터 + v4 진입: 에러 없이 Setup/None, 양방향 + mps 스윕에서 동작."""
    from infrapilot.analysis_core.models import Setup
    state, clusters, close, atr = _run_engine(_choppy())
    produced = 0
    for direction in (1, -1):
        for mps in (1.0, 3.0, 6.0):
            cfg = SetupConfig(min_plan_score=mps)
            s = make_setup(clusters, state, close, atr, direction, cfg)
            assert s is None or isinstance(s, Setup)
            if s is not None:
                produced += 1
                # 불변식: 손절/목표가 방향에 맞는 쪽
                if direction == 1:
                    assert s.stop < s.entry and s.target > s.entry
                else:
                    assert s.stop > s.entry and s.target < s.entry
                assert s.rr > 0 and s.score >= mps and s.mode == "v4_reversion"
    assert produced > 0, "어떤 (방향,mps)에서도 Setup이 안 나오면 커버리지 부족"


def test_v8_clusters_integration_deterministic():
    state, clusters, close, atr = _run_engine(_choppy())
    cfg = SetupConfig(min_plan_score=2.0)
    a = make_setup(clusters, state, close, atr, 1, cfg)
    b = make_setup(clusters, state, close, atr, 1, cfg)
    assert _norm(a, False) == _norm(b, False)
