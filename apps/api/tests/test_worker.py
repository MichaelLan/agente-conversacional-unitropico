import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from admin_fakes import AUTH, TOKEN, FakeD1, call
from test_app import request
from test_chat import ABSTAINED, ANSWERED, DEFAULT_503, Binding
from test_retrieval import EMPTY, post

ROOT = Path(__file__).parents[1]


@pytest.fixture
def worker(monkeypatch):
    """Load worker.py with a test-only SDK whose asgi.fetch drives the app."""
    sdk = ModuleType("workers")
    sdk.WorkerEntrypoint = type("WorkerEntrypoint", (), {})
    sdk.calls = []

    async def fetch(app, incoming, env, ctx):
        """Record the handoff and run the real app with env in the scope."""
        sdk.calls.append((app, env, ctx))
        return await asyncio.to_thread(post, app, incoming.payload, incoming.path, env)

    class Response:
        """Minimal stand-in for workers.Response.from_json."""

        def __init__(self, status, headers, body):
            self.status, self.headers, self.body = status, headers, body

        @staticmethod
        def from_json(body, status=200, headers=None):
            return Response(status, headers or {}, body)

    sdk.asgi = SimpleNamespace(fetch=fetch)
    sdk.Response = Response
    monkeypatch.setitem(sys.modules, "workers", sdk)
    spec = importlib.util.spec_from_file_location(
        "worker_under_test", ROOT / "src/worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.sdk = sdk
    return module


class Incoming:
    """Request double: method, URL, headers (case-insensitive) and a JSON payload."""

    def __init__(self, path, payload=None, method="POST", headers=None):
        """Store the request parts."""
        self.path, self.payload, self.method = path, payload, method
        self.url = f"https://api.example.test{path}?x=1"
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.headers = SimpleNamespace(get=lambda n, d=None: self._headers.get(n, d))


def entry(worker, **env):
    """Build a worker entrypoint with the given env vars and bindings."""
    instance = worker.Default()
    instance.env = SimpleNamespace(**env)
    instance.ctx = object()
    return instance


def test_composes_once_and_resolves_binding_per_request(worker):
    """One app per isolate; each request uses its own env's RAG binding."""
    first = Binding(200, ANSWERED)
    second = Binding(200, ABSTAINED)
    chat = Incoming("/chat", {"question": "x"})
    assert asyncio.run(entry(worker, RAG=first).fetch(chat)) == (200, ANSWERED)
    assert asyncio.run(entry(worker, RAG=second).fetch(chat)) == (
        200,
        ABSTAINED,
    )
    assert len(first.calls) == len(second.calls) == 1
    assert worker.sdk.calls[0][0] is worker.sdk.calls[1][0]


def test_missing_binding_fails_closed(worker):
    """Without a binding, API routes fail closed."""
    instance = entry(worker)
    run = asyncio.run
    assert run(instance.fetch(Incoming("/chat", {"question": "x"}))) == (
        503,
        DEFAULT_503,
    )
    assert run(instance.fetch(Incoming("/retrieval/query", {"question": "x"}))) == (
        503,
        EMPTY,
    )


def test_settings_come_from_env_mapping(worker):
    """EXPOSE_DOCS and timeouts are read from the Worker env, not os.environ."""
    instance = entry(worker, EXPOSE_DOCS="true", RAG_ANSWER_TIMEOUT_SECONDS="7")
    app = worker._compose(instance.env)
    assert request(app, "/docs")[0]["status"] == 200


def test_local_worker_configuration() -> None:
    """Fix compatibility, the RAG service, admin storage and rate limit bindings."""
    config = json.loads((ROOT / "wrangler.jsonc").read_text())
    assert config == {
        "name": "unitropico-api",
        "main": "src/worker.py",
        "compatibility_date": "2026-11-08",
        "compatibility_flags": ["python_workers"],
        "workers_dev": False,
        "services": [{"binding": "RAG", "service": "unitropico-rag"}],
        "d1_databases": [
            {
                "binding": "DB",
                "database_name": "unitropico",
                "database_id": "REPLACE_WITH_D1_DATABASE_ID",
                "migrations_dir": "../../infra/d1/migrations",
            }
        ],
        "r2_buckets": [{"binding": "DOCUMENTS", "bucket_name": "unitropico-documents"}],
        "workflows": [
            {
                "binding": "INGESTION",
                "name": "ingestion",
                "class_name": "IngestionWorkflow",
                "script_name": "unitropico-ingestion",
            }
        ],
        "ratelimits": [
            {
                "name": "CHAT_LIMITER",
                "namespace_id": "1001",
                "simple": {"limit": 20, "period": 60},
            },
            {
                "name": "RETRIEVAL_LIMITER",
                "namespace_id": "1002",
                "simple": {"limit": 30, "period": 60},
            },
            {
                "name": "ADMIN_AUTH_LIMITER",
                "namespace_id": "1003",
                "simple": {"limit": 10, "period": 60},
            },
        ],
    }
    migrations = ROOT / config["d1_databases"][0]["migrations_dir"]
    assert (migrations / "0001_initial.sql").is_file()
    assert "ADMIN_TOKEN" not in json.dumps(config)


def test_admin_bindings_resolved_per_request(worker):
    """Admin adapters read DB/DOCUMENTS/INGESTION and the secret from the env."""
    db = FakeD1()
    env = SimpleNamespace(ADMIN_TOKEN=TOKEN, DB=db, DOCUMENTS=object())
    app = worker._compose(env)
    status, _, body = call(app, "GET", "/admin/documents", headers=AUTH, env=env)
    assert (status, body) == (200, {"documents": []})
    status, _, body = call(
        app, "GET", "/admin/documents", headers=AUTH, env=SimpleNamespace()
    )
    assert status == 503
    assert body["error"]["code"] == "storage_unavailable"


def test_admin_disabled_without_secret(worker):
    """Without the ADMIN_TOKEN secret the admin routes stay closed."""
    env = SimpleNamespace(DB=FakeD1())
    app = worker._compose(env)
    status, _, body = call(app, "GET", "/admin/documents", headers=AUTH, env=env)
    assert status == 503
    assert body["error"]["code"] == "admin_disabled"


def _upload(path="/admin/uploads", **headers):
    """POST double for the admin upload route."""
    return Incoming(path, {}, headers=headers)


def test_worker_rejects_uploads_before_the_asgi_bridge(worker):
    """Refusals use headers alone: asgi.fetch (which buffers the body) never runs."""
    db = FakeD1()
    cases = [
        ({}, {}, 503, "admin_disabled"),
        ({"ADMIN_TOKEN": TOKEN}, {}, 401, "unauthorized"),
        (
            {"ADMIN_TOKEN": TOKEN},
            {"authorization": "Bearer wrong", "content-length": "5"},
            401,
            "unauthorized",
        ),
        ({"ADMIN_TOKEN": TOKEN}, dict(AUTH), 411, "length_required"),
        (
            {"ADMIN_TOKEN": TOKEN, "ADMIN_MAX_UPLOAD_BYTES": "100"},
            {**AUTH, "content-length": "101"},
            413,
            "payload_too_large",
        ),
        (
            {"ADMIN_TOKEN": TOKEN},
            {**AUTH, "content-length": str(100 * 1024 * 1024)},
            413,
            "payload_too_large",
        ),
    ]
    for env, headers, status, code in cases:
        worker._app = None
        response = asyncio.run(entry(worker, DB=db, **env).fetch(_upload(**headers)))
        assert response.status == status
        assert response.body["error"]["code"] == code
        assert (response.headers.get("WWW-Authenticate") == "Bearer") == (status == 401)
    assert worker.sdk.calls == []


def test_worker_lets_valid_and_other_requests_reach_fastapi(worker):
    """A well-formed upload and unrelated routes still use the ASGI bridge."""
    env = {"ADMIN_TOKEN": TOKEN, "DB": FakeD1()}
    ok = _upload(**{**AUTH, "content-length": "10"})
    assert asyncio.run(entry(worker, **env).fetch(ok)) is not None
    other = Incoming("/chat", {"question": "x"})
    assert asyncio.run(entry(worker, **env).fetch(other)) == (503, DEFAULT_503)
    assert len(worker.sdk.calls) == 2


class Ratelimit:
    """Fake Rate Limiting binding: denies after `quota` calls, records options."""

    def __init__(self, quota):
        """Start the counter."""
        self.quota, self.calls = quota, []

    async def limit(self, **options):
        """Record the options and report success while under the quota."""
        self.calls.append(options)
        return {"success": len(self.calls) <= self.quota}


def test_worker_wires_rate_limit_bindings(worker):
    """CHAT_LIMITER / RETRIEVAL_LIMITER are resolved per request from the env."""
    rag, chat_limiter, retrieval_limiter = (
        Binding(200, ANSWERED),
        Ratelimit(1),
        Ratelimit(0),
    )
    env = {
        "RAG": rag,
        "CHAT_LIMITER": chat_limiter,
        "RETRIEVAL_LIMITER": retrieval_limiter,
    }
    chat = Incoming("/chat", {"question": "x"})
    assert asyncio.run(entry(worker, **env).fetch(chat)) == (200, ANSWERED)
    status, body = asyncio.run(entry(worker, **env).fetch(chat))
    assert (status, body["error"]["code"]) == (429, "rate_limited")
    query = Incoming("/retrieval/query", {"question": "x"})
    assert asyncio.run(entry(worker, **env).fetch(query))[0] == 429
    assert len(rag.calls) == 1
    # The fake ASGI bridge sends no CF-Connecting-IP, so the key is anonymous.
    assert chat_limiter.calls == [{"key": "anonymous"}] * 2
    assert retrieval_limiter.calls == [{"key": "anonymous"}]


def test_worker_fails_open_without_rate_limit_bindings(worker):
    """Without limiter bindings the chat still answers (fail-open)."""
    rag = Binding(200, ANSWERED)
    chat = Incoming("/chat", {"question": "x"})
    assert asyncio.run(entry(worker, RAG=rag).fetch(chat)) == (200, ANSWERED)


def test_worker_fails_closed_when_rate_limit_required(worker):
    """RATE_LIMIT_REQUIRED=true without bindings answers 503 before RAG."""
    rag = Binding(200, ANSWERED)
    chat = Incoming("/chat", {"question": "x"})
    instance = entry(worker, RAG=rag, RATE_LIMIT_REQUIRED="true")
    assert asyncio.run(instance.fetch(chat)) == (503, DEFAULT_503)
    assert rag.calls == []


@pytest.mark.parametrize("path", ["/chat", "/retrieval/query"])
def test_worker_rejects_large_public_bodies_before_the_asgi_bridge(worker, path):
    """413 over 64 KiB and 411 for chunked bodies, decided from headers alone."""
    rag = Binding(200, ANSWERED)
    big = Incoming(path, {}, headers={"content-length": str(64 * 1024 + 1)})
    chunked = Incoming(path, {}, headers={"transfer-encoding": "chunked"})
    for incoming, status, code in [
        (big, 413, "payload_too_large"),
        (chunked, 411, "length_required"),
    ]:
        response = asyncio.run(entry(worker, RAG=rag).fetch(incoming))
        assert (response.status, response.body["error"]["code"]) == (status, code)
    assert worker.sdk.calls == [] and rag.calls == []
    ok = Incoming(path, {"question": "x"}, headers={"content-length": "20"})
    asyncio.run(entry(worker, RAG=rag).fetch(ok))
    assert len(worker.sdk.calls) == 1


def _admin(method, path, **headers):
    """Request double for an arbitrary admin route."""
    return Incoming(path, {}, method=method, headers=headers)


def test_worker_authenticates_other_admin_routes_before_the_bridge(worker):
    """No token / bad token / oversized or chunked bodies never reach asgi.fetch."""
    db = FakeD1()
    big = str(100 * 1024 * 1024)
    cases = [
        ({}, {}, 503),
        ({"ADMIN_TOKEN": TOKEN}, {"content-length": big}, 401),
        ({"ADMIN_TOKEN": TOKEN}, {**AUTH, "content-length": big}, 413),
        ({"ADMIN_TOKEN": TOKEN}, {**AUTH, "transfer-encoding": "chunked"}, 411),
    ]
    for env, headers, status in cases:
        worker._app = None
        response = asyncio.run(
            entry(worker, DB=db, **env).fetch(
                _admin("PATCH", "/admin/documents/x", **headers)
            )
        )
        assert response.status == status
    assert worker.sdk.calls == []
    ok = _admin("GET", "/admin/documents", **AUTH)
    asyncio.run(entry(worker, DB=db, ADMIN_TOKEN=TOKEN).fetch(ok))
    assert len(worker.sdk.calls) == 1


def test_worker_throttles_failed_admin_auth(worker):
    """The limiter is read from env, keyed by client, and only after a 401."""
    limiter = Ratelimit(1)
    env = {"ADMIN_TOKEN": TOKEN, "DB": FakeD1(), "ADMIN_AUTH_LIMITER": limiter}
    ip = {"cf-connecting-ip": "2001:db8::1"}
    bad = _admin("GET", "/admin/documents", **ip)
    statuses = []
    for _ in range(2):
        response = asyncio.run(entry(worker, **env).fetch(bad))
        statuses.append((response.status, response.headers.get("Retry-After")))
    assert statuses == [(401, None), (429, "60")]
    good = _admin("GET", "/admin/documents", **AUTH, **ip)
    asyncio.run(entry(worker, **env).fetch(good))
    assert len(limiter.calls) == 2
    # Missing binding: fail-open, plain 401.
    response = asyncio.run(entry(worker, ADMIN_TOKEN=TOKEN).fetch(bad))
    assert response.status == 401
