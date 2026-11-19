"""Vercel AI Gateway embeddings adapter (OpenAI-compatible ``/v1/embeddings``).

``POST {url}`` with ``{"model", "input": [...]}`` returns ``data[i].embedding``.
The fetch callable is ``workers.fetch`` (JSON body works in SDK 1.9.6),
injected so this module never imports the runtime.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from math import isfinite

from unitropico_ingestion.core.logger import logger
from unitropico_ingestion.domain.ingestion import ErrorCode, IngestionError

MAX_INPUTS = 96
TIMEOUT_SECONDS = 60.0
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})


def _failed(detail: str, *, permanent: bool = False) -> IngestionError:
    return IngestionError(ErrorCode.EMBEDDING_FAILED, detail, permanent=permanent)


class GatewayEmbedder:
    """Implement ``Embedder``; one request per call, retries belong to the step."""

    def __init__(
        self,
        *,
        api_key: str | None,
        fetch: Callable[..., Awaitable[object]],
        model: str,
        url: str,
        timeout: float = TIMEOUT_SECONDS,
    ):
        """Store configuration; a missing key fails only when embedding is needed."""
        self._key, self._fetch, self._model = api_key, fetch, model
        self._url, self._timeout = url, timeout

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one finite vector per input, in input order.

        Dimensions are validated by the caller against the configured index.
        """
        if not self._key:
            raise IngestionError(
                ErrorCode.CONFIG_MISSING, "ai_gateway_api_key", permanent=True
            )
        if not 1 <= len(texts) <= MAX_INPUTS:
            raise _failed(f"inputs={len(texts)}", permanent=True)
        body = json.dumps({"model": self._model, "input": list(texts)})
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._fetch(
                    self._url,
                    method="POST",
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                    body=body,
                    redirect="manual",
                )
                status = getattr(response, "status", None)
                if status != 200:
                    payload = None
                else:
                    payload = await response.json()  # type: ignore[attr-defined]
        except TimeoutError:
            logger.warning("embedding_failed reason=timeout")
            raise _failed("timeout") from None
        except Exception as error:  # JS errors are opaque on the host
            name = type(error).__name__
            logger.warning("embedding_failed reason=%s", name)
            raise _failed(f"transport={name}") from None
        if type(status) is not int:
            raise _failed("invalid_status")
        if status != 200:
            logger.warning("embedding_failed status=%s", status)
            permanent = 400 <= status < 500 and status not in _RETRYABLE_STATUS
            raise _failed(f"status={status}", permanent=permanent)
        return _parse(payload, len(texts))


def _parse(payload: object, expected: int) -> list[list[float]]:
    """Validate count, order and finiteness; reorder by ``index``."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != expected:
        raise _failed("invalid_response", permanent=True)
    vectors: list[list[float] | None] = [None] * expected
    for position, item in enumerate(data):
        if not isinstance(item, dict):
            raise _failed("invalid_response", permanent=True)
        index = item.get("index", position)
        embedding = item.get("embedding")
        if (
            type(index) is not int
            or not 0 <= index < expected
            or vectors[index] is not None
            or not isinstance(embedding, list)
            or not embedding
            or not all(type(x) in (int, float) and isfinite(x) for x in embedding)
        ):
            raise _failed("invalid_response", permanent=True)
        vectors[index] = [float(x) for x in embedding]
    return vectors  # type: ignore[return-value]
