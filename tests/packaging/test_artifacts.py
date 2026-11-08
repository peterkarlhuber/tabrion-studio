"""STU-018: the built wheel and sdist (``TABRION_STUDIO_DIST`` = folder with the artifacts of ``python -m build``)."""

from __future__ import annotations

import os
from pathlib import Path
import tarfile
import zipfile

import pytest

pytestmark = pytest.mark.packaging


@pytest.fixture(scope="module")
def dist() -> Path:
    folder = os.environ.get("TABRION_STUDIO_DIST")
    if not folder:
        pytest.fail("set TABRION_STUDIO_DIST to the folder with the built wheel and sdist")
    return Path(folder)


def _one(dist: Path, pattern: str) -> Path:
    found = sorted(dist.glob(pattern))
    assert len(found) == 1, f"expected one {pattern} in {dist}, found {found}"
    return found[0]


def test_wheel_contains_the_frontend(dist: Path) -> None:
    wheel = _one(dist, "tabrion_studio-*.whl")
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        index = zf.read("tabrion_studio/static/index.html").decode("utf-8")
        metadata = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta = zf.read(metadata).decode("utf-8")
    assert "<title>Tabrion Studio</title>" in index
    assert any(n.startswith("tabrion_studio/static/assets/") and n.endswith(".js") for n in names)
    assert "tabrion_studio/py.typed" in names
    # No sources, tests, node_modules or source maps in the wheel.
    assert not any(n.startswith(("frontend/", "tests/", "scripts/")) for n in names)
    assert not any("node_modules" in n or n.endswith(".map") for n in names)
    assert "Requires-Dist: fastapi" in meta
    assert "Requires-Dist: tabrion[migrations,postgres,sqlalchemy]" in meta or "Requires-Dist: tabrion" in meta


def test_sdist_can_build_the_wheel(dist: Path) -> None:
    sdist = _one(dist, "tabrion_studio-*.tar.gz")
    with tarfile.open(sdist) as tf:
        names = {n.split("/", 1)[1] for n in tf.getnames() if "/" in n}
    assert "src/tabrion_studio/static/index.html" in names
    assert "hatch_build.py" in names
    assert "frontend/package-lock.json" in names
    assert not any(n.startswith(("frontend/node_modules", "frontend/dist")) for n in names)
