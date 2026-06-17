"""Analyzer Lambda — 상위 50개 코인 Neely 엘리어트 파동 분석 (배치)."""

import asyncio
import json
import os
import time
from pathlib import Path

from infrapilot.providers.copilot import CopilotProvider
from infrapilot.providers.collectors.market import fetch_top_symbols, fetch_ohlcv
from infrapilot.db.repository import init_db, save_analysis

TIMEFRAMES = ["15m", "1h", "4h", "1d"]
CANDLES_PER_TF = 12
BATCH_SIZE = 3

_NEELY_RULES_CACHE: str | None = None


def _extract_entry(entry: dict) -> str:
    """JSON 항목 하나를 압축 규칙 문자열로 변환."""
    name = entry.get("pattern_name") or entry.get("concept_name") or entry.get("Pattern", "")
    rule = (
        entry.get("essential_price_rules")
        or entry.get("condition")
        or entry.get("structural_implication")
        or entry.get("essential_rules")
        or entry.get("definition")
        or ""
    )
    action = entry.get("trading_action") or ""
    # 각 필드 150자 제한
    rule = rule[:150]
    action = action[:120]
    parts = [f"[{name}]"]
    if rule:
        parts.append(rule)
    if action:
        parts.append(f"→ {action}")
    return " | ".join(parts)


def _flatten_object(obj: dict, prefix: str = "") -> list[str]:
    """object 구조(Batch_02, Batch_09 등)의 중첩 데이터를 평탄화."""
    lines = []
    for k, v in obj.items():
        if k in ("module", "topic", "description"):
            continue
        label = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            lines.extend(_flatten_object(v, label))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    lines.append(_extract_entry(item))
                else:
                    lines.append(f"[{label}] {item}")
        else:
            lines.append(f"[{label}] {v}")
    return lines


def _load_wave_rules() -> str:
    global _NEELY_RULES_CACHE
    if _NEELY_RULES_CACHE:
        return _NEELY_RULES_CACHE

    rules_dir = Path(__file__).parent.parent / "strategies" / "neely"
    sections: list[str] = []

    for batch_file in sorted(rules_dir.glob("Batch_*.json")):
        try:
            with open(batch_file) as f:
                data = json.load(f)

            batch_name = batch_file.stem
            lines: list[str] = []

            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict):
                        line = _extract_entry(entry)
                        if line.strip("[] "):
                            lines.append(line)
            elif isinstance(data, dict):
                lines.extend(_flatten_object(data))

            if lines:
                sections.append(f"\n## {batch_name}\n" + "\n".join(lines))

        except Exception as e:
            print(f"  규칙 로드 실패 {batch_file.name}: {e}")

    _NEELY_RULES_CACHE = "\n".join(sections) if sections else (
        "- Impulse: 5 waves up, wave 3 never shortest\n"
        "- Correction: 3 waves (a,b,c) counter-trend"
    )
    return _NEELY_RULES_CACHE


def _format_candles(candles: list[dict]) -> str:
    """O|H|L|C 형식, 최근 CANDLES_PER_TF개."""
    return ";".join(
        f"{c['open']:.4f}|{c['high']:.4f}|{c['low']:.4f}|{c['close']:.4f}"
        for c in candles[-CANDLES_PER_TF:]
    )


