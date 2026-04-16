"""Copilot provider 테스트."""

import asyncio

from infrapilot.providers.copilot import (
    CopilotProvider,
    has_local_copilot_cli,
    load_copilot_token,
)


async def main():
    token = None
    try:
        token = load_copilot_token()
    except FileNotFoundError:
        if not has_local_copilot_cli():
            raise

    provider = CopilotProvider(oauth_token=token)

    response = await provider.chat(
        messages=[{"role": "user", "content": "BTC 트레이딩에서 엘리엇 파동이 뭐야?"}]
    )

    print(f"모델: {response.model}")
    print(f"응답: {response.content}")


asyncio.run(main())
