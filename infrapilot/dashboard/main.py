"""InfraPilot Dashboard — FastAPI.

웹 API는 rankings.json(=scanner_job 산출물)만 읽는다. 거래소 직접 호출 X (일관성).
데이터 소스는 load_rankings() 하나로 격리 — 나중에 Redis로 이 함수만 교체.
(구 LLM analyzer DB 라우트는 보존하되 import를 lazy화해 앱 기동이 DB에 안 묶이게 함.)
"""

import json
from pathlib import Path

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

# ── 데이터 소스 (교체 지점) ──────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
RANKINGS_PATH = ROOT / "data" / "rankings.json"

EXCHANGES = [
    {"id": "upbit", "active": True},
    {"id": "krx", "active": False},
    {"id": "bybit", "active": False},
    {"id": "nasdaq", "active": False},
]


def load_rankings() -> dict:
    """랭킹 데이터 단일 소스. 지금은 rankings.json, 나중에 Redis로 이 함수만 교체.
    (예: redis.get('rankings') → json.loads). 거래소 직접 호출은 하지 않는다."""
    if not RANKINGS_PATH.exists():
        raise HTTPException(status_code=503, detail="rankings 아직 없음 — scanner_job 먼저 실행")
    with open(RANKINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


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


@app.get("/health")
async def health():
    return {"status": "ok"}
