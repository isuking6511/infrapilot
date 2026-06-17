"""GitHub Copilot provider."""

import json
import subprocess
import time
import asyncio
from pathlib import Path

import httpx

from infrapilot.providers.base import LLMProvider, LLMResponse


COPILOT_API = "https://api.githubcopilot.com/chat/completions"


class CopilotProvider(LLMProvider):
    """GitHub Copilot 프로바이더.
    
    GitHub Copilot 구독자만 사용 가능.
    `gh auth login` 필요.
    """

    def __init__(
        self,
        oauth_token: str | None = None,
        model: str = "gpt-4o",
    ) -> None:
        self._oauth_token = oauth_token
        self._model = model
        self._copilot_token: str | None = None
        self._copilot_token_expires_at: float = 0

    async def _get_copilot_token(self) -> str:
        """Copilot 세션 토큰 발급 (OAuth 토큰으로 교환).

        토큰은 약 30분 후 만료되므로 만료 전에 재발급합니다.
        """
        # 만료 60초 전에 미리 갱신
        if self._copilot_token and time.time() < self._copilot_token_expires_at - 60:
            return self._copilot_token

        if not self._oauth_token:
            raise ValueError("OAuth token is required for direct Copilot API auth flow.")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.github.com/copilot_internal/v2/token",
                headers={
                    "Authorization": f"token {self._oauth_token}",
                    "Editor-Version": "vscode/1.95.0",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("token")
            if not isinstance(token, str) or not token.strip():
                raise ValueError("Copilot token missing in token exchange response.")

            expires_at = data.get("expires_at")
            if isinstance(expires_at, (int, float)):
                expires_at_ts = float(expires_at)
            elif isinstance(expires_at, str):
                try:
                    expires_at_ts = float(expires_at)
                except ValueError:
                    expires_at_ts = time.time() + 1800
            else:
                expires_at_ts = time.time() + 1800

            self._copilot_token = token
            self._copilot_token_expires_at = expires_at_ts
        return token

    def _copilot_cli_path(self) -> Path:
        path = (
            Path.home()
            / "Library/Application Support/Code/User/globalStorage/github.copilot-chat/copilotCli/copilot"
        )
        if not path.exists():
            raise FileNotFoundError(
                "Copilot CLI binary not found. Open VS Code with Copilot Chat installed."
            )
        return path

    async def _chat_via_cli(self, messages: list[dict], system: str | None = None) -> LLMResponse:
        # Copilot CLI non-interactive 입력은 단일 프롬프트가 안정적이라 대화를 평문으로 합칩니다.
        prompt_parts: list[str] = []
        if system:
            prompt_parts.append(f"[system]\n{system}")
        for msg in messages:
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", ""))
            prompt_parts.append(f"[{role}]\n{content}")
        prompt = "\n\n".join(prompt_parts).strip()

        cli_path = self._copilot_cli_path()

        proc = await asyncio.create_subprocess_exec(
            str(cli_path),
            "-s",
            "-p",
            prompt,
            "--allow-all-tools",
            "--allow-all-paths",
            "--allow-all-urls",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Copilot CLI call failed: {err or 'unknown error'}")

        content = stdout.decode("utf-8", errors="replace").strip()
        if not content:
            raise RuntimeError("Copilot CLI returned empty response.")

        return LLMResponse(content=content, model=self._model)

    async def chat(
        self,
        messages: list[dict],
        system: str | None = None,
    ) -> LLMResponse:
        if system:
            messages = [{"role": "system", "content": system}, *messages]

        # 1순위: 직접 API 호출 시도
        if self._oauth_token:
            try:
                token = await self._get_copilot_token()
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        COPILOT_API,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Editor-Version": "vscode/1.95.0",
                            "Copilot-Integration-Id": "vscode-chat",
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
            except Exception:
                # 내부 경로/토큰 교환 실패 시 로컬 Copilot CLI 경로로 폴백
                pass

        # 2순위: 로컬 Copilot CLI 호출
        return await self._chat_via_cli(messages=messages, system=None)

    @property
    def name(self) -> str:
        return "copilot"


def load_copilot_token() -> str:
    """로컬 GitHub Copilot OAuth 토큰 읽기.

    우선순위:
    1. VS Code/Cursor의 github-copilot 설정 파일
    2. gh CLI (gh auth token)
    """
    config_paths = [
        Path.home() / ".config/github-copilot/hosts.json",
        Path.home() / ".config/github-copilot/apps.json",
    ]

    for path in config_paths:
        if path.exists():
            data = json.loads(path.read_text())
            for key, value in data.items():
                if "github.com" in key and "oauth_token" in value:
                    return value["oauth_token"]

    # fallback: gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = result.stdout.strip()
        if token:
            return token
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    raise FileNotFoundError(
        "Copilot 토큰을 찾을 수 없습니다. "
        "VS Code/Cursor에서 GitHub Copilot 로그인을 먼저 하세요."
    )


def has_local_copilot_cli() -> bool:
    """로컬 VS Code Copilot CLI 바이너리 존재 여부."""
    return (
        Path.home()
        / "Library/Application Support/Code/User/globalStorage/github.copilot-chat/copilotCli/copilot"
    ).exists()
