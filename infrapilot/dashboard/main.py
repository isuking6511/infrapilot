"""InfraPilot Dashboard — FastAPI."""

import json
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from infrapilot.db.repository import (
    get_latest_analysis,
    get_analysis_by_symbol,
    get_ohlcv,
)

app = FastAPI(title="InfraPilot Dashboard")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

TIMEFRAMES = ["15m", "1h", "4h", "1d"]


def _short(symbol: str) -> str:
    return symbol.replace("/USDT:USDT", "").replace("/USDT", "")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    rows = get_latest_analysis()

    # symbol → {timeframe: analysis_row}
    symbols_data: dict = {}
    for row in rows:
        sym = row["symbol"]
        tf = row["timeframe"]
        if sym not in symbols_data:
            symbols_data[sym] = {}
        symbols_data[sym][tf] = row

    correction_ending = [
        s for s, data in symbols_data.items()
        if any(d.get("status") == "correction_ending" for d in data.values())
    ]
    impulse_ending = [
        s for s, data in symbols_data.items()
        if any(d.get("status") == "impulse_ending" for d in data.values())
    ]

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

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "symbol": symbol,
        "symbol_short": _short(symbol),
        "analysis": analysis,
        "timeframes": TIMEFRAMES,
        "ohlcv_json": json.dumps(ohlcv_data),
    })


@app.get("/health")
async def health():
    return {"status": "ok"}
