"""확정 피벗 탐지 + 3피벗 ITH/ITL 분류 (lookahead 없음).

정본: research/pine/wcse_v8/engine.py 의 피벗 블록(366-398) + confirm_it(221-230).
  (v3_19 wcse_backtest/structure.py 와 피벗 로직은 byte-identical — 8개 케이스
   교차검증 완료. 그래서 어느 쪽을 봐도 결과는 같다. 정본은 v8로 통일.)

v8 매핑:
  win_h/win_l max·min & count==1   → _is_pivot_high/_is_pivot_low (strict max/min)
  idx_recent                       → PivotEngine._recent_idx
  confirm_it                       → PivotEngine._classify_3pivot
  피벗 확정 루프 (j=i-swLen, j>=swLen) → PivotEngine.update

원본과의 차이(의도적):
  - numpy 배열(highs/lows) → list[Candle] 위에서 동작. analysis_core 의존성 최소화.
  - ts: pd.Timestamp → int(epoch ms). models.Candle/Pivot 계약을 따름.
  로직(비교·인덱싱·분류 조건)은 한 글자도 바꾸지 않음 → 골든 스냅샷 호환.

왜 pivots만 따로: 피벗 '확정'은 lookahead 통제의 핵심 지점이라 한 곳에 격리해야
검증(전체 vs 누적 일치)이 깔끔하다. BOS/CHoCH·FVG·OB·LTH 등 상위 구조는 ict.py로.

★ lookahead 금지 핵심:
  i번째 봉의 피벗 여부는 center = i - swLen 봉에 대해 판정하되, 이웃 검사 범위가
  [center-swLen .. center+swLen] = [i-2·swLen .. i] 이므로 update(i)는 절대
  i보다 미래 봉을 읽지 않는다. 따라서 bar(center)의 피벗은 i=center+swLen 시점에
  가서야 확정된다(Pine ta.pivothigh(h, swLen, swLen)와 동일한 확정지연).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import Candle, Pivot, PT_HIGH, PT_LOW


@dataclass
class PivotEvent:
    """update(i) 한 번의 결과 통지. ict 등 상위가 내부 배열을 직접 읽지 않고
    이벤트만 보고 구조를 전진시키도록(느슨한 결합).

    필드는 현재 소비자(ict 스텝1)가 실제로 쓰는 것만 — YAGNI.
    classified_*: 이번 봉에 새로 ITH/ITL로 '분류된' 피벗의 (price, bar). 없으면 None.
    (bar가 필요한 이유: 상위에서 LTH/LTL 히스토리에 (price, bar)로 쌓기 때문)
    """
    new_high: bool = False
    new_low: bool = False
    classified_high: Optional[tuple[float, int]] = None
    classified_low: Optional[tuple[float, int]] = None


def _is_pivot_high(candles: list[Candle], i: int, left: int, right: int) -> bool:
    """candles[i-right].high 가 좌 left · 우 right 이웃보다 strict 하게 큰가.

    structure._is_pivot_high 1:1. `>=` 면 실패 → 동일가 무승부 시 피벗 아님(strict).
    """
    center = i - right
    if center - left < 0 or i >= len(candles):
        return False
    pivot_val = candles[center].high
    for j in range(center - left, center + right + 1):
        if j == center:
            continue
        if candles[j].high >= pivot_val:
            return False
    return True


def _is_pivot_low(candles: list[Candle], i: int, left: int, right: int) -> bool:
    """structure._is_pivot_low 1:1 (`<=` 면 실패)."""
    center = i - right
    if center - left < 0 or i >= len(candles):
        return False
    pivot_val = candles[center].low
    for j in range(center - left, center + right + 1):
        if j == center:
            continue
        if candles[j].low <= pivot_val:
            return False
    return True


@dataclass
class PivotEngine:
    """봉을 하나씩 먹여 확정 피벗을 누적하는 상태 머신.

    structure.StructureEngine 의 피벗 전용 축소판. update(i)는 candles[0..i]만
    참조하므로, 전체를 한 번에 돌리든 봉을 하나씩 흘리든 결과가 같아야 한다
    (lookahead 없음의 정의). 이 불변식을 테스트로 고정한다.
    """
    sw_len: int = 8
    max_piv: int = 80
    pivots: list[Pivot] = field(default_factory=list)

    def reset(self) -> None:
        self.pivots = []

    def _push(self, p: Pivot) -> None:
        """structure._push_pivot 1:1 — 추가 후 max_piv 초과 시 가장 오래된 것 제거."""
        self.pivots.append(p)
        if len(self.pivots) > self.max_piv:
            self.pivots.pop(0)

    def _recent_idx(self, ptype: int, k: int) -> int:
        """k번째(0=최신) 동종 피벗의 리스트 인덱스, 없으면 -1. structure._recent_idx 1:1."""
        cnt = 0
        for i in range(len(self.pivots) - 1, -1, -1):
            if self.pivots[i].ptype == ptype:
                if cnt == k:
                    return i
                cnt += 1
        return -1

    def recent_pivot(self, ptype: int, k: int = 0) -> Optional[Pivot]:
        """k번째(0=최신) 동종 피벗 객체, 없으면 None. 상위 모듈이 내부 인덱스/배열을
        직접 다루지 않고 피벗을 얻는 접근자(느슨한 결합). dual-fib 앵커 등에 사용."""
        idx = self._recent_idx(ptype, k)
        return self.pivots[idx] if idx >= 0 else None

    def _classify_3pivot(self, ptype: int) -> Optional[tuple[float, int]]:
        """최근 3개 동종 피벗 중 '중간'이 양 옆을 strict 초과하면 ITH/ITL로 분류.

        structure._classify_3pivot 1:1. 미래 피벗 참조 없음 — 그 시점까지 누적된
        피벗만 사용. 분류되면 가운데 피벗의 classified=1 로 세팅하고 (price, bar) 반환.
        """
        ai = self._recent_idx(ptype, 2)  # 가장 오래된
        bi = self._recent_idx(ptype, 1)  # 중간(후보)
        ci = self._recent_idx(ptype, 0)  # 가장 최신
        if ai < 0 or bi < 0 or ci < 0:
            return None
        pa = self.pivots[ai].price
        pb = self.pivots[bi].price
        pc = self.pivots[ci].price
        if ptype == PT_HIGH and pa < pb and pc < pb:
            self.pivots[bi].classified = 1
            return pb, self.pivots[bi].bar
        if ptype == PT_LOW and pa > pb and pc > pb:
            self.pivots[bi].classified = 1
            return pb, self.pivots[bi].bar
        return None

    def update(self, i: int, candles: list[Candle]) -> PivotEvent:
        """봉 i 관측 후 상태 전진. structure.StructureEngine.update 의 (1)+(2)단계.

        high·low를 독립적으로 검사(원본도 elif 아닌 if 두 개) → 한 봉에서 양쪽
        피벗이 동시에 확정될 수 있음.

        반환: 이번 봉의 PivotEvent. 반환값을 무시해도 동작 동일(하위호환) —
        detect_pivots 등 기존 호출자는 그대로 작동한다.
        """
        ev = PivotEvent()
        if _is_pivot_high(candles, i, self.sw_len, self.sw_len):
            pb = i - self.sw_len
            c = candles[pb]
            self._push(Pivot(bar=pb, ts=c.ts, price=c.high, ptype=PT_HIGH))
            ev.new_high = True
            ev.classified_high = self._classify_3pivot(PT_HIGH)
        if _is_pivot_low(candles, i, self.sw_len, self.sw_len):
            pb = i - self.sw_len
            c = candles[pb]
            self._push(Pivot(bar=pb, ts=c.ts, price=c.low, ptype=PT_LOW))
            ev.new_low = True
            ev.classified_low = self._classify_3pivot(PT_LOW)
        return ev


def detect_pivots(candles: list[Candle], sw_len: int = 8, max_piv: int = 80) -> list[Pivot]:
    """전체 봉을 순차 처리해 확정 피벗 리스트 반환(편의 함수).

    내부적으로도 PivotEngine을 봉 단위로 전진시킨다 — '전체 한번에'와 '하나씩
    누적'이 같은 코드경로라 결과가 결정적으로 일치한다.
    """
    eng = PivotEngine(sw_len=sw_len, max_piv=max_piv)
    for i in range(len(candles)):
        eng.update(i, candles)
    return eng.pivots
