"""scanner_job — 운영 배치 잡 (봉 마감 주기 실행 가정, 실시간 아님).

흐름: 업비트 KRW 전종목 수집(확정봉) → analysis_core.scan() → save_rankings(JSON).

설계:
- 확정봉: 마지막(미마감) 봉을 drop → candles[-1]이 '닫힌 봉'. bias/scan 전부 확정봉 기준 →
  실행마다 결정적(라이브 봉 깜빡임 제거). BTC bias도 BTC 확정봉으로 1회 산출해 알트에 주입.
- save_rankings(data) 분리: 지금은 JSON 파일, 나중에 Redis로 이 함수만 교체.
- 종목 실패 격리: 한 종목 예외가 전체를 막지 않음(dead-list).
- analysis_core는 순수 — 이 잡(수집·저장·스케줄)이 바깥 레이어. 단방향 의존 유지.

★ 이 모듈의 JSON 스키마 = 웹 API 계약. setup.entry/stop/target/direction/rr/score 포함
  (웹이 차트에 진입·손절·목표 3선 + 캔들을 그릴 재료).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import ccxt
import redis

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from infrapilot.analysis_core.models import Candle, Setup
from infrapilot.analysis_core.ict import StructureEngine, StructureConfig
from infrapilot.analysis_core.setup import compute_bias, apply_btc_gate, resolve_direction, SetupConfig
from infrapilot.analysis_core.scanner import scan, ScanInput, ScannerConfig, TickerRank, _wilder_atr

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
TFS = ["1d", "4h", "1h", "15m"]
BARS = 200                 # TF당 수집 봉 수
TOP_N_CANDLES = 15         # 캔들 배열을 payload에 담을 TF별 상위 종목 수(용량 절감)
NEAR_GATE = {"15m": 2.5, "1h": 3.0, "4h": 4.0, "1d": 6.0}   # 웹 근접 게이트(튜닝값)
OUT_PATH = Path(__file__).resolve().parents[1] / "data" / "rankings.json"   # (구 JSON 경로, 미사용)

# Redis 저장소 (모듈 레벨 1회 생성, 하드코딩 X — 로컬=localhost / K8s=Service명 'redis')
_redis = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")),
                     decode_responses=True, socket_connect_timeout=3, socket_timeout=3)
_TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}   # TTL=봉주기×2


# ──────────────────────────── 수집 (확정봉) ────────────────────────────
def fetch(ex, symbol, tf, total=BARS) -> list[Candle]:
    """페이지네이션으로 ~total봉. 마지막(미마감) 봉은 drop → [-1]=확정봉."""
    since = ex.milliseconds() - (total + 1) * TF_MS[tf]
    rows = []
    while len(rows) < total + 1:
        batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=200)
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
    candles = [Candle(ts=int(r[0]), open=float(r[1]), high=float(r[2]),
                      low=float(r[3]), close=float(r[4]), volume=float(r[5])) for r in uniq]
    return candles[:-1] if len(candles) > 1 else candles    # 확정봉만


def _ema(vals, span):
    a = 2.0 / (span + 1); e = vals[0]; out = [e]
    for v in vals[1:]:
        e = e + a * (v - e); out.append(e)
    return out


def htf_flags(candles, span=50):
    closes = [c.close for c in candles]
    e = _ema(closes, span)
    return closes[-1] > e[-1], closes[-1] < e[-1]


def resolve_bias(candles, btc_bull, btc_bear, is_btc, scfg, structcfg, use_btc_bias):
    atr = _wilder_atr(candles)
    eng = StructureEngine(structcfg)
    for i in range(len(candles)):
        eng.update(i, candles, atr[i])
    hb, hbe = htf_flags(candles)
    bull, bear = compute_bias(eng.state, candles[-1].close, hb, hbe, scfg.req_align)
    bull, bear = apply_btc_gate(bull, bear, btc_bull, btc_bear, is_btc, use_btc_bias)
    return resolve_direction(bull, bear)


# ──────────────────────────── 직렬화 (= 웹 API 계약) ────────────────────────────
def _setup_dict(s: Setup | None):
    if s is None:
        return None
    return {
        "direction": s.direction,   # +1 long / -1 short
        "entry": s.entry,           # 웹: 진입선
        "stop": s.stop,             # 웹: 손절선
        "target": s.target,         # 웹: 목표선
        "rr": s.rr,
        "score": s.score,
        "src_text": s.src_text,     # 합류 근거 라벨
        "mode": s.mode,
    }


def _rank_dict(tr: TickerRank):
    return {
        "symbol": tr.symbol,
        "rank_score": tr.rank_score,      # setup 없으면 None(검색용 행)
        "distance_atr": tr.distance_atr,
        "close": tr.close,
        "setup": _setup_dict(tr.setup),
    }


def build_payload(by_tf, candles_by_sym, btc_bias, dead) -> dict:
    """웹이 그대로 소비할 JSON 구조 생성."""
    tfs = {}
    for tf, rows in by_tf.items():
        as_of = candles_by_sym.get("BTC/KRW", {}).get(tf)
        tfs[tf] = {
            "as_of_ts": as_of[-1][0] if as_of else None,   # 확정봉 ts
            "ranking": [_rank_dict(tr) for tr in rows],
        }
    # 캔들: TF별 상위 N종목만 (용량 절감)
    candles_out: dict[str, dict[str, list]] = {}
    for tf, rows in by_tf.items():
        for tr in [r for r in rows if r.setup is not None][:TOP_N_CANDLES]:
            arr = candles_by_sym.get(tr.symbol, {}).get(tf)
            if arr:
                candles_out.setdefault(tr.symbol, {})[tf] = arr
    return {
        "generated_at": int(time.time() * 1000),
        "exchange": "upbit",
        "quote": "KRW",
        "btc_bias": btc_bias,
        "timeframes": tfs,
        "candles": candles_out,
        "dead": [list(d) for d in dead],
    }


# ──────────────────────────── 저장 (교체 지점: JSON → 나중에 Redis) ────────────────────────────
def save_rankings(data: dict) -> None:
    """저장소(교체 지점) — Redis. TF별 분리 키 + TTL(봉주기×2). 값은 JSON 문자열.
    load_rankings()가 이 키들을 모아 동일한 full dict로 재조립하므로 API는 불변.

    키:  rankings:{tf}          = {meta(generated_at/exchange/quote/btc_bias), as_of_ts, ranking}
         candles:{symbol}:{tf}  = [[ts,o,h,l,c,v], ...]  (상위15만)
    """
    meta = {k: data[k] for k in ("generated_at", "exchange", "quote", "btc_bias")}
    for tf, t in data["timeframes"].items():
        _redis.set(f"rankings:{tf}",
                   json.dumps({**meta, "as_of_ts": t["as_of_ts"], "ranking": t["ranking"]}, ensure_ascii=False),
                   ex=_TF_SEC.get(tf, 3600) * 2)
    for sym, bytf in data["candles"].items():
        for tf, arr in bytf.items():
            _redis.set(f"candles:{sym}:{tf}", json.dumps(arr), ex=_TF_SEC.get(tf, 3600) * 2)


# ──────────────────────────── 오케스트레이션 (골격) ────────────────────────────
def _arr(candles: list[Candle]) -> list[list]:
    return [[c.ts, c.open, c.high, c.low, c.close, c.volume] for c in candles]


def run(symbols=None, save=True) -> dict:
    """봉 마감 주기에 호출. 전종목 × TF 수집(확정봉) → bias → scan → build → save.

    웹 랭킹이므로 use_btc_bias=False(숏 차단은 자동매매 몫) → 양방향 랭킹. 종목 실패 격리.
    """
    ex = ccxt.upbit({"enableRateLimit": True})
    if symbols is None:
        m = ex.load_markets()
        symbols = sorted(s for s, mk in m.items() if mk.get("quote") == "KRW" and mk.get("active", True))
    cfg = ScannerConfig(structure=StructureConfig(), setup=SetupConfig(min_plan_score=3.0),
                        near_dist_atr_by_tf=NEAR_GATE)

    by_tf: dict[str, list] = {}
    candles_by_sym: dict[str, dict[str, list]] = {}
    dead: list[tuple] = []
    btc_bias = {"bull": None, "bear": None, "ref_close": None, "ref_bar_ts": None}

    for tf in TFS:
        t0 = time.time()
        try:
            btc = fetch(ex, "BTC/KRW", tf)
        except Exception:
            dead.append(("BTC/KRW", f"{tf}:fetch")); traceback.print_exc(); continue
        btc_bull, btc_bear = htf_flags(btc)
        candles_by_sym.setdefault("BTC/KRW", {})[tf] = _arr(btc)
        if tf == "4h":
            btc_bias = {"bull": btc_bull, "bear": btc_bear,
                        "ref_close": btc[-1].close, "ref_bar_ts": btc[-1].ts}

        inputs = []
        for sym in symbols:
            try:
                c = btc if sym == "BTC/KRW" else fetch(ex, sym, tf)
                if not c:
                    dead.append((sym, f"{tf}:빈데이터")); continue
                candles_by_sym.setdefault(sym, {})[tf] = _arr(c)
                bias = resolve_bias(c, btc_bull, btc_bear, "BTC" in sym, cfg.setup, cfg.structure, use_btc_bias=False)
                inputs.append(ScanInput(sym, tf, c, bias))
            except Exception as e:
                dead.append((sym, f"{tf}:{type(e).__name__}"))
            if sym != "BTC/KRW":
                time.sleep(0.1)

        res = scan(inputs, cfg)
        by_tf[tf] = res.get(tf, [])
        n_setup = sum(1 for r in by_tf[tf] if r.setup is not None)
        print(f"[{tf}] 평가 {len(inputs)}종 / 셋업 {n_setup}종 / 죽은 {sum(1 for d in dead if d[1].startswith(tf))}건 "
              f"/ {time.time()-t0:.0f}s / as_of_ts={btc[-1].ts}")

    payload = build_payload(by_tf, candles_by_sym, btc_bias, dead)
    if save:
        save_rankings(payload)
        print(f"\n[저장] Redis 갱신  generated_at={payload['generated_at']}  (rankings:* / candles:*)")
    return payload


if __name__ == "__main__":
    run()
