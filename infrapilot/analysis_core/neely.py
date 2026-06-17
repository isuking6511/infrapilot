"""피보 확장 사다리 생성 — 무상태 순수함수.

정본 v8 engine.py 465-500 의 'py = ...' 확장 산식만 떼어낸다. 레그(앵커) 선택·게이팅
(leg_sig/lt_sig)·가중치·store 적립은 호출자(StructureEngine._fib_ladder/_lt_fib) 책임.
neely는 '어떤 가격들이 나오는가'(기하)만 안다 → 무상태·결정적·외부 import 없음.

범위 (사용자 확정):
  포함: 확장 사다리 가격 생성 (IT 세트 / dual / LT 세트 공용).
  제외: 되돌림 규칙(Rule 1~7)·임펄스 하드룰 — v4 채택 엔진의 진입조건이 아니라
        패널 표시용. 필요해지면 별도 모듈(neely_context 등)로 분리.
"""
from __future__ import annotations

from collections.abc import Sequence


def fib_ladder(low: float, high: float, direction: int, multiples: Sequence[float]) -> list[float]:
    """[low, high] 레그에 ext 배수를 적용한 확장 레벨 가격들(양수만, 입력 배수 순서 유지).

    v8 산식 1:1:
      direction == 1  → low + m*(high-low)
      direction == -1 → high - m*(high-low)

    호출자가 넘기는 (low, high, direction):
      IT 세트:  low=last_conf_itl, high=last_conf_ith, dir=레그방향(g_dir)
      dual:     dir1 → low=anchor, high=last_conf_ith   (span=ITH-anchor)
                dir-1→ low=last_conf_itl, high=anchor    (span=anchor-ITL)
      LT 세트:  low=lt_low, high=lt_high, dir=LT레그방향(gd)
    """
    diff = high - low
    out: list[float] = []
    for m in multiples:
        py = low + m * diff if direction == 1 else high - m * diff
        if py > 0:
            out.append(py)
    return out
