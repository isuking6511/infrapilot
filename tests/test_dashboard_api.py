"""대시보드 API 계약 테스트 — 외부 의존성 0 (CI에서 AWS·DB·거래소 없이 실행).

Redis는 fakeredis(메모리), RDS는 repository 함수를 가짜로 바꿔 끼운다.
검증 대상: 응답 스키마, 입력 검증(injection 차단), rate limit, 장애 격리(Redis/DB 다운 시 503), /metrics.
"""
import json

import fakeredis
import psycopg2
import pytest
import redis
from fastapi.testclient import TestClient

from infrapilot.dashboard import main
from infrapilot.db import repository

SETUP = {"direction": 1, "entry": 100.0, "stop": 90.0, "target": 130.0, "rr": 3.0,
         "score": 4.0, "src_text": "OB+FVG", "mode": "reversion"}


def _seed(r):
    meta = {"generated_at": 1_700_000_000_000, "exchange": "upbit", "quote": "KRW",
            "btc_bias": {"bull": True, "bear": False, "ref_close": 1.0, "ref_bar_ts": 1}}
    ranking = [
        {"symbol": "BTC/KRW", "rank_score": 9.5, "distance_atr": 0.5, "close": 100.0, "setup": SETUP},
        {"symbol": "ETH/KRW", "rank_score": None, "distance_atr": None, "close": 5.0, "setup": None},
    ]
    for tf in main.TIMEFRAMES:
        r.set(f"rankings:{tf}", json.dumps({**meta, "as_of_ts": 1_699_999_000_000, "ranking": ranking}))
    r.set("candles:BTC/KRW:1h", json.dumps([[1, 1, 2, 0.5, 1.5, 10]]))
    r.set("scanner:last_run", json.dumps({"finished_at": 1_700_000_100.0, "duration_sec": 312.4,
                                          "evaluated": {"1h": 180}, "setups": {"1h": 7}, "dead": 2}))


class FakeRepo:
    def __init__(self):
        self.votes, self.comments = {}, []

    def cast_vote(self, symbol, tf, direction, voter):
        self.votes[(symbol, tf, voter)] = direction

    def get_vote_summary(self, symbol, tf):
        vals = [d for (s, t, _), d in self.votes.items() if (s, t) == (symbol, tf)]
        return {"bull": vals.count(1), "bear": vals.count(-1)}

    def add_comment(self, symbol, tf, content, author):
        self.comments.insert(0, {"symbol": symbol, "tf": tf, "content": content,
                                 "author_hash": author, "created_at": "2026-10-03T00:00:00Z"})

    def get_comments(self, symbol, tf, limit):
        return [c for c in self.comments if (c["symbol"], c["tf"]) == (symbol, tf)][:limit]


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(main, "_redis", r)
    return r


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepo()
    for name in ("cast_vote", "get_vote_summary", "add_comment", "get_comments"):
        monkeypatch.setattr(repository, name, getattr(fake, name))
    return fake


@pytest.fixture
def client(fake_redis, repo):
    _seed(fake_redis)
    return TestClient(main.app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_rankings_contract(client):
    d = client.get("/api/rankings/1h?limit=1").json()
    assert d["tf"] == "1h" and d["total"] == 2 and len(d["ranking"]) == 1
    s = d["ranking"][0]["setup"]
    assert {"entry", "stop", "target", "rr", "direction"} <= s.keys()   # 프론트 3선 재료


def test_bad_timeframe_404(client):
    assert client.get("/api/rankings/5m").status_code == 404


def test_chart_ready_and_pending(client):
    assert client.get("/api/chart/1h/BTC/KRW").json()["chart_status"] == "ready"
    assert client.get("/api/chart/4h/BTC/KRW").json()["chart_status"] == "pending"
    assert client.get("/api/chart/1h/NOPE/KRW").status_code == 404


def test_search(client):
    d = client.get("/api/search?q=btc").json()
    assert d["results"][0]["symbol"] == "BTC/KRW" and set(d["results"][0]["setup_tfs"]) == set(main.TIMEFRAMES)


def test_no_rankings_is_503(fake_redis, repo):
    assert TestClient(main.app).get("/api/rankings/1h").status_code == 503


def test_redis_down_is_503_without_leaking_internals(monkeypatch, repo):
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(main, "_redis", dead)
    r = TestClient(main.app).get("/api/rankings/1h")
    assert r.status_code == 503
    assert "127.0.0.1" not in r.text   # 호스트/포트 같은 내부 정보 비노출


def test_vote_upsert_one_per_ip(client, repo):
    for direction in (1, -1, 1):   # 같은 IP가 여러 번 → 마지막 표만 남음
        assert client.post("/api/vote", json={"symbol": "btc/krw", "tf": "1h", "direction": direction}).status_code == 200
    assert client.get("/api/votes/1h/BTC/KRW").json() == {"symbol": "BTC/KRW", "tf": "1h", "bull": 1, "bear": 0}


@pytest.mark.parametrize("body", [
    {"symbol": "BTC/KRW'; DROP TABLE votes;--", "tf": "1h", "direction": 1},
    {"symbol": "BTC/KRW", "tf": "5m", "direction": 1},
    {"symbol": "BTC/KRW", "tf": "1h", "direction": 2},
])
def test_vote_validation(client, body):
    assert client.post("/api/vote", json=body).status_code == 422


def test_comment_length_and_blank(client):
    ok = {"symbol": "BTC/KRW", "tf": "1h", "content": "  <b>hi</b>  "}
    assert client.post("/api/comment", json=ok).status_code == 200
    got = client.get("/api/comments/1h/BTC/KRW").json()["comments"][0]
    assert got["content"] == "<b>hi</b>"   # 저장은 원문 그대로, 이스케이프는 렌더링 시점(프론트)
    assert client.post("/api/comment", json={**ok, "content": "   "}).status_code == 422
    assert client.post("/api/comment", json={**ok, "content": "x" * 401}).status_code == 422


def test_comment_rate_limit(client):
    body = {"symbol": "BTC/KRW", "tf": "1h", "content": "spam"}
    codes = [client.post("/api/comment", json=body).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429   # 분당 5개


def test_db_down_isolated_to_social_api(client, monkeypatch):
    def boom(*a, **k):
        raise psycopg2.OperationalError("could not connect to server: secret-host:5432")
    monkeypatch.setattr(repository, "get_vote_summary", boom)
    r = client.get("/api/votes/1h/BTC/KRW")
    assert r.status_code == 503 and "secret-host" not in r.text
    assert client.get("/api/rankings/1h").status_code == 200   # 랭킹은 계속 동작


def test_metrics(client):
    client.get("/api/rankings/1h")
    client.get("/api/chart/1h/BTC/KRW")
    body = client.get("/metrics").text
    assert 'infrapilot_http_requests_total{method="GET",route="/api/rankings/{tf}",status="200"}' in body
    assert 'route="/api/chart/{tf}/{symbol:path}"' in body   # 실제 종목명이 아닌 템플릿 → 카디널리티 고정
    assert "infrapilot_scanner_last_success_timestamp_seconds 1.7000001e+09" in body
    assert 'infrapilot_scanner_setups{tf="1h"} 7.0' in body
    assert "infrapilot_redis_up 1.0" in body


def test_iso_utc():
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    assert repository._iso_utc(datetime(2026, 10, 3, 9, 0, tzinfo=kst)) == "2026-10-03T00:00:00Z"
    assert repository._iso_utc(datetime(2026, 10, 3, 0, 0)) == "2026-10-03T00:00:00Z"
