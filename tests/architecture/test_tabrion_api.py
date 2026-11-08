"""AC-7: only public Tabrion names (scripts/check_tabrion_api.py), with a mutation probe."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_tabrion_api.py"
sys.path.insert(0, str(SCRIPT.parent))
import check_tabrion_api  # noqa: E402

sys.path.remove(str(SCRIPT.parent))


def test_the_package_uses_public_names_only() -> None:
    result = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout
    assert "tabrion api check: ok" in result.stdout


@pytest.mark.parametrize(
    "source",
    [
        "import tabrion._sa",
        "import tabrion._sa.database as db",
        "from tabrion._sa.database import Database",
        "from tabrion.sqlite import _helper",
        "import tabrion\ntabrion._LAYER_B",
        "import tabrion.sqlite\ntabrion.sqlite._x()",
        "import tabrion as t\nt._sa",
    ],
)
def test_private_names_are_found(source: str) -> None:
    assert check_tabrion_api.check_source(source)


@pytest.mark.parametrize(
    "source",
    [
        "import tabrion\ntabrion.__version__",
        "from tabrion import Database, safe_url",
        "from tabrion.sqlite import connect",
        "import os\nos._exit",
        "from . import _private",
        "import tabrionx._y",
    ],
)
def test_public_names_pass(source: str) -> None:
    assert check_tabrion_api.check_source(source) == []


def test_mutation_probe_turns_the_check_red(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from tabrion._sa.database import Database\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), str(bad)], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "private Tabrion module tabrion._sa.database" in result.stdout
