"""InfraPilot Dashboard — FastAPI.

데이터 소스 두 개를 생명주기로 나눠 쓴다:
- 랭킹·캔들 = Redis (scanner_job이 봉마감마다 덮어씀, 재생성 가능한 캐시)
- 투표·댓글 = RDS (재생성 불가한 사용자 데이터)
웹 API는 거래소를 직접 호출하지 않는다 → 모든 사용자가 같은 확정봉 기준 결과를 본다(일관성).
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

import psycopg2
import redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="InfraPilot Dashboard")

# 프론트는 같은 오리진(FastAPI가 정적 HTML 직접 서빙)이라 CORS가 필요 없지만,
# 로컬 프론트 개발 서버용으로만 화이트리스트를 둔다(와일드카드 * 금지).
ALLOWED_ORIGINS = [
    "http://localhost:3000", "http://127.0.0.1:3000",
    "http://localhost:5173", "http://127.0.0.1:5173",   # vite
    "http://localhost:8000", "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── 데이터 소스: Redis ──────────────────────────────────────────────────
# 연결 객체 모듈 레벨 1회. 하드코딩 X — 로컬=localhost / K8s=Service명 'redis'.
_redis = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")),
                     decode_responses=True, socket_connect_timeout=3, socket_timeout=3)

TIMEFRAMES = ["15m", "1h", "4h", "1d"]
TF_SET = set(TIMEFRAMES)

EXCHANGES = [
    {"id": "upbit", "active": True},
    {"id": "krx", "active": False},
    {"id": "bybit", "active": False},
    {"id": "nasdaq", "active": False},
]


def load_rankings() -> dict:
    """랭킹 데이터 단일 소스(Redis). save_rankings가 넣은 TF별 키(rankings:{tf}, candles:{sym}:{tf})를
    모아 하나의 dict로 재조립 → 저장소를 바꿔도 API 라우트는 불변(인터페이스 분리).

    키 없으면(스캔 전/만료) 503, Redis 연결 실패도 503(빈 화면 말고 명확한 에러)."""
    try:
        tfs, meta = {}, {}
        for tf in TIMEFRAMES:
            raw = _redis.get(f"rankings:{tf}")
            if raw:
                o = json.loads(raw)
                meta = o
                tfs[tf] = {"as_of_ts": o["as_of_ts"], "ranking": o["ranking"]}
        if not tfs:
            raise HTTPException(status_code=503, detail="rankings 아직 없음 — scanner_job 먼저 실행")
        candles: dict[str, dict] = {}
        for key in _redis.scan_iter("candles:*"):
            _, sym, tf = key.split(":", 2)   # symbol에 '/'(BTC/KRW)는 있어도 ':'는 없어 안전
            val = _redis.get(key)
            if val:
                candles.setdefault(sym, {})[tf] = json.loads(val)
        return {**meta, "timeframes": tfs, "candles": candles}
    except redis.exceptions.RedisError:
        # 내부 에러 문자열(호스트·포트)은 응답에 싣지 않는다(§5.4) — 원인은 로그/메트릭으로.
        raise HTTPException(status_code=503, detail="랭킹 저장소(Redis) 연결 실패")


# ── 관측성: Prometheus 메트릭 ───────────────────────────────────────────
# 전용 레지스트리 → 기본 프로세스 메트릭과 섞이지 않고, 테스트에서 재import해도 중복 등록 오류 없음.
METRICS = CollectorRegistry()
HTTP_REQUESTS = Counter(
    "infrapilot_http_requests_total", "HTTP 요청 수",
    ["method", "route", "status"], registry=METRICS,
)
HTTP_LATENCY = Histogram(
    "infrapilot_http_request_duration_seconds", "HTTP 요청 처리 시간",
    ["route"], registry=METRICS,
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)


class ScannerCollector:
    """스크레이프 시점에 Redis의 scanner:last_run을 읽어 게이지로 노출.

    왜 이렇게: scanner_job은 몇 분 돌고 끝나는 CronJob이라 직접 스크레이프할 수 없다.
    Pushgateway를 띄우면 1GB 노드에 프로세스가 하나 더 늘어나므로, 이미 있는 Redis에
    실행 결과를 남기고 항상 떠 있는 웹이 대신 노출한다. 알람 기준은
    'time() - last_success > 봉주기×2' (= 스캐너가 멈췄다).
    """

    def collect(self):
        up = GaugeMetricFamily("infrapilot_redis_up", "Redis 연결 가능 여부(1/0)")
        try:
            raw = _redis.get("scanner:last_run")
            up.add_metric([], 1)
        except redis.exceptions.RedisError:
            raw = None
            up.add_metric([], 0)
        yield up
        if not raw:
            return
        r = json.loads(raw)
        g = GaugeMetricFamily("infrapilot_scanner_last_success_timestamp_seconds", "마지막 스캔 완료 시각(unix)")
        g.add_metric([], r["finished_at"])
        yield g
        g = GaugeMetricFamily("infrapilot_scanner_duration_seconds", "마지막 스캔 소요 시간")
        g.add_metric([], r["duration_sec"])
        yield g
        g = GaugeMetricFamily("infrapilot_scanner_dead_symbols", "마지막 스캔에서 실패한 (종목,TF) 수")
        g.add_metric([], r["dead"])
        yield g
        ev = GaugeMetricFamily("infrapilot_scanner_symbols_evaluated", "TF별 평가 종목 수", labels=["tf"])
        st = GaugeMetricFamily("infrapilot_scanner_setups", "TF별 셋업 발생 종목 수", labels=["tf"])
        for tf, n in r.get("evaluated", {}).items():
            ev.add_metric([tf], n)
        for tf, n in r.get("setups", {}).items():
            st.add_metric([tf], n)
        yield ev
        yield st


METRICS.register(ScannerCollector())


@app.middleware("http")
async def _observe(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # label은 실제 경로(/api/chart/1h/BTC/KRW)가 아니라 라우트 템플릿(/api/chart/{tf}/{symbol:path})
        # → 종목 수만큼 시계열이 폭증하는 카디널리티 문제 방지.
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            HTTP_LATENCY.labels(path).observe(time.perf_counter() - start)


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(METRICS), media_type=CONTENT_TYPE_LATEST)


# ── DB 장애 처리 ────────────────────────────────────────────────────────
@app.exception_handler(psycopg2.OperationalError)
async def _db_unavailable(request: Request, exc: psycopg2.OperationalError):
    # RDS가 내려가도 랭킹(Redis)은 계속 보이게 — 투표/댓글 API만 503으로 격리. 내부 정보는 숨김.
    return JSONResponse(status_code=503, content={"detail": "투표/댓글 저장소(DB)에 연결할 수 없음"})


@app.exception_handler(psycopg2.Error)
async def _db_error(request: Request, exc: psycopg2.Error):
    return JSONResponse(status_code=500, content={"detail": "DB 처리 중 오류"})


# ── 페이지 ──────────────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """스캐너 대시보드(정적 HTML, /api/* 소비). FastAPI가 직접 서빙 → 별도 웹서버 불필요."""
    return FileResponse(STATIC_DIR / "index.html")


# ── 웹 API: 랭킹 (Redis) ────────────────────────────────────────────────
@app.get("/api/exchanges")
async def api_exchanges():
    """거래소 목록 + 활성여부(준비중 표시용)."""
    return EXCHANGES


@app.get("/api/rankings/{tf}")
async def api_rankings(tf: str, limit: int = Query(5, ge=1, le=60)):
    """해당 TF 랭킹 상위 limit개. as_of_ts(확정봉 기준) 포함."""
    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {TIMEFRAMES}")
    d = load_rankings()
    t = d["timeframes"].get(tf)
    if not t:
        raise HTTPException(status_code=404, detail="해당 TF 데이터 없음")
    return {
        "exchange": d["exchange"], "quote": d["quote"], "tf": tf,
        "as_of_ts": t["as_of_ts"], "generated_at": d["generated_at"],
        "btc_bias": d["btc_bias"], "limit": limit, "total": len(t["ranking"]),
        "ranking": t["ranking"][:limit],
    }


@app.get("/api/chart/{tf}/{symbol:path}")
async def api_chart(tf: str, symbol: str):
    """캔들 + setup(진입·손절·목표 3선 재료). 심볼에 '/'가 있어 tf를 앞에 둔다(/api/chart/4h/BTC/KRW).
    candles 없으면(상위 N 밖) chart_status='pending' — 거래소 직접 호출은 하지 않음(일관성)."""
    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {TIMEFRAMES}")
    d = load_rankings()
    t = d["timeframes"].get(tf, {})
    entry = next((r for r in t.get("ranking", []) if r["symbol"] == symbol), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="종목 없음")
    candles = d.get("candles", {}).get(symbol, {}).get(tf)
    return {
        "symbol": symbol, "tf": tf, "as_of_ts": t.get("as_of_ts"),
        "close": entry["close"], "setup": entry["setup"],
        "candles": candles,                                   # null이면 프론트가 '차트 준비중'
        "chart_status": "ready" if candles else "pending",
    }


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1, max_length=20), limit: int = Query(60, ge=1, le=200)):
    """종목 검색(전 TF 심볼 합집합에서 부분일치). 셋업 있는 TF 표시."""
    d = load_rankings()
    ql = q.upper()
    found: dict[str, dict] = {}
    for tf in TIMEFRAMES:
        for r in d["timeframes"].get(tf, {}).get("ranking", []):
            if ql in r["symbol"].upper():
                e = found.setdefault(r["symbol"], {"symbol": r["symbol"], "close": r["close"], "setup_tfs": []})
                if r["setup"] is not None:
                    e["setup_tfs"].append(tf)
    results = sorted(found.values(), key=lambda x: (-len(x["setup_tfs"]), x["symbol"]))[:limit]
    return {"query": q, "count": len(found), "results": results}


# ── 투표/댓글 (RDS 영속 — 분석 랭킹과 달리 재생성 불가한 사용자 데이터) ─────────────
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}/[A-Z]{2,10}$")


def _client_ip(request: Request) -> str:
    """요청자 IP. k3s/web.yaml의 Service가 externalTrafficPolicy: Local이라 kube-proxy가
    출발지 IP를 SNAT하지 않는다 → request.client.host가 실제 사용자 IP.
    (Cluster 정책이면 모든 요청이 노드 IP로 보여서 '전원이 같은 사람'이 되고, 1인 1표와
    rate limit이 전체 사용자 단위로 걸리는 버그가 생긴다.)
    X-Forwarded-For는 일부러 안 읽는다 — 앞단 프록시가 없는 구성에서 그 헤더는 클라이언트가
    마음대로 위조할 수 있는 값이라서. Ingress/LB를 앞에 두면 그때 신뢰 프록시 기준으로 읽는다."""
    return request.client.host if request.client else "unknown"


def _anon_hash(ip: str) -> str:
    """IP를 그대로 저장하지 않고 salt와 함께 해시 — 비가역, 그래도 같은 IP는 같은 값이라
    '1인 1표/작성자 구분'에는 쓸 수 있다. IP 우회(재부팅·VPN)로 여러 표를 던지는 건 못 막는
    한계가 있음(CLAUDE.md §5.4에 명시된 절충)."""
    salt = os.environ.get("VOTE_SALT", "")
    return hashlib.sha256(f"{ip}:{salt}".encode()).hexdigest()


def _rate_limit(key: str, limit: int, window_sec: int) -> None:
    """Redis INCR+EXPIRE 기반 고정 윈도우 rate limit. 이미 연결돼 있는 _redis를 재사용해
    새 인프라 없이 스팸/남용을 완화(CLAUDE.md §5.4). Redis 장애 시엔 열어둔다(가용성 우선)."""
    try:
        n = _redis.incr(key)
        if n == 1:
            _redis.expire(key, window_sec)
    except redis.exceptions.RedisError:
        return
    if n > limit:
        raise HTTPException(status_code=429, detail="너무 자주 요청함 — 잠시 후 다시 시도")


def _check_symbol(v: str) -> str:
    v = v.upper()
    if not SYMBOL_RE.match(v):
        raise ValueError("symbol 형식 오류 (예: BTC/KRW)")
    return v


def _check_tf(v: str) -> str:
    if v not in TF_SET:
        raise ValueError(f"지원 TF: {TIMEFRAMES}")
    return v


class VoteIn(BaseModel):
    symbol: str
    tf: str
    direction: int

    _v_symbol = field_validator("symbol")(_check_symbol)
    _v_tf = field_validator("tf")(_check_tf)

    @field_validator("direction")
    @classmethod
    def _valid_direction(cls, v: int) -> int:
        if v not in (1, -1):
            raise ValueError("direction은 1(상승) 또는 -1(하락)만 허용")
        return v


class CommentIn(BaseModel):
    symbol: str
    tf: str
    content: str = Field(min_length=1, max_length=400)

    _v_symbol = field_validator("symbol")(_check_symbol)
    _v_tf = field_validator("tf")(_check_tf)

    @field_validator("content")
    @classmethod
    def _stripped(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("내용이 비어있음")
        return v


@app.post("/api/vote")
async def api_vote(body: VoteIn, request: Request):
    from infrapilot.db import repository   # lazy: DB 드라이버 문제가 있어도 랭킹 화면은 기동

    ip_hash = _anon_hash(_client_ip(request))
    _rate_limit(f"ratelimit:vote:{ip_hash}", limit=30, window_sec=60)
    repository.cast_vote(body.symbol, body.tf, body.direction, ip_hash)
    return {"status": "ok"}


@app.get("/api/votes/{tf}/{symbol:path}")
async def api_votes(tf: str, symbol: str):
    from infrapilot.db import repository

    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {TIMEFRAMES}")
    return {"symbol": symbol, "tf": tf, **repository.get_vote_summary(symbol, tf)}


@app.post("/api/comment")
async def api_comment(body: CommentIn, request: Request):
    from infrapilot.db import repository

    ip_hash = _anon_hash(_client_ip(request))
    _rate_limit(f"ratelimit:comment:{ip_hash}", limit=5, window_sec=60)
    repository.add_comment(body.symbol, body.tf, body.content, ip_hash)
    return {"status": "ok"}


@app.get("/api/comments/{tf}/{symbol:path}")
async def api_comments(tf: str, symbol: str, limit: int = Query(50, ge=1, le=200)):
    from infrapilot.db import repository

    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {TIMEFRAMES}")
    # content는 그대로 반환 — XSS 방지는 프론트가 렌더링할 때 이스케이프
    # (여기서 이스케이프하면 API를 다른 클라이언트가 쓸 때 이중 이스케이프될 수 있어서).
    return {"symbol": symbol, "tf": tf, "comments": repository.get_comments(symbol, tf, limit)}


@app.get("/health")
async def health():
    """liveness/readiness 공용. 의존성(Redis·DB)은 일부러 확인하지 않는다 —
    Redis가 잠깐 죽었다고 웹 Pod까지 재시작/트래픽 차단되면 장애가 번지기 때문(장애 격리).
    의존성 상태는 /metrics의 infrapilot_redis_up 으로 본다."""
    return {"status": "ok"}