def _build_batch_prompt(batch: list[tuple[str, dict]], wave_rules: str) -> str:
    symbols_block = ""
    for symbol, ohlcv_by_tf in batch:
        tf_lines = "\n".join(
            f"  [{tf}] {_format_candles(ohlcv_by_tf.get(tf, []))}"
            for tf in TIMEFRAMES
        )
        symbols_block += f"\n### {symbol}\n{tf_lines}\n"

    symbol_list = [s for s, _ in batch]

    return f"""You are a Glenn Neely NeoWave Elliott Wave specialist. Analyze each symbol strictly using the NeoWave ruleset below.

=== GLENN NEELY NEOWAVE RULES (Batch_01 ~ Batch_14) ===
{wave_rules}
=== END RULES ===

## Analysis Protocol (apply to EACH symbol × EACH timeframe):

STEP 1 — Monowave Identification
  - Identify directional price swings from O/H/L/C data
  - Label each swing as m0, m1, m2, m3...

STEP 2 — Retracement Rule (Batch_02)
  - Calculate: m2 retracement % of m1
  - Rule 1: m2 < 38.2% → m1 is :5 or :s5 (strong impulse)
  - Rule 2: 38.2~61.8% → :5 or :c3 (ambiguous, check m3)
  - Rule 3: 61.8% → borderline, check m0
  - Rule 4: 61.8~100% → :3 or :F3 (likely correction)
  - Rule 5: 100~161.8% → :F3 (irregular correction b-wave)
  - Rule 6: 161.8~261.8% → :F3 or :c3 (strong reversal)
  - Rule 7: >261.8% → :F3 (trend change confirmed)

STEP 3 — Pattern Construction (Batch_06, Batch_07)
  - Impulse (Trending): 5 segments, wave3 never shortest, extension in 1/3/5
  - Zigzag (5-3-5): b < 61.8% of a, c beyond end of a
  - Flat (3-3-5): b retraces ~100% of a, c ~100% of b
  - Triangle (3-3-3-3-3): converging trendlines, e-wave thrust follows
  - Complex: two corrections linked by x-wave

STEP 4 — Rule of Alternation (Batch_08)
  - Wave 2 and Wave 4 must differ in price/time/complexity

STEP 5 — Post-Constructive Validation (Batch_04)
  - After impulse: market must break 2-4 trendline quickly
  - After zigzag: retrace ≥61.8% of entire pattern
  - After flat: full 100% retracement expected

STEP 6 — Current Wave Position & Signal
  Assign exact wave label (e.g. "wave 3 of impulse", "c-wave of zigzag", "e-wave of triangle")

## status values:
- "impulse": impulse in progress (waves 1-4 area)
- "impulse_ending": wave 5 nearly complete → SHORT candidate
- "correction_ending": c/e-wave nearly complete → LONG candidate
- "correction": correction in progress
- "unclear": insufficient data or conflicting signals

## reasoning field — MUST include:
  - Which Neely retracement rule triggered (e.g. "Rule 4: m2=72% of m1 → :F3")
  - Pattern type identified (Impulse/Zigzag/Flat/Triangle/Complex)
  - Current wave label (e.g. "in wave 3", "completing c-wave")
  - Key price levels observed
  - Why this status was assigned

OHLCV format: open|high|low|close, last {CANDLES_PER_TF} candles
{symbols_block}

Return ONLY valid JSON:
{{
  "BTC/USDT": {{
    "15m": {{
      "wave_count": "3 of 5",
      "trend": "up",
      "status": "impulse",
      "confidence": 75,
      "reasoning": "Rule 2 triggered (m2=45% retrace of m1 → :5). Impulse construction: wave1 extended (161.8% of wave3). Currently in wave3 subdivision. 2-4 trendline intact. Rule of alternation: wave2 was sharp zigzag, wave4 expected flat."
    }},
    "1h": {{...}},
    "4h": {{...}},
    "1d": {{...}}
  }}
}}

Symbols to analyze: {symbol_list}"""


def _parse_batch_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except Exception:
                pass
        return {}


def handler(event, context):
    """EventBridge에서 15분마다 호출."""
    init_db()

    copilot = CopilotProvider(oauth_token=os.environ["COPILOT_TOKEN"], model="gpt-4o")
    print("AI: GitHub Models (gpt-4o)")

    wave_rules = _load_wave_rules()
    rule_lines = wave_rules.count("\n")
    print(f"Neely 규칙 로드 완료: {rule_lines}줄")

    symbols = fetch_top_symbols(n=50)
    print(f"분석 대상: {len(symbols)}개, 배치 크기: {BATCH_SIZE}")

    analyzed = 0
    for i in range(0, len(symbols), BATCH_SIZE):
        batch_symbols = symbols[i:i + BATCH_SIZE]
        batch: list[tuple[str, dict]] = []

        for symbol in batch_symbols:
            ohlcv_by_tf = {}
            for tf in TIMEFRAMES:
                try:
                    ohlcv_by_tf[tf] = fetch_ohlcv(symbol, tf, limit=CANDLES_PER_TF)
                except Exception as e:
                    print(f"  OHLCV 실패 {symbol}/{tf}: {e}")
            if ohlcv_by_tf:
                batch.append((symbol, ohlcv_by_tf))

        if not batch:
            continue

        try:
            prompt = _build_batch_prompt(batch, wave_rules)

            # 429 발생 시 최대 3회 재시도 (60초 대기)
            for attempt in range(3):
                try:
                    resp = asyncio.run(copilot.chat(messages=[{"role": "user", "content": prompt}]))
                    results = _parse_batch_response(resp.content)
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        print(f"  Rate limit, {60}초 대기 후 재시도 ({attempt+1}/3)...")
                        time.sleep(60)
                    else:
                        raise

            now_ms = int(time.time() * 1000)
            for symbol, tf_data in results.items():
                if not isinstance(tf_data, dict):
                    continue
                for tf, data in tf_data.items():
                    if tf in TIMEFRAMES and isinstance(data, dict):
                        save_analysis(symbol, tf, now_ms, data)
                analyzed += 1
                print(f"  완료: {symbol}")

        except Exception as e:
            print(f"  배치 오류 (심볼 {[s for s, _ in batch]}): {e}")

        time.sleep(2)

    print(f"분석 완료: {analyzed}/{len(symbols)}개")
    return {"status": "ok", "analyzed": analyzed}
