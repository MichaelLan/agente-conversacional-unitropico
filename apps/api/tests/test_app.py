import ast
import asyncio
import importlib
import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from starlette.types import Message


def make_app() -> FastAPI:
    """Construye la aplicación por su única entrada pública de composición."""
    return importlib.import_module("unitropico_api.main").create_app()


def request(app: FastAPI, path: str) -> list[Message]:
    """Ejecuta un GET ASGI en memoria y conserva sus mensajes de respuesta."""
    messages: list[Message] = []

    async def receive() -> Message:
        """Entrega un GET sin cuerpo al endpoint síncrono o asíncrono."""
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        """Captura los mensajes HTTP emitidos por la aplicación."""
        messages.append(message)

    async def run() -> None:
        """Invoca ASGI sin servidor, red ni cliente HTTP adicional."""
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": b"",
                "root_path": "",
                "headers": [],
                "server": ("test", 80),
                "client": ("test", 1234),
            },
            receive,
            send,
        )

    asyncio.run(run())
    return messages


@pytest.mark.parametrize(
    ("path", "status", "body"),
    [("/health", 200, {"status": "ok"}), ("/missing", 404, {"detail": "Not Found"})],
)
def test_http_contract(
    monkeypatch: pytest.MonkeyPatch, path: str, status: int, body: dict[str, str]
) -> None:
    """Verifica estado, JSON y finalización de salud local y ruta inexistente."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    messages = request(make_app(), path)
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == status
    assert (b"content-type", b"application/json") in messages[0]["headers"]
    assert all(message["type"] == "http.response.body" for message in messages[1:])
    assert json.loads(b"".join(message["body"] for message in messages[1:])) == body
    assert not messages[-1].get("more_body", False)


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
def test_explicit_log_levels(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    """Aplica cada nivel permitido al logger propio sin duplicar handlers."""
    monkeypatch.setenv("LOG_LEVEL", level)
    make_app()
    logger = logging.getLogger("unitropico_api")
    assert logger.level == getattr(logging, level)
    handlers = tuple(logger.handlers)
    make_app()
    assert tuple(logger.handlers) == handlers
    assert sum(h.name == "unitropico_api.stderr" for h in handlers) == 1
    assert not logger.propagate


def test_default_and_fresh_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lee el entorno en cada composición, no al importar ni desde un singleton."""
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    make_app()
    monkeypatch.delenv("LOG_LEVEL")
    make_app()
    assert logging.getLogger("unitropico_api").level == logging.INFO


@pytest.mark.parametrize("level", ["", "info", "TRACE", "20", " INFO "])
def test_invalid_level_fails_before_logging(
    monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    """Rechaza configuración inválida sin modificar el logging existente."""
    logger = logging.getLogger("unitropico_api")
    before = (logger.level, tuple(logger.handlers), logger.propagate)
    monkeypatch.setenv("LOG_LEVEL", level)
    with pytest.raises(ValidationError, match="log_level"):
        make_app()
    assert (logger.level, tuple(logger.handlers), logger.propagate) == before


def test_configuration_boundary() -> None:
    """Restringe acceso al entorno e importación de settings a sus responsables."""
    make_app()
    source = Path(__file__).parents[1] / "src" / "unitropico_api"
    settings_importers = set()
    environment_readers = set()
    for path in source.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "settings" in node.module.split("."):
                    settings_importers.add(path.relative_to(source).as_posix())
                if node.module == "os":
                    environment_readers.add(path.relative_to(source).as_posix())
            if isinstance(node, ast.Import):
                if any(alias.name == "os" for alias in node.names):
                    environment_readers.add(path.relative_to(source).as_posix())
    assert settings_importers == {"main.py"}
    assert environment_readers == {"core/settings.py"}
