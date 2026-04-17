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
"""