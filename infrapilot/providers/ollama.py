"""Ollama provider — 무료 로컬 AI."""

import httpx

from infrapilot.providers.base import LLMProvider, LLMResponse


class OllamaProvider(LLMProvider):
    """Ollama 로컬 프로바이더."""

    def __init__(
        self,
        model: str = "exaone3.5:2.4b"  # LG에서 만든 한국어 모델
,
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._base_url = base_url

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> LLMResponse:
        
        if system:
            messages = [{"role": "system", "content": system}, *messages]

        # Ollama API 호출
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["message"]["content"],
            model=self._model,
        )
    
    @property
    def name(self) -> str:
        return "ollama"