import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "unitropico_ingestion"
RUNTIME = {"js", "workers", "pyodide"}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_runtime_modules_only_in_worker_and_infra():
    """Runtime modules only in worker and infra."""
    for path in PACKAGE.rglob("*.py"):
        if "infra" in path.relative_to(PACKAGE).parts:
            continue
        roots = {name.split(".")[0] for name in _imports(path)}
        assert not roots & RUNTIME, path


def test_settings_only_composed_in_worker():
    """Settings only composed in worker."""
    for path in PACKAGE.rglob("*.py"):
        if path.parent.name == "core":
            continue
        assert "unitropico_ingestion.core.settings" not in _imports(path), path
    assert "unitropico_ingestion.core.settings" in _imports(SRC / "worker.py")


def test_no_fastapi_or_pydantic():
    """No fastapi or pydantic."""
    for path in SRC.rglob("*.py"):
        roots = {name.split(".")[0] for name in _imports(path)}
        assert not roots & {"fastapi", "pydantic"}, path
