"""scanner_job ↔ 대시보드 계약 테스트.

scanner_job이 Redis에 쓰는 형식(save_rankings/record_run)과 대시보드가 읽는 형식(load_rankings,
/metrics)이 어긋나면 배포 후에야 빈 화면으로 드러난다 → 거래소 호출 없이 그 접점만 검증.
"""
import importlib.util
import json
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

from infrapilot.analysis_core.models import Setup
from infrapilot.analysis_core.scanner import TickerRank
from infrapilot.dashboard import main

_spec = importlib.util.spec_from_file_location(
    "scanner_job", Path(__file__).resolve().parents[1] / "scripts" / "scanner_job.py")
scanner_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scanner_job)


@pytest.fixture
def r(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(scanner_job, "_redis", fake)
    monkeypatch.setattr(main, "_redis", fake)
    return fake


def _rank(symbol, setup):
    return TickerRank(symbol=symbol, tf="1h", rank_score=8.0 if setup else None,
                      distance_atr=0.4 if setup else None, close=100.0, setup=setup)


def test_payload_roundtrip_through_redis(r):
    setup = Setup(direction=1, entry=100.0, stop=95.0, target=115.0, rr=3.0, score=4.0,
                  src_text="OB", mode="reversion")
    by_tf = {"1h": [_rank("XRP/KRW", setup), _rank("DOGE/KRW", None)]}
    candles = {"BTC/KRW": {"1h": [[1_000, 1, 1, 1, 1, 1]]}, "XRP/KRW": {"1h": [[1_000, 1, 2, 0, 1, 9]]}}
    payload = scanner_job.build_payload(by_tf, candles, {"bull": True, "bear": False}, dead=[])

    scanner_job.save_rankings(payload)
    scanner_job.record_run(started_at=0.0, by_tf=by_tf, n_eval={"1h": 2}, dead=[("X/KRW", "1h:err")])

    # TTL = 봉주기 × 2 (스캐너가 멈추면 오래된 랭킹이 영원히 남지 않고 503으로 드러남)
    assert 0 < r.ttl("rankings:1h") <= 7200

    c = TestClient(main.app)
    d = c.get("/api/rankings/1h?limit=5").json()
    assert [x["symbol"] for x in d["ranking"]] == ["XRP/KRW", "DOGE/KRW"]
    assert d["ranking"][0]["setup"]["target"] == 115.0
    assert c.get("/api/chart/1h/XRP/KRW").json()["chart_status"] == "ready"

    stats = json.loads(r.get("scanner:last_run"))
    assert stats["setups"] == {"1h": 1} and stats["dead"] == 1
    assert 'infrapilot_scanner_symbols_evaluated{tf="1h"} 2.0' in c.get("/metrics").text
