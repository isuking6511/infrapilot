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


def cast_vote(symbol: str, timeframe: str, direction: int, voter_hash: str) -> None:
    """찬(1)/반(-1) 투표. 같은 voter_hash가 다시 투표하면 기존 표를 갱신(누적 아님) —
    "1인 1표" 원칙(voter_hash 단위, IP 우회는 못 막는 한계는 CLAUDE.md §5.4에 명시)."""
    sql = """
        INSERT INTO votes (symbol, timeframe, direction, voter_hash)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (symbol, timeframe, voter_hash) DO UPDATE SET
            direction  = EXCLUDED.direction,
            created_at = NOW()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, direction, voter_hash))
        conn.commit()


def get_vote_summary(symbol: str, timeframe: str) -> dict:
    """종목·TF의 찬/반 집계."""
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE direction = 1)  AS bull,
            COUNT(*) FILTER (WHERE direction = -1) AS bear
        FROM votes
        WHERE symbol = %s AND timeframe = %s
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe))
            bull, bear = cur.fetchone()
    return {"bull": bull, "bear": bear}


def add_comment(symbol: str, timeframe: str, content: str, author_hash: str) -> None:
    """댓글 저장. content 길이 검증은 호출자(API 레이어)가 이미 했다고 가정 —
    DB VARCHAR(500)이 마지막 방어선."""
    sql = """
        INSERT INTO comments (symbol, timeframe, content, author_hash)
        VALUES (%s, %s, %s, %s)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, content, author_hash))
        conn.commit()


def get_comments(symbol: str, timeframe: str, limit: int = 50) -> list[dict]:
    """최신순 댓글. XSS 방지(HTML 이스케이프)는 렌더링 시점(프론트)에서 처리."""
    sql = """
        SELECT content, author_hash, created_at
        FROM comments
        WHERE symbol = %s AND timeframe = %s
        ORDER BY created_at DESC
        LIMIT %s
    """
    cols = ["content", "author_hash", "created_at"]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, limit))
            return [dict(zip(cols, row)) for row in cur.fetchall()]