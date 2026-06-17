# -*- coding: utf-8 -*-
"""
WCSE — Wave-Confluence Structural Engine (Python 포팅, 명세서 PART B 1:1)
Pine v7 / v6_strategy 의미론을 봉 단위로 재현한다.

파이프라인(매 봉):
  1) 피벗(swLen) → ST/IT/LT 구조 분류 (ICT 계층)
  2) BOS/CHoCH → 넥라인·오더블록·SD목표 레벨 등록
  3) FVG + 리밸런스 극점(rITH/rITL)
  4) 꼬리 레벨 / 피보 사다리(레그 변경시 1회, legSig 가드)
  5) 수평 레벨 1회터치 소멸 (빗각·포크·돈치안 = 동적, 비소멸)
  6) 합류 점수(S) — 가격대 cap 3, LT 가중, Neely 시간보너스
  7) 상태기계: 없음→암드(존)→스윕 트리거(터틀수프)→활성
  8) TP 사다리 50/30/20 + TP1 후 본절(BE), 구조무효화 쿨다운
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
import pandas as pd


# ──────────────────────────── 파라미터 ────────────────────────────
@dataclass
class WCSEParams:
    # 구조 (거칠게)
    sw_len: int = 8
    max_piv: int = 80
    conf_tol_atr: float = 0.5
    min_leg_atr: float = 1.5
    max_leg_atr: float = 0.0           # 0=끔
    # 상위TF 바이어스
    htf_mult: int = 4                  # 상위TF = 현재TF × N (예: 1D→4D 근사) — 리샘플 기반
    htf_ema: int = 50
    req_align: bool = True
    # 경제성 (최적화 대상)
    min_plan_score: float = 3.0
    sweep_buf_atr: float = 0.25
    rr_min: float = 2.0
    # 진입/관리
    use_sweep: bool = True
    use_ladder: bool = True
    zone_near_atr: float = 10.0
    cooldown_bars: int = 20
    t_bonus: float = 1.0
    scan_every: int = 2
    # v8 재분석 반영
    min_zone_dist_atr: float = 0.5     # 현재가에 붙은 존 암드 금지
    vol_sweep: bool = True             # 스윕 거래량 검증
    vol_sweep_x: float = 1.3
    use_vol_lvl: bool = True           # 고거래량 캔들 레벨
    vol_lvl_len: int = 50
    w_vol: float = 1.2
    lt_fib: bool = True                # LT 스윙 앵커 피보 1세트
    # 레벨 소스
    ext_multiples: tuple = (0.618, 1.0, 1.272, 1.618, 2.618)
    dual_fib: bool = True
    wick_ratio: float = 0.55
    # 동적 라인
    use_diagonals: bool = True
    fan_n: int = 4
    don_len: int = 20
    # 가중치 (고정 권장 — 자유도 축소)
    w_fib: float = 1.5
    w_diag: float = 1.5
    w_fork: float = 1.0
    w_neck: float = 0.5
    w_wick: float = 1.0
    w_fvg: float = 1.0
    w_don: float = 1.0
    w_zone: float = 1.2
    w_ob: float = 1.2
    w_lt: float = 1.8
    # 체결 모델
    fee_pct: float = 0.055             # 테이커 % (편도)
    slip_pct: float = 0.02             # 슬리피지 % (편도, 불리하게)
    risk_pct: float = 1.0
    init_equity: float = 10_000.0
    pessimistic: bool = True           # 같은 봉 SL·TP 동시 → SL 우선


# ──────────────────────────── 내부 상태 객체 ────────────────────────────
@dataclass
class Level:
    price: float
    wt: float
    active: bool = True
    src: int = 0   # 1꼬리 2피보 3넥라인 6FVG 8가격대 9OB 10LT/리밸런스


@dataclass
class Fvg:
    top: float
    bot: float
    dir: int       # 1 상승갭(지지) / -1 하락갭(저항)
    filled: bool = False


@dataclass
class DynLine:
    """동적 라인: anchor_bar에서 slope(가격/봉)로 연장. 비소멸."""
    a_bar: int
    a_price: float
    slope: float
    src: int       # 5빗각/부채살 4돌파선 7포크
    wt: float

    def value(self, bar: int) -> float:
        return self.a_price + self.slope * (bar - self.a_bar)


@dataclass
class Trade:
    dir: int
    entry_bar: int
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    qty: float
    score: float
    exit_bar: int = -1
    pnl: float = 0.0
    r_mult: float = 0.0
    reason: str = ""
    fills: list = field(default_factory=list)


# ──────────────────────────── 엔진 ────────────────────────────
class WCSEEngine:
    def __init__(self, p: WCSEParams = None):
        self.p = p or WCSEParams()

    # ---------- 보조 ----------
    @staticmethod
    def _atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
        h, l, c = df["high"].values, df["low"].values, df["close"].values
        pc = np.roll(c, 1); pc[0] = c[0]
        tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
        atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values
        return atr

    def _htf_bias(self, df: pd.DataFrame) -> np.ndarray:
        """상위TF EMA 바이어스 근사: htf_mult봉 묶음 종가 EMA, 미래참조 없이 직전 확정값."""
        p = self.p
        c = df["close"].values
        n = len(c)
        grp = (np.arange(n) // p.htf_mult)
        htf_close = pd.Series(c).groupby(grp).last()
        ema = htf_close.ewm(span=p.htf_ema, adjust=False).mean()
        # 각 봉에 '직전 완성된' 상위봉의 (close>ema) 부호를 매핑 (lookahead 방지: shift 1그룹)
        sign = np.sign(htf_close.values - ema.values)
        sign = np.concatenate([[0.0], sign[:-1]])      # 직전 그룹값
        return sign[grp]

    # ---------- 메인 ----------
    def run(self, df: pd.DataFrame, collect_scores: bool = False):
        """df: columns open/high/low/close/volume, DatetimeIndex 권장."""
        p = self.p
        o = df["open"].values; h = df["high"].values
        l = df["low"].values;  c = df["close"].values
        n = len(df)
        atr = self._atr(df)
        htf = self._htf_bias(df)

        # 피벗/구조
        pivP: list[float] = []; pivB: list[int] = []
        pivT: list[int] = [];   pivK: list[int] = []
        ith_hist: list[tuple] = []; itl_hist: list[tuple] = []
        lt_lvls: list[float] = []
        cur_ith = cur_itl = None
        last_conf_ith = last_conf_itl = None
        ms_dir = 0
        neckline = None
        # 레벨/FVG/리밸런스
        levels: list[Level] = []
        fvgs: list[Fvg] = []
        reb_ith = reb_itl = None
        leg_sig = None
        # 동적 라인 캐시 (피벗 확정 시 재계산)
        dyn_lines: list[DynLine] = []
        # 상태기계
        st = 0; a_dir = 0
        a_zone = a_sweep = None; a_touched = False; a_be = False
        a_score = 0.0
        tr_open: Trade | None = None
        stand_until = -1; last_inv_dir = 0
        equity = p.init_equity
        trades: list[Trade] = []
        eq_curve = np.full(n, np.nan)
        score_series = np.full(n, np.nan) if collect_scores else None

        vol = df["volume"].values
        vol_ma = pd.Series(vol).rolling(20, min_periods=1).mean().values
        lt_h: list[tuple] = []; lt_l: list[tuple] = []   # (price, bar)
        lt_sig = None
        hv_bar = -1
        a_vol_ok = False
        fee = p.fee_pct / 100.0
        slip = p.slip_pct / 100.0

        # ---------- 내부 함수 ----------
        def add_lvl(price: float, w: float, src: int, tol: float):
            for lv in levels:
                if lv.active and abs(lv.price - price) < tol * 0.4:
                    lv.wt += w
                    return
            levels.append(Level(price, w, True, src))
            if len(levels) > 150:
                levels.pop(0)

        def idx_recent(ty: int, k: int):
            cnt = 0
            for i in range(len(pivT) - 1, -1, -1):
                if pivT[i] == ty:
                    if cnt == k:
                        return i
                    cnt += 1
            return -1

        def confirm_it(ty: int):
            ai, bi, ci = idx_recent(ty, 2), idx_recent(ty, 1), idx_recent(ty, 0)
            if ai < 0 or bi < 0 or ci < 0:
                return None
            pa, pb_, pc = pivP[ai], pivP[bi], pivP[ci]
            ok = (pa < pb_ > pc) if ty == 1 else (pa > pb_ < pc)
            if not ok:
                return None
            pivK[bi] = 1
            return pb_, pivB[bi]

        def first_after(ty: int, after_bar: int):
            for i in range(len(pivP)):
                if pivT[i] == ty and pivK[i] >= 1 and pivB[i] > after_bar:
                    return pivB[i], pivP[i]
            return None, None

        def rebuild_dynlines(bar: int):
            """피벗 확정 시 호출 — 빗각(담는선)·부채살·포크·돈치안 슬로프 캐시."""
            dyn_lines.clear()
            if not p.use_diagonals:
                return
            struct = [(pivB[i], pivP[i], pivT[i]) for i in range(len(pivP)) if pivK[i] >= 1]
            if len(struct) < 2:
                return
            # 컨텍스트 극점 (추적창)
            highs = [(b, pr) for b, pr, t in struct if t == 1]
            lows = [(b, pr) for b, pr, t in struct if t == -1]
            down_ctx = ms_dir < 0 or (ms_dir == 0 and htf[bar] < 0)
            # ① 담는 선: 극점 → 이후 모든 같은타입 피벗을 담는 가장 최근 피벗
            def containing(anchor, pts, ty):
                ab, ap = anchor
                tol = p.conf_tol_atr * atr[bar]
                for i in range(len(pts) - 1, -1, -1):
                    tb, tp_ = pts[i]
                    if tb <= ab:
                        continue
                    sl = (tp_ - ap) / max(1, tb - ab)
                    ok = True
                    for jb, jp in pts:
                        if jb > ab:
                            lv = ap + sl * (jb - ab)
                            if ty == 1 and jp > lv + tol: ok = False; break
                            if ty == -1 and jp < lv - tol: ok = False; break
                    if ok:
                        return sl
                return None
            if highs:
                anc = max(highs, key=lambda x: x[1])
                sl = containing(anc, highs, 1)
                if sl is not None:
                    dyn_lines.append(DynLine(anc[0], anc[1], sl, 5, p.w_diag))
            if lows:
                anc = min(lows, key=lambda x: x[1])
                sl = containing(anc, lows, -1)
                if sl is not None:
                    dyn_lines.append(DynLine(anc[0], anc[1], sl, 5, p.w_diag))
            # ② 부채살: 컨텍스트 극점 → 이후 구조 피벗 fan_n개
            anc = (max(highs, key=lambda x: x[1]) if down_ctx and highs
                   else min(lows, key=lambda x: x[1]) if lows else None)
            if anc and p.fan_n > 0:
                cnt = 0
                for b, pr, t in struct:
                    if cnt >= p.fan_n: break
                    if b > anc[0]:
                        sl = (pr - anc[1]) / max(1, b - anc[0])
                        dyn_lines.append(DynLine(anc[0], anc[1], sl, 5, p.w_diag * 0.8))
                        cnt += 1
            # ③ 피치포크: 방탄 앵커 (v7 G5) — P0 극점, P1 반대, P2 같은타입
            ty0 = 1 if down_ctx else -1
            cands = sorted([s for s in struct if s[2] == ty0],
                           key=lambda x: -x[1] if ty0 == 1 else x[1])
            fork = None
            for ab, ap, _ in cands:
                t1 = next(((b2, p2) for b2, p2, t2 in struct if t2 == -ty0 and b2 > ab), None)
                if not t1: continue
                t2 = next(((b3, p3) for b3, p3, t3 in struct if t3 == ty0 and b3 > t1[0]), None)
                if not t2: continue
                fork = (ab, ap, t1, t2); break
            if fork is None:  # 폴백: 가장 오래된 유효 피벗
                for ab, ap, ty_ in struct:
                    t1 = next(((b2, p2) for b2, p2, t2 in struct if t2 == -ty_ and b2 > ab), None)
                    if not t1: continue
                    t2 = next(((b3, p3) for b3, p3, t3 in struct if t3 == ty_ and b3 > t1[0]), None)
                    if not t2: continue
                    fork = (ab, ap, t1, t2); break
            if fork:
                ab, ap, (b1, p1), (b2, p2) = fork
                mslope = ((p1 + p2) / 2 - ap) / max(1, (b1 + b2) / 2 - ab)
                dyn_lines.append(DynLine(ab, ap, mslope, 7, p.w_fork))           # median
                off1 = p1 - (ap + mslope * (b1 - ab))
                off2 = p2 - (ap + mslope * (b2 - ab))
                dyn_lines.append(DynLine(ab, ap + off1, mslope, 7, p.w_fork))    # tine L
                dyn_lines.append(DynLine(ab, ap + off2, mslope, 7, p.w_fork))    # tine H

        def time_asym(bar: int):
            if len(pivP) < 2:
                return None, False, 0
            b1, b0 = pivB[-2], pivB[-1]
            imp = max(1, b0 - b1)
            pb_ = bar - b0
            tr_ = pb_ / imp
            p1_, p0_ = pivP[-2], pivP[-1]
            m1d = 1 if p0_ > p1_ else -1
            leg = abs(p0_ - p1_)
            fast = leg > 0 and abs(c[bar] - p0_) / leg >= 1.0 and pb_ <= imp
            return tr_, fast, m1d

        def cluster_score(price: float, tol: float, bar: int):
            """명세 B-3: 레벨합류 + 동적라인 + 가격대(cap3) + LT."""
            s = 0.0
            for lv in levels:
                if lv.active and abs(lv.price - price) <= tol:
                    s += lv.wt
            for dl in dyn_lines:
                if abs(dl.value(bar) - price) <= tol:
                    s += dl.wt
            # Donchian (동적, 비소멸)
            if bar >= p.don_len:
                dH = h[bar - p.don_len:bar].max()
                dL = l[bar - p.don_len:bar].min()
                if abs(dH - price) <= tol: s += p.w_don
                if abs(dL - price) <= tol: s += p.w_don
            zc = 0
            for i in range(len(pivP)):
                if pivK[i] >= 1 and zc < 3 and abs(pivP[i] - price) <= tol:
                    s += p.w_zone; zc += 1
            for lt in lt_lvls:
                if abs(lt - price) <= tol:
                    s += p.w_lt
            return s

        def close_trade(t: Trade, bar: int, px: float, frac: float, reason: str):
            nonlocal equity
            px_eff = px * (1 - slip * t.dir)               # 청산 슬리피지 불리
            gross = (px_eff - t.entry) * t.dir * t.qty * frac
            cost = (t.entry + px_eff) * t.qty * frac * fee
            pnl = gross - cost
            equity += pnl
            t.pnl += pnl
            t.fills.append((bar, px_eff, frac, reason))

        # ---------- 메인 루프 ----------
        for i in range(n):
            tol = p.conf_tol_atr * atr[i]
            piv_event = False

            # 1) 피벗 확정 (swLen 우측 지연)
            j = i - p.sw_len
            if j >= p.sw_len:
                win_h = h[j - p.sw_len:i + 1]; win_l = l[j - p.sw_len:i + 1]
                if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                    pivP.append(h[j]); pivB.append(j); pivT.append(1); pivK.append(0)
                    piv_event = True
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
                    piv_event = True
                    r = confirm_it(-1)
                    if r:
                        cur_itl, _ = r; last_conf_itl = r[0]
                        itl_hist.append(r)
                        if len(itl_hist) >= 3:
                            (pm1, _), (pm, _), (pp1, _) = itl_hist[-3], itl_hist[-2], itl_hist[-1]
                            if pm < pm1 and pm < pp1:
                                lt_lvls.append(pm); lt_lvls[:] = lt_lvls[-10:]
                                lt_l.append((pm, itl_hist[-2][1])); lt_l[:] = lt_l[-6:]
                # 추적 한도
                while len(pivP) > p.max_piv:
                    pivP.pop(0); pivB.pop(0); pivT.pop(0); pivK.pop(0)

            # 2) BOS/CHoCH + 넥라인/OB/SD
            if cur_ith is not None and c[i] > cur_ith:
                ms_dir = 1; neckline = cur_ith
                add_lvl(cur_ith, p.w_neck, 3, tol)
                for k in range(1, min(13, i + 1)):          # 불리시 OB
                    if c[i - k] < o[i - k]:
                        add_lvl(l[i - k], p.w_ob, 9, tol); break
                if last_conf_itl is not None:
                    sw = cur_ith - last_conf_itl
                    if sw > 0:
                        add_lvl(cur_ith + 1.0 * sw, p.w_fib * .8, 2, tol)
                        add_lvl(cur_ith + 2.0 * sw, p.w_fib * .8, 2, tol)
                        add_lvl(cur_ith + 2.5 * sw, p.w_fib * .6, 2, tol)
                cur_ith = None
            if cur_itl is not None and c[i] < cur_itl:
                ms_dir = -1; neckline = cur_itl
                add_lvl(cur_itl, p.w_neck, 3, tol)
                for k in range(1, min(13, i + 1)):          # 베어리시 OB
                    if c[i - k] > o[i - k]:
                        add_lvl(h[i - k], p.w_ob, 9, tol); break
                if last_conf_ith is not None:
                    sw = last_conf_ith - cur_itl
                    if sw > 0:
                        add_lvl(cur_itl - 1.0 * sw, p.w_fib * .8, 2, tol)
                        add_lvl(cur_itl - 2.0 * sw, p.w_fib * .8, 2, tol)
                        add_lvl(cur_itl - 2.5 * sw, p.w_fib * .6, 2, tol)
                cur_itl = None

            # 3) FVG + 리밸런스
            if i >= 2:
                if l[i] > h[i - 2]:
                    add_lvl(h[i - 2], p.w_fvg, 6, tol)
                    fvgs.append(Fvg(l[i], h[i - 2], 1))
                if h[i] < l[i - 2]:
                    add_lvl(l[i - 2], p.w_fvg, 6, tol)
                    fvgs.append(Fvg(l[i - 2], h[i], -1))
                if len(fvgs) > 30: fvgs.pop(0)
            for f in fvgs:
                if f.filled: continue
                if f.dir == -1 and h[i] >= f.bot:
                    f.filled = True; reb_ith = h[i]; add_lvl(h[i], p.w_lt, 10, tol)
                if f.dir == 1 and l[i] <= f.top:
                    f.filled = True; reb_itl = l[i]; add_lvl(l[i], p.w_lt, 10, tol)

            # 4) 꼬리 레벨
            rng = h[i] - l[i]
            if rng > 0:
                body_hi, body_lo = max(o[i], c[i]), min(o[i], c[i])
                if (h[i] - body_hi) / rng >= p.wick_ratio: add_lvl(body_hi, p.w_wick, 1, tol)
                if (body_lo - l[i]) / rng >= p.wick_ratio: add_lvl(body_lo, p.w_wick, 1, tol)

            # 4b) v8: 고거래량 캔들 레벨 — 최고거래량 봉 변경시 몸통 경계 등록
            if p.use_vol_lvl and i >= p.vol_lvl_len:
                w0 = i - p.vol_lvl_len + 1
                hv = w0 + int(np.argmax(vol[w0:i + 1]))
                if hv != hv_bar:
                    hv_bar = hv
                    add_lvl(max(o[hv], c[hv]), p.w_vol, 11, tol)
                    add_lvl(min(o[hv], c[hv]), p.w_vol, 11, tol)

            # 5) 수평 레벨 소멸 (원칙 7) — 동적 라인은 면제
            for lv in levels:
                if lv.active and l[i] <= lv.price <= h[i]:
                    lv.active = False

            # 6) 피보 사다리: 레그 변경시 1회 (legSig — 인플레이션 가드)
            if last_conf_ith is not None and last_conf_itl is not None:
                sig = last_conf_ith + last_conf_itl
                if sig != leg_sig:
                    diff = last_conf_ith - last_conf_itl
                    sz_ok = diff >= p.min_leg_atr * atr[i] and \
                            (p.max_leg_atr <= 0 or diff <= p.max_leg_atr * atr[i])
                    if diff > 0 and sz_ok:
                        leg_sig = sig
                        ib = ith_hist[-1][1] if ith_hist else -1
                        lb = itl_hist[-1][1] if itl_hist else -1
                        g_dir = 1 if ib > lb else -1
                        for m in p.ext_multiples:
                            py = last_conf_itl + m * diff if g_dir == 1 else last_conf_ith - m * diff
                            if py > 0: add_lvl(py, p.w_fib, 2, tol)
                        if p.dual_fib:
                            ai = idx_recent(-1, 1) if g_dir == 1 else idx_recent(1, 1)
                            if ai >= 0:
                                ap_ = pivP[ai]
                                span = (last_conf_ith - ap_) if g_dir == 1 else (ap_ - last_conf_itl)
                                if span > 0:
                                    for m in p.ext_multiples:
                                        py = ap_ + m * span if g_dir == 1 else ap_ - m * span
                                        if py > 0: add_lvl(py, p.w_fib * .7, 2, tol)

            # 6b) v8: LT 스윙 앵커 1세트 — IT 세트와 겹침 띠 (addLvl 병합 = 자동 부스트)
            if p.lt_fib and lt_h and lt_l:
                (lH, lHb), (lL, lLb) = lt_h[-1], lt_l[-1]
                sigL = lH + lL
                if sigL != lt_sig and lH > lL:
                    lt_sig = sigL
                    dL = lH - lL
                    gd = 1 if lHb > lLb else -1
                    for m in p.ext_multiples:
                        py = lL + m * dL if gd == 1 else lH - m * dL
                        if py > 0: add_lvl(py, p.w_fib, 2, tol)

            if piv_event:
                rebuild_dynlines(i)

            # 7) 시간 비대칭 / 바이어스
            tr_, fast_end, m1d = time_asym(i)
            t_bon = p.t_bonus if (tr_ is not None and tr_ >= 1.0) else 0.0
            bull = ms_dir > 0 and (not p.req_align or htf[i] > 0)
            bear = ms_dir < 0 and (not p.req_align or htf[i] < 0)
            d = -1 if bear else (1 if bull else 0)

            # 8) 상태기계
            if st == 2 and tr_open is not None:
                t = tr_open
                # BE 전환
                if p.use_ladder and not a_be and \
                   ((t.dir == 1 and h[i] >= t.tp1) or (t.dir == -1 and l[i] <= t.tp1)):
                    # TP1 50% 체결
                    close_trade(t, i, t.tp1, 0.5, "TP1")
                    a_be = True
                eff_sl = t.entry if a_be else t.sl
                rem = 1.0 - sum(f[2] for f in t.fills)
                struct_bad = (ms_dir > 0 or (reb_ith and c[i] > reb_ith)) if t.dir == -1 \
                        else (ms_dir < 0 or (reb_itl and c[i] < reb_itl))
                hit_sl = (h[i] >= eff_sl) if t.dir == -1 else (l[i] <= eff_sl)
                hit_tp3 = (l[i] <= t.tp3) if t.dir == -1 else (h[i] >= t.tp3)
                hit_tp2 = a_be and ((l[i] <= t.tp2) if t.dir == -1 else (h[i] >= t.tp2))
                done = False
                if struct_bad:
                    if rem > 0: close_trade(t, i, c[i], rem, "구조무효")
                    stand_until = i + p.cooldown_bars; last_inv_dir = t.dir
                    done = True
                elif p.pessimistic and hit_sl:
                    if rem > 0: close_trade(t, i, eff_sl, rem, "BE" if a_be else "SL")
                    done = True
                else:
                    if hit_tp2 and rem > 0.21:
                        close_trade(t, i, t.tp2, 0.3, "TP2"); rem -= 0.3
                    if hit_tp3 and rem > 0:
                        close_trade(t, i, t.tp3, rem, "TP3"); done = True
                    elif (not p.pessimistic) and hit_sl:
                        if rem > 0: close_trade(t, i, eff_sl, rem, "BE" if a_be else "SL")
                        done = True
                    elif not p.use_ladder and hit_tp2 and rem > 0:
                        close_trade(t, i, t.tp2, rem, "TP"); done = True
                if done:
                    t.exit_bar = i
                    risk_amt = abs(t.entry - t.sl) * t.qty
                    t.r_mult = t.pnl / risk_amt if risk_amt > 0 else 0.0
                    trades.append(t); tr_open = None; st = 0; a_dir = 0

            elif st == 1:
                zlo, zhi = a_zone - tol * .5, a_zone + tol * .5
                cancel = (ms_dir > 0 or c[i] > zhi + tol or (reb_ith and c[i] > reb_ith)) \
                    if a_dir == -1 else \
                    (ms_dir < 0 or c[i] < zlo - tol or (reb_itl and c[i] < reb_itl))
                if cancel:
                    st = 0; a_dir = 0
                else:
                    if a_dir == -1 and h[i] >= zlo:
                        a_touched = True; a_sweep = max(a_sweep or h[i], h[i])
                        if vol[i] >= p.vol_sweep_x * vol_ma[i]: a_vol_ok = True
                    if a_dir == 1 and l[i] <= zhi:
                        a_touched = True; a_sweep = min(a_sweep or l[i], l[i])
                        if vol[i] >= p.vol_sweep_x * vol_ma[i]: a_vol_ok = True
                    trig = a_touched and (not p.vol_sweep or a_vol_ok) and \
                           ((c[i] < zlo) if a_dir == -1 else (c[i] > zhi))
                    if trig:
                        entry = c[i] * (1 + slip * a_dir)
                        # patch: when use_sweep=False, a_sweep may be None — fall back to zone edge
                        sweep_ref = a_sweep if a_sweep is not None else (zhi if a_dir == -1 else zlo)
                        sl = sweep_ref + p.sweep_buf_atr * atr[i] if a_dir == -1 \
                            else max(sweep_ref - p.sweep_buf_atr * atr[i], 1e-9)
                        rsk = max(1e-9, abs(entry - sl))
                        tps = [entry + a_dir * k * rsk for k in (1.5, p.rr_min, 3.5)]
                        d1 = d2 = 1e18; d3 = 0.0
                        for lv in levels:
                            if not lv.active: continue
                            dd = abs(lv.price - entry)
                            on = lv.price < entry if a_dir == -1 else lv.price > entry
                            if on and dd <= atr[i] * 25:
                                if dd >= rsk and dd < d1: d1, tps[0] = dd, lv.price
                                if dd >= p.rr_min * rsk and dd < d2: d2, tps[1] = dd, lv.price
                                if dd > d3: d3, tps[2] = dd, lv.price
                        if d3 < p.rr_min * rsk:
                            tps[2] = entry + a_dir * 3.5 * rsk
                        qty = equity * p.risk_pct / 100.0 / rsk
                        tr_open = Trade(a_dir, i, entry, sl, tps[0], tps[1], tps[2],
                                        qty, a_score)
                        a_be = False; st = 2

            if st == 0 and d != 0 and (i > stand_until or d != last_inv_dir) \
               and not (fast_end and d == m1d) and i % p.scan_every == 0:
                best_p, best_s = None, 0.0
                for lv in levels:
                    if not lv.active: continue
                    ok_side = lv.price > c[i] if d == -1 else lv.price < c[i]
                    dd0 = abs(lv.price - c[i])
                    if ok_side and p.min_zone_dist_atr * atr[i] <= dd0 <= atr[i] * p.zone_near_atr:
                        s = cluster_score(lv.price, tol, i) + t_bon
                        if s >= p.min_plan_score and s > best_s:
                            best_s, best_p = s, lv.price
                if best_p is not None:
                    a_dir = d; a_zone = best_p; a_sweep = None
                    a_touched = (not p.use_sweep); a_vol_ok = (not p.vol_sweep)
                    a_score = best_s; st = 1
                if collect_scores:
                    score_series[i] = best_s

            eq_curve[i] = equity + (
                (c[i] - tr_open.entry) * tr_open.dir * tr_open.qty
                * (1 - sum(f[2] for f in tr_open.fills)) if tr_open else 0.0)

        # 미청산 강제 종료
        if tr_open is not None:
            rem = 1.0 - sum(f[2] for f in tr_open.fills)
            if rem > 0:
                close_trade(tr_open, n - 1, c[-1], rem, "EOD")
            tr_open.exit_bar = n - 1
            risk_amt = abs(tr_open.entry - tr_open.sl) * tr_open.qty
            tr_open.r_mult = tr_open.pnl / risk_amt if risk_amt > 0 else 0.0
            trades.append(tr_open)

        out = {"trades": trades, "equity": pd.Series(eq_curve, index=df.index)}
        if collect_scores:
            out["scores"] = pd.Series(score_series, index=df.index)
        return out


# ──────────────────────────── 성과 지표 ────────────────────────────
def metrics(result: dict, bars_per_year: int = 365) -> dict:
    trades = result["trades"]; eq = result["equity"].dropna()
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy_R": 0.0, "total_return_pct": 0.0,
                "mdd_pct": 0.0, "sharpe": 0.0}
    pnl = np.array([t.pnl for t in trades])
    rm = np.array([t.r_mult for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    ret = eq.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(bars_per_year) if ret.std() > 0 else 0.0
    dd = (eq / eq.cummax() - 1).min()
    return {
        "trades": len(trades),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": float(pf),
        "expectancy_R": float(rm.mean()),
        "total_return_pct": float(eq.iloc[-1] / eq.iloc[0] - 1) * 100,
        "mdd_pct": float(dd) * 100,
        "sharpe": float(sharpe),
    }
