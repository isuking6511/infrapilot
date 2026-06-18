"""다종목 스캐너 — 여러 (symbol, tf) 평가 → TF별 점수순 랭킹 (웹 상위 랭킹용).

[역할] 순수 함수. 캔들 받아 랭킹 '계산·정렬'만. DB/Redis/API 절대 안 만짐
  (analysis_core 단방향 원칙). 일관성·캐싱·상위 N 자르기는 바깥 레이어 책임.

[파이프라인] 각 (symbol, tf): StructureEngine → compute_clusters → make_setup →
  setup 있으면 rank_score 계산, 없으면(bias 0 / 임계 미달) 랭킹 제외.

[거리 정규화] distance_atr = |close - entry| / atr → ATR로 나눠 가격대 무관 공정 비교
  (BTC 6만 / XRP 1.2가 같은 잣대). rank_score = setup.score − k·distance_atr.

[정렬] TF별 독립 정렬. rank_score 내림차순, 동률이면 distance_atr 가까운 순.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional

from .models import Candle, Setup
from .ict import StructureConfig, StructureEngine
from .confluence import compute_clusters
from .setup import SetupConfig, make_setup


# ──────────────────────────── 입출력 타입 ────────────────────────────
@dataclass
class ScanInput:
    """스캔 한 건. candles는 호출자가 준비(scanner는 출처 모름). bias는 상위에서
    해석된 방향(±1/0; compute_bias + btc_gate 등 게이트 적용 후)."""
    symbol: str
    tf: str
    candles: list[Candle]
    bias: int


@dataclass
class TickerRank:
    """랭킹/검색 한 줄. 웹이 소비. setup이 없는 티커도 결과에 포함된다(검색용) —
    그 경우 rank_score/setup/distance_atr = None. 거르기(랭킹만/전체검색)는 호출자 몫.
    """
    symbol: str
    tf: str
    close: float
    rank_score: Optional[float] = None
    setup: Optional[Setup] = None
    distance_atr: Optional[float] = None


@dataclass
class ScannerConfig:
    """스캐너 파라미터.

    rank_k: 거리 패널티 가중치. 기본 0.5는 **검증된 최적값이 아니라 합리적 기본값**이다 —
      k는 진입을 바꾸지 않고 '랭킹 표시 순서'만 정하므로 백테스트 검증 대상이 아니라
      운영 튜닝 대상. 운영 중 cfg.rank_k로 조정한다.
      (기본 0.5 근거: score≈3~8, distance_atr∈0~near_dist_atr(10) → 패널티 0~5로
       점수가 주신호·거리는 보조. "2 ATR 더 가까운 존 ≈ +1점".)
    min_plan_score: (symbol, tf) → 임계값. 종목·TF별로 caller가 채워 주입(하드코딩 X).
      비어있으면 setup.min_plan_score 기본값 사용.
    """
    rank_k: float = 0.5
    structure: StructureConfig = field(default_factory=StructureConfig)
    setup: SetupConfig = field(default_factory=SetupConfig)
    min_plan_score: dict[tuple[str, str], float] = field(default_factory=dict)
    # 웹 랭킹용 TF별 근접 게이트(진입가가 현재가에서 N ATR 이내일 때만 셋업 인정).
    # 비어있으면 setup.near_dist_atr(=10) 그대로 → 자동매매/백테스트 의미 유지.
    # 값은 **튜닝값(검증 아님)** — 먼 강한 zone이 임박하지 않은데 상위에 뜨는 걸 막는 표시 규칙.
    near_dist_atr_by_tf: dict[str, float] = field(default_factory=dict)


def rank_score(score: float, distance_atr: float, k: float) -> float:
    """rank_score = score − k·distance_atr. 가까운 진입일수록(거리↓) 높은 점수."""
    return score - k * distance_atr


def _wilder_atr(candles: list[Candle], n: int = 14) -> list[float]:
    """Wilder ATR (= v8 _atr: ewm alpha=1/n, adjust=False). 결정적·외부의존 없음."""
    if not candles:
        return []
    tr = []
    pc = candles[0].close
    for cd in candles:
        tr.append(max(cd.high - cd.low, abs(cd.high - pc), abs(cd.low - pc)))
        pc = cd.close
    out, a, al = [], tr[0], 1.0 / n
    for i, x in enumerate(tr):
        a = x if i == 0 else a + al * (x - a)
        out.append(a)
    return out


def _sort_key(tr: TickerRank):
    """랭킹 정렬 키: setup 있는 것 먼저, rank_score 내림차순, 동률이면 distance_atr 오름차순.
    setup 없는(rank_score None) 행은 뒤로(검색용), symbol로 결정적 정렬."""
    has = tr.rank_score is None
    return (has, -(tr.rank_score or 0.0),
            tr.distance_atr if tr.distance_atr is not None else math.inf,
            tr.symbol)


# ──────────────────────────── 스캔 (골격) ────────────────────────────
def _evaluate(inp: ScanInput, cfg: ScannerConfig) -> TickerRank:
    """한 (symbol, tf) 평가 → TickerRank. setup 없으면 rank_score/setup/distance_atr=None.

    htf는 입력에 없어 0.0(중립)로 전진 — dynlines down_ctx는 ms_dir에만 의존(소수의
    ms_dir==0 초기구간만 영향). 진입 방향은 inp.bias(상위에서 게이트 적용)를 그대로 사용.
    """
    candles = inp.candles
    if not candles:
        return TickerRank(inp.symbol, inp.tf, close=math.nan)

    atr = _wilder_atr(candles)
    eng = StructureEngine(cfg.structure)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i])

    bar = len(candles) - 1
    close = candles[bar].close
    atr_last = atr[bar]
    clusters = compute_clusters(eng.state, candles, bar, atr_last, cfg.structure)

    mps = cfg.min_plan_score.get((inp.symbol, inp.tf), cfg.setup.min_plan_score)
    near = cfg.near_dist_atr_by_tf.get(inp.tf)   # 웹 랭킹용 TF 근접 게이트(없으면 기존값)
    setup_cfg = replace(cfg.setup, min_plan_score=mps,
                        near_dist_atr=near if near is not None else cfg.setup.near_dist_atr)
    s = make_setup(clusters, eng.state, close, atr_last, inp.bias, setup_cfg)

    if s is None:
        return TickerRank(inp.symbol, inp.tf, close=close)   # 검색용으로 포함(랭킹 제외)
    dist = abs(close - s.entry) / atr_last if atr_last > 0 else math.inf
    return TickerRank(inp.symbol, inp.tf, close=close,
                      rank_score=rank_score(s.score, dist, cfg.rank_k),
                      setup=s, distance_atr=dist)


def scan(inputs: list[ScanInput], cfg: ScannerConfig) -> dict[str, list[TickerRank]]:
    """여러 (symbol, tf) 평가 → TF별 정렬된 TickerRank 리스트(모든 티커 포함).

    각 ScanInput을 _evaluate → TF별 그룹화 → TF 내 정렬(rank_score 내림차순, 동률 시
    distance_atr 오름차순; setup 없는 행은 뒤로). 상위 N 자르기·거르기는 호출자 몫
    (비즈니스 규칙 N 하드코딩 금지). DB/Redis/API 미접촉(순수 계산).
    """
    by_tf: dict[str, list[TickerRank]] = {}
    for inp in inputs:
        by_tf.setdefault(inp.tf, []).append(_evaluate(inp, cfg))
    for tf in by_tf:
        by_tf[tf].sort(key=_sort_key)
    return by_tf
