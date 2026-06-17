"""포지션 사이징 — 정본 v8 engine.py 587 (qty = equity·risk_pct/100 / |entry-stop|) 1:1.

risk.py는 Setup을 받아 '수량(units)만' 계산한다. 진입 판정·주문 집행은 하지 않는다
(InfraPilot은 분석/시각화 플랫폼, 자동매매 아님 — CLAUDE.md 정체성). 순수 함수.
"""
from __future__ import annotations

import math

from .models import Setup


def position_size(setup: Setup, equity: float, risk_pct: float = 1.0) -> float:
    """리스크 기반 수량. v8: equity × risk_pct/100 ÷ |entry-stop|.

    손절폭이 0/음수/비정상(NaN·inf)이거나 equity·risk_pct가 비정상/비양수면 0.0 반환
    (0으로 나누기 방지 — 포지션 없음).
    """
    risk_per_unit = abs(setup.entry - setup.stop)
    if not math.isfinite(risk_per_unit) or risk_per_unit <= 0:
        return 0.0
    if not (math.isfinite(equity) and math.isfinite(risk_pct)) or equity <= 0 or risk_pct <= 0:
        return 0.0
    return equity * risk_pct / 100.0 / risk_per_unit
