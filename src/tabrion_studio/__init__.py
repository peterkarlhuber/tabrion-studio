"""Tabrion Studio: a local developer tool for SQLite and PostgreSQL databases, built on Tabrion.

The command ``tabrion-studio`` (:mod:`tabrion_studio.cli`) starts one local server per project folder on a loopback
port and serves the built frontend from :mod:`tabrion_studio` ``static/``. Status: skeleton (STU-001, STU-002).
"""

from __future__ import annotations

from tabrion_studio._version import __version__

__all__ = ["__version__"]
