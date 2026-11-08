"""The ``tabrion-studio`` command (03 section 4.1, STU-ADR-0001).

``tabrion-studio [PROJECT] [--port N] [--no-browser] [--idle-timeout MINUTES]`` serves PROJECT (default: the current
folder) on 127.0.0.1 and opens the system browser. If a Studio already runs for that folder, the command asks it for
a new launch URL, opens that and exits; it never starts a second server. ``--stop`` ends the running instance.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys
import time
from typing import TextIO
import webbrowser

from tabrion_studio._version import __version__
from tabrion_studio.idle import DEFAULT_IDLE_MINUTES
from tabrion_studio.instance import ControlError, InstanceLock, InstancePaths, control, find_running
from tabrion_studio.paths import UnsafePathError, default_state_dir

# Taken when the module loads; the server stack (FastAPI, Uvicorn) is imported only when this process serves, so
# "ready in" includes those imports, and a second start that reuses the running instance never loads them.
_STARTED = time.perf_counter()

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _tabrion_version() -> str:
    try:
        import tabrion
    except ImportError:  # pragma: no cover - tabrion is a required dependency
        return "not installed"
    return str(tabrion.__version__)


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("the port must be between 0 and 65535 (0 = any free port)")
    return port


def _minutes(value: str) -> float:
    minutes = float(value)
    if not minutes >= 0:  # also rejects NaN
        raise argparse.ArgumentTypeError("the idle timeout must be 0 (off) or a positive number of minutes")
    return minutes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tabrion-studio",
        description="Tabrion Studio: a local developer tool for SQLite and PostgreSQL databases.",
    )
    parser.add_argument("project", nargs="?", default=".", help="project folder (default: the current folder)")
    parser.add_argument(
        "--port", type=_port, default=0, help="port on 127.0.0.1 (default: a free port chosen by the system)"
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the system browser")
    parser.add_argument(
        "--idle-timeout",
        type=_minutes,
        default=DEFAULT_IDLE_MINUTES,
        metavar="MINUTES",
        help=f"stop after this many minutes without an open tab (default: {DEFAULT_IDLE_MINUTES:g}; 0 = never)",
    )
    parser.add_argument("--stop", action="store_true", help="stop the Studio that runs for the project folder")
    parser.add_argument(
        "--state-dir", type=Path, default=None, help="state directory (default: the per-user state directory)"
    )
    parser.add_argument(
        "--version", action="version", version=f"tabrion-studio {__version__} (tabrion {_tabrion_version()})"
    )
    return parser


def _open_browser(url: str) -> None:
    webbrowser.open(url, new=2)


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr
    args = build_parser().parse_args(argv)
    project = Path(args.project).expanduser()
    if not project.is_dir():
        print(f"tabrion-studio: not a folder: {project}", file=err)
        return EXIT_USAGE
    project = project.resolve()
    state_dir: Path = args.state_dir if args.state_dir is not None else default_state_dir()
    paths = InstancePaths.for_project(state_dir, project)
    lock = InstanceLock(paths.lock)
    try:
        acquired = lock.acquire()
    except (OSError, UnsafePathError) as exc:
        print(f"tabrion-studio: cannot use the state directory {state_dir}: {exc}", file=err)
        return EXIT_ERROR

    if args.stop:
        return _stop(lock, acquired, paths, project, out, err)
    if not acquired:
        return _reuse(paths, project, open_browser=not args.no_browser, out=out, err=err)

    from tabrion_studio.server import PortUnavailableError, ServeOptions, serve

    options = ServeOptions(
        project=project,
        state_dir=state_dir,
        port=args.port,
        idle_timeout_seconds=args.idle_timeout * 60,
        open_browser=None if args.no_browser else _open_browser,
        started=_STARTED,
    )
    try:
        return serve(options, lock, out)
    except PortUnavailableError as exc:
        lock.release()
        print(f"tabrion-studio: {exc}", file=err)
        return EXIT_ERROR


def _reuse(paths: InstancePaths, project: Path, *, open_browser: bool, out: TextIO, err: TextIO) -> int:
    """A Studio already runs for this folder: ask it for a launch URL instead of starting a second server."""
    info = find_running(paths)
    if info is None:
        print(f"tabrion-studio: another Tabrion Studio holds the lock for {project} but does not answer", file=err)
        return EXIT_ERROR
    try:
        url = str(control(info, "launch")["url"])
    except (ControlError, KeyError) as exc:
        print(f"tabrion-studio: the running Tabrion Studio did not answer: {exc}", file=err)
        return EXIT_ERROR
    print(f"Tabrion Studio is already running for {project}: {url}", file=out, flush=True)
    if open_browser:
        _open_browser(url)
    return EXIT_OK


def _stop(lock: InstanceLock, acquired: bool, paths: InstancePaths, project: Path, out: TextIO, err: TextIO) -> int:
    if acquired:
        lock.release()
        print(f"No Tabrion Studio is running for {project}.", file=out, flush=True)
        return EXIT_OK
    info = find_running(paths)
    if info is None:
        print(f"tabrion-studio: the Tabrion Studio for {project} does not answer", file=err)
        return EXIT_ERROR
    try:
        control(info, "shutdown")
    except ControlError as exc:
        print(f"tabrion-studio: the stop request failed: {exc}", file=err)
        return EXIT_ERROR
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if lock.acquire():
            lock.release()
            print(f"Tabrion Studio for {project} stopped.", file=out, flush=True)
            return EXIT_OK
        time.sleep(0.1)
    print(f"tabrion-studio: the Tabrion Studio for {project} did not stop within 15 s", file=err)
    return EXIT_ERROR
