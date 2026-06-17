"""ict.StructureEngine 단계별 골든 테스트 — v8 정본 대조.

원칙: 한 단계 stub을 채울 때마다 그 단계가 '소유하는' 상태를 v8의 해당 블록과 1:1 비교.
StructureEngine.update()는 아직 미구현 스텝(NotImplementedError)이 있으므로, 각 테스트는
해당 스텝의 private 메서드만 봉 단위로 호출해 격리 검증한다.
"""
from __future__ import annotations

import math
import random

from infrapilot.analysis_core.models import Candle
from infrapilot.analysis_core.ict import StructureEngine, StructureConfig


def _vol_stream(seed: int):
    """가격 rng와 독립된 거래량 스트림(시드 고정). 가끔 스파이크 → argmax 전이 유발.
    별도 Random이라 가격 시퀀스(=피벗/분류 커버리지)를 건드리지 않는다."""
    vrng = random.Random(seed ^ 0x5151)
    def nxt() -> float:
        v = vrng.uniform(0.5, 3.0)
        return v * 4.0 if vrng.random() < 0.1 else v
    return nxt


def _synthetic_candles(n: int = 400, seed: int = 7) -> list[Candle]:
    """test_analysis_pivots 와 동일한 합성 OHLCV(시드 고정) + 변동 거래량."""
    rng = random.Random(seed)
    vol = _vol_stream(seed)
    out: list[Candle] = []
    price = 100.0
    ts = 1_700_000_000_000
    for _ in range(n):
        drift = math.sin(len(out) / 13.0) * 1.5
        step = drift + rng.uniform(-1.0, 1.0)
        o = price
        c = price + step
        hi = max(o, c) + rng.uniform(0.0, 1.2)
        lo = min(o, c) - rng.uniform(0.0, 1.2)
        out.append(Candle(ts=ts, open=o, high=hi, low=lo, close=c, volume=vol()))
        price = c
        ts += 900_000
    return out


def _choppy_candles(n: int = 1500, seed: int = 2) -> list[Candle]:
    """분류 ITH/ITL을 충분히(≥3) 만들어 LTH/LTL 분기를 실제로 태우는 거친 데이터.
    두 주파수 합 → 봉마다 변동 + 피크 높이가 들쭉날쭉(중간이 양옆보다 높/낮 유발)."""
    rng = random.Random(seed)
    vol = _vol_stream(seed)
    out: list[Candle] = []
    ts = 1_700_000_000_000
    for i in range(n):
        c = 100 + 3.0 * math.sin(i / 9.0) + 5.0 * math.sin(i / 23.0) + rng.uniform(-1.5, 1.5)
        o = 100 + 3.0 * math.sin((i - 1) / 9.0) + 5.0 * math.sin((i - 1) / 23.0)
        hi = max(o, c) + rng.uniform(0.0, 1.0)
        lo = min(o, c) - rng.uniform(0.0, 1.0)
        out.append(Candle(ts=ts, open=o, high=hi, low=lo, close=c, volume=vol()))
        ts += 900_000
    return out


def _v8_step1_reference(candles, sw=8, max_piv=80):
    """정본 v8 engine.py 368-398 의 '스텝1만' 복제(BOS 스텝2 제외).

    스텝1이 소유하는 상태(pivots/분류, ith_hist/itl_hist, lt_lvls/lt_h/lt_l,
    last_conf_*, 그리고 스텝2에서 해제되기 전의 cur_*)를 그대로 산출.
    """
    import numpy as np
    h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles])
    pivP, pivB, pivT, pivK = [], [], [], []
    ith_hist, itl_hist = [], []
    lt_lvls, lt_h, lt_l = [], [], []
    cur_ith = cur_itl = None
    last_conf_ith = last_conf_itl = None

    def idx_recent(ty, k):
        cnt = 0
        for ii in range(len(pivT) - 1, -1, -1):
            if pivT[ii] == ty:
                if cnt == k:
                    return ii
                cnt += 1
        return -1

    def confirm_it(ty):
        ai, bi, ci = idx_recent(ty, 2), idx_recent(ty, 1), idx_recent(ty, 0)
        if ai < 0 or bi < 0 or ci < 0:
            return None
        pa, pb_, pc = pivP[ai], pivP[bi], pivP[ci]
        ok = (pa < pb_ > pc) if ty == 1 else (pa > pb_ < pc)
        if not ok:
            return None
        pivK[bi] = 1
        return pb_, pivB[bi]

    for i in range(len(candles)):
        j = i - sw
        if j >= sw:
            win_h = h[j - sw:i + 1]
            win_l = l[j - sw:i + 1]
            if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                pivP.append(h[j]); pivB.append(j); pivT.append(1); pivK.append(0)
                r = confirm_it(1)
                if r:
                    cur_ith, _ = r; last_conf_ith = r[0]
                    ith_hist.append(r)
                    if len(ith_hist) >= 3:
                        (pm1, _), (pm, _), (pp1, _) = ith_hist[-3], ith_hist[-2], ith_hist[-1]
                        if pm > pm1 and pm > pp1:
                            lt_lvls.append(pm); lt_lvls[:] = lt_lvls[-10:]
                            lt_h.append((pm, ith_hist[-2][1])); lt_h[:] = lt_h[-6:]
            if l[j] == win_l.min() and (win_l == l[j]).sum() == 1:
                pivP.append(l[j]); pivB.append(j); pivT.append(-1); pivK.append(0)
                r = confirm_it(-1)
                if r:
                    cur_itl, _ = r; last_conf_itl = r[0]
                    itl_hist.append(r)
                    if len(itl_hist) >= 3:
                        (pm1, _), (pm, _), (pp1, _) = itl_hist[-3], itl_hist[-2], itl_hist[-1]
                        if pm < pm1 and pm < pp1:
                            lt_lvls.append(pm); lt_lvls[:] = lt_lvls[-10:]
                            lt_l.append((pm, itl_hist[-2][1])); lt_l[:] = lt_l[-6:]
            while len(pivP) > max_piv:
                pivP.pop(0); pivB.pop(0); pivT.pop(0); pivK.pop(0)

    return {
        "pivots": [(pivB[i], pivT[i], round(float(pivP[i]), 8), pivK[i]) for i in range(len(pivP))],
        "ith_hist": [(round(float(p), 8), b) for p, b in ith_hist],
        "itl_hist": [(round(float(p), 8), b) for p, b in itl_hist],
        "lt_lvls": [round(float(x), 8) for x in lt_lvls],
        "lt_h": [(round(float(p), 8), b) for p, b in lt_h],
        "lt_l": [(round(float(p), 8), b) for p, b in lt_l],
        "last_conf_ith": None if last_conf_ith is None else round(float(last_conf_ith), 8),
        "last_conf_itl": None if last_conf_itl is None else round(float(last_conf_itl), 8),
        "cur_ith": None if cur_ith is None else round(float(cur_ith), 8),
        "cur_itl": None if cur_itl is None else round(float(cur_itl), 8),
    }


