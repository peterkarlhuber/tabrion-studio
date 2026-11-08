"""The ``tabrion-studio`` command (03 section 4.1, STU-ADR-0001).

``tabrion-studio [PROJECT] [--port N] [--no-browser] [--print-url] [--idle-timeout MINUTES]`` serves PROJECT
(default: the current folder) on 127.0.0.1 and opens the system browser. If a Studio already runs for that folder,
the command asks it for a new launch, opens that and exits; it never starts a second server. ``--stop`` ends the
running instance.

The browser is opened with the ``file://`` path of a private redirect file, never with the launch URL itself: the
URL carries a one-time token, and the arguments of the browser process are visible to every local user (review F-02).
Only ``--print-url`` shows a launch URL, in this terminal.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import sys
import time
from typing import TextIO
import webbrowser

from tabrion_studio._version import __version__
from tabrion_studio.browser import sandbox_hint, sandboxed_default_browser
from tabrion_studio.idle import DEFAULT_IDLE_MINUTES
from tabrion_studio.instance import (
    ControlError,
    ImpostorError,
    InstanceLock,
    InstancePaths,
    control,
    find_running,
    validate_launch_url,
    validate_redirect_file,
)
from tabrion_studio.paths import STATE_DIR_ENV, UnsafePathError, default_log_dir, default_state_dir, remove_file

# Taken when the module loads; the server stack (FastAPI, Uvicorn) is imported only when this process serves, so
# "ready in" includes those imports, and a second start that reuses the running instance never loads them.
_STARTED = time.perf_counter()

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


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
        "--print-url",
        action="store_true",
        help="print a one-time sign-in link (for a browser started by hand; anyone who sees it can use it once)",
    )
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
        "--log-dir",
        type=Path,
        default=None,
        help="log directory (default: the per-user log directory, or STATE_DIR/logs with --state-dir)",
    )
    parser.add_argument("--version", action="version", version=f"tabrion-studio {__version__}")
    return parser


def _open_browser(url: str) -> None:
    """Open ``url`` (the ``file://`` URI of a redirect file, never a URL with a token) in the system browser."""
    webbrowser.open(url, new=2)


def _warn_about_a_sandboxed_browser(state_dir: Path, err: TextIO) -> None:
    """TD-002 (review S-4): a snap or Flatpak browser may not read the redirect file; say what to do instead."""
    found = sandboxed_default_browser()
    if found is not None:
        print(sandbox_hint(*found, state_dir=state_dir), file=err, flush=True)


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
    explicit_state = args.state_dir is not None or bool(os.environ.get(STATE_DIR_ENV))
    log_dir: Path = args.log_dir if args.log_dir is not None else default_log_dir(state_dir if explicit_state else None)
    paths = InstancePaths.for_project(state_dir, project)
    lock = InstanceLock(paths.lock)
    try:
        acquired = lock.acquire()
        if acquired:
            # Whoever holds the lock owns the info file. One left behind by a crashed instance names a port that
            # another program may now use; remove it at once so nobody reads it (review F-01).
            remove_file(paths.info)
    except (OSError, UnsafePathError) as exc:
        lock.release()
        print(f"tabrion-studio: cannot use the state directory {state_dir}: {exc}", file=err)
        return EXIT_ERROR

    if args.stop:
        return _stop(lock, acquired, paths, project, out, err)
    if not args.no_browser:
        _warn_about_a_sandboxed_browser(state_dir, err)
    if not acquired:
        return _reuse(paths, project, open_browser=not args.no_browser, print_url=args.print_url, out=out, err=err)

    from tabrion_studio.server import PortUnavailableError, ServeOptions, serve

    options = ServeOptions(
        project=project,
        state_dir=state_dir,
        port=args.port,
        idle_timeout_seconds=args.idle_timeout * 60,
        open_browser=None if args.no_browser else _open_browser,
        print_url=args.print_url,
        log_dir=log_dir,
        started=_STARTED,
    )
    try:
        return serve(options, lock, out)
    except PortUnavailableError as exc:
        lock.release()
        print(f"tabrion-studio: {exc}", file=err)
        return EXIT_ERROR


def _reuse(
    paths: InstancePaths, project: Path, *, open_browser: bool, print_url: bool, out: TextIO, err: TextIO
) -> int:
    """A Studio already runs for this folder: ask it for a launch instead of starting a second server."""
    info = find_running(paths)
    if info is None:
        print(f"tabrion-studio: another Tabrion Studio holds the lock for {project} but does not answer", file=err)
        return EXIT_ERROR
    running = f"Tabrion Studio is already running for {project}: {info.url}"
    if not open_browser and not print_url:
        print(running, file=out, flush=True)
        return EXIT_OK
    try:
        doc = control(info, "launch", body={"redirect": open_browser})
        url = validate_launch_url(info, doc.get("url"))
        redirect = validate_redirect_file(paths, url, doc.get("redirect")) if open_browser else None
    except ImpostorError as exc:
        print(f"tabrion-studio: {exc}", file=err)
        return EXIT_ERROR
    except ControlError as exc:
        print(f"tabrion-studio: the running Tabrion Studio did not answer: {exc}", file=err)
        return EXIT_ERROR
    print(running, file=out, flush=True)
    if print_url:
        print(f"One-time sign-in link (do not share): {url}", file=out, flush=True)
    if redirect is not None:
        _open_browser(redirect.resolve().as_uri())
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
    except ControlError as exc:  # includes ImpostorError: the token was not sent
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
