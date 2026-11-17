import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("arguments", [("check", "."), ("format", "--check", ".")])
def test_ruff_without_git(tmp_path: Path, arguments: tuple[str, ...]) -> None:
    """Excluye artefactos sin Git y sigue verificando el código propio."""
    root = Path(__file__).parents[1]
    shutil.copyfile(root / "pyproject.toml", tmp_path / "pyproject.toml")
    source = tmp_path / "src"
    source.mkdir()
    own_file = source / "example.py"
    own_file.write_text("VALUE = 1\n")
    invalid_source = "import os\nvalue=  1\n"
    for name in (".venv-workers", "python_modules", ".wrangler"):
        generated = tmp_path / name
        generated.mkdir()
        (generated / "generated.py").write_text(invalid_source)
    assert not (tmp_path / ".git").exists()
    command = [sys.executable, "-m", "ruff", *arguments]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""

    own_file.write_text(invalid_source)
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "example.py" in result.stdout + result.stderr
    assert "generated.py" not in result.stdout + result.stderr
