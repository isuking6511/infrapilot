"""Analyzer Lambda — 상위 50개 코인 엘리어트 파동 분석."""

import json
import os
import time
from pathlib import Path

import google.generativeai as genai

from infrapilot.providers.collectors.market import fetch_top_symbols, fetch_ohlcv
from infrapilot.db.repository import init_db, save_analysis

TIMEFRAMES = ["15m", "1h", "4h", "1d"]
CANDLES_PER_TF = 20  # 파동 분석에 충분한 캔들 수


def _load_wave_rules() -> str:
    """Batch_01 핵심 규칙만 로드 (토큰 절약)."""
    rules_path = Path(__file__).parent.parent / "strategies" / "neely" / "Batch_01_Data_and_Monowave.json"
    try:
        with open(rules_path) as f:
            rules = json.load(f)
        # 핵심 패턴만 추출 (pattern_name + trading_action)
        summary = []
        for r in rules:
            summary.append(f"- {r['pattern_name']}: {r.get('trading_action', '')}")
        return "\n".join(summary)
    except Exception:
        return "- Impulse: 5 waves up, wave 3 never shortest\n- Correction: 3 waves (a,b,c) counter-trend"


def _format_candles(candles: list[dict]) -> str:
    """OHLCV → 콤팩트 문자열 (o|h|l|c)."""
    return ";".join(
        f"{c['open']:.4f}|{c['high']:.4f}|{c['low']:.4f}|{c['close']:.4f}"
        for c in candles[-CANDLES_PER_TF:]
    )


def _build_prompt(symbol: str, ohlcv_by_tf: dict, wave_rules: str) -> str:
    tf_lines = "\n".join(
        f"[{tf}] {_format_candles(ohlcv_by_tf.get(tf, []))}"
        for tf in TIMEFRAMES
    )
    return f"""Elliott Wave Analysis for {symbol}

Rules:
{wave_rules}

status 정의:
- "impulse": 상승 파동 진행 중
- "impulse_ending": 5파 완성 직전 (매도 후보)
- "correction_ending": c파 완성 직전 (매수 후보)

Recent OHLCV (format: open|high|low|close, last {CANDLES_PER_TF} candles):
{tf_lines}

JSON만 반환 (다른 텍스트 없이):
{{"15m":{{"wave_count":"3","trend":"up","status":"correction_ending","confidence":70,"reasoning":"brief reason"}},"1h":{{...}},"4h":{{...}},"1d":{{...}}}}"""


def _parse_response(text: str) -> dict:
    """Gemini 응답에서 JSON 파싱."""
    text = text.strip()
    # 마크다운 코드블록 제거
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # JSON 블록만 추출 시도
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        return {}


def handler(event, context):
    """EventBridge에서 15분마다 호출."""
    init_db()

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel("gemini-2.5-flash-preview-04-17")
    wave_rules = _load_wave_rules()

    symbols = fetch_top_symbols(n=50)
    print(f"분석 대상: {len(symbols)}개")

    analyzed = 0
    for symbol in symbols:
        try:
            # 4개 타임프레임 OHLCV 수집
            ohlcv_by_tf = {}
            for tf in TIMEFRAMES:
                try:
                    ohlcv_by_tf[tf] = fetch_ohlcv(symbol, tf, limit=CANDLES_PER_TF)
                except Exception as e:
                    print(f"  OHLCV 실패 {symbol}/{tf}: {e}")

            if not ohlcv_by_tf:
                continue

            # Gemini 분석
            prompt = _build_prompt(symbol, ohlcv_by_tf, wave_rules)
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
            )
            result = _parse_response(response.text)

            # DB 저장
            now_ms = int(time.time() * 1000)
            for tf, tf_data in result.items():
                if tf in TIMEFRAMES and isinstance(tf_data, dict):
                    save_analysis(symbol, tf, now_ms, tf_data)

            analyzed += 1
            print(f"  완료: {symbol}")

        except Exception as e:
            print(f"  오류 {symbol}: {e}")

        time.sleep(1)  # Gemini 무료 티어 rate limit 여유

    print(f"분석 완료: {analyzed}/{len(symbols)}개")
    return {"status": "ok", "analyzed": analyzed}
