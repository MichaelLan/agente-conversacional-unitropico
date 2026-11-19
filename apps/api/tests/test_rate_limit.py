import asyncio
import logging
from types import SimpleNamespace

import pytest
from admin_fakes import AUTH, TOKEN, call
from test_chat import ANSWERED, DEFAULT_503, Answers
from test_retrieval import EMPTY, Client

from unitropico_api.api.rate_limit import (
    MAX_PUBLIC_BODY_BYTES,
    RATE_LIMITED_MESSAGE,
    early_public_body_rejection,
)
from unitropico_api.core.settings import Settings
from unitropico_api.domain.errors import RateLimiterUnavailable
from unitropico_api.infra.rate_limit import WorkersRateLimiter
from unitropico_api.main import create_app
from unitropico_api.services.rate_limit import (
    ANONYMOUS_KEY,
    DEFAULT_SALT,
    RateLimitGuard,
    client_key,
)

IP = "203.0.113.77"
LIMITED = {"error": {"code": "rate_limited", "message": RATE_LIMITED_MESSAGE}}
RETRIEVED = {"status": "abstained", "evidence": []}


class Counter:
    """Fake limiter: allows `limit` requests per key and records the keys."""

    def __init__(self, limit: int) -> None:
        """Start with empty counters."""
        self.limit, self.keys = limit, []

    async def allow(self, key: str) -> bool:
        """Count the request and allow it while under the limit."""
        self.keys.append(key)
        return self.keys.count(key) <= self.limit


class Broken:
    """Fake limiter whose backend is unavailable."""

    async def allow(self, key: str) -> bool:
        """Always fail like a missing binding."""
        raise RateLimiterUnavailable("BindingMissing")


def _chat(app, ip: str | None = IP):
    """POST a valid question to /chat with an optional client IP."""
    headers = {"cf-connecting-ip": ip} if ip else {}
    return call(app, "POST", "/chat", body={"question": "hola"}, headers=headers)


def test_chat_is_429_after_the_quota_and_rag_is_not_called():
    """Requests over the quota get 429 + Retry-After and never reach RAG."""
    answers = Answers(ANSWERED)
    app = create_app(chat_client=answers, chat_limiter=Counter(2))
    assert [_chat(app)[0] for _ in range(2)] == [200, 200]
    status, headers, body = _chat(app)
    assert (status, body) == (429, LIMITED)
    assert headers["retry-after"] == "60"
    assert len(answers.calls) == 2


def test_quota_is_per_client():
    """Another client keeps its own quota."""
    app = create_app(chat_client=Answers(ANSWERED), chat_limiter=Counter(1))
    assert _chat(app)[0] == 200
    assert _chat(app)[0] == 429
    assert _chat(app, "198.51.100.1")[0] == 200


def test_retrieval_has_its_own_limiter():
    """/retrieval/query uses RETRIEVAL limiter; /chat is unaffected by it."""
    rag = Client(RETRIEVED)
    app = create_app(
        rag_client=rag, chat_client=Answers(ANSWERED), retrieval_limiter=Counter(1)
    )
    headers = {"cf-connecting-ip": IP}
    first = call(
        app, "POST", "/retrieval/query", body={"question": "x"}, headers=headers
    )
    second = call(
        app, "POST", "/retrieval/query", body={"question": "x"}, headers=headers
    )
    assert first[0] == 200
    assert (second[0], second[1]["retry-after"], second[2]) == (429, "60", LIMITED)
    assert len(rag.calls) == 1
    assert [_chat(app)[0] for _ in range(3)] == [200, 200, 200]


def test_limit_applies_before_request_validation():
    """Over the quota, even an invalid body is refused with 429, not 422."""
    app = create_app(chat_client=Answers(ANSWERED), chat_limiter=Counter(0))
    status, _, body = call(app, "POST", "/chat", body={"question": ""})
    assert (status, body) == (429, LIMITED)


def test_key_is_hashed_stable_and_salted():
    """Keys are deterministic HMACs that never contain the IP."""
    key = client_key(IP, DEFAULT_SALT)
    assert key == client_key(f" {IP} ", DEFAULT_SALT)
    assert len(key) == 32 and IP not in key
    assert key != client_key("198.51.100.1", DEFAULT_SALT)
    assert key != client_key(IP, "other-salt")
    assert client_key(None, DEFAULT_SALT) == client_key("", "x") == ANONYMOUS_KEY


def test_app_passes_hashed_key_and_never_logs_the_ip(caplog):
    """The limiter sees only the salted hash; logs carry neither IP nor key."""
    limiter = Counter(0)
    settings = Settings(rate_limit_salt="pepper")
    app = create_app(
        settings=settings, chat_client=Answers(ANSWERED), chat_limiter=limiter
    )
    with caplog.at_level(logging.DEBUG, logger="unitropico_api"):
        assert _chat(app)[0] == 429
    assert limiter.keys == [client_key(IP, "pepper")]
    assert "rate limited route=chat" in caplog.text
    assert IP not in caplog.text and limiter.keys[0] not in caplog.text


def test_missing_header_uses_anonymous_key():
    """Without CF-Connecting-IP (local development) the key is `anonymous`."""
    limiter = Counter(5)
    app = create_app(chat_client=Answers(ANSWERED), chat_limiter=limiter)
    assert _chat(app, None)[0] == 200
    assert limiter.keys == [ANONYMOUS_KEY]