def _engine_step1_state(candles, sw=8, max_piv=80):
    """우리 엔진의 스텝1만 격리 실행(BOS 등 미구현 스텝 우회)."""
    eng = StructureEngine(StructureConfig(sw_len=sw, max_piv=max_piv))
    for i in range(len(candles)):
        eng._detect_pivots_and_classify(i, candles)
    s = eng.state
    return {
        "pivots": [(p.bar, p.ptype, round(p.price, 8), p.classified) for p in s.pivots],
        "ith_hist": [(round(p, 8), b) for p, b in s.ith_hist],
        "itl_hist": [(round(p, 8), b) for p, b in s.itl_hist],
        "lt_lvls": [round(x, 8) for x in s.lt_lvls],
        "lt_h": [(round(p, 8), b) for p, b in s.lt_h],
        "lt_l": [(round(p, 8), b) for p, b in s.lt_l],
        "last_conf_ith": None if s.last_conf_ith is None else round(s.last_conf_ith, 8),
        "last_conf_itl": None if s.last_conf_itl is None else round(s.last_conf_itl, 8),
        "cur_ith": None if s.cur_ith is None else round(s.cur_ith, 8),
        "cur_itl": None if s.cur_itl is None else round(s.cur_itl, 8),
    }


def test_step1_matches_v8():
    """스텝1(피벗 분류 + LTH/LTL + cur_/last_conf)이 v8과 1:1.

    매끄러운 데이터(분류 적음)와 거친 데이터(LTH/LTL 분기 다수) 둘 다 대조 —
    후자가 lt_lvls/lt_h/lt_l 경로까지 v8과 일치함을 보장."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    for candles in (_synthetic_candles(), _choppy_candles()):
        assert _engine_step1_state(candles) == _v8_step1_reference(candles)


def test_step1_produces_lt_levels():
    """거친 데이터에서 LTH/LTL이 실제로 잡혀야(테스트가 빈껍데기 아님 + LT 분기 커버)."""
    st = _engine_step1_state(_choppy_candles())
    assert st["lt_h"] and st["lt_l"] and st["lt_lvls"], "LTH/LTL(lt_h/lt_l/lt_lvls)이 채워져야 함"


def test_step1_deterministic():
    candles = _synthetic_candles()
    assert _engine_step1_state(candles) == _engine_step1_state(candles)


# ───────────────────────── 스텝 2: BOS/CHoCH + 넥라인·OB·SD ─────────────────────────

def _v8_atr(candles, n: int = 14):
    """정본 v8 engine.py _atr 1:1 (ewm alpha=1/n, adjust=False)."""
    import numpy as np
    import pandas as pd
    h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles])
    c = np.array([c.close for c in candles])
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def _v8_step12_reference(candles, atr, cfg, sw=8, max_piv=80):
    """정본 v8 engine.py 368-426 (스텝1 피벗/분류 + 스텝2 BOS·넥라인·OB·SD) 복제.

    add_lvl(병합 tol*0.4, cap 150)·src 코드·SD 배수·OB lookback 모두 v8 그대로.
    레벨은 (price, wt, src, active)로, BOS는 (bar, dir)로 산출."""
    import numpy as np
    o = np.array([c.open for c in candles]); h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles]);  c = np.array([c.close for c in candles])
    pivP, pivB, pivT, pivK = [], [], [], []
    ith_hist, itl_hist = [], []
    cur_ith = cur_itl = None
    last_conf_ith = last_conf_itl = None
    ms_dir = 0; neckline = None
    levels = []
    bos = []

    def idx_recent(ty, k):
        cnt = 0
        for ii in range(len(pivT) - 1, -1, -1):
            if pivT[ii] == ty:
                if cnt == k:
                    return ii
                cnt += 1
        return -1

    def confirm_it(ty):
        ai, bi, ci = idx_recent(ty, 2), idx_recent(ty, 1), idx_recent(ty, 0)
        if ai < 0 or bi < 0 or ci < 0:
            return None
        pa, pb_, pc = pivP[ai], pivP[bi], pivP[ci]
        if not ((pa < pb_ > pc) if ty == 1 else (pa > pb_ < pc)):
            return None
        pivK[bi] = 1
        return pb_, pivB[bi]

    def add_lvl(price, w, src, tol):
        for lv in levels:
            if lv["active"] and abs(lv["price"] - price) < tol * 0.4:
                lv["wt"] += w
                return
        levels.append({"price": price, "wt": w, "active": True, "src": src})
        if len(levels) > cfg.max_levels:
            levels.pop(0)

    for i in range(len(candles)):
        tol = cfg.conf_tol_atr * atr[i]
        # --- 스텝1 ---
        j = i - sw
        if j >= sw:
            win_h = h[j - sw:i + 1]; win_l = l[j - sw:i + 1]
            if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                pivP.append(h[j]); pivB.append(j); pivT.append(1); pivK.append(0)
                r = confirm_it(1)
                if r:
                    cur_ith, _ = r; last_conf_ith = r[0]; ith_hist.append(r)
            if l[j] == win_l.min() and (win_l == l[j]).sum() == 1:
                pivP.append(l[j]); pivB.append(j); pivT.append(-1); pivK.append(0)
                r = confirm_it(-1)
                if r:
                    cur_itl, _ = r; last_conf_itl = r[0]; itl_hist.append(r)
            while len(pivP) > max_piv:
                pivP.pop(0); pivB.pop(0); pivT.pop(0); pivK.pop(0)
        # --- 스텝2 ---
        if cur_ith is not None and c[i] > cur_ith:
            broken = cur_ith; ms_dir = 1; neckline = broken; bos.append((i, 1))
            add_lvl(broken, cfg.w_neck, 3, tol)
            for k in range(1, min(13, i + 1)):
                if c[i - k] < o[i - k]:
                    add_lvl(l[i - k], cfg.w_ob, 9, tol); break
            if last_conf_itl is not None:
                swd = broken - last_conf_itl
                if swd > 0:
                    add_lvl(broken + 1.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken + 2.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken + 2.5 * swd, cfg.w_fib * .6, 2, tol)
            cur_ith = None
        if cur_itl is not None and c[i] < cur_itl:
            broken = cur_itl; ms_dir = -1; neckline = broken; bos.append((i, -1))
            add_lvl(broken, cfg.w_neck, 3, tol)
            for k in range(1, min(13, i + 1)):
                if c[i - k] > o[i - k]:
                    add_lvl(h[i - k], cfg.w_ob, 9, tol); break
            if last_conf_ith is not None:
                swd = last_conf_ith - broken
                if swd > 0:
                    add_lvl(broken - 1.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken - 2.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken - 2.5 * swd, cfg.w_fib * .6, 2, tol)
            cur_itl = None

    return {
        "levels": [(round(float(lv["price"]), 6), round(float(lv["wt"]), 6), lv["src"], lv["active"]) for lv in levels],
        "ms_dir": ms_dir,
        "neckline": None if neckline is None else round(float(neckline), 6),
        "cur_ith": None if cur_ith is None else round(float(cur_ith), 6),
        "cur_itl": None if cur_itl is None else round(float(cur_itl), 6),
        "bos": bos,
    }


def _engine_step12_state(candles, atr, cfg):
    """우리 엔진의 스텝1+2만 격리 실행(스텝3~ 미구현 우회). BOS는 _bos_choch 호출
    직전 상태로 재구성."""
    eng = StructureEngine(cfg)
    bos = []
    for i in range(len(candles)):
        eng._detect_pivots_and_classify(i, candles)
        tol = cfg.conf_tol_atr * atr[i]
        pre_ith, pre_itl = eng.state.cur_ith, eng.state.cur_itl
        eng._bos_choch(i, candles, tol)
        if pre_ith is not None and candles[i].close > pre_ith:
            bos.append((i, 1))
        if pre_itl is not None and candles[i].close < pre_itl:
            bos.append((i, -1))
    s = eng.state
    return {
        "levels": [(round(lv.price, 6), round(lv.weight, 6), int(lv.source), lv.active) for lv in s.levels],
        "ms_dir": s.ms_dir,
        "neckline": None if s.neckline is None else round(s.neckline, 6),
        "cur_ith": None if s.cur_ith is None else round(s.cur_ith, 6),
        "cur_itl": None if s.cur_itl is None else round(s.cur_itl, 6),
        "bos": bos,
    }


def test_step2_matches_v8():
    """스텝2: BOS 이벤트 + 적립 레벨(개수+누적가중치+src+active) + cur_ 해제 모두 v8과 1:1."""
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy/pandas 미설치")
    cfg = StructureConfig()
    for candles in (_synthetic_candles(), _choppy_candles()):
        atr = _v8_atr(candles)
        assert _engine_step12_state(candles, atr, cfg) == _v8_step12_reference(candles, atr, cfg)


def test_step2_has_bos_and_all_sources():
    """커버리지: BOS가 실제로 발생하고 넥라인(3)·OB(9)·SD피보(2) 레벨이 모두 적립돼야."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    st = _engine_step12_state(candles, _v8_atr(candles), cfg)
    assert st["bos"], "BOS 이벤트가 있어야 함"
    srcs = {src for _, _, src, _ in st["levels"]}
    assert {2, 3, 9} <= srcs, f"넥라인(3)·OB(9)·SD피보(2) 모두 있어야: 실제 {sorted(srcs)}"


