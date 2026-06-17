"""Lambda 핸들러 — OHLCV 수집 + RDS 저장."""

from infrapilot.providers.collectors.market import fetch_symbols, fetch_ohlcv
from infrapilot.db.repository import init_db, save_ohlcv


def handler(event, context):
    """EventBridge에서 15분마다 호출."""
    
    # 1. DB 테이블 초기화 (없으면 생성)
    init_db()
    
    # 2. 심볼 필터링
    symbols = fetch_symbols()
    print(f"수집 대상: {len(symbols)}개")
    
    # 3. 각 심볼 OHLCV 수집 + 저장
    total_saved = 0
    for symbol in symbols:
        candles = fetch_ohlcv(symbol)
        saved = save_ohlcv(symbol, "15m", candles)
        total_saved += saved
    
    print(f"저장 완료: {total_saved}행")
    return {"status": "ok", "saved": total_saved}