"""실데이터 스모크 — 업비트 KRW 공개 OHLCV로 analysis_core 격리 확인 (임시 스크립트).

analysis_core 밖. 공개 OHLCV만(키 불필요). Private(잔고/주문) 절대 미사용.
사용:
  python scripts/smoke_realdata.py              # BTC/KRW 4h 하나
  python scripts/smoke_realdata.py all          # 메이저 4종 4h
  python scripts/smoke_realdata.py allkrw        # KRW 전종목 4h
  python scripts/smoke_realdata.py rank          # KRW 전종목 × 15m/1h/4h/1d 점수 랭킹

주의: 웹 랭킹은 use_btc_bias=False(숏 차단은 자동매매에서 처리) → 양방향 다 랭킹.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ccxt

from infrapilot.analysis_core.models import Candle
from infrapilot.analysis_core.ict import StructureEngine, StructureConfig
from infrapilot.analysis_core.setup import compute_bias, apply_btc_gate, resolve_direction, SetupConfig
from infrapilot.analysis_core.scanner import scan, ScanInput, ScannerConfig, _wilder_atr

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _fetch_retry(ex, symbol, tf, since, tries=4):
    for t in range(tries):
        try:
            return ex.fetch_ohlcv(symbol, tf, since=since, limit=200)
        except (ccxt.RateLimitExceeded, ccxt.DDoSProtection, ccxt.NetworkError) as e:
            wait = 5 * (t + 1)
            print(f"  [rate/net] {symbol} {tf} 재시도 {t+1}/{tries} ({wait}s): {type(e).__name__}")
            time.sleep(wait)
    return []


def fetch(ex, symbol, tf, total) -> list[Candle]:
    """업비트 요청당 최대 200봉 → since 전진 페이지네이션으로 ~total봉."""
    since = ex.milliseconds() - total * TF_MS[tf]
    rows = []
    while len(rows) < total:
        batch = _fetch_retry(ex, symbol, tf, since)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + TF_MS[tf]
        if len(batch) < 200:
            break
        time.sleep(0.15)
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda x: x[0]):
        if r[0] not in seen:
            seen.add(r[0]); uniq.append(r)
    return [Candle(ts=int(r[0]), open=float(r[1]), high=float(r[2]),
                   low=float(r[3]), close=float(r[4]), volume=float(r[5])) for r in uniq]


def ema(vals, span):
    a = 2.0 / (span + 1); e = vals[0]; out = [e]
    for v in vals[1:]:
        e = e + a * (v - e); out.append(e)
    return out


def htf_flags(candles, span=50):
    closes = [c.close for c in candles]
    e = ema(closes, span)
    return closes[-1] > e[-1], closes[-1] < e[-1]


def resolve_bias(candles, hb, hbe, is_btc, btc_bull, btc_bear, scfg, structcfg, use_btc_bias):
    atr = _wilder_atr(candles)
    eng = StructureEngine(structcfg)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i])
    bull, bear = compute_bias(eng.state, candles[-1].close, hb, hbe, scfg.req_align)
    bull, bear = apply_btc_gate(bull, bear, btc_bull, btc_bear, is_btc, use_btc_bias)
    return resolve_direction(bull, bear)


def _krw_symbols(ex):
    m = ex.load_markets()
    return sorted(s for s, mk in m.items() if mk.get("quote") == "KRW" and mk.get("active", True))


def run_tf(ex, symbols, tf, total, cfg, use_btc_bias, topn):
    """한 TF: 전종목 수집 → bias → scan → 상위 topn 랭킹 출력. (symbols 재사용 위해 캐시 X)"""
    btc = fetch(ex, "BTC/KRW", tf, total)
    btc_bull, btc_bear = htf_flags(btc)
    data = {"BTC/KRW": btc}
    dead = []
    t0 = time.time()
    for sym in symbols:
        if sym in data:
            continue
        try:
            c = fetch(ex, sym, tf, total)
            if c:
                data[sym] = c
            else:
                dead.append((sym, "빈데이터"))
        except Exception as e:
            dead.append((sym, type(e).__name__))
        time.sleep(0.1)
    t_fetch = time.time() - t0

    inputs = []
    for sym, c in data.items():
        try:
            hb, hbe = htf_flags(c)
            bias = resolve_bias(c, hb, hbe, "BTC" in sym, btc_bull, btc_bear, cfg.setup, cfg.structure, use_btc_bias)
            inputs.append(ScanInput(sym, tf, c, bias))
        except Exception as e:
            dead.append((sym, "bias:" + type(e).__name__))

    t1 = time.time(); res = scan(inputs, cfg); t_scan = time.time() - t1
    rows = res.get(tf, [])
    ranked = [r for r in rows if r.setup is not None]

    print(f"\n{'='*100}\n[{tf}] 평가 {len(inputs)}종 / 셋업 {len(ranked)}종 / 죽은 {len(dead)}건 "
          f"| 수집 {t_fetch:.0f}s scan {t_scan*1000:.0f}ms | btc_bias bull={btc_bull}")
    print(f"{'#':>2} {'SYMBOL':11} {'rank':>7} {'dir':>4} {'entry':>12} {'stop':>12} {'target':>12} {'rr':>5} {'dist':>5} {'close':>12}")
    print("-" * 100)
    for i, tr in enumerate(ranked[:topn], 1):
        s = tr.setup
        print(f"{i:>2} {tr.symbol:11} {tr.rank_score:>7.2f} {s.direction:>+4d} {s.entry:>12,.4g} {s.stop:>12,.4g} "
              f"{s.target:>12,.4g} {s.rr:>5.2f} {tr.distance_atr:>5.2f} {tr.close:>12,.4g}  [{s.src_text}]")
    if not ranked:
        print("  (셋업 없음)")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    ex = ccxt.upbit({"enableRateLimit": True})
    cfg = ScannerConfig(structure=StructureConfig(), setup=SetupConfig(min_plan_score=3.0))

    if mode == "rank":
        symbols = _krw_symbols(ex)
        # 웹 랭킹: TF별 근접 게이트(튜닝값) — 임박 진입만 상위로. 자동매매(v4)는 불변.
        cfg = ScannerConfig(structure=StructureConfig(), setup=SetupConfig(min_plan_score=3.0),
                            near_dist_atr_by_tf={"15m": 2.5, "1h": 3.0, "4h": 4.0, "1d": 6.0})
        print(f"[rank] KRW {len(symbols)}종 × {list(TF_MS)} (use_btc_bias=False, mps=3.0, 근접게이트 {cfg.near_dist_atr_by_tf})")
        for tf in ["15m", "1h", "4h", "1d"]:
            run_tf(ex, symbols, tf, total=200, cfg=cfg, use_btc_bias=False, topn=15)
        return

    # 단일 TF 진단 모드 (4h)
    symbols = (_krw_symbols(ex) if mode == "allkrw"
               else ["BTC/KRW", "ETH/KRW", "SOL/KRW", "XRP/KRW"] if mode == "all"
               else ["BTC/KRW"])
    run_tf(ex, symbols, "4h", total=300, cfg=cfg, use_btc_bias=True, topn=20)


if __name__ == "__main__":
    main()
