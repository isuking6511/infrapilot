CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(100)  NOT NULL,
    interval    VARCHAR(5)   NOT NULL,
    timestamp   BIGINT       NOT NULL,
    open        NUMERIC      NOT NULL,
    high        NUMERIC      NOT NULL,
    low         NUMERIC      NOT NULL,
    close       NUMERIC      NOT NULL,
    volume      NUMERIC      NOT NULL,
    created_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE (symbol, interval, timestamp)
);

CREATE TABLE IF NOT EXISTS analysis (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(100) NOT NULL,
    timeframe   VARCHAR(5)   NOT NULL,
    timestamp   BIGINT       NOT NULL,
    wave_count  VARCHAR(10),
    trend       VARCHAR(20),
    status      VARCHAR(30),
    confidence  SMALLINT,
    reasoning   TEXT,
    created_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE (symbol, timeframe, timestamp)
);

-- 익명 투표. voter_hash = sha256(ip + salt) — 원본 IP는 저장하지 않는다(비가역).
-- UNIQUE(symbol, timeframe, voter_hash)로 같은 IP당 1표만 유지(완화책 — IP 우회는 못 막음,
-- 한계는 CLAUDE.md §5.4에 명시). direction은 앱에서도 검증하지만 DB에서도 방어.
CREATE TABLE IF NOT EXISTS votes (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(100) NOT NULL,
    timeframe   VARCHAR(5)   NOT NULL,
    direction   SMALLINT     NOT NULL CHECK (direction IN (1, -1)),
    voter_hash  VARCHAR(64)  NOT NULL,
    created_at  TIMESTAMP    DEFAULT NOW(),
    UNIQUE (symbol, timeframe, voter_hash)
);

-- 익명 댓글. content 길이는 앱(400자)+DB(500자) 이중 제한. author_hash는 표시용(작성자 구분만).
CREATE TABLE IF NOT EXISTS comments (
    id           SERIAL PRIMARY KEY,
    symbol       VARCHAR(100) NOT NULL,
    timeframe    VARCHAR(5)   NOT NULL,
    content      VARCHAR(500) NOT NULL,
    author_hash  VARCHAR(64)  NOT NULL,
    created_at   TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_comments_symbol_tf ON comments (symbol, timeframe, created_at DESC);
"""