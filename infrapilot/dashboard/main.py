"""InfraPilot Dashboard — FastAPI.

웹 API는 rankings.json(=scanner_job 산출물)만 읽는다. 거래소 직접 호출 X (일관성).
데이터 소스는 load_rankings() 하나로 격리 — 나중에 Redis로 이 함수만 교체.
(구 LLM analyzer DB 라우트는 보존하되 import를 lazy화해 앱 기동이 DB에 안 묶이게 함.)
"""

import hashlib
import json
import os
import re
from pathlib import Path

import redis
from pydantic import BaseModel, Field, field_validator

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

app = FastAPI(title="InfraPilot Dashboard")

# 프론트가 호출할 수 있게 CORS 화이트리스트(개발 포트). 운영 도메인은 배포 시 추가.
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

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# ── 데이터 소스 (교체 지점: Redis) ──────────────────────────────────────────
# 연결 객체 모듈 레벨 1회. 하드코딩 X — 로컬=localhost / K8s=Service명 'redis'.
_redis = redis.Redis(host=os.getenv("REDIS_HOST", "localhost"), port=int(os.getenv("REDIS_PORT", "6379")),
                     decode_responses=True, socket_connect_timeout=3, socket_timeout=3)

EXCHANGES = [
    {"id": "upbit", "active": True},
    {"id": "krx", "active": False},
    {"id": "bybit", "active": False},
    {"id": "nasdaq", "active": False},
]


def load_rankings() -> dict:
    """랭킹 데이터 단일 소스(Redis). save_rankings가 넣은 TF별 키(rankings:{tf}, candles:{sym}:{tf})를
    모아 기존과 동일한 full dict로 재조립 → API 라우트 불변. 거래소 직접 호출 X.

    키 없으면(스캔 전/만료) 503, Redis 연결 실패도 503(빈 화면 말고 명확한 에러)."""
    try:
        tfs, meta = {}, {}
        for tf in ("15m", "1h", "4h", "1d"):
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
        return {**meta, "timeframes": tfs, "candles": candles, "dead": []}
    except redis.exceptions.RedisError as e:
        raise HTTPException(status_code=503, detail=f"Redis 연결/조회 실패: {e}")


def _short(symbol: str) -> str:
    return symbol.split("/")[0]


templates.env.filters["short"] = _short

