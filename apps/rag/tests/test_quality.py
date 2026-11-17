import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("arguments", [("check", "."), ("format", "--check", ".")])
def test_ruff_excludes_generated_files_without_git(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    """Run public Ruff commands without Git and exclude malformed generated code."""
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    (tmp_path / "pyproject.toml").write_text(project.read_text())
    (tmp_path / "source.py").write_text("value = 1\n")
    for directory in (".venv-workers", "python_modules", ".wrangler"):
        generated = tmp_path / directory
        generated.mkdir()
        (generated / "vendor.py").write_text("import os\n\nvalue=  1\n")
    assert not any(
        (parent / ".git").exists() for parent in (tmp_path, *tmp_path.parents)
    )

    result = subprocess.run(
        [sys.executable, "-m", "ruff", *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
