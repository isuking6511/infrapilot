"""Base LLM Provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """AI 응답 통합 포맷."""
    content: str
    model: str = ""
    input_tokens: int = 0


class LLMProvider(ABC):
    """모든 AI 프로바이더가 구현해야 하는 인터페이스."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> LLMResponse:
        """AI에게 메시지 보내고 응답 받기."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """프로바이더 이름."""
        ...