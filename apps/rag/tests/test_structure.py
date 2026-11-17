import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_langgraph_resolved() -> None:
    """Ensure the actual resolved dependency graph excludes LangGraph."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    names = {package["name"] for package in lock["package"]}
    assert not any(name.startswith("langgraph") for name in names)
    assert "langchain-core" in names
    assert "langchain" not in names


def test_service_import_boundary() -> None:
    """Keep concrete LangChain SDK imports outside service orchestration."""
    for path in (ROOT / "src/unitropico_rag/services").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any("langchain" in name for name in names), path
