"""봉단위 구조 엔진 (StructureEngine) — v8 정본의 incremental 구조 패스.

정본: research/pine/wcse_v8/engine.py 의 run() 메인 루프 중 '구조/레벨 적립' 부분
(스텝 1~6b + rebuild_dynlines). 트레이딩 상태기계(스텝 7~8)는 setup.py로 분리.

★ 이 모듈이 책임지는 것 (사용자 합의 1번 + 경계):
  - 봉을 하나씩 먹여(update) 구조 상태(StructureState)를 전진.
  - '소멸형 levels store'(꼬리/FVG/OB/넥라인/리밸런스/고거래량/피보)를 적립하고
    consume-on-touch(닿으면 소멸)를 v8 정본 순서로 적용.
  - '비소멸 동적레벨'(빗각/부채살/포크 = dyn_lines)을 피벗 확정 시 재계산해 보관.
  → confluence.py 는 이 StructureState(levels + dyn_lines + 분류피벗 + lt_lvls)를
    받아 '점수만' 계산. 소멸/순서 책임은 지지 않음.

★ 단방향: 피벗 탐지는 pivots.py(PivotEngine)에 위임. 피보 사다리는 neely.py의
  '순수 함수'를 엔진 루프가 호출해 결과 레벨만 받는다(neely는 상태 없음).

★ lookahead 금지: update(i)는 candles[0..i]와 atr[i]만 본다. 미래 봉 참조 없음.

────────────────────────────────────────────────────────────────────────
consume-on-touch 가 결과를 바꾸는 핵심 지점이라 v8 정본의 봉내 실행 순서를 고정한다:

  (1) 피벗 확정 + ITH/ITL 분류 + LTH/LTL 갱신      (engine.py 368-398)
  (2) BOS/CHoCH → 넥라인 · OB · SD목표 레벨 적립    (engine.py 400-426)
  (3) FVG 생성 + 충전→리밸런스(rITH/rITL) 레벨 적립  (engine.py 428-442)
  (4) 꼬리(wick) 레벨 적립                          (engine.py 444-449)
  (4b) 고거래량 캔들 몸통경계 레벨 적립              (engine.py 451-458)
  (5) consume-on-touch: 닿은 수평 레벨 소멸          (engine.py 460-463)
  (6) 피보 사다리 적립 (neely, leg_sig 1회 가드)     (engine.py 465-489)
  (6b) LT 스윙 앵커 피보 1세트                       (engine.py 490-500)
  (7) 피벗 이벤트면 dyn_lines 재계산                 (engine.py 502-503)

  ※ 왜 (5)consume 가 (6)피보보다 '먼저'인가: v8은 그 봉에 새로 적립된 피보 레벨을
    같은 봉에서 소멸시키지 않는다. 즉 피보는 적립된 다음 봉부터 consume 대상이 됨.
    이 순서를 어기면(피보 적립 후 consume) 활성 레벨 집합이 달라져 점수가 바뀐다.
    → 골든 테스트로 이 순서를 핀(소멸 순서 일치 케이스).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Candle, Pivot, LevelCandidate, LevelSource, PT_HIGH, PT_LOW
from .pivots import PivotEngine
from .neely import fib_ladder


# ──────────────────────────── 설정 (v8 기본값) ────────────────────────────
@dataclass
class StructureConfig:
    """구조 패스 파라미터. 정본 WCSEParams 중 구조/레벨 적립에 쓰는 부분만 발췌."""
    sw_len: int = 8
    max_piv: int = 80
    conf_tol_atr: float = 0.5          # tol = conf_tol_atr * atr[i]
    # 가중치 (v8 고정 권장값)
    w_neck: float = 0.5
    w_ob: float = 1.2
    w_fvg: float = 1.0
    w_wick: float = 1.0
    w_vol: float = 1.2
    w_fib: float = 1.5
    w_lt: float = 1.8
    w_diag: float = 1.5    # 빗각/부채살(동적)
    w_fork: float = 1.0    # 피치포크(동적)
    w_don: float = 1.0     # 돌파선(돈치안) — confluence score 시점 평가
    w_zone: float = 1.2    # 가격대(분류 피벗, cap 3) — confluence score 시점
    # 토글 (v8 engine.py가 실제로 분기하는 것만 — OB/FVG/리밸런스/consume은 v8에서
    #  토글 없이 항상 수행하므로 플래그 두지 않음. Pine의 show* 토글은 엔진 포팅에서 제거됨.)
    use_vol_lvl: bool = True
    use_diagonals: bool = True
    lt_fib: bool = True
    dual_fib: bool = True
    # 파라미터
    wick_ratio: float = 0.55
    vol_lvl_len: int = 50
    fan_n: int = 4
    don_len: int = 20      # 돈치안 길이 — confluence score 시점 평가
    min_leg_atr: float = 1.5
    max_leg_atr: float = 0.0           # 0 = 끔
    ext_multiples: tuple = (0.618, 1.0, 1.272, 1.618, 2.618)
    max_levels: int = 150              # v8 levels store cap


# ──────────────────────────── 내부 자료형 ────────────────────────────
@dataclass
class Fvg:
    """3봉 임밸런스. v8 Fvg 포팅(direction: +1 상승갭=지지, -1 하락갭=저항).
    v8엔 top/bot/dir/filled만 있음 — 미사용 필드(bar_created 등) 두지 않음(YAGNI)."""
    top: float
    bot: float
    direction: int
    filled: bool = False


@dataclass
class DynLine:
    """비소멸 동적 라인(빗각/부채살/포크). v8 DynLine 포팅.

    confluence가 점수 계산 시 value(bar)로 현재가 근처인지 평가. 소멸 안 됨.
    """
    a_bar: int
    a_price: float
    slope: float
    source: LevelSource    # DIAG | FORK
    weight: float

    def value(self, bar: int) -> float:
        return self.a_price + self.slope * (bar - self.a_bar)


@dataclass
class StructureState:
    """구조 엔진의 누적 상태 = confluence/setup이 소비하는 계약.

    v8 run() 의 지역 상태(pivP/pivK/cur_*/levels/fvgs/dyn_lines 등)를 한 객체로 묶음.
    """
    # 피벗(분류 포함) — pivots.PivotEngine 소유분의 참조
    pivots: list[Pivot] = field(default_factory=list)

    # 시장 구조
    ms_dir: int = 0                          # 1 상승 / -1 하락 / 0
    neckline: Optional[float] = None
    cur_ith: Optional[float] = None          # 아직 안 깨진 마지막 분류 ITH
    cur_itl: Optional[float] = None
    last_conf_ith: Optional[float] = None    # 마지막 확정 ITH (SD/피보 레그용)
    last_conf_itl: Optional[float] = None

    # LTH/LTL 3단계
    ith_hist: list[tuple[float, int]] = field(default_factory=list)   # (price, bar)
    itl_hist: list[tuple[float, int]] = field(default_factory=list)
    lt_lvls: list[float] = field(default_factory=list)                # 합류용 LT 가격
    lt_h: list[tuple[float, int]] = field(default_factory=list)       # LT 스윙 고
    lt_l: list[tuple[float, int]] = field(default_factory=list)       # LT 스윙 저

    # FVG / 리밸런스
    fvgs: list[Fvg] = field(default_factory=list)
    reb_ith: Optional[float] = None
    reb_itl: Optional[float] = None

    # 소멸형 레벨 store (꼬리/FVG/OB/넥라인/리밸런스/고거래량/피보)
    levels: list[LevelCandidate] = field(default_factory=list)
    # 비소멸 동적 레벨 (빗각/부채살/포크)
    dyn_lines: list[DynLine] = field(default_factory=list)

    # 봉내 가드 커서 (once-per-leg 인플레이션 방지)
    leg_sig: Optional[float] = None
    lt_sig: Optional[float] = None
    hv_bar: int = -1


# ──────────────────────────── 엔진 ────────────────────────────
class StructureEngine:
    """봉을 하나씩 먹여 StructureState를 전진시키는 상태 머신.

    사용:
        eng = StructureEngine(StructureConfig())
        for i in range(len(candles)):
            eng.update(i, candles, atr[i])
        state = eng.state   # confluence/setup이 소비
    """

    def __init__(self, config: Optional[StructureConfig] = None) -> None:
        self.cfg = config or StructureConfig()
        self.state = StructureState()
        # 피벗 탐지는 pivots.py에 위임(중복 구현 금지). state.pivots는 이 엔진의 리스트를 가리킴.
        self._piv = PivotEngine(sw_len=self.cfg.sw_len, max_piv=self.cfg.max_piv)
        self.state.pivots = self._piv.pivots

    def reset(self) -> None:
        self.__init__(self.cfg)

    # ---------- 레벨 store 원시연산 (v8 add_lvl 포팅) ----------
    def _add_level(self, price: float, weight: float, source: LevelSource, tol: float) -> None:
        """tol*0.4 내 활성 레벨이 있으면 가중치 병합(겹침=부스트), 없으면 신규.

        v8 add_lvl(engine.py 203-210) 1:1. 병합 거리·cap(max_levels) 보존.
        """
        s = self.state
        merge_dist = tol * 0.4
        for lv in s.levels:
            if lv.active and abs(lv.price - price) < merge_dist:
                lv.weight += weight
                return
        s.levels.append(LevelCandidate(price=price, weight=weight, source=source, active=True))
        if len(s.levels) > self.cfg.max_levels:
            s.levels.pop(0)

    # ---------- 메인: 봉 i 관측 후 전진 ----------
    def update(self, i: int, candles: list[Candle], atr_i: float, htf_i: float = 0.0) -> None:
        """v8 정본 봉내 순서(모듈 docstring 참조)대로 단계 호출.

        atr_i: 이 봉의 ATR(=v8 _atr, Wilder ewm). tol = conf_tol_atr * atr_i.
        htf_i: 이 봉의 상위TF 바이어스 부호(v8 _htf_bias). 동적라인 down_ctx 판정에만
               쓰임(ms_dir==0일 때). caller가 atr와 함께 봉별로 제공(엔진은 순수 유지).
        v8엔 atr/htf NaN이 없으므로(ewm) 별도 스킵 가드 없이 매 봉 처리 — 1:1.
        consume(_consume)가 피보(_fib_ladder)보다 먼저인 순서를 반드시 유지.
        """
        tol = self.cfg.conf_tol_atr * atr_i

        piv_event = self._detect_pivots_and_classify(i, candles)   # (1)
        self._bos_choch(i, candles, tol)                            # (2)
        self._fvg_and_rebalance(i, candles, tol)                    # (3)
        self._wick_levels(i, candles, tol)                          # (4)
        self._volume_levels(i, candles, tol)                        # (4b)
        self._consume_on_touch(i, candles)                          # (5) ★피보보다 먼저
        self._fib_ladder(i, candles, atr_i, tol)                    # (6) neely 순수함수 호출
        self._lt_fib(i, candles, tol)                               # (6b)
        if piv_event:
            self._rebuild_dynlines(i, atr_i, htf_i)                 # (7) v8: 내부에서 use_diagonals 가드

    # ---------- 단계별 stub (다음 단계에서 v8 라인 그대로 포팅) ----------
    def _detect_pivots_and_classify(self, i: int, candles: list[Candle]) -> bool:
        """(1) PivotEngine 전진 + 새 분류 발생 시 cur_*/last_conf_*/hist/LTH 갱신.
        반환: 이번 봉에 피벗 확정 이벤트가 있었는지(piv_event) → dyn_lines 재계산 트리거.

        v8 engine.py 368-398 1:1. 피벗 탐지/분류 자체는 PivotEngine이 하고,
        여기선 그 PivotEvent만 보고 구조 상태(cur_*/last_conf/hist/LTH)를 전진(느슨한 결합).
        cur_ith/cur_itl은 여기서 '세팅'만 하고, 돌파 시 해제는 스텝(2) BOS에서.
        """
        s = self.state
        ev = self._piv.update(i, candles)

        if ev.classified_high is not None:
            price, bar = ev.classified_high
            s.cur_ith = price
            s.last_conf_ith = price
            s.ith_hist.append((price, bar))
            # LTH: 분류 ITH 3개 중 중간이 양옆보다 높으면 LT 레벨/스윙고로 승격
            if len(s.ith_hist) >= 3:
                pm1, pm, pp1 = s.ith_hist[-3][0], s.ith_hist[-2][0], s.ith_hist[-1][0]
                if pm > pm1 and pm > pp1:
                    s.lt_lvls.append(pm); s.lt_lvls[:] = s.lt_lvls[-10:]
                    s.lt_h.append((pm, s.ith_hist[-2][1])); s.lt_h[:] = s.lt_h[-6:]

        if ev.classified_low is not None:
            price, bar = ev.classified_low
            s.cur_itl = price
            s.last_conf_itl = price
            s.itl_hist.append((price, bar))
            # LTL: 거울 조건 (중간이 양옆보다 낮으면)
            if len(s.itl_hist) >= 3:
                pm1, pm, pp1 = s.itl_hist[-3][0], s.itl_hist[-2][0], s.itl_hist[-1][0]
                if pm < pm1 and pm < pp1:
                    s.lt_lvls.append(pm); s.lt_lvls[:] = s.lt_lvls[-10:]
                    s.lt_l.append((pm, s.itl_hist[-2][1])); s.lt_l[:] = s.lt_l[-6:]

        return ev.new_high or ev.new_low

    def _bos_choch(self, i: int, candles: list[Candle], tol: float) -> None:
        """(2) close가 미해제 cur_ith/cur_itl 돌파 시 ms_dir·넥라인 갱신 + OB·SD목표 레벨.

        v8 engine.py 400-426 1:1. (v8은 BOS/CHoCH를 구분하지 않고 ms_dir·넥라인만 갱신.)

        ★ 봉내 순서 (블록당): BOS판정(close vs cur_*) → 넥라인/OB/SD 적립 → cur_* 해제.
          WHY: 적립이 '돌파된 값(broken)'을 쓰므로 해제보다 먼저 적립해야 함. 또 해제를
          마지막에 해야 다음 봉에 같은 레벨로 BOS가 중복 발화하지 않는다(여기서 1회만).
        ★ high·low 블록은 독립(if 두 개) — v8과 동일.
        ★ lookback은 과거 봉(i-k, k≥1)만 → 미래참조 없음.
        """
        s = self.state
        cfg = self.cfg
        c_i = candles[i].close

        # 불리시 BOS: 종가가 미해제 ITH 돌파
        if s.cur_ith is not None and c_i > s.cur_ith:
            broken = s.cur_ith
            s.ms_dir = 1
            s.neckline = broken
            self._add_level(broken, cfg.w_neck, LevelSource.NECK, tol)        # 넥라인 src3
            # 오더블록: BOS 직전 12봉 내 '마지막 약세(반대색) 캔들'의 저가 (v8 404-406)
            for k in range(1, min(13, i + 1)):
                if candles[i - k].close < candles[i - k].open:
                    self._add_level(candles[i - k].low, cfg.w_ob, LevelSource.OB, tol)  # src9
                    break
            # SD 목표: 직전 스윙폭(broken-마지막확정ITL) × {1,2,2.5} 상방 투영 (v8 407-412)
            if s.last_conf_itl is not None:
                sw = broken - s.last_conf_itl
                if sw > 0:
                    self._add_level(broken + 1.0 * sw, cfg.w_fib * 0.8, LevelSource.FIB, tol)
                    self._add_level(broken + 2.0 * sw, cfg.w_fib * 0.8, LevelSource.FIB, tol)
                    self._add_level(broken + 2.5 * sw, cfg.w_fib * 0.6, LevelSource.FIB, tol)
            s.cur_ith = None

        # 베어리시 BOS: 종가가 미해제 ITL 하향 돌파
        if s.cur_itl is not None and c_i < s.cur_itl:
            broken = s.cur_itl
            s.ms_dir = -1
            s.neckline = broken
            self._add_level(broken, cfg.w_neck, LevelSource.NECK, tol)
            # 오더블록: 마지막 강세(반대색) 캔들의 고가 (v8 417-419)
            for k in range(1, min(13, i + 1)):
                if candles[i - k].close > candles[i - k].open:
                    self._add_level(candles[i - k].high, cfg.w_ob, LevelSource.OB, tol)
                    break
            # SD 목표: (마지막확정ITH-broken) × {1,2,2.5} 하방 투영 (v8 420-425)
            if s.last_conf_ith is not None:
                sw = s.last_conf_ith - broken
                if sw > 0:
                    self._add_level(broken - 1.0 * sw, cfg.w_fib * 0.8, LevelSource.FIB, tol)
                    self._add_level(broken - 2.0 * sw, cfg.w_fib * 0.8, LevelSource.FIB, tol)
                    self._add_level(broken - 2.5 * sw, cfg.w_fib * 0.6, LevelSource.FIB, tol)
            s.cur_itl = None

    def _fvg_and_rebalance(self, i: int, candles: list[Candle], tol: float) -> None:
        """(3) 3봉 FVG 생성(src6) + 기존 FVG 충전 시 리밸런스 극점(reb_ith/itl, src10) 적립.

        v8 engine.py 428-442 1:1. 생성 직후 같은 봉의 fill-check 루프를 돈다(=새 FVG도
        그 봉에서 충전 판정 대상). 미래참조 없음(i, i-2만 참조).
        """
        s = self.state
        cfg = self.cfg
        cur = candles[i]
        if i >= 2:
            prev2 = candles[i - 2]
            if cur.low > prev2.high:                              # 상승갭(지지)
                self._add_level(prev2.high, cfg.w_fvg, LevelSource.FVG, tol)
                s.fvgs.append(Fvg(top=cur.low, bot=prev2.high, direction=1))
            if cur.high < prev2.low:                              # 하락갭(저항)
                self._add_level(prev2.low, cfg.w_fvg, LevelSource.FVG, tol)
                s.fvgs.append(Fvg(top=prev2.low, bot=cur.high, direction=-1))
            if len(s.fvgs) > 30:
                s.fvgs.pop(0)
        # 충전 → 리밸런스 극점 (v8 437-442)
        for f in s.fvgs:
            if f.filled:
                continue
            if f.direction == -1 and cur.high >= f.bot:
                f.filled = True
                s.reb_ith = cur.high
                self._add_level(cur.high, cfg.w_lt, LevelSource.LT_REB, tol)
            if f.direction == 1 and cur.low <= f.top:
                f.filled = True
                s.reb_itl = cur.low
                self._add_level(cur.low, cfg.w_lt, LevelSource.LT_REB, tol)

    def _wick_levels(self, i: int, candles: list[Candle], tol: float) -> None:
        """(4) 긴 꼬리 비율 ≥ wick_ratio면 몸통 경계를 꼬리 레벨(src1)로 적립.
        v8 engine.py 444-449 1:1."""
        cfg = self.cfg
        cur = candles[i]
        rng = cur.high - cur.low
        if rng > 0:
            body_hi = max(cur.open, cur.close)
            body_lo = min(cur.open, cur.close)
            if (cur.high - body_hi) / rng >= cfg.wick_ratio:
                self._add_level(body_hi, cfg.w_wick, LevelSource.WICK, tol)
            if (body_lo - cur.low) / rng >= cfg.wick_ratio:
                self._add_level(body_lo, cfg.w_wick, LevelSource.WICK, tol)

    def _volume_levels(self, i: int, candles: list[Candle], tol: float) -> None:
        """(4b) 최근 vol_lvl_len봉 최고거래량 캔들이 바뀌면 그 몸통 경계를 고거래량 레벨(src11)로.
        v8 engine.py 451-458 1:1. argmax는 첫 최대(동률 시 가장 이른 봉) — np.argmax와 동일.
        미래참조 없음(윈도우 [i-len+1 .. i])."""
        cfg = self.cfg
        s = self.state
        if not cfg.use_vol_lvl or i < cfg.vol_lvl_len:
            return
        w0 = i - cfg.vol_lvl_len + 1
        best = w0
        for idx in range(w0 + 1, i + 1):
            if candles[idx].volume > candles[best].volume:        # 엄격 비교 → 첫 최대 유지
                best = idx
        if best != s.hv_bar:
            s.hv_bar = best
            ch = candles[best]
            self._add_level(max(ch.open, ch.close), cfg.w_vol, LevelSource.VOL, tol)
            self._add_level(min(ch.open, ch.close), cfg.w_vol, LevelSource.VOL, tol)

    def _consume_on_touch(self, i: int, candles: list[Candle]) -> None:
        """(5) 봉 [low, high]가 활성 레벨 가격을 포함하면 그 레벨 소멸(active=False).

        v8 engine.py 460-463 1:1.
        - 닿음 정의: low <= price <= high (봉 전체 범위, 꼬리 포함). 종가 기준 아님.
        - 소멸 = active=False 한 번 (pop/remove 아님 — 가중치·이력 보존, confluence가 active만 읽음).
        - 순회 = levels 적립 순서 그대로(재정렬 없음) → 결정적.
        - 동적 라인(self.state.dyn_lines)·돈치안·가격대(zone)·lt_lvls는 이 store에 없어
          여기서 건드리지 않음(원칙7: 비소멸). 따라서 consume 대상은 soft levels뿐.

        ★ 호출 위치(update): 적립 (2)~(4b) '뒤', 피보 (6) '앞'.
          → (2)~(4b)에서 이번 봉에 적립된 레벨은 같은 봉에서 소멸될 수 있음(v8 동일).
          → (6)/(6b) 피보는 consume '뒤'에 적립되므로 같은 봉에서는 소멸 안 됨(다음 봉부터).
        """
        lo = candles[i].low
        hi = candles[i].high
        for lv in self.state.levels:
            if lv.active and lo <= lv.price <= hi:
                lv.active = False

    def _fib_ladder(self, i: int, candles: list[Candle], atr_i: float, tol: float) -> None:
        """(6) 레그(last_conf_ith/itl) 변경 시 1회만, IT 확장 피보 적립(+dual_fib면 1세트 더).

        v8 engine.py 465-489 1:1. leg_sig=두 앵커 합으로 '레그 변경' 감지(인플레이션 가드).
        피보 가격 산출은 neely.fib_ladder(무상태). 가중치(w_fib, dual은 ×0.7)·src·적립은 엔진.
        ★ consume(5) '뒤'라 이번 봉 적립 피보는 같은 봉에서 소멸 안 됨.
        """
        s = self.state
        cfg = self.cfg
        if s.last_conf_ith is None or s.last_conf_itl is None:
            return
        sig = s.last_conf_ith + s.last_conf_itl
        if sig == s.leg_sig:
            return
        diff = s.last_conf_ith - s.last_conf_itl
        sz_ok = diff >= cfg.min_leg_atr * atr_i and (cfg.max_leg_atr <= 0 or diff <= cfg.max_leg_atr * atr_i)
        if not (diff > 0 and sz_ok):
            return
        s.leg_sig = sig
        ib = s.ith_hist[-1][1] if s.ith_hist else -1
        lb = s.itl_hist[-1][1] if s.itl_hist else -1
        g_dir = 1 if ib > lb else -1
        # IT 세트: [itl, ith] 레그
        for py in fib_ladder(s.last_conf_itl, s.last_conf_ith, g_dir, cfg.ext_multiples):
            self._add_level(py, cfg.w_fib, LevelSource.FIB, tol)
        # dual: 2번째 최신 반대-타입 피벗을 추가 앵커로 (겹침=병합 부스트)
        if cfg.dual_fib:
            alt = self._piv.recent_pivot(PT_LOW if g_dir == 1 else PT_HIGH, 1)
            if alt is not None:
                ap_ = alt.price
                if g_dir == 1:
                    low, high = ap_, s.last_conf_ith        # span = ITH - anchor
                else:
                    low, high = s.last_conf_itl, ap_        # span = anchor - ITL
                if high - low > 0:
                    for py in fib_ladder(low, high, g_dir, cfg.ext_multiples):
                        self._add_level(py, cfg.w_fib * 0.7, LevelSource.FIB, tol)

    def _lt_fib(self, i: int, candles: list[Candle], tol: float) -> None:
        """(6b) LT 스윙 앵커(lt_h/lt_l 최신)로 확장 피보 1세트 — IT 세트와 겹치면 자동 부스트.
        v8 engine.py 490-500 1:1. lt_sig로 LT 레그 변경 1회 가드."""
        s = self.state
        cfg = self.cfg
        if not (cfg.lt_fib and s.lt_h and s.lt_l):
            return
        lH, lHb = s.lt_h[-1]
        lL, lLb = s.lt_l[-1]
        sigL = lH + lL
        if sigL == s.lt_sig or not (lH > lL):
            return
        s.lt_sig = sigL
        gd = 1 if lHb > lLb else -1
        for py in fib_ladder(lL, lH, gd, cfg.ext_multiples):
            self._add_level(py, cfg.w_fib, LevelSource.FIB, tol)

    def _rebuild_dynlines(self, i: int, atr_i: float, htf_i: float = 0.0) -> None:
        """(7) 피벗 확정 시 빗각(담는선)·부채살·포크(방탄앵커)를 재계산해 dyn_lines 교체.

        v8 engine.py 238-314 1:1. 비소멸(소멸 패스 면제) — confluence가 score 시점 value(bar)로 평가.
        - 담는선: 극점(고:최고가, 저:최저가) 앵커 → 이후 같은타입 피벗을 tol 내 '담는' 가장
          최근 기울기.
        - 부채살: down_ctx면 최고가, 아니면 최저가 앵커 → 이후 구조 피벗 fan_n개로 부채.
        - 피치포크: forkAuto 방탄 앵커(P0 극점타입 → 반대 → 같은타입 순서, 폴백 포함) median+tine.
        htf_i: down_ctx 판정용(ms_dir==0일 때만 영향). 미래참조 없음(struct=확정 분류피벗만).
        """
        s = self.state
        cfg = self.cfg
        s.dyn_lines.clear()
        if not cfg.use_diagonals:
            return
        struct = [(p.bar, p.price, p.ptype) for p in s.pivots if p.classified >= 1]
        if len(struct) < 2:
            return
        highs = [(b, pr) for b, pr, t in struct if t == PT_HIGH]
        lows = [(b, pr) for b, pr, t in struct if t == PT_LOW]
        down_ctx = s.ms_dir < 0 or (s.ms_dir == 0 and htf_i < 0)
        tol = cfg.conf_tol_atr * atr_i

        def containing(anchor, pts, ty):
            ab, ap = anchor
            for k in range(len(pts) - 1, -1, -1):
                tb, tp_ = pts[k]
                if tb <= ab:
                    continue
                sl = (tp_ - ap) / max(1, tb - ab)
                ok = True
                for jb, jp in pts:
                    if jb > ab:
                        lv = ap + sl * (jb - ab)
                        if ty == PT_HIGH and jp > lv + tol:
                            ok = False; break
                        if ty == PT_LOW and jp < lv - tol:
                            ok = False; break
                if ok:
                    return sl
            return None

        # ① 담는 선
        if highs:
            anc = max(highs, key=lambda x: x[1])
            sl = containing(anc, highs, PT_HIGH)
            if sl is not None:
                s.dyn_lines.append(DynLine(anc[0], anc[1], sl, LevelSource.DIAG, cfg.w_diag))
        if lows:
            anc = min(lows, key=lambda x: x[1])
            sl = containing(anc, lows, PT_LOW)
            if sl is not None:
                s.dyn_lines.append(DynLine(anc[0], anc[1], sl, LevelSource.DIAG, cfg.w_diag))

        # ② 부채살
        anc = (max(highs, key=lambda x: x[1]) if down_ctx and highs
               else min(lows, key=lambda x: x[1]) if lows else None)
        if anc and cfg.fan_n > 0:
            cnt = 0
            for b, pr, t in struct:
                if cnt >= cfg.fan_n:
                    break
                if b > anc[0]:
                    sl = (pr - anc[1]) / max(1, b - anc[0])
                    s.dyn_lines.append(DynLine(anc[0], anc[1], sl, LevelSource.DIAG, cfg.w_diag * 0.8))
                    cnt += 1

        # ③ 피치포크 (방탄 앵커)
        ty0 = PT_HIGH if down_ctx else PT_LOW
        cands = sorted([st for st in struct if st[2] == ty0],
                       key=lambda x: -x[1] if ty0 == PT_HIGH else x[1])
        fork = None
        for ab, ap, _ in cands:
            t1 = next(((b2, p2) for b2, p2, t2 in struct if t2 == -ty0 and b2 > ab), None)
            if not t1:
                continue
            t2 = next(((b3, p3) for b3, p3, t3 in struct if t3 == ty0 and b3 > t1[0]), None)
            if not t2:
                continue
            fork = (ab, ap, t1, t2)
            break
        if fork is None:   # 폴백: 가장 오래된 유효 앵커
            for ab, ap, ty_ in struct:
                t1 = next(((b2, p2) for b2, p2, t2 in struct if t2 == -ty_ and b2 > ab), None)
                if not t1:
                    continue
                t2 = next(((b3, p3) for b3, p3, t3 in struct if t3 == ty_ and b3 > t1[0]), None)
                if not t2:
                    continue
                fork = (ab, ap, t1, t2)
                break
        if fork:
            ab, ap, (b1, p1), (b2, p2) = fork
            mslope = ((p1 + p2) / 2 - ap) / max(1, (b1 + b2) / 2 - ab)
            s.dyn_lines.append(DynLine(ab, ap, mslope, LevelSource.FORK, cfg.w_fork))            # median
            off1 = p1 - (ap + mslope * (b1 - ab))
            off2 = p2 - (ap + mslope * (b2 - ab))
            s.dyn_lines.append(DynLine(ab, ap + off1, mslope, LevelSource.FORK, cfg.w_fork))     # tine L
            s.dyn_lines.append(DynLine(ab, ap + off2, mslope, LevelSource.FORK, cfg.w_fork))     # tine H
