"""Neely retracement rules + impulse hard rules.

Pine f_neely() (line 463-500): Rules 1~7 from m2/m1, condition a~f from m0/m1,
category i~iii from m3/m2. Direction: m2/m1 <= 0.618 -> impulse env, else
corrective. Used as CONTEXT in the Pine indicator. We expose it as both:
  - neely_context()  → table-style label (read-only)
  - neely_gate()     → boolean usable as an entry filter
"""
from __future__ import annotations

from typing import Optional

from structure import StructureState


def neely_context(state: StructureState, close_now: float) -> dict:
    """Return Pine-style {rule, cond, cat, dir_text, lean_text} or rule=0 if N<4."""
    pivots = state.pivots
    if len(pivots) < 4:
        return {"rule": 0, "cond": "", "cat": "", "dir_text": "—", "lean": "—",
                "r2": None, "directional": False}
    p3 = pivots[-4].price
    p2 = pivots[-3].price
    p1 = pivots[-2].price
    p0 = pivots[-1].price
    m0 = abs(p2 - p3)
    m1 = abs(p1 - p2)
    m2 = abs(p0 - p1)
    m3 = abs(close_now - p0)
    if m1 <= 0:
        return {"rule": 0, "cond": "", "cat": "", "dir_text": "—", "lean": "—",
                "r2": None, "directional": False}
    r2 = m2 / m1
    r0 = m0 / m1
    r3 = m3 / m2 if m2 > 0 else 0.0

    if   r2 < 0.382: rule = 1
    elif r2 < 0.615: rule = 2
    elif r2 <= 0.621: rule = 3
    elif r2 < 1.0:    rule = 4
    elif r2 < 1.618:  rule = 5
    elif r2 <= 2.618: rule = 6
    else:             rule = 7

    directional = r2 <= 0.618
    dir_text = "방향성(추세)" if directional else "비방향성(조정)"
    lean = ("임펄스 우세 → 추세 지속" if rule <= 2 else
            "경계(애매) → 관망"          if rule == 3 else
            "조정 우세 → 되돌림"          if rule == 4 else
            "100%+ 되돌림 → 직전 파동 무효, 재라벨")

    # v4 — Condition schemes per Neely MEW chapter 3
    if rule == 1:
        cond = "a" if r0 < 0.618 else "b" if r0 < 1.0 else "c" if r0 <= 1.618 else "d"
    elif rule == 2:
        cond = "a" if r0 < 0.382 else "b" if r0 < 0.618 else "c" if r0 < 1.0 else "d" if r0 <= 1.618 else "e"
    elif rule == 3:
        cond = "a" if r0 < 0.382 else "b" if r0 < 0.618 else "c" if r0 < 1.0 else "d" if r0 < 1.618 else "e" if r0 <= 2.618 else "f"
    elif rule == 4:
        cond = "a" if r0 < 0.382 else "b" if r0 < 1.0 else "c" if r0 < 1.618 else "d" if r0 <= 2.618 else "e"
    else:
        # v4: Rules 5/6/7 share their own simpler scheme
        cond = "a" if r0 < 1.0 else "b" if r0 < 1.618 else "c" if r0 <= 2.618 else "d"

    # v4: Category limited to Rule 4 only
    cat = ""
    if rule == 4:
        cat = "" if r3 < 1.0 else "i" if r3 < 1.618 else "ii" if r3 <= 2.618 else "iii"

    return {"rule": rule, "cond": cond, "cat": cat, "dir_text": dir_text,
            "lean": lean, "r2": r2, "directional": directional}


def neely_gate_ok(state: StructureState, close_now: float) -> bool:
    """When `neely_gate=on` is enabled in the experiment grid, we only allow
    entries while the recent leg ratio is directional (m2/m1 <= 0.618 i.e.
    Rule 1 or 2). Insufficient data → False (block to be safe).
    """
    ctx = neely_context(state, close_now)
    if ctx["rule"] == 0:
        return False
    return bool(ctx["directional"])


def impulse_valid(state: StructureState) -> Optional[bool]:
    """Pine line 871-876: 5-leg impulse hard rules on the last 6 pivots.
    Returns True / False / None (None = not enough pivots)."""
    pivots = state.pivots
    if len(pivots) < 6:
        return None
    P0 = pivots[-6].price
    P1 = pivots[-5].price
    P2 = pivots[-4].price
    P3 = pivots[-3].price
    P4 = pivots[-2].price
    P5 = pivots[-1].price
    L1 = abs(P1 - P0); L3 = abs(P3 - P2); L5 = abs(P5 - P4)
    wdir = -1 if P5 < P0 else 1
    if wdir == -1:
        alt_ok = (P1 < P0 and P2 > P1 and P3 < P2 and P4 > P3 and P5 < P4)
        w2_ok = P2 < P0
        w4_ok = P4 < P1
        w5_progress = P5 < P3
    else:
        alt_ok = (P1 > P0 and P2 < P1 and P3 > P2 and P4 < P3 and P5 > P4)
        w2_ok = P2 > P0
        w4_ok = P4 > P1
        w5_progress = P5 > P3
    w3_not_shortest = not (L3 < L1 and L3 < L5)
    return bool(alt_ok and w2_ok and w3_not_shortest and w4_ok and w5_progress)
