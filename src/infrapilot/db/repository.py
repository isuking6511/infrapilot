import os
import psycopg2
from infrapilot.db.schema import CREATE_TABLES_SQL

## RDS 저장
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