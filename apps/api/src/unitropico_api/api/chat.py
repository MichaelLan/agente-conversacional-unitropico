from fastapi import APIRouter, params
from fastapi.responses import JSONResponse

from unitropico_api.domain.chat import ChatRequest, ChatResponse
from unitropico_api.domain.errors import MalformedUpstream, UpstreamUnavailable
from unitropico_api.services.chat import ChatService

DEFAULT_REFERRAL = (
    "No puedo responder en este momento. Por favor, consulta directamente con "
    "Bienestar Universitario."
)
MAX_REFERRAL_LENGTH = 1000


def unavailable_body(referral: str | None = None) -> dict[str, str]:
    """Build the unavailable body, using a valid upstream referral if present."""
    valid = (
        referral is not None
        and bool(referral.strip())
        and len(referral) <= MAX_REFERRAL_LENGTH
    )
    return {
        "status": "unavailable",
        "referral": referral if valid else DEFAULT_REFERRAL,
    }


def create_chat_router(
    service: ChatService, dependencies: list[params.Depends] | None = None
) -> APIRouter:
    """Bind the chat service to the public POST /chat route.

    `dependencies` run before the body reaches the service (rate limiting).
    """
    router = APIRouter()

    @router.post(
        "/chat",
        response_model=ChatResponse,
        dependencies=dependencies or [],
        responses={
            429: {"description": "Rate limited; error envelope, Retry-After"},
            502: {"description": "Invalid RAG payload"},
            503: {"description": "RAG unavailable; carries a referral"},
        },
    )
    async def chat(payload: ChatRequest) -> JSONResponse:
        """Answer with cited evidence or abstain; never persists the conversation.

        Question is trimmed to 1-500 code points; history has at most 6 messages.
        Returns 503 when RAG is missing or failing and 502 on an invalid payload.
        """
        try:
            result = await service.answer(payload)
        except UpstreamUnavailable as error:
            return JSONResponse(unavailable_body(error.referral), status_code=503)
        except MalformedUpstream:
            return JSONResponse(unavailable_body(None), status_code=502)
        return JSONResponse(result.model_dump(mode="json"))

    return router
