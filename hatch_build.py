"""Hatch build hook (STU-018, 05 section 3.4): a wheel must contain the built frontend.

``scripts/build_frontend.py`` builds ``frontend/`` and copies the result to ``src/tabrion_studio/static``. This hook
does not run Node; it stops a wheel build without ``static/index.html`` with a clear message, so no wheel without its
frontend can be produced. Editable installs (``pip install -e .``) skip the check: in development the frontend is
served by ``npm run dev`` or built on demand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version == "editable":
            return
        index = Path(self.root) / "src" / "tabrion_studio" / "static" / "index.html"
        if not index.is_file():
            raise RuntimeError(
                f"{index} is missing: build the frontend first with `python scripts/build_frontend.py` "
                "(needs Node.js 22 and npm); the wheel must contain the built frontend (STU-018)."
            )
