"""진입 계획(Setup) 생성 — 채택안 v4 reversion + btc_bias.

정본: research/backtest/wcse_backtest/strategy.py 의 compute_bias + _build_reversion
(채택 v4 reversion). 입력 클러스터는 우리 confluence.compute_clusters(v8 cluster_score) 결과.

★ 검증 경계 (반드시 인지):
  채택 백테스트(운용문서 OOS PF)는 v4 structure+confluence+reversion 일체로 산출됐다.
  우리는 진입 로직만 v4를 쓰고 입력 클러스터는 v8 confluence다. 따라서
  "v8 클러스터 + v4 진입" 조합은 **운용문서 수치를 재현하지 않는다.**
  → 스캐너 단계에서 이 조합의 OOS PF를 재검증하기 전에는 **라이브 매매에 쓰지 말 것.**
  (포팅 정확성은 'v4 클러스터 입력 → _build_reversion 1:1' 골든으로 별도 증명한다.)

★ 계산/판정 분리: confluence가 매긴 클러스터 score를 '쓰되 재계산하지 않는다'.
  minPlanScore 등 임계는 cfg로 '주입'만 받아 비교한다(종목·TF별 값 하드코딩 금지 —
  scanner/config 몫).

★ pluggable: make_setup이 cfg.engine으로 진입 엔진을 고른다. 지금은 'v4_reversion'만
  구현. 'v8_reversion'은 백테스트 미통과(평균 PF 1.24 < v4 1.70)라 미구현 — 재검증 후 추가.

단방향 의존: setup → ict(StructureState)/confluence(LevelCluster) → … → models.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import Setup, LevelCluster, PT_HIGH, PT_LOW
from .ict import StructureState


# ──────────────────────────── 설정 (v4 채택값) ────────────────────────────
@dataclass
class SetupConfig:
    """진입 판정/구성 파라미터. wcse_backtest/config.yaml pine_defaults 발췌.

    min_plan_score: 합류 점수 임계. scanner/config가 종목·TF별로 '주입'(하드코딩 금지).
    """
    min_plan_score: float = 3.0   # ← 주입 대상 (기본은 자리표시; 종목·TF별로 외부 주입)
    rr_min: float = 2.0
    sl_buf_atr: float = 0.5
    stop_n: float = 2.0
    max_stop_atr: float = 4.0
    near_dist_atr: float = 10.0   # _build_reversion의 max_dist = atr*10
    req_align: bool = True
    engine: str = "v4_reversion"  # 진입 엔진 선택(pluggable). 현재 v4만 구현.


# ──────────────────────────── 바이어스 해석 (1:1) ────────────────────────────
def compute_bias(
    state: StructureState,
    close: float,
    htf_bull: bool,
    htf_bear: bool,
    req_align: bool,
) -> tuple[bool, bool]:
    """로컬 구조 바이어스 + (옵션) 상위TF 정렬. strategy.compute_bias 1:1.

    last_classified(ITH/ITL) 대신 우리 state.last_conf_ith/itl(= 마지막 분류 가격) 사용.
    """
    ith_p = state.last_conf_ith
    itl_p = state.last_conf_itl
    local_bull = state.ms_dir > 0 or (ith_p is not None and close > ith_p)
    local_bear = state.ms_dir < 0 or (itl_p is not None and close < itl_p)
    bull = local_bull and (not req_align or htf_bull)
    bear = local_bear and (not req_align or htf_bear)
    return bull, bear


def apply_btc_gate(
    bull: bool,
    bear: bool,
    btc_bull: bool,
    btc_bear: bool,
    is_btc_chart: bool,
    use_btc_bias: bool,
) -> tuple[bool, bool]:
    """알트는 BTC 4h close vs EMA50 정렬 필수, BTC 차트는 bypass. btc_bias_snippet.pine 1:1.

    운용문서: 롱은 BTC bullish, 숏은 BTC bearish일 때만 알트 진입 허용.
    """
    gate_long = (not use_btc_bias) or is_btc_chart or btc_bull
    gate_short = (not use_btc_bias) or is_btc_chart or btc_bear
    return bull and gate_long, bear and gate_short


def resolve_direction(bull: bool, bear: bool) -> int:
    """bull/bear → 방향 int. (strategy.generate_signal의 d 계산과 동일.)"""
    return 1 if bull else (-1 if bear else 0)


# ──────────────────────────── 진입 엔진 디스패처 ────────────────────────────
def make_setup(
    clusters: list[LevelCluster],
    state: StructureState,
    close: float,
    atr: float,
    bias: int,
    cfg: SetupConfig,
) -> Optional[Setup]:
    """합류 클러스터 + 방향(bias)으로 진입 계획(Setup) 생성. 없으면 None.

    pluggable 진입점: cfg.engine으로 구현 선택. bias(±1/0)는 상위(compute_bias +
    apply_btc_gate + neely/cooldown 게이트)에서 이미 해석된 방향 — 여기선 그 방향으로
    클러스터를 골라 entry/stop/target/rr만 구성한다.
    """
    if bias == 0 or not clusters or atr <= 0:
        return None
    if cfg.engine == "v4_reversion":
        return _reversion_v4(clusters, state, close, atr, bias, cfg)
    if cfg.engine == "v8_reversion":
        return _reversion_v8(clusters, state, close, atr, bias, cfg)
    raise ValueError(f"알 수 없는 진입 엔진: {cfg.engine}")


# ──────────────────────────── v4 reversion (골격) ────────────────────────────
def _reversion_v4(
    clusters: list[LevelCluster],
    state: StructureState,
    close: float,
    atr: float,
    direction: int,
    cfg: SetupConfig,
) -> Optional[Setup]:
    """채택 v4 reversion. strategy.py _build_reversion(56-155) 1:1 포팅 예정.

    스텝 (다음 단계에서 본문 채움 + 골든 'v4 클러스터→_build_reversion 1:1'):
      1) 최적 클러스터 선택: okSide(숏=close 위 / 롱=아래) + near(|mid-close|≤atr·near_dist)
         + score≥min_plan_score, score 최대. 없으면 None.                  (strategy 71-82)
      2) entry = best.mid ; cap_d = max_stop_atr·atr                          (84-85)
      3) 보호 스윙: stop-side 최근접 피벗(숏=PT_HIGH>entry / 롱=PT_LOW<entry, cap_d 이내).
         + 리밸런스 피벗(state.reb_ith/reb_itl)도 더 가까우면 채택.            (87-103)
      4) sl: 보호스윙±sl_buf_atr·atr, 없으면 entry±stop_n·atr. min_stop=0.5·atr로
         [entry±min_stop, entry±cap_d] 클램프, 양수 보장.                      (105-120)
      5) rsk=|entry-sl|; ≤0이면 None. tp = entry∓rsk·rr_min 초기값.            (122-125)
      6) 목표 갱신: entry+리스크 너머 최근접 클러스터(on_target) 있으면 tp=그 mid. (127-138)
      7) tp≤0이면 None. rr=|tp-entry|/rsk. Setup(mode='v4_reversion') 반환.    (140-155)

    가드: min_plan_score는 cfg 주입값만 비교(하드코딩 X). score/src_text는 confluence 값 그대로.
    """
    # (1) 최적 클러스터 — okSide + near + score≥mps, score 최대(strict >). (strategy 71-82)
    max_dist = atr * cfg.near_dist_atr
    best: Optional[LevelCluster] = None
    best_score = 0.0
    for cl in clusters:
        ok_side = (direction == -1 and cl.mid > close) or (direction == 1 and cl.mid < close)
        near = abs(cl.mid - close) <= max_dist
        if ok_side and near and cl.score >= cfg.min_plan_score and cl.score > best_score:
            best = cl
            best_score = cl.score
    if best is None:
        return None

    # (2) entry = 클러스터 mid, cap_d = max_stop_atr·atr
    entry = best.mid
    cap_d = cfg.max_stop_atr * atr

    # (3) 보호 스윙: stop-side 최근접 구조 피벗(cap_d 이내). + 리밸런스 피벗(더 가까우면). (87-103)
    prot: Optional[float] = None
    for p in state.pivots:
        if direction == -1 and p.ptype == PT_HIGH and p.price > entry and (p.price - entry) <= cap_d:
            if prot is None or p.price < prot:
                prot = p.price
        if direction == 1 and p.ptype == PT_LOW and p.price < entry and (entry - p.price) <= cap_d:
            if prot is None or p.price > prot:
                prot = p.price
    # v4 채택 showReb=true: 리밸런스 극점이 더 가까운 보호선이 될 수 있음
    if direction == -1 and state.reb_ith is not None and state.reb_ith > entry and (state.reb_ith - entry) <= cap_d:
        if prot is None or state.reb_ith < prot:
            prot = state.reb_ith
    if direction == 1 and state.reb_itl is not None and state.reb_itl < entry and (entry - state.reb_itl) <= cap_d:
        if prot is None or state.reb_itl > prot:
            prot = state.reb_itl

    # (4) sl: 보호스윙±버퍼, 없으면 stopN·ATR. min_stop=0.5·ATR로 [±min_stop, ±cap_d] 클램프. (105-120)
    sl_buf = cfg.sl_buf_atr * atr
    if prot is not None:
        sl_struct = prot + sl_buf if direction == -1 else prot - sl_buf
    else:
        sl_struct = entry + cfg.stop_n * atr if direction == -1 else entry - cfg.stop_n * atr

    min_stop = 0.5 * atr
    if direction == -1:
        slv = max(entry + min_stop, min(sl_struct, entry + cap_d))
    else:
        slv = min(entry - min_stop, max(sl_struct, entry - cap_d))
        if slv <= 0:
            slv = max(entry * 0.01, entry - cap_d)

    # (5) 리스크 + rr_min 기본 목표. (122-125)
    rsk = abs(entry - slv)
    if rsk <= 0:
        return None
    tpv = entry - rsk * cfg.rr_min if direction == -1 else entry + rsk * cfg.rr_min

    # (6) 목표 갱신: entry±리스크 너머 최근접 클러스터 있으면 그 mid. (127-138)
    best_t: Optional[LevelCluster] = None
    best_td = float("inf")
    for cl in clusters:
        on_target = (direction == -1 and cl.mid < entry - rsk) or (direction == 1 and cl.mid > entry + rsk)
        if on_target:
            dd = abs(cl.mid - entry)
            if dd < best_td:
                best_td = dd
                best_t = cl
    if best_t is not None:
        tpv = best_t.mid

    # (7) tp 검증 + rr. (rr_min 미달 별도 처리/포기 없음 — strategy.py 그대로) (140-155)
    if tpv <= 0:
        return None
    rr = abs(tpv - entry) / max(rsk, 1e-9)

    return Setup(
        direction=direction,
        entry=entry,
        stop=slv,
        target=tpv,
        rr=rr,
        score=best.score,
        src_text=best.src_text,
        mode="v4_reversion",
    )


# ──────────────────────────── v8 reversion (미구현 — 자리만) ────────────────────────────
def _reversion_v8(
    clusters: list[LevelCluster],
    state: StructureState,
    close: float,
    atr: float,
    direction: int,
    cfg: SetupConfig,
) -> Optional[Setup]:
    """v8 no-sweep reversion(상태기계 + TP 사다리). **미구현.**

    백테스트 미통과(동일 데이터 평균 OOS PF 1.24 < v4 1.70, 강종목 ETH/XRP에서 v4에 밀림).
    SOL 등 일부에서 우위라 가능성은 있으나, 재검증(스캐너 OOS) 통과 전엔 운영 코드에 넣지
    않는다(YAGNI). 추가 시 Setup에 tp2/tp3 필드도 그때 함께 도입.
    """
    raise NotImplementedError("v8_reversion: 백테스트 미통과 — 재검증 후 구현 (현재 호출 금지)")