def test_step2_weight_merge_accumulates():
    """병합이 일어난 레벨이 하나라도 있어 wt가 기본 가중치보다 큰 경우를 확인
    (tol*0.4 병합이 죽은 코드가 아님 보장)."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    st = _engine_step12_state(candles, _v8_atr(candles), cfg)
    base = {cfg.w_neck, cfg.w_ob, cfg.w_fib * 0.8, cfg.w_fib * 0.6}
    merged = [w for _, w, _, _ in st["levels"] if all(abs(w - b) > 1e-9 for b in base)]
    assert merged, "병합으로 가중치가 합산된 레벨이 최소 하나는 있어야(병합 경로 커버)"


# ──────────────────── 스텝 3·4·4b: FVG/리밸런스 · 꼬리 · 고거래량 ────────────────────

def _v8_step1234_reference(candles, atr, cfg, sw=8, max_piv=80, do_consume=False):
    """정본 v8 engine.py 368-458(+do_consume시 460-463) 복제.
    스텝1·2 + 3 FVG/리밸런스 + 4 꼬리 + 4b 고거래량 (+ 5 consume). 봉내 순서 v8 그대로."""
    import numpy as np
    o = np.array([c.open for c in candles]); h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles]);  c = np.array([c.close for c in candles])
    vol = np.array([c_.volume for c_ in candles])
    pivP, pivB, pivT, pivK = [], [], [], []
    ith_hist, itl_hist = [], []
    cur_ith = cur_itl = None
    last_conf_ith = last_conf_itl = None
    ms_dir = 0; neckline = None
    levels = []
    fvgs = []
    reb_ith = reb_itl = None
    hv_bar = -1

    def idx_recent(ty, k):
        cnt = 0
        for ii in range(len(pivT) - 1, -1, -1):
            if pivT[ii] == ty:
                if cnt == k:
                    return ii
                cnt += 1
        return -1

    def confirm_it(ty):
        ai, bi, ci = idx_recent(ty, 2), idx_recent(ty, 1), idx_recent(ty, 0)
        if ai < 0 or bi < 0 or ci < 0:
            return None
        pa, pb_, pc = pivP[ai], pivP[bi], pivP[ci]
        if not ((pa < pb_ > pc) if ty == 1 else (pa > pb_ < pc)):
            return None
        pivK[bi] = 1
        return pb_, pivB[bi]

    def add_lvl(price, w, src, tol):
        for lv in levels:
            if lv["active"] and abs(lv["price"] - price) < tol * 0.4:
                lv["wt"] += w
                return
        levels.append({"price": price, "wt": w, "active": True, "src": src})
        if len(levels) > cfg.max_levels:
            levels.pop(0)

    for i in range(len(candles)):
        tol = cfg.conf_tol_atr * atr[i]
        # 스텝1
        j = i - sw
        if j >= sw:
            win_h = h[j - sw:i + 1]; win_l = l[j - sw:i + 1]
            if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                pivP.append(h[j]); pivB.append(j); pivT.append(1); pivK.append(0)
                r = confirm_it(1)
                if r:
                    cur_ith, _ = r; last_conf_ith = r[0]; ith_hist.append(r)
            if l[j] == win_l.min() and (win_l == l[j]).sum() == 1:
                pivP.append(l[j]); pivB.append(j); pivT.append(-1); pivK.append(0)
                r = confirm_it(-1)
                if r:
                    cur_itl, _ = r; last_conf_itl = r[0]; itl_hist.append(r)
            while len(pivP) > max_piv:
                pivP.pop(0); pivB.pop(0); pivT.pop(0); pivK.pop(0)
        # 스텝2
        if cur_ith is not None and c[i] > cur_ith:
            broken = cur_ith; ms_dir = 1; neckline = broken
            add_lvl(broken, cfg.w_neck, 3, tol)
            for k in range(1, min(13, i + 1)):
                if c[i - k] < o[i - k]:
                    add_lvl(l[i - k], cfg.w_ob, 9, tol); break
            if last_conf_itl is not None:
                swd = broken - last_conf_itl
                if swd > 0:
                    add_lvl(broken + 1.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken + 2.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken + 2.5 * swd, cfg.w_fib * .6, 2, tol)
            cur_ith = None
        if cur_itl is not None and c[i] < cur_itl:
            broken = cur_itl; ms_dir = -1; neckline = broken
            add_lvl(broken, cfg.w_neck, 3, tol)
            for k in range(1, min(13, i + 1)):
                if c[i - k] > o[i - k]:
                    add_lvl(h[i - k], cfg.w_ob, 9, tol); break
            if last_conf_ith is not None:
                swd = last_conf_ith - broken
                if swd > 0:
                    add_lvl(broken - 1.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken - 2.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken - 2.5 * swd, cfg.w_fib * .6, 2, tol)
            cur_itl = None
        # 스텝3 FVG + 리밸런스
        if i >= 2:
            if l[i] > h[i - 2]:
                add_lvl(h[i - 2], cfg.w_fvg, 6, tol)
                fvgs.append({"top": l[i], "bot": h[i - 2], "dir": 1, "filled": False})
            if h[i] < l[i - 2]:
                add_lvl(l[i - 2], cfg.w_fvg, 6, tol)
                fvgs.append({"top": l[i - 2], "bot": h[i], "dir": -1, "filled": False})
            if len(fvgs) > 30:
                fvgs.pop(0)
        for f in fvgs:
            if f["filled"]:
                continue
            if f["dir"] == -1 and h[i] >= f["bot"]:
                f["filled"] = True; reb_ith = h[i]; add_lvl(h[i], cfg.w_lt, 10, tol)
            if f["dir"] == 1 and l[i] <= f["top"]:
                f["filled"] = True; reb_itl = l[i]; add_lvl(l[i], cfg.w_lt, 10, tol)
        # 스텝4 꼬리
        rng = h[i] - l[i]
        if rng > 0:
            body_hi, body_lo = max(o[i], c[i]), min(o[i], c[i])
            if (h[i] - body_hi) / rng >= cfg.wick_ratio:
                add_lvl(body_hi, cfg.w_wick, 1, tol)
            if (body_lo - l[i]) / rng >= cfg.wick_ratio:
                add_lvl(body_lo, cfg.w_wick, 1, tol)
        # 스텝4b 고거래량
        if cfg.use_vol_lvl and i >= cfg.vol_lvl_len:
            w0 = i - cfg.vol_lvl_len + 1
            hv = w0 + int(np.argmax(vol[w0:i + 1]))
            if hv != hv_bar:
                hv_bar = hv
                add_lvl(max(o[hv], c[hv]), cfg.w_vol, 11, tol)
                add_lvl(min(o[hv], c[hv]), cfg.w_vol, 11, tol)
        # 스텝5 consume-on-touch (v8 460-463) — 적립 뒤, 피보 앞
        if do_consume:
            for lv in levels:
                if lv["active"] and l[i] <= lv["price"] <= h[i]:
                    lv["active"] = False

    return {
        "levels": [(round(float(lv["price"]), 6), round(float(lv["wt"]), 6), lv["src"], lv["active"]) for lv in levels],
        "fvgs": [(round(float(f["top"]), 6), round(float(f["bot"]), 6), f["dir"], f["filled"]) for f in fvgs],
        "reb_ith": None if reb_ith is None else round(float(reb_ith), 6),
        "reb_itl": None if reb_itl is None else round(float(reb_itl), 6),
        "hv_bar": hv_bar,
        "ms_dir": ms_dir,
        "neckline": None if neckline is None else round(float(neckline), 6),
    }


def _engine_step1234_state(candles, atr, cfg, do_consume=False):
    """우리 엔진의 스텝1~4b(+do_consume시 5) 격리 실행. update() 순서와 동일."""
    eng = StructureEngine(cfg)
    for i in range(len(candles)):
        eng._detect_pivots_and_classify(i, candles)
        tol = cfg.conf_tol_atr * atr[i]
        eng._bos_choch(i, candles, tol)
        eng._fvg_and_rebalance(i, candles, tol)
        eng._wick_levels(i, candles, tol)
        eng._volume_levels(i, candles, tol)
        if do_consume:
            eng._consume_on_touch(i, candles)
    s = eng.state
    return {
        "levels": [(round(lv.price, 6), round(lv.weight, 6), int(lv.source), lv.active) for lv in s.levels],
        "fvgs": [(round(f.top, 6), round(f.bot, 6), f.direction, f.filled) for f in s.fvgs],
        "reb_ith": None if s.reb_ith is None else round(s.reb_ith, 6),
        "reb_itl": None if s.reb_itl is None else round(s.reb_itl, 6),
        "hv_bar": s.hv_bar,
        "ms_dir": s.ms_dir,
        "neckline": None if s.neckline is None else round(s.neckline, 6),
    }


def test_step34_matches_v8():
    """스텝3·4·4b: levels(개수+wt+src+active) + fvgs + reb_* + hv_bar 모두 v8과 1:1."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    for candles in (_synthetic_candles(), _choppy_candles()):
        atr = _v8_atr(candles)
        assert _engine_step1234_state(candles, atr, cfg) == _v8_step1234_reference(candles, atr, cfg)


