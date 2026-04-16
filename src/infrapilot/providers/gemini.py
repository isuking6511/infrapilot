"""Gemini provider — Google AI (google-genai SDK)."""

from google import genai
from google.genai import types

from infrapilot.providers.base import LLMProvider, LLMResponse


class GeminiProvider(LLMProvider):
    """Google Gemini 프로바이더 (신규 SDK)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-1.5-flash",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> LLMResponse:
        
        # 1. 메시지 포맷 변환 (이전 대화 기록 유지)
        # [{"role": "user", "content": "..."}] -> types.Content 형식으로 매핑
        contents = []
        for msg in messages:
            # Gemini는 'user'와 'model' role을 사용합니다.
            role = "user" if msg["role"] == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg["content"])]
                )
            )

        # 2. 시스템 프롬프트 및 설정 (Config)
        config_kwargs = {"response_mime_type": "application/json"}
        if system:
            config_kwargs["system_instruction"] = system

        config = types.GenerateContentConfig(**config_kwargs)

        # 3. 비동기 호출
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )

        # 4. 올바른 응답 객체 추출

        candidate = response.candidates[0]
        content = candidate.content.parts[0].text

        return LLMResponse(
            content=content,
            model=self._model,
        )

    @property
    def name(self) -> str:
        return "gemini"