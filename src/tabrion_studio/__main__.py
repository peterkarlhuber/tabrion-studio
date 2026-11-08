"""``python -m tabrion_studio`` is the same as the ``tabrion-studio`` command."""

from __future__ import annotations

import sys

from tabrion_studio.cli import main

if __name__ == "__main__":
    sys.exit(main())