def test_step34_sources_present():
    """커버리지: FVG(6)·리밸런스(10)·꼬리(1)·고거래량(11) src가 실제로 적립돼야."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    st = _engine_step1234_state(candles, _v8_atr(candles), cfg)
    srcs = {src for _, _, src, _ in st["levels"]}
    assert {1, 6, 10, 11} <= srcs, f"꼬리(1)·FVG(6)·리밸런스(10)·고거래량(11) 모두 있어야: {sorted(srcs)}"
    assert any(f[3] for f in st["fvgs"]) or st["reb_ith"] is not None or st["reb_itl"] is not None, \
        "리밸런스 충전이 한 번은 일어나야"


# ───────────────────────── 스텝 5: consume-on-touch (정밀) ─────────────────────────

def test_step5_matches_v8():
    """스텝1~5(소멸 포함)의 levels active 전이가 v8과 1:1.
    레벨 (price, wt, src, active) 전체 — 즉 어떤 레벨이 어느 봉에 죽었는지까지 일치."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    for candles in (_synthetic_candles(), _choppy_candles()):
        atr = _v8_atr(candles)
        assert (_engine_step1234_state(candles, atr, cfg, do_consume=True)
                == _v8_step1234_reference(candles, atr, cfg, do_consume=True))


def test_step5_actually_consumes():
    """커버리지: consume가 실제로 일부 레벨을 죽여야(active=False 존재).
    또 consume 없을 때 대비 active 수가 줄어드는지 대조."""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    atr = _v8_atr(candles)
    no_consume = _engine_step1234_state(candles, atr, cfg, do_consume=False)
    consumed = _engine_step1234_state(candles, atr, cfg, do_consume=True)
    dead = [lv for lv in consumed["levels"] if not lv[3]]
    assert dead, "consume로 죽은(active=False) 레벨이 최소 하나는 있어야"
    n_active_before = sum(1 for lv in no_consume["levels"] if lv[3])
    n_active_after = sum(1 for lv in consumed["levels"] if lv[3])
    assert n_active_after < n_active_before, "consume 후 active 레벨이 줄어야"


