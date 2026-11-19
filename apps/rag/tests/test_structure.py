import ast
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/unitropico_rag"


def imports(path: Path) -> set[str]:
    """Return the absolute module names imported by a source file."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# Option B (docs/research/langchain-evaluation.md): only langchain-core.
EXCLUDED_DISTRIBUTIONS = (
    "langchain",
    "langchain-openai",
    "langchain-classic",
    "langchain-community",
    "langchain-cloudflare",
    "langchain-text-splitters",
    "langgraph",
)
EXCLUDED_MODULES = tuple(name.replace("-", "_") for name in EXCLUDED_DISTRIBUTIONS)
# LangChain sync entry points (may use threads) and their direct equivalents.
SYNC_CALLS = frozenset(
    {
        "invoke",
        "batch",
        "batch_as_completed",
        "stream",
        "transform",
        "get_relevant_documents",
        "embed_query",
        "embed_documents",
        "generate",
        "generate_prompt",
    }
)


@pytest.mark.parametrize(
    "lock_name,key", [("uv.lock", "package"), ("pylock.toml", "packages")]
)
def test_only_langchain_core_resolved(lock_name: str, key: str) -> None:
    """Host and WASM locks pin langchain-core 1.8.3 and no other LangChain package."""
    lock = tomllib.loads((ROOT / lock_name).read_text())
    versions = {package["name"]: package["version"] for package in lock[key]}
    assert versions["langchain-core"] == "1.8.3"
    assert not any(
        name in EXCLUDED_DISTRIBUTIONS or name.startswith("langgraph")
        for name in versions
    )


def test_no_sync_langchain_calls_in_production_code() -> None:
    """Workers have no threads: only ainvoke/abatch/astream may be called.

    Flags any call to a LangChain sync entry point and any use of
    ``RunnableParallel`` by name (its sync path runs a thread pool).
    """
    offenders = []
    for path in [*PACKAGE.rglob("*.py"), ROOT / "src/worker.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in SYNC_CALLS
            ):
                offenders.append((path.name, node.lineno, node.func.attr))
            if isinstance(node, ast.Name | ast.alias) and "RunnableParallel" in (
                getattr(node, "id", None),
                getattr(node, "name", None),
            ):
                offenders.append((path.name, node.lineno, "RunnableParallel"))
    assert offenders == []


def test_sync_guard_detects_offenders() -> None:
    """The guard's AST pattern matches the forms it must forbid."""
    source = "chain.invoke(x)\nretriever.batch([x])\nmodel.stream(x)\n"
    calls = [
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert set(calls) <= SYNC_CALLS and len(calls) == 3


@pytest.mark.parametrize("layer", ["api", "domain", "services", "infra"])
def test_settings_only_imported_by_composition(layer: str) -> None:
    """Only main.py (and worker.py) may import Settings."""
    for path in (PACKAGE / layer).rglob("*.py"):
        assert "unitropico_rag.core.settings" not in imports(path), path


@pytest.mark.parametrize(
    "layer,forbidden",
    [
        (
            "domain",
            (
                "unitropico_rag.services",
                "unitropico_rag.infra",
                "fastapi",
                "langchain_core",
                "langsmith",
            ),
        ),
        ("api", ("langchain_core", "langsmith")),
        ("services", ("unitropico_rag.infra", "unitropico_rag.api", "fastapi")),
        ("infra", ("unitropico_rag.services", "unitropico_rag.api", "fastapi")),
        ("", ("workers", "js", *EXCLUDED_MODULES)),
    ],
)
def test_layer_boundaries(layer: str, forbidden: tuple[str, ...]) -> None:
    """Keep layers acyclic, runtime SDKs and non-core LangChain out of the package."""
    for path in (PACKAGE / layer).rglob("*.py"):
        for name in imports(path):
            assert not any(
                name == prefix or name.startswith(prefix + ".") for prefix in forbidden
            ), (path, name)
