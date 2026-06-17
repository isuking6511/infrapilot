"""합류 점수 — 정본 v8 engine.py cluster_score(329-352) 1:1.

각 active 레벨 가격을 '중심'으로, tol 이내의 모든 근거를 합산해 LevelCluster를 만든다.
근거 소스(v8 cluster_score 순서대로):
  1) active soft levels (꼬리/피보/FVG/OB/넥라인/리밸런스/고거래량) → lv.weight 합산
  2) 동적 라인(빗각/부채살/포크) — value(bar)가 tol 이내면 dl.weight (비소멸)
  3) 돈치안 — 직전 don_len봉 최고/최저(현재봉 제외)가 tol 이내면 w_don (동적, 비소멸)
  4) 가격대(분류 피벗) — tol 이내, 최대 3개까지만(cap3) w_zone (밀집 과대평가 방지)
  5) LT 레벨(lt_lvls) — tol 이내면 w_lt (고가중)

★ 점수 '계산'만 한다(사실 계산). 임계(min_plan_score) 비교·방향(ok_side)·거리 필터·
  시간보너스(t_bon)는 setup.py 몫(진입 판정). v8에선 이들이 호출부(engine.py 594-602)에,
  순수 점수는 cluster_score 함수에 있다 — 우리는 그 함수만 포팅한다.

★ 입력 계약(스텝5 active 필터 실현): state.levels 중 active=True만 '중심'이자 '근거'.
  소멸(active=False)된 레벨은 중심도 안 되고 합산에도 안 들어간다.

단방향 의존: confluence → ict(StructureState/StructureConfig) → pivots/neely → models.
"""
from __future__ import annotations

from .models import Candle, LevelCluster, LevelSource
from .ict import StructureState, StructureConfig


def _score_at(
    price: float,
    tol: float,
    bar: int,
    state: StructureState,
    candles: list[Candle],
    cfg: StructureConfig,
) -> tuple[float, int, set[LevelSource]]:
    """가격 price에 대한 합류 점수·구성원수·기여 src. v8 cluster_score 1:1.

    cnt는 기여 요소 개수(우리 보조값 — v8엔 없음). 점수/구성 src가 v8 검증 대상.
    """
    s = 0.0
    cnt = 0
    srcs: set[LevelSource] = set()

    # 1) active soft levels
    for lv in state.levels:
        if lv.active and abs(lv.price - price) <= tol:
            s += lv.weight
            srcs.add(lv.source)
            cnt += 1
    # 2) 동적 라인 (value(bar))
    for dl in state.dyn_lines:
        if abs(dl.value(bar) - price) <= tol:
            s += dl.weight
            srcs.add(dl.source)
            cnt += 1
    # 3) 돈치안 — 직전 don_len봉(현재봉 제외) 최고/최저
    if bar >= cfg.don_len:
        window = candles[bar - cfg.don_len:bar]
        dH = max(cd.high for cd in window)
        dL = min(cd.low for cd in window)
        if abs(dH - price) <= tol:
            s += cfg.w_don; srcs.add(LevelSource.DON); cnt += 1
        if abs(dL - price) <= tol:
            s += cfg.w_don; srcs.add(LevelSource.DON); cnt += 1
    # 4) 가격대(분류 피벗) — cap 3
    zc = 0
    for p in state.pivots:
        if p.classified >= 1 and zc < 3 and abs(p.price - price) <= tol:
            s += cfg.w_zone; srcs.add(LevelSource.ZONE); zc += 1; cnt += 1
    # 5) LT 레벨
    for lt in state.lt_lvls:
        if abs(lt - price) <= tol:
            s += cfg.w_lt; srcs.add(LevelSource.LT_REB); cnt += 1

    return s, cnt, srcs


def compute_clusters(
    state: StructureState,
    candles: list[Candle],
    bar: int,
    atr_i: float,
    cfg: StructureConfig,
) -> list[LevelCluster]:
    """각 active 레벨 가격을 중심으로 LevelCluster(중심가·총점·구성원수·기여 src) 산출.

    v8 호출부(engine.py 595-602)는 active 레벨을 순회하며 cluster_score를 매긴다 —
    여기선 그 '점수 계산'만 수행(방향/거리/임계 필터는 setup). 순회 순서 = levels 적립 순서.
    """
    tol = cfg.conf_tol_atr * atr_i
    clusters: list[LevelCluster] = []
    for lv in state.levels:
        if not lv.active:
            continue
        score, cnt, srcs = _score_at(lv.price, tol, bar, state, candles, cfg)
        clusters.append(LevelCluster(mid=lv.price, score=score, cnt=cnt, sources=frozenset(srcs)))
    return clusters
