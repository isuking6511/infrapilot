"""시장 데이터 수집기 — ccxt 기반."""

import ccxt

exchange = ccxt.bybit()  # API 키 없어도 시세 조회는 됨


def fetch_symbols(min_volume: float = 5_000_000) -> list[str]:
    """거래대금 조건에 맞는 심볼 목록."""
    markets = exchange.fetch_tickers()  # 전체 마켓 조회
    
    symbols = []
    for symbol, ticker in markets.items():
        if not (symbol.endswith("/USDT") or symbol.endswith("/USDT:USDT")):
            continue
        if float(ticker.get("quoteVolume") or 0) >= min_volume:
            symbols.append(symbol)
    
    return symbols


def fetch_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 200) -> list[dict]:
    """단일 심볼 OHLCV."""
    raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    # raw = [[timestamp, open, high, low, close, volume], ...]
    
    return [
        {
            "timestamp": row[0],
            "open":  row[1],
            "high":  row[2],
            "low":   row[3],
            "close": row[4],
            "volume": row[5],
        }
        for row in raw
    ]