TIMEFRAMES = ["15m", "1h", "4h", "1d"]


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """새 합류 스캐너 대시보드(정적 HTML, /api/* 소비). FastAPI가 직접 서빙."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/legacy", response_class=HTMLResponse)
async def index(request: Request):
    from infrapilot.db.repository import get_latest_analysis   # lazy: DB 없어도 앱 기동
    rows = get_latest_analysis()

    # symbol → {timeframe: analysis_row}
    symbols_data: dict = {}
    for row in rows:
        sym = row["symbol"]
        tf = row["timeframe"]
        if sym not in symbols_data:
            symbols_data[sym] = {}
        symbols_data[sym][tf] = row

    # 상위 타임프레임 우선순위로 대표 신호 결정
    SIGNAL_TF_PRIORITY = ["1d", "4h", "1h", "15m"]

    def dominant_signal(tf_data: dict) -> str | None:
        for tf in SIGNAL_TF_PRIORITY:
            status = tf_data.get(tf, {}).get("status")
            if status in ("correction_ending", "impulse_ending"):
                return status
        return None

    correction_ending = []
    impulse_ending = []
    for s, data in symbols_data.items():
        sig = dominant_signal(data)
        if sig == "correction_ending":
            correction_ending.append(s)
        elif sig == "impulse_ending":
            impulse_ending.append(s)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "symbols_data": symbols_data,
        "correction_ending": correction_ending,
        "impulse_ending": impulse_ending,
        "timeframes": TIMEFRAMES,
        "short": _short,
    })


@app.get("/symbol/{symbol:path}", response_class=HTMLResponse)
async def symbol_detail(request: Request, symbol: str):
    from infrapilot.db.repository import get_analysis_by_symbol, get_ohlcv   # lazy
    analysis_rows = get_analysis_by_symbol(symbol)
    if not analysis_rows:
        raise HTTPException(status_code=404, detail="분석 데이터 없음")

    analysis = {row["timeframe"]: row for row in analysis_rows}

    ohlcv_data: dict = {}
    for tf in TIMEFRAMES:
        candles = get_ohlcv(symbol, tf, limit=100)
        # JSON 직렬화를 위해 Decimal → float 변환
        ohlcv_data[tf] = [
            {k: float(v) if k != "timestamp" else int(v) for k, v in c.items()}
            for c in candles
        ]

    # BTC/USDT → BYBIT:BTCUSDT
    tv_symbol = "BYBIT:" + symbol.split(":")[0].replace("/", "")

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "symbol": symbol,
        "symbol_short": _short(symbol),
        "tv_symbol": tv_symbol,
        "analysis": analysis,
        "timeframes": TIMEFRAMES,
        "ohlcv_json": json.dumps(ohlcv_data),
    })


# ── 웹 API (rankings.json 기반) ──────────────────────────────────────────
TF_SET = {"15m", "1h", "4h", "1d"}


@app.get("/api/exchanges")
async def api_exchanges():
    """거래소 목록 + 활성여부(준비중 표시용)."""
    return EXCHANGES


@app.get("/api/rankings/{tf}")
async def api_rankings(tf: str, limit: int = Query(5, ge=1, le=60)):
    """해당 TF 랭킹 상위 limit개. as_of_ts(확정봉 기준) 포함."""
    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {sorted(TF_SET)}")
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
    """캔들 + setup(3선 재료). 심볼에 '/'가 있어 tf를 앞에 둔다(/api/chart/4h/BTC/KRW).
    candles 없으면(상위 N 밖) chart_status='pending' — 거래소 직접 호출은 하지 않음(일관성)."""
    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {sorted(TF_SET)}")
    d = load_rankings()
    t = d["timeframes"].get(tf, {})
    entry = next((r for r in t.get("ranking", []) if r["symbol"] == symbol), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"종목 없음: {symbol}")
    candles = d.get("candles", {}).get(symbol, {}).get(tf)
    return {
        "symbol": symbol, "tf": tf, "as_of_ts": t.get("as_of_ts"),
        "close": entry["close"], "setup": entry["setup"],
        "candles": candles,                                   # null이면 프론트가 '차트 준비중'
        "chart_status": "ready" if candles else "pending",
    }


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1), limit: int = Query(60, ge=1, le=200)):
    """종목 검색(전 TF 심볼 합집합에서 부분일치). 셋업 있는 TF 표시."""
    d = load_rankings()
    ql = q.upper()
    found: dict[str, dict] = {}
    for tf in TF_SET:
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
    """NodePort/ClusterIP 앞에 프록시가 없는 지금 구성 기준 request.client.host 사용.
    한계: 향후 Ingress/LB를 앞에 두면 X-Forwarded-For를 신뢰 가능한 프록시에서만
    읽도록 다시 손봐야 함(스푸핑 방지) — 지금은 명시만 해둔다."""
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
        if n > limit:
            raise HTTPException(status_code=429, detail="너무 자주 요청함 — 잠시 후 다시 시도")
    except redis.exceptions.RedisError:
        return


class VoteIn(BaseModel):
    symbol: str
    tf: str
    direction: int

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        v = v.upper()
        if not SYMBOL_RE.match(v):
            raise ValueError("symbol 형식 오류 (예: BTC/KRW)")
        return v

    @field_validator("tf")
    @classmethod
    def _valid_tf(cls, v: str) -> str:
        if v not in TF_SET:
            raise ValueError(f"지원 TF: {sorted(TF_SET)}")
        return v

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

    @field_validator("symbol")
    @classmethod
    def _valid_symbol(cls, v: str) -> str:
        v = v.upper()
        if not SYMBOL_RE.match(v):
            raise ValueError("symbol 형식 오류 (예: BTC/KRW)")
        return v

    @field_validator("tf")
    @classmethod
    def _valid_tf(cls, v: str) -> str:
        if v not in TF_SET:
            raise ValueError(f"지원 TF: {sorted(TF_SET)}")
        return v

    @field_validator("content")
    @classmethod
    def _stripped(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("내용이 비어있음")
        return v


@app.post("/api/vote")
async def api_vote(body: VoteIn, request: Request):
    from infrapilot.db.repository import cast_vote   # lazy: DB 없어도 앱 기동

    ip_hash = _anon_hash(_client_ip(request))
    _rate_limit(f"ratelimit:vote:{ip_hash}", limit=30, window_sec=60)
    cast_vote(body.symbol, body.tf, body.direction, ip_hash)
    return {"status": "ok"}


@app.get("/api/votes/{tf}/{symbol:path}")
async def api_votes(tf: str, symbol: str):
    from infrapilot.db.repository import get_vote_summary   # lazy

    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {sorted(TF_SET)}")
    return {"symbol": symbol, "tf": tf, **get_vote_summary(symbol, tf)}


@app.post("/api/comment")
async def api_comment(body: CommentIn, request: Request):
    from infrapilot.db.repository import add_comment   # lazy

    ip_hash = _anon_hash(_client_ip(request))
    _rate_limit(f"ratelimit:comment:{ip_hash}", limit=5, window_sec=60)
    add_comment(body.symbol, body.tf, body.content, ip_hash)
    return {"status": "ok"}


@app.get("/api/comments/{tf}/{symbol:path}")
async def api_comments(tf: str, symbol: str, limit: int = Query(50, ge=1, le=200)):
    from infrapilot.db.repository import get_comments   # lazy

    if tf not in TF_SET:
        raise HTTPException(status_code=404, detail=f"지원 TF: {sorted(TF_SET)}")
    # content는 여기서 그대로 반환 — XSS 방지는 프론트가 textContent로 렌더링할 때 처리
    # (여기서 이스케이프하면 API를 다른 클라이언트가 쓸 때 이중 이스케이프될 수 있어서).
    return {"symbol": symbol, "tf": tf, "comments": get_comments(symbol, tf, limit)}


@app.get("/health")
async def health():
    return {"status": "ok"}