def test_step5_order_pre_consume_dies_post_consume_survives():
    """★ 스텝1 WHY 핵심: 같은 봉에서
       - consume '앞'에 적립된(관통된) 레벨 → 죽는다
       - consume '뒤'에 적립된(관통된) 레벨 → 그 봉엔 안 죽는다(다음 봉부터)
    update()의 적립(2~4b)→consume(5)→피보(6) 순서를 그대로 모사."""
    from infrapilot.analysis_core.models import LevelSource
    eng = StructureEngine(StructureConfig())
    # 봉 [low=90, high=110] — price=100을 관통
    bar = Candle(ts=0, open=95, high=110, low=90, close=105, volume=1.0)
    candles = [bar]
    tol = 1.0
    P = 100.0
    eng._add_level(P, 1.0, LevelSource.WICK, tol)     # consume 앞에 적립(2~4b 모사)
    eng._consume_on_touch(0, candles)                 # (5)
    eng._add_level(P, 1.0, LevelSource.FIB, tol + 100)  # consume 뒤에 적립(6 피보 모사, 병합 안 되게 큰 tol회피 위해 별도)
    pre = [lv for lv in eng.state.levels if lv.source == LevelSource.WICK]
    post = [lv for lv in eng.state.levels if lv.source == LevelSource.FIB]
    assert pre and all(not lv.active for lv in pre), "consume 앞 관통 레벨은 죽어야"
    assert post and all(lv.active for lv in post), "consume 뒤 적립 레벨은 그 봉에 살아야"


def test_step5_dynlines_not_consumed():
    """비소멸: dyn_lines(빗각/포크)는 가격이 관통해도 consume 패스가 안 건드림(원칙7 면제).
    consume는 state.levels만 순회하므로 dyn_lines는 영향 없음."""
    from infrapilot.analysis_core.models import LevelSource
    from infrapilot.analysis_core.ict import DynLine
    eng = StructureEngine(StructureConfig())
    bar = Candle(ts=0, open=95, high=110, low=90, close=105, volume=1.0)
    # 현재 봉 범위 내 값을 갖는 동적 라인
    dl = DynLine(a_bar=0, a_price=100.0, slope=0.0, source=LevelSource.DIAG, weight=1.5)
    eng.state.dyn_lines.append(dl)
    eng._add_level(100.0, 1.0, LevelSource.WICK, 1.0)   # soft 레벨은 죽어야(대조군)
    eng._consume_on_touch(0, [bar])
    assert eng.state.dyn_lines[0] is dl, "dyn_lines 원소가 그대로 보존"
    assert not eng.state.levels[0].active, "soft 레벨(대조군)은 죽어야"
    # dyn_lines는 active 개념 없이 그대로 — 관통해도 점수 계산에서 계속 쓰임


def test_step5_active_filter_excludes_consumed():
    """confluence 계약: 소멸된 레벨은 active 필터로 점수에서 제외돼야.
    (confluence.py는 미구현 — 여기선 active 필터 의미만 계약으로 고정.)"""
    try:
        import numpy  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    eng = StructureEngine(cfg)
    atr = _v8_atr(candles)
    for i in range(len(candles)):
        eng._detect_pivots_and_classify(i, candles)
        tol = cfg.conf_tol_atr * atr[i]
        eng._bos_choch(i, candles, tol)
        eng._fvg_and_rebalance(i, candles, tol)
        eng._wick_levels(i, candles, tol)
        eng._volume_levels(i, candles, tol)
        eng._consume_on_touch(i, candles)
    active = [lv for lv in eng.state.levels if lv.active]      # confluence가 쓰는 집합
    dead = [lv for lv in eng.state.levels if not lv.active]
    assert dead, "죽은 레벨이 있어야(테스트 의미)"
    assert all(lv.active for lv in active), "active 필터 결과엔 죽은 레벨 없음"
    assert len(active) + len(dead) == len(eng.state.levels)


# ─────────────── 스텝 6·6b·7 + 전체 파이프라인: 피보 · LT피보 · 동적라인 ───────────────

def _v8_htf_bias(candles, htf_mult=4, htf_ema=50):
    """정본 v8 engine.py _htf_bias 1:1 (직전 완성 상위봉 부호, lookahead shift)."""
    import numpy as np
    import pandas as pd
    c = np.array([x.close for x in candles])
    n = len(c)
    grp = np.arange(n) // htf_mult
    htf_close = pd.Series(c).groupby(grp).last()
    ema = htf_close.ewm(span=htf_ema, adjust=False).mean()
    sign = np.sign(htf_close.values - ema.values)
    sign = np.concatenate([[0.0], sign[:-1]])
    return sign[grp]


