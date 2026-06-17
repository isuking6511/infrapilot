"""scanner.scan 검증 — 랭킹 정렬 / setup 없는 티커 포함(랭킹 제외) / 결정성 / 거리 정규화 공정성."""
from __future__ import annotations

import math
import random

from infrapilot.analysis_core.models import Candle
from infrapilot.analysis_core.ict import StructureConfig
from infrapilot.analysis_core.setup import SetupConfig
from infrapilot.analysis_core.scanner import ScanInput, ScannerConfig, scan


def _shape(n=800, seed=2):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        c = 100 + 3.0 * math.sin(i / 9.0) + 5.0 * math.sin(i / 23.0) + rng.uniform(-1.5, 1.5)
        o = 100 + 3.0 * math.sin((i - 1) / 9.0) + 5.0 * math.sin((i - 1) / 23.0)
        out.append((o, max(o, c) + rng.uniform(0, 1), min(o, c) - rng.uniform(0, 1), c))
    return out


def _candles(shape, scale=1.0):
    ts = 1_700_000_000_000
    cs = []
    for o, hi, lo, c in shape:
        cs.append(Candle(ts=ts, open=o * scale, high=hi * scale, low=lo * scale, close=c * scale, volume=1.0))
        ts += 900_000
    return cs


def _flat(n=300):
    """변동 거의 없는 평탄 데이터 — 셋업 안 뜸(검색용 행)."""
    ts = 1_700_000_000_000
    return [Candle(ts=ts + i * 900_000, open=100, high=100.1, low=99.9, close=100, volume=1.0) for i in range(n)]


def _cfg(mps=2.0, k=0.5):
    return ScannerConfig(rank_k=k, structure=StructureConfig(), setup=SetupConfig(min_plan_score=mps))


def _ser(tr):
    return (tr.symbol, tr.tf, round(tr.close, 6),
            None if tr.rank_score is None else round(tr.rank_score, 6),
            None if tr.distance_atr is None else round(tr.distance_atr, 6),
            None if tr.setup is None else (tr.setup.direction, round(tr.setup.entry, 6),
                                           round(tr.setup.stop, 6), round(tr.setup.target, 6)))


def test_ranking_sorted_desc():
    """TF 내 rank_score 내림차순, setup 없는 행은 맨 뒤."""
    inputs = []
    for s in range(5):
        inputs.append(ScanInput(f"SYM{s}", "4h", _candles(_shape(seed=s)), bias=1))
        inputs.append(ScanInput(f"SYM{s}", "4h", _candles(_shape(seed=s)), bias=-1))
    inputs.append(ScanInput("FLAT", "4h", _flat(), bias=1))
    res = scan(inputs, _cfg())
    rows = res["4h"]
    scored = [r.rank_score for r in rows if r.rank_score is not None]
    assert scored == sorted(scored, reverse=True), "rank_score 내림차순이어야"
    # None(검색용) 행은 정렬상 점수 있는 행들 뒤
    first_none = next((i for i, r in enumerate(rows) if r.rank_score is None), len(rows))
    assert all(rows[i].rank_score is None for i in range(first_none, len(rows))), "None 행은 뒤쪽에 모임"


def test_no_setup_included_but_unranked():
    """셋업 없는 티커도 결과에 포함(검색용), rank_score/setup=None."""
    res = scan([ScanInput("FLAT", "4h", _flat(), bias=1),
                ScanInput("ZERO", "4h", _candles(_shape(seed=1)), bias=0)], _cfg())
    rows = {r.symbol: r for r in res["4h"]}
    assert set(rows) == {"FLAT", "ZERO"}, "모든 티커 반환(거르기는 호출자)"
    for sym in ("FLAT", "ZERO"):
        assert rows[sym].rank_score is None and rows[sym].setup is None
        assert not math.isnan(rows[sym].close)
    # bias=0은 make_setup에서 None — 랭킹 제외 확인
    assert rows["ZERO"].setup is None


def test_determinism():
    inputs = [ScanInput(f"S{s}", "4h", _candles(_shape(seed=s)), bias=1) for s in range(4)]
    a = scan(inputs, _cfg())
    b = scan(inputs, _cfg())
    assert [_ser(r) for r in a["4h"]] == [_ser(r) for r in b["4h"]]


def test_tf_independent_grouping():
    """TF별로 따로 그룹·정렬."""
    inputs = [ScanInput("A", "4h", _candles(_shape(seed=1)), bias=1),
              ScanInput("A", "1h", _candles(_shape(seed=1)), bias=1),
              ScanInput("B", "1h", _candles(_shape(seed=2)), bias=-1)]
    res = scan(inputs, _cfg())
    assert set(res) == {"4h", "1h"}
    assert len(res["4h"]) == 1 and len(res["1h"]) == 2


def test_distance_normalization_fairness():
    """동일 형태를 BTC(×600)·XRP(×0.012)로 스케일 → rank_score·distance_atr 스케일 불변.
    (절대가격이 아니라 ATR 배수로 비교하므로 6만짜리와 1.2짜리가 공정)."""
    sh = _shape(seed=2)
    inputs = [ScanInput("BTCUSDT", "4h", _candles(sh, 600.0), bias=1),
              ScanInput("XRPUSDT", "4h", _candles(sh, 0.012), bias=1)]
    res = scan(inputs, _cfg(mps=1.0))
    rows = {r.symbol: r for r in res["4h"]}
    btc, xrp = rows["BTCUSDT"], rows["XRPUSDT"]
    assert btc.setup is not None and xrp.setup is not None, "양쪽 다 셋업 떠야(비교 성립)"
    assert abs(btc.distance_atr - xrp.distance_atr) < 1e-6, "거리(ATR 정규화)는 스케일 불변"
    assert abs(btc.rank_score - xrp.rank_score) < 1e-6, "rank_score도 스케일 불변 → 공정 비교"
    # 가격(close/entry)은 당연히 스케일만큼 차이
    assert abs(btc.close / xrp.close - 600.0 / 0.012) < 1.0
