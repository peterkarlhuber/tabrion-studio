"""STU-011 (TD-013): the tabrion 0.1 workarounds stay in ``services/tabrion_compat.py``.

Only that module reaches below tabrion's public 0.1 surface (``Transaction.connection``, the driver's connection, its
cursors and cancel functions). When tabrion 0.2 brings ``Transaction.stream``/``cancel`` and ``Database.inspector``
(TAB-033, TAB-042), the move changes that file only.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "tabrion_studio"
COMPAT = SRC / "services" / "tabrion_compat.py"
BELOW_THE_SURFACE = re.compile(
    r"\.dbapi_connection|cancel_safe|\.interrupt\(|\btransaction\.connection\b|exec_driver_sql|"
    r"\.cursor\(|tabrion\._sa|_v02"
)


def sources() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if path != COMPAT)


@pytest.mark.parametrize("path", sources(), ids=lambda p: str(p.relative_to(SRC)))
def test_only_the_compat_module_reaches_the_driver(path: Path) -> None:
    found = BELOW_THE_SURFACE.findall(path.read_text(encoding="utf-8"))
    assert found == [], f"{path.name}: {found}; put it in services/tabrion_compat.py"


def test_the_compat_module_is_what_the_check_guards() -> None:
    assert BELOW_THE_SURFACE.search(COMPAT.read_text(encoding="utf-8"))
