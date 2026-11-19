"""LangChain ``BaseChatModel`` over the project's ``TextGenerator`` port.

In production the generator is ``GatewayClient``, which keeps every provider
control (strict ``json_schema``, ``reasoning_effort``, ``max_tokens``, input
budget, manual redirects, rejection of truncated, refused or tool-directed
output, closed error codes). This adapter only maps messages in and an
``AIMessage`` out, exposing token counts in ``response_metadata`` and
``usage_metadata``. It is async-only: there are no threads in Python Workers.
"""

from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from unitropico_rag.domain.generation import (
    GeneratedText,
    GenerationError,
    TokenUsage,
)

ASYNC_ONLY_MESSAGE = (
    "GeneratorChatModel is async-only: Python Workers have no threads; "
    "use ainvoke/abatch/astream"
)


def usage_metadata(usage: TokenUsage | None) -> UsageMetadata | None:
    """Return LangChain usage metadata when input and output counts are known."""
    if usage is None or usage.prompt_tokens is None or usage.completion_tokens is None:
        return None
    total = usage.total_tokens
    if total is None:
        total = usage.prompt_tokens + usage.completion_tokens
    return UsageMetadata(
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        total_tokens=total,
    )


def message_usage(message: BaseMessage) -> TokenUsage | None:
    """Recover provider-reported counts from an ``AIMessage``.

    Prefers the raw ``token_usage`` (keeps unknown counts as None); falls back
    to LangChain's standard ``usage_metadata`` for other chat models.
    """
    raw = message.response_metadata.get("token_usage")
    if isinstance(raw, dict):
        counts = {
            name: raw.get(name) if type(raw.get(name)) is int else None
            for name in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        return TokenUsage(**counts)
    standard = getattr(message, "usage_metadata", None)
    if standard:
        return TokenUsage(
            standard.get("input_tokens"),
            standard.get("output_tokens"),
            standard.get("total_tokens"),
        )
    return None


class GeneratorChatModel(BaseChatModel):
    """Expose a ``TextGenerator`` (``GatewayClient``) as a LangChain chat model.

    Accepts exactly one system and one human message with string content and
    no ``stop``/extra kwargs. Errors from the generator propagate unchanged
    (``GenerationError`` keeps its code and ``request_attempted``).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    generator: Any = Field(exclude=True)
    model_name: str = "gateway"
    cache: bool | None = False  # never consult a global LLM cache

    @property
    def _llm_type(self) -> str:
        """Identify this adapter in LangChain metadata."""
        return "unitropico-gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        """Return non-secret identifying parameters."""
        return {"model_name": self.model_name}

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Refuse the sync path (LangChain may run it in a thread)."""
        raise NotImplementedError(ASYNC_ONLY_MESSAGE)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Delegate one completion to the generator and wrap its result."""
        if (
            stop is not None
            or kwargs
            or len(messages) != 2
            or not isinstance(messages[0], SystemMessage)
            or not isinstance(messages[1], HumanMessage)
            or not isinstance(messages[0].content, str)
            or not isinstance(messages[1].content, str)
        ):
            raise GenerationError("invalid_input")
        generated = await self.generator.complete(
            messages[0].content, messages[1].content
        )
        if not isinstance(generated, GeneratedText) or not isinstance(
            generated.content, str
        ):
            raise GenerationError("invalid_response", request_attempted=True)
        metadata: dict[str, Any] = {"model_name": self.model_name}
        if generated.usage is not None:
            metadata["token_usage"] = {
                "prompt_tokens": generated.usage.prompt_tokens,
                "completion_tokens": generated.usage.completion_tokens,
                "total_tokens": generated.usage.total_tokens,
            }
        message = AIMessage(
            content=generated.content,
            response_metadata=metadata,
            usage_metadata=usage_metadata(generated.usage),
        )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={"token_usage": metadata.get("token_usage")},
        )
