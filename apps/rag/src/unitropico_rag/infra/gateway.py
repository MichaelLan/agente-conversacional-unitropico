import asyncio
import json
from collections.abc import Awaitable, Callable
from math import isfinite
from typing import Protocol, get_args

from pydantic import SecretStr

from unitropico_rag.core.logger import logger
from unitropico_rag.domain.generation import (
    GeneratedText,
    GenerationError,
    ReasoningEffort,
    StructuredOutput,
    TokenUsage,
)


class FetchResponse(Protocol):
    """Describe the response surface GatewayClient needs from a fetch transport."""

    status: int

    async def json(self) -> object:
        """Decode the body into Python JSON values."""
        ...


Fetch = Callable[..., Awaitable[FetchResponse]]


class _InvalidResponse(ValueError):
    """Carry a safe classification for a rejected provider payload."""

    def __init__(self, code: str = "invalid_response"):
        """Store the controlled error code."""
        super().__init__(code)
        self.code = code


class GatewayClient:
    """Send one bounded OpenAI-compatible request to Vercel AI Gateway; no retries.

    Limits are engineering controls, not cost guarantees. The fetch callable is
    injected at composition: ``await fetch(url, method=, headers=, body=,
    redirect=)`` returning an object with integer ``status`` and async ``json()``.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr,
        fetch: Fetch,
        url: str,
        timeout: float,
        max_tokens: int,
        max_input_chars: int,
        reasoning_effort: ReasoningEffort,
        structured_output: StructuredOutput | None = None,
    ):
        """Require explicit configuration; reject invalid values before any I/O."""
        if (
            not isinstance(model, str)
            or not model.strip()
            or not isinstance(api_key, SecretStr)
            or not api_key.get_secret_value().strip()
            or not callable(fetch)
            or not isinstance(url, str)
            or not url.startswith("https://")
            or type(timeout) not in (int, float)
            or not isfinite(timeout)
            or not 0 < timeout <= 60
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= 32768
            or type(max_input_chars) is not int
            or max_input_chars < 1
            or reasoning_effort not in get_args(ReasoningEffort)
            or not (
                structured_output is None
                or isinstance(structured_output, StructuredOutput)
            )
        ):
            raise GenerationError("invalid_config")
        self._model, self._key, self._fetch, self._url = model, api_key, fetch, url
        self._timeout, self._max_tokens = timeout, max_tokens
        self._max_input_chars, self._effort = max_input_chars, reasoning_effort
        self._structured = structured_output

    def _body(self, system_prompt: str, user_content: str) -> str:
        """Serialize the chat completion request."""
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": self._max_tokens,
            "reasoning_effort": self._effort,
            "stream": False,
        }
        if self._structured is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": self._structured.name,
                    "strict": True,
                    "schema": self._structured.schema,
                },
            }
        return json.dumps(body)

    async def complete(self, system_prompt: str, user_content: str) -> GeneratedText:
        """Return one complete candidate within the configured input budget."""
        if (
            not isinstance(system_prompt, str)
            or not isinstance(user_content, str)
            or not system_prompt.strip()
            or not user_content.strip()
            or len(system_prompt) + len(user_content) > self._max_input_chars
        ):
            raise GenerationError("invalid_input")
        body = self._body(system_prompt, user_content)
        code, detail = "transport_error", ""
        try:
            async with asyncio.timeout(self._timeout):
                # "manual" never follows redirects; any non-200 is rejected below.
                response = await self._fetch(
                    self._url,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {self._key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    body=body,
                    redirect="manual",
                )
                status = getattr(response, "status", None)
                if type(status) is not int or status != 200:
                    code, detail = "http_error", f"status={status!r}"
                else:
                    code = "invalid_response"
                    return self._parse(await response.json())
        except TimeoutError:
            code, detail = "timeout", ""
        except _InvalidResponse as error:
            code, detail = error.code, ""
        except (OSError, ValueError, TypeError, AttributeError) as error:
            detail = f"error={type(error).__name__}"
        logger.warning("generation_failed code=%s %s", code, detail)
        # Raise outside handlers so even implicit exception context is absent.
        raise GenerationError(code, request_attempted=True) from None

    @staticmethod
    def _parse(payload: object) -> GeneratedText:
        """Reject malformed, partial, refused or tool-directed provider output."""
        if not isinstance(payload, dict):
            raise _InvalidResponse
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise _InvalidResponse
        choice = choices[0]
        if not isinstance(choice, dict):
            raise _InvalidResponse
        if choice.get("finish_reason") == "length":
            raise _InvalidResponse("truncated")
        if choice.get("finish_reason") != "stop":
            raise _InvalidResponse
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
            raise _InvalidResponse
        usage = payload.get("usage")
        counts = {}
        if usage is not None:
            if not isinstance(usage, dict):
                raise _InvalidResponse
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                count = usage.get(name)
                if count is not None and (type(count) is not int or count < 0):
                    raise _InvalidResponse
                counts[name] = count
        return GeneratedText(
            message["content"], TokenUsage(**counts) if usage is not None else None
        )
