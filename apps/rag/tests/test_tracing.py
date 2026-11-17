import subprocess
import sys
from pathlib import Path


def test_cold_import_and_query_disable_inherited_tracing() -> None:
    """Keep SDK loading after composition and forbid network in a fresh process."""
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import asyncio
import os
import socket
import sys
from unittest.mock import patch

with patch.object(socket.socket, "connect", side_effect=AssertionError("network")), \\
     patch.object(socket, "create_connection", side_effect=AssertionError("network")):
    from unitropico_rag.main import create_app
    assert not any(
        name == prefix or name.startswith(prefix + ".")
        for name in sys.modules
        for prefix in ("langchain_core", "langsmith")
    )
    create_app()
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        assert os.environ[name] == "false"
    from unitropico_rag.infra.langchain import normalize_question
    assert asyncio.run(normalize_question("  local  ")) == "local"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            "PYTHONPATH": str(source),
            "LANGSMITH_TRACING": "true",
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_TRACING": "true",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
