"""GitHub Copilot provider — GitHub Models API 사용."""

import httpx

from infrapilot.providers.base import LLMProvider, LLMResponse

GITHUB_MODELS_API = "https://models.inference.ai.azure.com/chat/completions"


class CopilotProvider(LLMProvider):
    """GitHub Models API 프로바이더.

    GitHub 계정의 Personal Access Token으로 GPT-4o 등 호출.
    https://github.com/marketplace/models
    """

    def __init__(
        self,
        oauth_token: str,
        model: str = "gpt-4o",
    ) -> None:
        self._token = oauth_token
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> LLMResponse:
        if system:
            messages = [{"role": "system", "content": system}, *messages]

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                GITHUB_MODELS_API,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()

        return LLMResponse(
            content=data["choices"][0]["message"]["content"],
            model=self._model,
        )

    @property
    def name(self) -> str:
        return "github-models"
