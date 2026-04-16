"""Gemini provider 테스트."""

import asyncio
import os
from dotenv import load_dotenv

from infrapilot.providers.gemini import GeminiProvider


async def main():
    load_dotenv()
    api_key = os.environ["GEMINI_API_KEY"]

    provider = GeminiProvider(api_key=api_key)

    response = await provider.chat(
        messages=[{"role": "user", "content": "안녕! BTC 트레이딩에서 엘리엇 파동이란?"}]
    )

    print(f"모델: {response.model}")
    print(f"응답: {response.content}")


asyncio.run(main())