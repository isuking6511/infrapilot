import os
import psycopg2
from infrapilot.db.schema import CREATE_TABLES_SQL


def get_connection():
    return psycopg2.connect(
        host     = os.environ["DB_HOST"],
        port     = os.environ.get("DB_PORT", "5432"),
        dbname   = os.environ["DB_NAME"],
        user     = os.environ["DB_USER"],
        password = os.environ["DB_PASSWORD"],
    )


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
        conn.commit()


def save_ohlcv(symbol: str, interval: str, candles: list[dict]) -> int:
    if not candles:
        return 0

    sql = """
        INSERT INTO ohlcv (symbol, interval, timestamp, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, interval, timestamp) DO NOTHING
    """

    rows = [
        (symbol, interval, c["timestamp"], c["open"], c["high"], c["low"], c["close"], c["volume"])
        for c in candles
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
        return cur.rowcount


def save_analysis(symbol: str, timeframe: str, timestamp: int, data: dict) -> None:
    sql = """
        INSERT INTO analysis (symbol, timeframe, timestamp, wave_count, trend, status, confidence, reasoning)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
            wave_count = EXCLUDED.wave_count,
            trend      = EXCLUDED.trend,
            status     = EXCLUDED.status,
            confidence = EXCLUDED.confidence,
            reasoning  = EXCLUDED.reasoning,
            created_at = NOW()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                symbol, timeframe, timestamp,
                data.get("wave_count"), data.get("trend"),
                data.get("status"), data.get("confidence"),
                data.get("reasoning"),
            ))
        conn.commit()


def get_latest_analysis() -> list[dict]:
    """심볼별·타임프레임별 최신 분석 1건씩."""
    sql = """
        SELECT DISTINCT ON (symbol, timeframe)
            symbol, timeframe, wave_count, trend, status, confidence, reasoning, created_at
        FROM analysis
        ORDER BY symbol, timeframe, created_at DESC
    """
    cols = ["symbol", "timeframe", "wave_count", "trend", "status", "confidence", "reasoning", "created_at"]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_analysis_by_symbol(symbol: str) -> list[dict]:
    """특정 심볼의 타임프레임별 최신 분석."""
    sql = """
        SELECT DISTINCT ON (timeframe)
            symbol, timeframe, wave_count, trend, status, confidence, reasoning, created_at
        FROM analysis
        WHERE symbol = %s
        ORDER BY timeframe, created_at DESC
    """
    cols = ["symbol", "timeframe", "wave_count", "trend", "status", "confidence", "reasoning", "created_at"]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol,))
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_ohlcv(symbol: str, timeframe: str, limit: int = 100) -> list[dict]:
    """차트용 OHLCV (최신순 → 정순 정렬)."""
    sql = """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = %s AND interval = %s
        ORDER BY timestamp DESC
        LIMIT %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, limit))
            rows = cur.fetchall()

    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    result = [dict(zip(cols, row)) for row in rows]
    return list(reversed(result))