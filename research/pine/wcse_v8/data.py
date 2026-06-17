# -*- coding: utf-8 -*-
"""데이터 로더 + 스모크 테스트.
지호 환경: ccxt 수집기 → PostgreSQL (InfraPilot 스키마 재사용).
이 샌드박스에는 거래소 접근이 없으므로 합성 데이터로 엔진 무결성만 검증한다.
"""
import numpy as np
import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=[0], index_col=0)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def load_ccxt(symbol="BTC/USDT", timeframe="1d", limit=1500, exchange="bybit"):
    """지호 PC에서 실행용. pip install ccxt"""
    import ccxt
    ex = getattr(ccxt, exchange)()
    o = ex.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(o, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts")


def load_postgres(symbol: str, tf: str, dsn: str) -> pd.DataFrame:
    """InfraPilot RDS 스키마용 (psycopg2)."""
    import psycopg2
    q = """SELECT ts, open, high, low, close, volume FROM ohlcv
           WHERE symbol=%s AND timeframe=%s ORDER BY ts"""
    with psycopg2.connect(dsn) as conn:
        return pd.read_sql(q, conn, params=(symbol, tf),
                           parse_dates=["ts"]).set_index("ts")


def synth_ohlcv(n=2000, seed=42, s0=60_000.0) -> pd.DataFrame:
    """레짐(추세/횡보) 섞인 합성 시세 — 엔진 무결성 스모크용."""
    rng = np.random.default_rng(seed)
    drift = np.zeros(n)
    i = 0
    while i < n:
        seg = rng.integers(60, 240)
        drift[i:i + seg] = rng.choice([-0.0015, 0.0, 0.002])
        i += seg
    ret = drift + rng.normal(0, 0.02, n)
    close = s0 * np.exp(np.cumsum(ret))
    op = np.roll(close, 1); op[0] = s0
    spread = np.abs(rng.normal(0, 0.012, n)) * close
    high = np.maximum(op, close) + spread
    low = np.minimum(op, close) - spread
    vol = rng.lognormal(10, 0.5, n)
    idx = pd.date_range("2021-01-01", periods=n, freq="D")
    return pd.DataFrame({"open": op, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


if __name__ == "__main__":
    import json, time
    from engine import WCSEEngine, WCSEParams, metrics
    from optimize import sensitivity, walk_forward

    df = synth_ohlcv()
    p = WCSEParams()
    t0 = time.time()
    res = WCSEEngine(p).run(df, collect_scores=True)
    dt = time.time() - t0
    m = metrics(res)
    print(f"[스모크] {len(df)}봉 처리 {dt:.2f}s")
    print(json.dumps(m, indent=2, ensure_ascii=False))

    # 점수 인플레이션 검증: 동일 데이터 구간 반복 스캔 시 점수 분포가 안정적인가
    sc = res["scores"].dropna()
    if len(sc) > 20:
        half = len(sc) // 2
        print(f"[점수 안정성] 전반부 평균 {sc.iloc[:half].mean():.2f} / "
              f"후반부 평균 {sc.iloc[half:].mean():.2f} "
              f"(레짐 차이 외 구조적 우상향이 없어야 정상)")

    # 체결 무결성 검사
    bad = [t for t in res["trades"]
           if abs(sum(f[2] for f in t.fills) - 1.0) > 1e-9]
    print(f"[체결 무결성] 부분익절 합계≠100% 트레이드: {len(bad)}건 (0이어야 정상)")

    print("\n[민감도 1D — min_plan_score]")
    sens = sensitivity(df, p, {"min_plan_score": [2.0, 2.5, 3.0, 3.5, 4.0]})
    print(sens[["param", "value", "trades", "win_rate",
                "profit_factor", "expectancy_R"]].to_string(index=False))

    print("\n[워크포워드 70/30]")
    wf = walk_forward(df, p, grid={
        "min_plan_score": [2.5, 3.0, 3.5],
        "sweep_buf_atr": [0.25, 0.4],
        "rr_min": [2.0, 2.5]})
    print(json.dumps(wf, indent=2, ensure_ascii=False, default=str))
