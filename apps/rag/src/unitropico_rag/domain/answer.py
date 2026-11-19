from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from unitropico_rag.domain.generation import TokenUsage

AbstentionReason = Literal[
    "no_evidence",
    "model_abstained",
    "invalid_output",
    "invalid_citation",
    "stale_source",
]

# How the retrieval question is built from the current question and history:
# "none" (current question only), "concat" (cost-free heuristic) or "rewrite"
# (standalone reformulation by the chat model, one extra call per follow-up).
FollowupStrategy = Literal["none", "concat", "rewrite"]


@dataclass(frozen=True)
class ConversationTurn:
    """One prior turn supplied by the client; context only, never instructions."""

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class AnswerQuery:
    """Validated question plus bounded, non-persisted conversation history."""

    question: str
    history: tuple[ConversationTurn, ...] = ()


class CitationReference(BaseModel):
    """Accept only a chunk identifier and a quote from the untrusted candidate."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    chunk_id: str
    quote: str


class AnswerCandidate(BaseModel):
    """Describe the untrusted JSON envelope; cross-field rules run in the service."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    status: Literal["answered", "abstained"]
    answer: str | None
    citations: list[CitationReference]


# Strict JSON Schema mirroring AnswerCandidate for provider structured outputs.
ANSWER_CANDIDATE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "abstained"]},
        "answer": {"type": ["string", "null"]},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["chunk_id", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "answer", "citations"],
    "additionalProperties": False,
}


class StandaloneQuestion(BaseModel):
    """Describe the untrusted rewrite envelope: exactly one question string."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    question: str


# Strict JSON Schema mirroring StandaloneQuestion for provider structured outputs.
STANDALONE_QUESTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AnswerCitation:
    """Carry catalog-attested provenance and a quote verified against the chunk."""

    chunk_id: str
    document_id: str
    document_title: str
    version_id: str
    version_label: str | None
    section: str | None
    page_start: int
    page_end: int
    quote: str


@dataclass(frozen=True)
class AnswerResult:
    """Report structural/provenance validation, NOT semantic support for every claim.

    Usage is provider-reported metadata, never a monetary cost guarantee.
    generation_attempted means consumption is possible, not confirmed.
    """

    status: Literal["answered", "abstained", "unavailable"]
    answer: str | None = None
    reason: AbstentionReason | None = None
    citations: tuple[AnswerCitation, ...] = ()
    referral: str | None = None
    generation_attempted: bool = False
    usage: TokenUsage | None = None