def test_unavailable_limiter_fails_open_with_warning(caplog):
    """A missing binding lets the request through and logs a warning."""
    answers = Answers(ANSWERED)
    limiter = WorkersRateLimiter(lambda: None)
    app = create_app(chat_client=answers, chat_limiter=limiter)
    with caplog.at_level(logging.WARNING, logger="unitropico_api"):
        assert _chat(app)[0] == 200
    assert len(answers.calls) == 1
    assert (
        "rate limiter unavailable route=chat reason=BindingMissing fail=open"
        in caplog.text
    )
    assert IP not in caplog.text


@pytest.mark.parametrize("limiter", [Broken(), None])
def test_required_limiter_fails_closed(limiter, caplog):
    """RATE_LIMIT_REQUIRED turns a missing/failing limiter into the route's 503."""
    answers, rag = Answers(ANSWERED), Client(RETRIEVED)
    settings = Settings.from_mapping({"RATE_LIMIT_REQUIRED": "true"})
    app = create_app(
        settings=settings,
        rag_client=rag,
        chat_client=answers,
        chat_limiter=limiter,
        retrieval_limiter=limiter,
    )
    with caplog.at_level(logging.WARNING, logger="unitropico_api"):
        assert _chat(app)[::2] == (503, DEFAULT_503)
        retrieval = call(app, "POST", "/retrieval/query", body={"question": "x"})
    assert retrieval[::2] == (503, EMPTY)
    assert answers.calls == rag.calls == []
    assert "fail=closed" in caplog.text


def test_without_limiters_routes_are_not_limited():
    """The historical composition (no limiters) keeps working unchanged."""
    app = create_app(chat_client=Answers(ANSWERED))
    assert [_chat(app)[0] for _ in range(20)] == [200] * 20


def test_admin_routes_are_not_rate_limited():
    """Admin routes keep token auth only; the public limiter is not consulted."""
    limiter = Counter(0)
    app = create_app(settings=Settings(admin_token=TOKEN), chat_limiter=limiter)
    status, _, body = call(app, "GET", "/admin/documents", headers=AUTH)
    assert status == 503 and body["error"]["code"] == "storage_unavailable"
    assert limiter.keys == []


def test_guard_maps_decisions():
    """Allowed / limited / unavailable from the port's outcome and the policy."""
    run = asyncio.run
    assert run(RateLimitGuard(Counter(1), route="chat").check(IP)) == "allowed"
    assert run(RateLimitGuard(Counter(0), route="chat").check(IP)) == "limited"
    assert run(RateLimitGuard(Broken(), route="chat").check(IP)) == "allowed"
    closed = RateLimitGuard(Broken(), route="chat", required=True)
    assert run(closed.check(IP)) == "unavailable"


class Binding:
    """Fake Rate Limiting binding returning a fixed result or raising."""

    def __init__(self, result: object) -> None:
        """Keep the outcome and record the options received."""
        self.result, self.calls = result, []

    async def limit(self, **options: object) -> object:
        """Record the options and return or raise the configured outcome."""
        self.calls.append(options)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize(
    ("result", "allowed"),
    [
        ({"success": True}, True),
        ({"success": False}, False),
        (SimpleNamespace(success=True), True),
        (SimpleNamespace(success=False), False),
    ],
)
def test_adapter_maps_success(result, allowed):
    """`{success}` (mapping or proxy attribute) maps to the port's bool."""
    binding = Binding(result)
    assert asyncio.run(WorkersRateLimiter(lambda: binding).allow("k1")) is allowed
    assert binding.calls == [{"key": "k1"}]


@pytest.mark.parametrize(
    ("binding", "reason"),
    [
        (None, "BindingMissing"),
        (Binding(RuntimeError("boom")), "RuntimeError"),
        (Binding({"success": "yes"}), "InvalidResult"),
        (Binding({}), "InvalidResult"),
        (Binding(None), "InvalidResult"),
    ],
)
def test_adapter_failures_are_unavailable(binding, reason):
    """Missing binding, binding errors and odd results raise RateLimiterUnavailable."""
    with pytest.raises(RateLimiterUnavailable) as raised:
        asyncio.run(WorkersRateLimiter(lambda: binding).allow("k1"))
    assert raised.value.reason == reason


def _headers(**values):
    """Case-insensitive header getter double."""
    return {k.replace("_", "-"): v for k, v in values.items()}.get


@pytest.mark.parametrize("path", ["/chat", "/retrieval/query", "//chat/"])
def test_early_body_rejection(path):
    """413 over 64 KiB, 411 for chunked bodies without length, else continue."""
    over = early_public_body_rejection(
        "POST", path, _headers(content_length=str(MAX_PUBLIC_BODY_BYTES + 1))
    )
    assert over[0] == 413 and over[2]["error"]["code"] == "payload_too_large"
    chunked = early_public_body_rejection(
        "POST", path, _headers(transfer_encoding="chunked")
    )
    assert chunked[0] == 411 and chunked[2]["error"]["code"] == "length_required"
    bogus = early_public_body_rejection("POST", path, _headers(content_length="1e9"))
    assert bogus[0] == 411
    exact = str(MAX_PUBLIC_BODY_BYTES)
    assert (
        early_public_body_rejection("POST", path, _headers(content_length=exact))
        is None
    )
    assert early_public_body_rejection("POST", path, _headers()) is None


def test_early_body_rejection_ignores_other_routes():
    """GET, /health and admin routes are not concerned."""
    big = _headers(content_length=str(10**9))
    assert early_public_body_rejection("GET", "/chat", big) is None
    assert early_public_body_rejection("POST", "/health", big) is None
    assert early_public_body_rejection("POST", "/admin/uploads", big) is None