def _v8_full_reference(candles, atr, htf, cfg, sw=8, max_piv=80, cluster_bar=None):
    """정본 v8 engine.py run() 구조 패스 368-503 전체 복제(스텝1~7). 트레이딩(8) 제외.
    cluster_bar 지정 시 그 봉에서 active 레벨별 cluster_score(329-352)도 산출."""
    import numpy as np
    o = np.array([c.open for c in candles]); h = np.array([c.high for c in candles])
    l = np.array([c.low for c in candles]);  c = np.array([c.close for c in candles])
    vol = np.array([x.volume for x in candles])
    pivP, pivB, pivT, pivK = [], [], [], []
    ith_hist, itl_hist = [], []
    lt_lvls, lt_h, lt_l = [], [], []
    cur_ith = cur_itl = None
    last_conf_ith = last_conf_itl = None
    ms_dir = 0; neckline = None
    levels = []; fvgs = []
    reb_ith = reb_itl = None
    leg_sig = lt_sig = None
    hv_bar = -1
    dyn_lines = []

    def idx_recent(ty, k):
        cnt = 0
        for ii in range(len(pivT) - 1, -1, -1):
            if pivT[ii] == ty:
                if cnt == k:
                    return ii
                cnt += 1
        return -1

    def confirm_it(ty):
        ai, bi, ci = idx_recent(ty, 2), idx_recent(ty, 1), idx_recent(ty, 0)
        if ai < 0 or bi < 0 or ci < 0:
            return None
        pa, pb_, pc = pivP[ai], pivP[bi], pivP[ci]
        if not ((pa < pb_ > pc) if ty == 1 else (pa > pb_ < pc)):
            return None
        pivK[bi] = 1
        return pb_, pivB[bi]

    def add_lvl(price, w, src, tol):
        for lv in levels:
            if lv["active"] and abs(lv["price"] - price) < tol * 0.4:
                lv["wt"] += w
                return
        levels.append({"price": price, "wt": w, "active": True, "src": src})
        if len(levels) > cfg.max_levels:
            levels.pop(0)

    def rebuild_dynlines(bar):
        dyn_lines.clear()
        if not cfg.use_diagonals:
            return
        struct = [(pivB[ii], pivP[ii], pivT[ii]) for ii in range(len(pivP)) if pivK[ii] >= 1]
        if len(struct) < 2:
            return
        highs = [(b, pr) for b, pr, t in struct if t == 1]
        lows = [(b, pr) for b, pr, t in struct if t == -1]
        down_ctx = ms_dir < 0 or (ms_dir == 0 and htf[bar] < 0)

        def containing(anchor, pts, ty):
            ab, ap = anchor
            tol = cfg.conf_tol_atr * atr[bar]
            for ii in range(len(pts) - 1, -1, -1):
                tb, tp_ = pts[ii]
                if tb <= ab:
                    continue
                sl = (tp_ - ap) / max(1, tb - ab)
                ok = True
                for jb, jp in pts:
                    if jb > ab:
                        lv = ap + sl * (jb - ab)
                        if ty == 1 and jp > lv + tol:
                            ok = False; break
                        if ty == -1 and jp < lv - tol:
                            ok = False; break
                if ok:
                    return sl
            return None

        if highs:
            anc = max(highs, key=lambda x: x[1])
            sl = containing(anc, highs, 1)
            if sl is not None:
                dyn_lines.append((anc[0], anc[1], sl, 5, cfg.w_diag))
        if lows:
            anc = min(lows, key=lambda x: x[1])
            sl = containing(anc, lows, -1)
            if sl is not None:
                dyn_lines.append((anc[0], anc[1], sl, 5, cfg.w_diag))
        anc = (max(highs, key=lambda x: x[1]) if down_ctx and highs
               else min(lows, key=lambda x: x[1]) if lows else None)
        if anc and cfg.fan_n > 0:
            cnt = 0
            for b, pr, t in struct:
                if cnt >= cfg.fan_n:
                    break
                if b > anc[0]:
                    sl = (pr - anc[1]) / max(1, b - anc[0])
                    dyn_lines.append((anc[0], anc[1], sl, 5, cfg.w_diag * 0.8))
                    cnt += 1
        ty0 = 1 if down_ctx else -1
        cands = sorted([st for st in struct if st[2] == ty0], key=lambda x: -x[1] if ty0 == 1 else x[1])
        fork = None
        for ab, ap, _ in cands:
            t1 = next(((b2, p2) for b2, p2, t2 in struct if t2 == -ty0 and b2 > ab), None)
            if not t1:
                continue
            t2 = next(((b3, p3) for b3, p3, t3 in struct if t3 == ty0 and b3 > t1[0]), None)
            if not t2:
                continue
            fork = (ab, ap, t1, t2); break
        if fork is None:
            for ab, ap, ty_ in struct:
                t1 = next(((b2, p2) for b2, p2, t2 in struct if t2 == -ty_ and b2 > ab), None)
                if not t1:
                    continue
                t2 = next(((b3, p3) for b3, p3, t3 in struct if t3 == ty_ and b3 > t1[0]), None)
                if not t2:
                    continue
                fork = (ab, ap, t1, t2); break
        if fork:
            ab, ap, (b1, p1), (b2, p2) = fork
            mslope = ((p1 + p2) / 2 - ap) / max(1, (b1 + b2) / 2 - ab)
            dyn_lines.append((ab, ap, mslope, 7, cfg.w_fork))
            off1 = p1 - (ap + mslope * (b1 - ab))
            off2 = p2 - (ap + mslope * (b2 - ab))
            dyn_lines.append((ab, ap + off1, mslope, 7, cfg.w_fork))
            dyn_lines.append((ab, ap + off2, mslope, 7, cfg.w_fork))

    for i in range(len(candles)):
        tol = cfg.conf_tol_atr * atr[i]
        piv_event = False
        # 스텝1
        j = i - sw
        if j >= sw:
            win_h = h[j - sw:i + 1]; win_l = l[j - sw:i + 1]
            if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                pivP.append(h[j]); pivB.append(j); pivT.append(1); pivK.append(0); piv_event = True
                r = confirm_it(1)
                if r:
                    cur_ith, _ = r; last_conf_ith = r[0]; ith_hist.append(r)
                    if len(ith_hist) >= 3:
                        (pm1, _), (pm, _), (pp1, _) = ith_hist[-3], ith_hist[-2], ith_hist[-1]
                        if pm > pm1 and pm > pp1:
                            lt_lvls.append(pm); lt_lvls[:] = lt_lvls[-10:]
                            lt_h.append((pm, ith_hist[-2][1])); lt_h[:] = lt_h[-6:]
            if l[j] == win_l.min() and (win_l == l[j]).sum() == 1:
                pivP.append(l[j]); pivB.append(j); pivT.append(-1); pivK.append(0); piv_event = True
                r = confirm_it(-1)
                if r:
                    cur_itl, _ = r; last_conf_itl = r[0]; itl_hist.append(r)
                    if len(itl_hist) >= 3:
                        (pm1, _), (pm, _), (pp1, _) = itl_hist[-3], itl_hist[-2], itl_hist[-1]
                        if pm < pm1 and pm < pp1:
                            lt_lvls.append(pm); lt_lvls[:] = lt_lvls[-10:]
                            lt_l.append((pm, itl_hist[-2][1])); lt_l[:] = lt_l[-6:]
            while len(pivP) > max_piv:
                pivP.pop(0); pivB.pop(0); pivT.pop(0); pivK.pop(0)
        # 스텝2
        if cur_ith is not None and c[i] > cur_ith:
            broken = cur_ith; ms_dir = 1; neckline = broken
            add_lvl(broken, cfg.w_neck, 3, tol)
            for k in range(1, min(13, i + 1)):
                if c[i - k] < o[i - k]:
                    add_lvl(l[i - k], cfg.w_ob, 9, tol); break
            if last_conf_itl is not None:
                swd = broken - last_conf_itl
                if swd > 0:
                    add_lvl(broken + 1.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken + 2.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken + 2.5 * swd, cfg.w_fib * .6, 2, tol)
            cur_ith = None
        if cur_itl is not None and c[i] < cur_itl:
            broken = cur_itl; ms_dir = -1; neckline = broken
            add_lvl(broken, cfg.w_neck, 3, tol)
            for k in range(1, min(13, i + 1)):
                if c[i - k] > o[i - k]:
                    add_lvl(h[i - k], cfg.w_ob, 9, tol); break
            if last_conf_ith is not None:
                swd = last_conf_ith - broken
                if swd > 0:
                    add_lvl(broken - 1.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken - 2.0 * swd, cfg.w_fib * .8, 2, tol)
                    add_lvl(broken - 2.5 * swd, cfg.w_fib * .6, 2, tol)
            cur_itl = None
        # 스텝3
        if i >= 2:
            if l[i] > h[i - 2]:
                add_lvl(h[i - 2], cfg.w_fvg, 6, tol)
                fvgs.append({"top": l[i], "bot": h[i - 2], "dir": 1, "filled": False})
            if h[i] < l[i - 2]:
                add_lvl(l[i - 2], cfg.w_fvg, 6, tol)
                fvgs.append({"top": l[i - 2], "bot": h[i], "dir": -1, "filled": False})
            if len(fvgs) > 30:
                fvgs.pop(0)
        for f in fvgs:
            if f["filled"]:
                continue
            if f["dir"] == -1 and h[i] >= f["bot"]:
                f["filled"] = True; reb_ith = h[i]; add_lvl(h[i], cfg.w_lt, 10, tol)
            if f["dir"] == 1 and l[i] <= f["top"]:
                f["filled"] = True; reb_itl = l[i]; add_lvl(l[i], cfg.w_lt, 10, tol)
        # 스텝4
        rng = h[i] - l[i]
        if rng > 0:
            body_hi, body_lo = max(o[i], c[i]), min(o[i], c[i])
            if (h[i] - body_hi) / rng >= cfg.wick_ratio:
                add_lvl(body_hi, cfg.w_wick, 1, tol)
            if (body_lo - l[i]) / rng >= cfg.wick_ratio:
                add_lvl(body_lo, cfg.w_wick, 1, tol)
        # 스텝4b
        if cfg.use_vol_lvl and i >= cfg.vol_lvl_len:
            w0 = i - cfg.vol_lvl_len + 1
            hv = w0 + int(np.argmax(vol[w0:i + 1]))
            if hv != hv_bar:
                hv_bar = hv
                add_lvl(max(o[hv], c[hv]), cfg.w_vol, 11, tol)
                add_lvl(min(o[hv], c[hv]), cfg.w_vol, 11, tol)
        # 스텝5 consume
        for lv in levels:
            if lv["active"] and l[i] <= lv["price"] <= h[i]:
                lv["active"] = False
        # 스텝6 피보
        if last_conf_ith is not None and last_conf_itl is not None:
            sig = last_conf_ith + last_conf_itl
            if sig != leg_sig:
                diff = last_conf_ith - last_conf_itl
                sz_ok = diff >= cfg.min_leg_atr * atr[i] and (cfg.max_leg_atr <= 0 or diff <= cfg.max_leg_atr * atr[i])
                if diff > 0 and sz_ok:
                    leg_sig = sig
                    ib = ith_hist[-1][1] if ith_hist else -1
                    lb = itl_hist[-1][1] if itl_hist else -1
                    g_dir = 1 if ib > lb else -1
                    for m in cfg.ext_multiples:
                        py = last_conf_itl + m * diff if g_dir == 1 else last_conf_ith - m * diff
                        if py > 0:
                            add_lvl(py, cfg.w_fib, 2, tol)
                    if cfg.dual_fib:
                        ai = idx_recent(-1, 1) if g_dir == 1 else idx_recent(1, 1)
                        if ai >= 0:
                            ap_ = pivP[ai]
                            span = (last_conf_ith - ap_) if g_dir == 1 else (ap_ - last_conf_itl)
                            if span > 0:
                                for m in cfg.ext_multiples:
                                    py = ap_ + m * span if g_dir == 1 else ap_ - m * span
                                    if py > 0:
                                        add_lvl(py, cfg.w_fib * .7, 2, tol)
        # 스텝6b LT피보
        if cfg.lt_fib and lt_h and lt_l:
            (lH, lHb), (lL, lLb) = lt_h[-1], lt_l[-1]
            sigL = lH + lL
            if sigL != lt_sig and lH > lL:
                lt_sig = sigL
                dL = lH - lL
                gd = 1 if lHb > lLb else -1
                for m in cfg.ext_multiples:
                    py = lL + m * dL if gd == 1 else lH - m * dL
                    if py > 0:
                        add_lvl(py, cfg.w_fib, 2, tol)
        # 스텝7 동적라인
        if piv_event:
            rebuild_dynlines(i)

    clusters_out = None
    if cluster_bar is not None:
        b = cluster_bar
        tolc = cfg.conf_tol_atr * atr[b]
        clusters_out = []
        for lv in levels:
            if not lv["active"]:
                continue
            price = lv["price"]
            sc = 0.0
            for l2 in levels:
                if l2["active"] and abs(l2["price"] - price) <= tolc:
                    sc += l2["wt"]
            for (ab, ap, sl, src, w) in dyn_lines:
                if abs((ap + sl * (b - ab)) - price) <= tolc:
                    sc += w
            if b >= cfg.don_len:
                dH = h[b - cfg.don_len:b].max(); dL = l[b - cfg.don_len:b].min()
                if abs(dH - price) <= tolc:
                    sc += cfg.w_don
                if abs(dL - price) <= tolc:
                    sc += cfg.w_don
            zc = 0
            for ii in range(len(pivP)):
                if pivK[ii] >= 1 and zc < 3 and abs(pivP[ii] - price) <= tolc:
                    sc += cfg.w_zone; zc += 1
            for lt in lt_lvls:
                if abs(lt - price) <= tolc:
                    sc += cfg.w_lt
            clusters_out.append((round(float(price), 5), round(float(sc), 5)))

    result = {
        "levels": [(round(float(lv["price"]), 5), round(float(lv["wt"]), 5), lv["src"], lv["active"]) for lv in levels],
        "dyn_lines": [(b, round(float(ap), 5), round(float(sl), 5), src, round(float(w), 5)) for b, ap, sl, src, w in dyn_lines],
        "fvgs": [(round(float(f["top"]), 5), round(float(f["bot"]), 5), f["dir"], f["filled"]) for f in fvgs],
        "reb_ith": None if reb_ith is None else round(float(reb_ith), 5),
        "reb_itl": None if reb_itl is None else round(float(reb_itl), 5),
        "ms_dir": ms_dir,
        "neckline": None if neckline is None else round(float(neckline), 5),
        "leg_sig": None if leg_sig is None else round(float(leg_sig), 5),
        "lt_sig": None if lt_sig is None else round(float(lt_sig), 5),
        "hv_bar": hv_bar,
    }
    if cluster_bar is not None:
        result["clusters"] = clusters_out
    return result


