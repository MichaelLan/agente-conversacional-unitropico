import ast
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from test_app import request
from test_retrieval import EMPTY, post
from test_service_binding import Binding

ROOT = Path(__file__).parents[1]


def test_worker_bridge() -> None:
    """Require a native async class entrypoint with no module-level composition."""
    tree = ast.parse((ROOT / "src" / "worker.py").read_text())
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    assert len(classes) == 1
    assert classes[0].name == "Default"
    assert [ast.unparse(base) for base in classes[0].bases] == ["WorkerEntrypoint"]
    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "fetch"
        for node in classes[0].body
    )
    assert all(isinstance(node, ast.ImportFrom | ast.ClassDef) for node in tree.body)


def test_worker_per_request_and_missing_binding(monkeypatch):
    """Use a test-only SDK shim but real apps, ports and ASGI route execution."""
    sdk = ModuleType("workers")
    sdk.WorkerEntrypoint = type("WorkerEntrypoint", (), {})
    apps = []
    contexts = []

    async def fetch(app, incoming, env, ctx):
        """Capture SDK handoff and exercise the real composed application."""
        apps.append(app)
        contexts.append((env, ctx))
        assert incoming == "/health"
        return app

    sdk.asgi = SimpleNamespace(fetch=fetch)
    monkeypatch.setitem(sys.modules, "workers", sdk)
    spec = importlib.util.spec_from_file_location(
        "worker_under_test", ROOT / "src/worker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    worker = module.Default()
    worker.ctx = object()
    for binding in (Binding(payload={"status": "abstained", "evidence": []}), None):
        worker.env = SimpleNamespace(RAG=binding) if binding else SimpleNamespace()
        app = asyncio.run(worker.fetch("/health"))
        health = request(app, "/health")
        assert health[0]["status"] == 200
        assert json.loads(health[-1]["body"]) == {"status": "ok"}
        if binding:
            assert binding.calls == []
        expected = (
            (200, {"status": "abstained", "evidence": []}) if binding else (503, EMPTY)
        )
        assert post(app, {"question": "x"}) == expected
        assert post(app, {"question": "x"}, "/chat") == (404, {"detail": "Not Found"})
        assert contexts[-1] == (worker.env, worker.ctx)
    assert apps[0] is not apps[1]


def test_local_worker_configuration() -> None:
    """Fix compatibility and the existing RAG service target without deployment."""
    config = json.loads((ROOT / "wrangler.jsonc").read_text())
    assert config == {
        "name": "unitropico-api-local",
        "main": "src/worker.py",
        "compatibility_date": "2026-11-08",
        "compatibility_flags": ["python_workers"],
        "workers_dev": False,
        "services": [{"binding": "RAG", "service": "unitropico-rag-poc"}],
    }
