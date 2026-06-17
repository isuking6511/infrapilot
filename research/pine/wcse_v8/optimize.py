# -*- coding: utf-8 -*-
"""
WCSE 최적화 — 명세서 PART C 프로토콜
  1) 민감도: 경제성 3개(min_plan_score, sweep_buf_atr, rr_min)를 한 축씩 흔들어
     '뾰족한 봉우리 vs 넓은 고원' 판별 (고원 중심 채택)
  2) 워크포워드: IS에서 고원 중심 → OOS 1회 검증 (OOS PF ≥ IS×0.6 통과)
  3) 다종목 동시 생존 필터
"""
from __future__ import annotations
import itertools
import numpy as np
import pandas as pd
from dataclasses import replace
from engine import WCSEEngine, WCSEParams, metrics

ECON_GRID = {
    "min_plan_score": [2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
    "sweep_buf_atr":  [0.1, 0.25, 0.4, 0.6],
    "rr_min":         [1.5, 2.0, 2.5, 3.0],
}


def run_one(df: pd.DataFrame, base: WCSEParams, **over) -> dict:
    p = replace(base, **over)
    res = WCSEEngine(p).run(df)
    m = metrics(res)
    m.update(over)
    return m


def sensitivity(df: pd.DataFrame, base: WCSEParams, grid: dict = None) -> pd.DataFrame:
    """한 번에 한 파라미터씩 1D 스윕 — 고원 판별용."""
    grid = grid or ECON_GRID
    rows = []
    for key, vals in grid.items():
        for v in vals:
            m = run_one(df, base, **{key: v})
            m["param"], m["value"] = key, v
            rows.append(m)
    return pd.DataFrame(rows)


def grid_search(df: pd.DataFrame, base: WCSEParams, grid: dict = None,
                min_trades: int = 15) -> pd.DataFrame:
    grid = grid or ECON_GRID
    keys = list(grid)
    rows = []
    for combo in itertools.product(*grid.values()):
        over = dict(zip(keys, combo))
        m = run_one(df, base, **over)
        rows.append(m)
    out = pd.DataFrame(rows)
    return out[out["trades"] >= min_trades].copy() if "trades" in out else out


def plateau_pick(gs: pd.DataFrame, keys, score_col="profit_factor") -> dict:
    """고원 중심 선택: 각 조합의 점수를 '이웃 조합 중앙값'으로 평활 후 최댓값.
    뾰족한 단독 봉우리는 이웃 평활에서 깎여 자동 탈락."""
    if gs.empty:
        return {}
    gs = gs.replace([np.inf, -np.inf], np.nan).dropna(subset=[score_col]).copy()
    vals = {k: sorted(gs[k].unique()) for k in keys}

    def neighbors(row):
        sel = gs
        cond = pd.Series(True, index=gs.index)
        for k in keys:
            vlist = vals[k]
            idx = vlist.index(row[k])
            near = {vlist[j] for j in (idx - 1, idx, idx + 1) if 0 <= j < len(vlist)}
            cond &= gs[k].isin(near)
        return sel[cond][score_col].median()

    gs["smoothed"] = gs.apply(neighbors, axis=1)
    best = gs.loc[gs["smoothed"].idxmax()]
    return {k: best[k] for k in keys} | {"smoothed_pf": float(best["smoothed"])}


def walk_forward(df: pd.DataFrame, base: WCSEParams, split: float = 0.7,
                 grid: dict = None, min_trades: int = 10) -> dict:
    grid = grid or ECON_GRID
    cut = int(len(df) * split)
    df_is, df_oos = df.iloc[:cut], df.iloc[cut:]
    gs = grid_search(df_is, base, grid, min_trades)
    if gs.empty:
        return {"verdict": "IS 트레이드 부족 — 데이터/필터 점검"}
    pick = plateau_pick(gs, list(grid))
    chosen = {k: pick[k] for k in grid}
    m_is = run_one(df_is, base, **chosen)
    m_oos = run_one(df_oos, base, **chosen)
    ratio = (m_oos.get("profit_factor", 0) / m_is["profit_factor"]
             if m_is.get("profit_factor") else 0)
    return {
        "chosen": chosen,
        "IS": m_is, "OOS": m_oos,
        "oos_is_pf_ratio": round(float(ratio), 3),
        "verdict": "통과 (고원 유지)" if ratio >= 0.6 else "과최적화 의심 — 기각",
    }


def multi_symbol(dfs: dict[str, pd.DataFrame], base: WCSEParams, chosen: dict) -> pd.DataFrame:
    """선택 파라미터의 다종목 동시 생존 검사."""
    rows = []
    for sym, df in dfs.items():
        m = run_one(df, base, **chosen)
        m["symbol"] = sym
        rows.append(m)
    return pd.DataFrame(rows).set_index("symbol")
