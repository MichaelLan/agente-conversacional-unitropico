import asyncio
import json
from collections.abc import Sequence
from math import isfinite

from langchain_core.embeddings import Embeddings
from pydantic import SecretStr

from unitropico_rag.core.logger import logger
from unitropico_rag.infra.gateway import Fetch

MAX_INPUTS = 100


class EmbeddingError(Exception):
    """Expose a controlled code, never provider payloads or input text."""

    def __init__(self, code: str):
        """Store the safe classification."""
        super().__init__(code)
        self.code = code


class _InvalidEmbeddings(ValueError):
    """Signal a malformed provider payload."""


class GatewayEmbedder:
    """Embed texts through Vercel AI Gateway's OpenAI-compatible endpoint; no retries.

    ``POST {url}`` with ``{"model", "input": [...]}`` and reads ``data[i].embedding``.
    The fetch callable follows the same contract as ``GatewayClient``.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr,
        fetch: Fetch,
        url: str,
        timeout: float,
        dimensions: int,
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
            or type(dimensions) is not int
            or not 1 <= dimensions <= 1536
        ):
            raise EmbeddingError("invalid_config")
        self._model, self._key, self._fetch, self._url = model, api_key, fetch, url
        self._timeout, self._dimensions = timeout, dimensions

    @property
    def dimensions(self) -> int:
        """Return the vector length every response must have."""
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one finite vector per input, in input order."""
        if (
            isinstance(texts, str)
            or not isinstance(texts, Sequence)
            or not 1 <= len(texts) <= MAX_INPUTS
            or not all(isinstance(text, str) and text.strip() for text in texts)
        ):
            raise EmbeddingError("invalid_input")
        body = json.dumps({"model": self._model, "input": list(texts)})
        code, detail = "transport_error", ""
        try:
            async with asyncio.timeout(self._timeout):
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
                    return self._parse(await response.json(), len(texts))
        except TimeoutError:
            code, detail = "timeout", ""
        except _InvalidEmbeddings:
            code, detail = "invalid_response", ""
        except (OSError, ValueError, TypeError, AttributeError) as error:
            detail = f"error={type(error).__name__}"
        logger.warning("embedding_failed code=%s %s", code, detail)
        raise EmbeddingError(code) from None

    def _parse(self, payload: object, expected: int) -> list[list[float]]:
        """Validate count, order, length and finiteness of every vector."""
        if not isinstance(payload, dict):
            raise _InvalidEmbeddings
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise _InvalidEmbeddings
        vectors: list[list[float] | None] = [None] * expected
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise _InvalidEmbeddings
            index = item.get("index", position)
            if type(index) is not int or not 0 <= index < expected:
                raise _InvalidEmbeddings
            if vectors[index] is not None:
                raise _InvalidEmbeddings
            vectors[index] = self._vector(item.get("embedding"))
        return vectors  # type: ignore[return-value]  # all slots filled above

    def _vector(self, values: object) -> list[float]:
        """Accept exactly ``dimensions`` finite numbers (bools rejected)."""
        if not isinstance(values, list) or len(values) != self._dimensions:
            raise _InvalidEmbeddings
        vector = []
        for value in values:
            if type(value) not in (int, float) or not isfinite(value):
                raise _InvalidEmbeddings
            vector.append(float(value))
        return vector


ASYNC_ONLY_MESSAGE = (
    "GatewayEmbeddings is async-only: Python Workers have no threads; "
    "use aembed_query/aembed_documents"
)


class GatewayEmbeddings(Embeddings):
    """LangChain ``Embeddings`` over ``GatewayEmbedder``; async methods only.

    All validation (input, response shape, dimensions, finiteness, error
    classification) stays in ``GatewayEmbedder``. Documents are sent in
    sequential batches of at most ``MAX_INPUTS``. The sync methods raise
    because LangChain's default async fallbacks would run them in a thread.
    """

    def __init__(self, embedder: GatewayEmbedder):
        """Wrap an already configured embedder."""
        if not isinstance(embedder, GatewayEmbedder):
            raise EmbeddingError("invalid_config")
        self._embedder = embedder

    @property
    def dimensions(self) -> int:
        """Return the vector length every response must have."""
        return self._embedder.dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Refuse the sync path."""
        raise NotImplementedError(ASYNC_ONLY_MESSAGE)

    def embed_query(self, text: str) -> list[float]:
        """Refuse the sync path."""
        raise NotImplementedError(ASYNC_ONLY_MESSAGE)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in order, in batches of at most ``MAX_INPUTS``."""
        if isinstance(texts, str) or not isinstance(texts, Sequence) or not texts:
            raise EmbeddingError("invalid_input")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_INPUTS):
            vectors.extend(
                await self._embedder.embed(texts[start : start + MAX_INPUTS])
            )
        return vectors

    async def aembed_query(self, text: str) -> list[float]:
        """Embed one query text."""
        if not isinstance(text, str):
            raise EmbeddingError("invalid_input")
        (vector,) = await self._embedder.embed([text])
        return vector