def _engine_full_state(candles, atr, htf, cfg):
    """우리 엔진 전체 파이프라인(update 스텝1~7)."""
    eng = StructureEngine(cfg)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i], htf[i])
    s = eng.state
    return {
        "levels": [(round(lv.price, 5), round(lv.weight, 5), int(lv.source), lv.active) for lv in s.levels],
        "dyn_lines": [(d.a_bar, round(d.a_price, 5), round(d.slope, 5), int(d.source), round(d.weight, 5)) for d in s.dyn_lines],
        "fvgs": [(round(f.top, 5), round(f.bot, 5), f.direction, f.filled) for f in s.fvgs],
        "reb_ith": None if s.reb_ith is None else round(s.reb_ith, 5),
        "reb_itl": None if s.reb_itl is None else round(s.reb_itl, 5),
        "ms_dir": s.ms_dir,
        "neckline": None if s.neckline is None else round(s.neckline, 5),
        "leg_sig": None if s.leg_sig is None else round(s.leg_sig, 5),
        "lt_sig": None if s.lt_sig is None else round(s.lt_sig, 5),
        "hv_bar": s.hv_bar,
    }


def test_full_ict_matches_v8():
    """전체 파이프라인(스텝1~7): levels(피보 포함)·dyn_lines·fvgs·상태 모두 v8과 1:1."""
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy/pandas 미설치")
    cfg = StructureConfig()
    for candles in (_synthetic_candles(), _choppy_candles()):
        atr = _v8_atr(candles)
        htf = _v8_htf_bias(candles)
        assert _engine_full_state(candles, atr, htf, cfg) == _v8_full_reference(candles, atr, htf, cfg)


