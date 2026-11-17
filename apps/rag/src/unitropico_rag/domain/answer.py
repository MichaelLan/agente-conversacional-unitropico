from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from unitropico_rag.domain.evidence import EvidenceIdentity
from unitropico_rag.domain.generation import TokenUsage


class CitationReference(BaseModel):
    """Accept only a chunk identifier and an exact, nonblank quote."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    chunk_id: str
    quote: str


class AnswerCandidate(BaseModel):
    """Describe the untrusted JSON envelope; cross-field rules run in the service."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    status: Literal["answered", "abstained"]
    answer: str | None
    citations: list[CitationReference]


@dataclass(frozen=True)
class AnswerCitation(EvidenceIdentity):
    """Carry server-attested provenance and an exact retrieved-text substring."""

    quote: str


@dataclass(frozen=True)
class AnswerResult:
    """Report structural/provenance validation, NOT semantic support for every claim.

    Usage is provider-reported metadata, never a monetary cost guarantee.
    generation_attempted means consumption is possible, not confirmed.
    """

    status: Literal["answered", "abstained", "unavailable"]
    answer: str | None = None
    citations: tuple[AnswerCitation, ...] = ()
    generation_attempted: bool = False
    usage: TokenUsage | None = None
