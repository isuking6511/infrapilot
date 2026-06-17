"""Strategist Agent — 사용자 전략 자동 로드."""

import json
import importlib
from pathlib import Path

from infrapilot.providers.base import LLMProvider
from infrapilot.agents.base import BaseAgent
from infrapilot.agents.messages import AgentMessage


class StrategistAgent(BaseAgent):
    """전략 판단 Agent.
    
    strategies/ 폴더의 모든 JSON과 Python 모듈을 자동 로드.
    """

    def __init__(
        self,
        provider: LLMProvider,
        strategies_dir: Path | str = "src/infrapilot/strategies",
    ) -> None:
        self._strategies_dir = Path(strategies_dir)

        # JSON 지식베이스 로드
        self._knowledge = self._load_json_knowledge()

        # Python 전략 모듈 로드
        self._python_strategies = self._load_python_strategies()

        # system prompt 생성
        system_prompt = self._build_system_prompt()

        super().__init__(
            name="strategist",
            provider=provider,
            system_prompt=system_prompt,
        )

    def _load_json_knowledge(self) -> dict:
        """strategies/ 하위 모든 JSON 자동 로드 (재귀)."""
        knowledge = {}

        if not self._strategies_dir.exists():
            print(f"⚠️  {self._strategies_dir} not found")
            return knowledge

        # 재귀적으로 모든 .json 파일 찾기
        for json_file in sorted(self._strategies_dir.rglob("*.json")):
            try:
                with open(json_file, encoding="utf-8") as f:
                    # 파일 경로를 키로 사용 (예: "neely/wave-1")
                    key = json_file.relative_to(self._strategies_dir).with_suffix("")
                    knowledge[str(key)] = json.load(f)
                print(f"✔ JSON loaded: {key}")
            except Exception as e:
                print(f"✘ JSON failed: {json_file} — {e}")

        return knowledge

    def _load_python_strategies(self) -> dict:
        """strategies/ 하위 모든 .py 파일을 정보로 수집."""
        strategies = {}

        if not self._strategies_dir.exists():
            return strategies

        for py_file in sorted(self._strategies_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # __init__.py 등 제외

            # 파이썬 모듈의 docstring을 읽어서 설명으로 사용
            try:
                module_name = py_file.stem
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    strategies[module_name] = {
                        "description": module.__doc__ or "No description",
                        "module": module,
                    }
                    print(f"✔ Python loaded: {module_name}")
            except Exception as e:
                print(f"✘ Python failed: {py_file} — {e}")

        return strategies

    def _build_system_prompt(self) -> str:
        """로드한 지식 + 전략 설명을 system prompt로 조립."""
        base = """당신은 트레이딩 전략 전문가입니다.

역할:
- 아래 제공된 전략 지식베이스를 엄격히 따릅니다
- 시장 데이터와 규칙을 종합하여 매매 시그널을 생성합니다

=== 지식베이스 (JSON) ===
"""

        knowledge_text = ""
        for name, data in self._knowledge.items():
            knowledge_text += (
                f"\n[{name}]\n"
                f"{json.dumps(data, ensure_ascii=False, indent=2)}\n"
            )

        strategies_text = "\n=== 사용 가능한 분석 방법 (Python) ===\n"
        for name, info in self._python_strategies.items():
            strategies_text += f"- {name}: {info['description'][:200]}\n"

        output_format = """

=== 출력 형식 (JSON) ===
{
    "signal": "BUY" | "SELL" | "HOLD",
    "confidence": 0.0 ~ 1.0,
    "entry_price": float | null,
    "stop_loss": float | null,
    "take_profit": float | null,
    "reasoning": "판단 근거 (어떤 지식을 사용했는지 명시)"
}
"""

        return base + knowledge_text + strategies_text + output_format

    async def process(self, message: AgentMessage) -> dict:
        ohlcv = message.data.get("ohlcv", [])
        analysis = message.data.get("analysis", {})

        prompt = (
            f"=== 시장 데이터 ===\n"
            f"OHLCV: {json.dumps(ohlcv[-20:], indent=2)}\n\n"
            f"=== Analyst 분석 ===\n"
            f"{json.dumps(analysis, ensure_ascii=False, indent=2)}\n\n"
            f"위 데이터를 지식베이스에 맞춰 분석하고 JSON으로 응답."
        )

        raw = await self._ask_ai(prompt)
        return json.loads(raw)

    @property
    def loaded_count(self) -> dict:
        return {
            "json": len(self._knowledge),
            "python": len(self._python_strategies),
        }