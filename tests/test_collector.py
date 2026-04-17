# tests/test_collector.py

from infrapilot.providers.collectors.market import fetch_symbols, fetch_ohlcv


def test_fetch_symbols():
    """심볼 리스트 정상 반환 확인."""
    symbols = fetch_symbols(min_volume=5_000_000)
    
    print(f"\n수집된 심볼 수: {len(symbols)}")
    print(f"샘플 5개: {symbols[:5]}")
    
    assert len(symbols) > 0, "심볼이 하나도 없음"
    assert all("/USDT" in s for s in symbols), "USDT 외 심볼 포함됨"


def test_fetch_ohlcv():
    """BTC 캔들 데이터 정상 반환 확인."""
    candles = fetch_ohlcv("BTC/USDT", timeframe="15m", limit=10)
    
    print(f"\n캔들 수: {len(candles)}")
    print(f"첫 번째 캔들: {candles[0]}")
    
    assert len(candles) == 10
    assert "open" in candles[0]
    assert "close" in candles[0]


if __name__ == "__main__":
    test_fetch_symbols()
    test_fetch_ohlcv()
    print("\OKAY")
