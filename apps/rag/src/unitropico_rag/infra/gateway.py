import asyncio
import json
from collections.abc import Awaitable, Callable
from math import isfinite
from typing import Protocol

from pydantic import SecretStr

from unitropico_rag.domain.generation import GeneratedText, GenerationError, TokenUsage


class FetchResponse(Protocol):
    """Describe the Workers response surface without importing its runtime."""

    status: int

    async def json(self) -> object:
        """Decode JSON into Python values, as Workers does."""
        ...


class GatewayClient:
    """Send one bounded OpenAI-compatible request; never retry or read settings.

    Limits are engineering controls, not cost guarantees. Inject async Workers
    fetch at composition; this module makes no runtime compatibility claim.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr,
        fetch: Callable[..., Awaitable[FetchResponse]],
        timeout: float = 20,
        max_tokens: int = 512,
    ):
        """Require explicit credentials and model; reject invalid config offline."""
        if (
            not isinstance(model, str)
            or not model.strip()
            or not isinstance(api_key, SecretStr)
            or not api_key.get_secret_value().strip()
            or not callable(fetch)
            or type(timeout) not in (int, float)
            or not isfinite(timeout)
            or not 0 < timeout <= 60
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= 2048
        ):
            raise GenerationError("invalid_config")
        self._model, self._key, self._fetch = model, api_key, fetch
        self._timeout, self._max_tokens = timeout, max_tokens

    async def complete(self, system_prompt: str, user_content: str) -> GeneratedText:
        """Return only a complete candidate within a combined 20,000-char input."""
        if (
            not isinstance(system_prompt, str)
            or not isinstance(user_content, str)
            or not system_prompt.strip()
            or not user_content.strip()
            or len(system_prompt) + len(user_content) > 20000
        ):
            raise GenerationError("invalid_input")
        body = json.dumps(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": self._max_tokens,
                "stream": False,
            }
        )
        code = "transport_error"
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._fetch(
                    "https://ai-gateway.vercel.sh/v1/chat/completions",
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {self._key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    body=body,
                    redirect="error",
                )
                code = "http_error"
                if type(response.status) is int and response.status == 200:
                    code = "invalid_response"
                    return self._parse(await response.json())
        except TimeoutError:
            code = "timeout"
        except Exception:
            pass
        # Raise outside handlers so even implicit exception context is absent.
        raise GenerationError(code, request_attempted=True) from None

    @staticmethod
    def _parse(payload: object) -> GeneratedText:
        """Reject malformed, partial, refused or tool-directed provider output."""
        if not isinstance(payload, dict):
            raise ValueError
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
            raise ValueError
        message = choice.get("message")
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            or message.get("refusal") is not None
            or message.get("tool_calls") is not None
            or message.get("function_call") is not None
        ):
            raise ValueError
        usage = payload.get("usage")
        counts = {}
        if usage is not None:
            if not isinstance(usage, dict):
                raise ValueError
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                count = usage.get(name)
                if count is not None and (type(count) is not int or count < 0):
                    raise ValueError
                counts[name] = count
        return GeneratedText(
            message["content"], TokenUsage(**counts) if usage is not None else None
        )
