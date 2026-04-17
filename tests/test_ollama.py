"""Ollama provider 테스트."""

import asyncio
from infrapilot.providers.ollama import OllamaProvider


async def main():
    # Provider 만들기
    provider = OllamaProvider()

    # AI에게 질문
    response = await provider.chat(
        messages=[{"role": "user", "content": "안녕! 자기소개 해줘"}]
    )

    print(f"모델: {response.model}")
    print(f"응답: {response.content}")


# async 함수 실행
asyncio.run(main())