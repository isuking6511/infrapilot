"""RDS 스키마 — 재생성 불가한 사용자 데이터(투표·댓글)만 둔다.

분석 결과(랭킹·캔들)는 deterministic이라 언제든 재계산 가능 → Redis 캐시.
예전 ohlcv/analysis 테이블은 Lambda+RDS 파이프라인 시절 것이라 제거했다
(데이터 생명주기로 저장소를 나눈다는 원칙: 재생성 가능=캐시, 불가=영속 DB).

전부 `IF NOT EXISTS` → 몇 번을 실행해도 결과가 같다(idempotent). 그래서 앱이
첫 DB 접근 때 한 번 실행해도 안전하다(repository._ensure_schema).
"""

CREATE_TABLES_SQL = """
-- 익명 투표. voter_hash = sha256(ip + salt) — 원본 IP는 저장하지 않는다(비가역).
-- UNIQUE(symbol, timeframe, voter_hash)로 같은 IP당 1표만 유지(완화책 — IP 우회는 못 막음,
-- 한계는 CLAUDE.md §5.4에 명시). direction은 앱에서도 검증하지만 DB에서도 방어.
CREATE TABLE IF NOT EXISTS votes (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(100) NOT NULL,
    timeframe   VARCHAR(5)   NOT NULL,
    direction   SMALLINT     NOT NULL CHECK (direction IN (1, -1)),
    voter_hash  VARCHAR(64)  NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (symbol, timeframe, voter_hash)
);

-- 익명 댓글. content 길이는 앱(400자)+DB(500자) 이중 제한. author_hash는 표시용(작성자 구분만).
CREATE TABLE IF NOT EXISTS comments (
    id           SERIAL PRIMARY KEY,
    symbol       VARCHAR(100) NOT NULL,
    timeframe    VARCHAR(5)   NOT NULL,
    content      VARCHAR(500) NOT NULL,
    author_hash  VARCHAR(64)  NOT NULL,
    created_at   TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comments_symbol_tf ON comments (symbol, timeframe, created_at DESC);
"""
