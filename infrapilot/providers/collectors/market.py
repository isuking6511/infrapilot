"""시장 데이터 수집기 — ccxt 기반."""

import time
import ccxt

exchange = ccxt.bybit({
    "timeout": 30000,       # 30초 (기본 10초에서 늘림)
    "enableRateLimit": True,
})


def fetch_symbols(min_volume: float = 5_000_000) -> list[str]:
    """거래대금 조건에 맞는 USDT 무기한 선물 심볼 목록."""
    markets = _fetch_tickers_with_retry()

    symbols = []
    for symbol, ticker in markets.items():
        if not _is_usdt_perp(symbol):
            continue
        if float(ticker.get("quoteVolume") or 0) >= min_volume:
            symbols.append(symbol)

    return symbols


def fetch_top_symbols(n: int = 50, min_volume: float = 50_000_000) -> list[str]:
    """거래대금 상위 n개 USDT 무기한 선물 심볼 (분석용). 일 50M USDT 이상."""
    markets = _fetch_tickers_with_retry()

    candidates = []
    for symbol, ticker in markets.items():
        if not _is_usdt_perp(symbol):
            continue
        vol = float(ticker.get("quoteVolume") or 0)
        if vol >= min_volume:
            candidates.append((symbol, vol))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in candidates[:n]]


def _is_usdt_perp(symbol: str) -> bool:
    """BTC/USDT:USDT 형태만 True (Bybit USDT 무기한 선물)."""
    return symbol.endswith("/USDT:USDT")


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


def _fetch_tickers_with_retry(max_retries: int = 3) -> dict:
    """fetch_tickers 재시도 — 지수 백오프."""
    last_error = None
    for attempt in range(max_retries):
        try:
            return exchange.fetch_tickers()
        except (ccxt.RequestTimeout, ccxt.NetworkError) as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s, 4s
    raise last_error
