"""투표·댓글 저장소 (RDS PostgreSQL).

- SQL은 전부 파라미터 바인딩(%s) → 문자열 포매팅 없음 → SQL injection 차단 (CLAUDE.md §5.4).
- 접속 정보는 환경변수(K8s Secret 주입)로만 받는다. 코드·이미지에 평문 없음 (§5.1).
- 요청마다 커넥션을 열고 닫는다. 트래픽이 작은 지금은 이게 가장 단순하고,
  커넥션 풀은 동시 접속이 늘면 도입(트레이드오프: 요청당 TCP+TLS 핸드셰이크 비용).
"""

import os
import threading
from datetime import timezone

import psycopg2

from infrapilot.db.schema import CREATE_TABLES_SQL

_schema_ready = False
_schema_lock = threading.Lock()


def _connect():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        # DB가 죽었을 때 요청이 무한정 매달리지 않게 — 3초 안에 실패하고 API가 503을 돌려준다.
        connect_timeout=3,
    )


def _ensure_schema(conn) -> None:
    """프로세스당 첫 접속 때 1회 CREATE TABLE IF NOT EXISTS 실행.

    왜 여기서: 이전엔 init_db()를 아무도 호출하지 않아 RDS를 새로 만들면 테이블이 없어서
    투표가 500으로 실패했다. 테이블 2개 규모라 Alembic 같은 마이그레이션 도구는 과하고,
    idempotent DDL을 첫 접근 때 실행하면 배포 순서(DB 먼저? 앱 먼저?)를 신경 쓸 필요가 없다.
    트레이드오프: 컬럼 변경 같은 '진짜 마이그레이션'이 필요해지면 그때 도구를 들인다.
    """
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
        conn.commit()
        _schema_ready = True


def get_connection():
    conn = _connect()
    try:
        _ensure_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def init_db() -> None:
    """수동 초기화용(로컬 docker-compose 등). 운영에서는 get_connection()이 알아서 처리."""
    conn = get_connection()
    conn.close()


def _iso_utc(dt) -> str:
    """TIMESTAMPTZ → 'Z'가 붙은 ISO 문자열. 브라우저가 시간대를 오해하지 않게(예전엔 naive
    TIMESTAMP라 KST 브라우저가 UTC 시각을 로컬로 읽어 '9시간 전'으로 보이는 버그가 있었다)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:   # with conn: 성공 시 commit, 예외 시 rollback
            cur.execute(sql, (symbol, timeframe, direction, voter_hash))
    finally:
        conn.close()


def get_vote_summary(symbol: str, timeframe: str) -> dict:
    """종목·TF의 찬/반 집계."""
    sql = """
        SELECT
            COUNT(*) FILTER (WHERE direction = 1)  AS bull,
            COUNT(*) FILTER (WHERE direction = -1) AS bear
        FROM votes
        WHERE symbol = %s AND timeframe = %s
    """
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe))
            bull, bear = cur.fetchone()
    finally:
        conn.close()
    return {"bull": bull, "bear": bear}


def add_comment(symbol: str, timeframe: str, content: str, author_hash: str) -> None:
    """댓글 저장. content 길이 검증은 API 레이어(pydantic)가 먼저 하고,
    DB VARCHAR(500)이 마지막 방어선."""
    sql = """
        INSERT INTO comments (symbol, timeframe, content, author_hash)
        VALUES (%s, %s, %s, %s)
    """
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, content, author_hash))
    finally:
        conn.close()


def get_comments(symbol: str, timeframe: str, limit: int = 50) -> list[dict]:
    """최신순 댓글. XSS 방지(HTML 이스케이프)는 렌더링 시점(프론트)에서 처리."""
    sql = """
        SELECT content, author_hash, created_at
        FROM comments
        WHERE symbol = %s AND timeframe = %s
        ORDER BY created_at DESC
        LIMIT %s
    """
    conn = get_connection()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, (symbol, timeframe, limit))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"content": c, "author_hash": a, "created_at": _iso_utc(t)} for c, a, t in rows]
