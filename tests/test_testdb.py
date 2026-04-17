import os
from dotenv import load_dotenv

load_dotenv()

from infrapilot.db.repository import init_db, save_ohlcv


def test_init_db():
    """테이블 생성 확인."""
    init_db()
    print("\nDB 초기화 완료")


def test_save_ohlcv():
    """OHLCV 저장 확인."""
    dummy_candles = [
        {
            "timestamp": 1700000000000,
            "open":  50000.0,
            "high":  51000.0,
            "low":   49000.0,
            "close": 50500.0,
            "volume": 100.0,
        }
    ]

    saved = save_ohlcv("BTCUSDT", "15m", dummy_candles)
    print(f"\n저장된 행 수: {saved}")

    # 중복 저장 테스트 (0이어야 함)
    saved_again = save_ohlcv("BTCUSDT", "15m", dummy_candles)
    assert saved_again == 0, "중복 저장이 되면 안 됨"
    print("중복 방지 확인 완료")


if __name__ == "__main__":
    test_init_db()
    test_save_ohlcv()
    print("\n모든 테스트 통과!")