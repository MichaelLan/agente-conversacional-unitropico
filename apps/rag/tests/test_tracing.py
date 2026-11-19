"""LangSmith tracing stays off in the composed Worker app, and nothing uses threads.

Runs in a subprocess so the inherited environment (tracing switched ON, fake
LangSmith key) and the patches cannot leak into other tests.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """
import asyncio
import os
import socket
import threading
from unittest.mock import patch

events = []

def forbidden(name):
    # Record before raising: LangChain swallows tracer-construction errors.
    def fail(*args, **kwargs):
        events.append(name)
        raise AssertionError(name)
    return fail

import langsmith
import langchain_core.tracers.langchain as lc_tracer

with patch.object(socket.socket, "connect", forbidden("network")), \\
     patch.object(socket, "create_connection", forbidden("network")), \\
     patch.object(threading.Thread, "start", forbidden("thread")), \\
     patch.object(asyncio.BaseEventLoop, "run_in_executor", forbidden("executor")), \\
     patch.object(langsmith.Client, "__init__", forbidden("langsmith client")), \\
     patch.object(lc_tracer.LangChainTracer, "__init__", forbidden("tracer")):
    from langsmith.utils import tracing_is_enabled
    from pydantic import SecretStr
    from test_answer_http import GatewayFetch, post
    from test_retrieval import Catalog, Retriever, CANDIDATE
    from test_retrieval_http import request

    from unitropico_rag.core.settings import Settings
    from unitropico_rag.main import build_app

    app = build_app(
        Settings(ai_gateway_api_key=SecretStr("fake-key")),
        fetch=GatewayFetch(),
        retriever=Retriever((CANDIDATE,)),
        catalog=Catalog(),
    )
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        assert os.environ[name] == "false", name
    assert tracing_is_enabled() is False
    status, body = post(app, {"question": "pregunta"})
    assert (status, body["status"]) == (200, "answered"), (status, body)
    status, body = request(app, {"question": "pregunta"})
    assert (status, body["status"]) == (200, "evidence_found"), (status, body)
assert events == [], events
"""


def test_composed_app_never_traces_spawns_threads_or_opens_sockets() -> None:
    """With tracing inherited ON, a full /answer creates no tracer or client."""
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        env={
            "PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'tests'}",
            "LANGSMITH_TRACING": "true",
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_TRACING": "true",
            "LANGSMITH_API_KEY": "fake-langsmith-key",
            "LANGSMITH_ENDPOINT": "https://example.invalid",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


def test_composition_imports_no_runtime_sdk() -> None:
    """Composition loads LangChain Core but never workers/js (Workers-only)."""
    script = """
import sys
from unitropico_rag.core.settings import Settings
from unitropico_rag.main import build_app
build_app(Settings())
assert "langchain_core" in sys.modules
assert not any(
    name == prefix or name.startswith(prefix + ".")
    for name in sys.modules
    for prefix in ("workers", "js", "langchain_openai", "langchain_classic",
                   "langchain_community", "langgraph", "langchain_text_splitters")
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={"PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