def test_fib_and_dynlines_present():
    """커버리지: 피보(src2) 레벨 + 동적라인(빗각5·포크7)이 실제로 생성돼야."""
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy/pandas 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    st = _engine_full_state(candles, _v8_atr(candles), _v8_htf_bias(candles), cfg)
    assert 2 in {src for _, _, src, _ in st["levels"]}, "피보(src2) 레벨이 있어야"
    dl_srcs = {src for _, _, _, src, _ in st["dyn_lines"]}
    assert 5 in dl_srcs and 7 in dl_srcs, f"빗각(5)·포크(7) 동적라인 있어야: {sorted(dl_srcs)}"


def test_full_ict_deterministic():
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy/pandas 미설치")
    cfg = StructureConfig()
    candles = _choppy_candles()
    atr = _v8_atr(candles); htf = _v8_htf_bias(candles)
    assert _engine_full_state(candles, atr, htf, cfg) == _engine_full_state(candles, atr, htf, cfg)


# ───────────────────────── confluence: 합류 점수 (cluster_score) ─────────────────────────

def _engine_clusters(candles, atr, htf, cfg, bar):
    """엔진을 전 구간 돌린 뒤 bar에서 compute_clusters → (중심가, 점수) 목록(store 순서)."""
    from infrapilot.analysis_core.confluence import compute_clusters
    eng = StructureEngine(cfg)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i], htf[i])
    cls = compute_clusters(eng.state, candles, bar, atr[bar], cfg)
    return [(round(c.mid, 5), round(c.score, 5)) for c in cls]


def test_confluence_matches_v8():
    """각 active 레벨 중심의 cluster_score(중심가+총점)가 v8과 1:1 (매끄러운+거친)."""
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy/pandas 미설치")
    cfg = StructureConfig()
    for candles in (_synthetic_candles(), _choppy_candles()):
        atr = _v8_atr(candles); htf = _v8_htf_bias(candles)
        bar = len(candles) - 1
        mine = _engine_clusters(candles, atr, htf, cfg, bar)
        ref = _v8_full_reference(candles, atr, htf, cfg, cluster_bar=bar)["clusters"]
        assert mine == ref


def test_confluence_non_vacuous():
    """커버리지: 클러스터가 실제로 생성되고, 합산 점수(>단일 wt)가 한 번은 나와야."""
    try:
        import numpy  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        import pytest
        pytest.skip("numpy/pandas 미설치")
    from infrapilot.analysis_core.confluence import compute_clusters
    cfg = StructureConfig()
    candles = _choppy_candles()
    atr = _v8_atr(candles); htf = _v8_htf_bias(candles)
    eng = StructureEngine(cfg)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i], htf[i])
    bar = len(candles) - 1
    cls = compute_clusters(eng.state, candles, bar, atr[bar], cfg)
    assert cls, "클러스터가 있어야"
    assert any(c.cnt >= 2 for c in cls), "여러 근거가 합류한(cnt≥2) 클러스터가 있어야"


def test_confluence_active_filter():
    """소멸(active=False) 레벨은 중심도 안 되고 합산에도 안 들어감(스텝5 계약 실현)."""
    from infrapilot.analysis_core.confluence import _score_at, compute_clusters
    from infrapilot.analysis_core.ict import StructureState
    from infrapilot.analysis_core.models import LevelCandidate, LevelSource
    cfg = StructureConfig()
    st = StructureState()
    st.levels = [
        LevelCandidate(100.0, 1.0, LevelSource.WICK, active=False),   # 죽음
        LevelCandidate(100.0, 2.0, LevelSource.FIB, active=True),     # 살아있음
    ]
    score, cnt, srcs = _score_at(100.0, tol=1.0, bar=0, state=st, candles=[], cfg=cfg)
    assert abs(score - 2.0) < 1e-9, "죽은 WICK 제외, 살아있는 FIB만 합산"
    assert LevelSource.WICK not in srcs and LevelSource.FIB in srcs
    mids = [c.mid for c in compute_clusters(st, [], 0, atr_i=2.0, cfg=cfg)]  # tol=1.0
    assert mids == [100.0], "active 레벨만 클러스터 중심"


def test_confluence_zone_cap3():
    """가격대(분류 피벗) 기여 cap 3 — 피벗 4개 밀집이어도 3×w_zone까지만."""
    from infrapilot.analysis_core.confluence import _score_at
    from infrapilot.analysis_core.ict import StructureState
    from infrapilot.analysis_core.models import Pivot, PT_HIGH, LevelSource
    cfg = StructureConfig()
    st = StructureState()
    for b in range(4):                                   # 4개 분류 피벗 100 근처
        st.pivots.append(Pivot(bar=b, ts=b, price=100.0 + 0.01 * b, ptype=PT_HIGH, classified=1))
    score, cnt, srcs = _score_at(100.0, tol=1.0, bar=0, state=st, candles=[], cfg=cfg)
    assert abs(score - 3 * cfg.w_zone) < 1e-9, f"cap3: 3×{cfg.w_zone} 여야, 실제 {score}"
    assert LevelSource.ZONE in srcs


def test_confluence_lt_weight():
    """LT 레벨이 tol 이내면 고가중(w_lt)으로 점수에 반영(비공허)."""
    from infrapilot.analysis_core.confluence import _score_at
    from infrapilot.analysis_core.ict import StructureState
    from infrapilot.analysis_core.models import LevelSource
    cfg = StructureConfig()
    st = StructureState()
    st.lt_lvls = [100.0, 100.5]                          # 둘 다 100 기준 tol 이내
    score, cnt, srcs = _score_at(100.0, tol=1.0, bar=0, state=st, candles=[], cfg=cfg)
    assert abs(score - 2 * cfg.w_lt) < 1e-9, f"2×{cfg.w_lt} 여야, 실제 {score}"
    assert LevelSource.LT_REB in srcs
