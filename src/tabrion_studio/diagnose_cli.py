"""``tabrion-studio diagnose``: the diagnosis report on the command line (03 section 1, STU-025, TD-036).

``tabrion-studio diagnose [PROJECT] --profile PROFILE [--format markdown|json] [--output FILE] [--confirm-prod]``
opens the profile's read-only connection, runs the diagnosis with its backup hints and writes the same report as
``POST /api/connections/{id}/diagnostics/report`` (:class:`.DiagnosisReports`, the same allowlist: no password,
token, URL or DSN, host, port, user name, database name, file path or value), to standard output or to ``FILE``. It
needs no running Studio and starts no server; it reads the same configuration as the server for PROJECT:

* **Profiles:** the user's own (``user:<name>``) and, only while the project file is trusted, the project's
  (``project:<name>``). Trust is given in Studio's dialog D-01 in the browser, never here: an untrusted or changed
  project file gives no project profile (``Configuration.entries``).
* **Secrets:** as in the server (keyring, ``env:``, ``file:``). There is no prompt: a profile whose password is
  missing is refused (``studio.secret_missing``).
* **PROD (D-02):** a PROD profile opens only with ``--confirm-prod``, this command's form of the confirmation. The
  registry checks it against the profile as it reads it when it connects (``require_prod_confirmation``, TD-038), the
  same check as the page's; a profile that became PROD after it was named is refused too. The flag has no
  environment variable or setting, so it is given for each run.

Errors are the fixed English texts of the problem catalog (:mod:`tabrion_studio.problems`), never the text of an
exception; log records go to standard error through the log redaction, at warning level and above. Exit codes:
0 the report was written, 1 it could not be made, 2 a usage error (an unknown profile, PROD not confirmed).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import logging
import os
from pathlib import Path
import sys
from typing import Any, TextIO

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

_logger = logging.getLogger("tabrion_studio.diagnose")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tabrion-studio diagnose",
        description="Write the diagnosis report of one connection profile (Markdown or JSON, without secrets). "
        "It opens the profile's read-only connection; no Studio needs to run.",
    )
    parser.add_argument("project", nargs="?", default=".", help="project folder (default: the current folder)")
    parser.add_argument(
        "--profile",
        required=True,
        metavar="PROFILE",
        help="the profile: its id (user:NAME or project:NAME) or its name when only one profile has it",
    )
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown", help="report format (default: markdown)"
    )
    parser.add_argument(
        "--output", type=Path, default=None, metavar="FILE", help="write the report to FILE (default: standard output)"
    )
    parser.add_argument(
        "--confirm-prod",
        action="store_true",
        help="confirm a connection to a profile marked as PROD (D-02); the connection stays read-only",
    )
    parser.add_argument(
        "--state-dir", type=Path, default=None, help="state directory (default: the per-user state directory)"
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="folder of settings.toml and profiles.toml (default: the per-user configuration folder, "
        "or STATE_DIR/config with --state-dir)",
    )
    return parser


def _say(err: TextIO, code: str, *, note: str | None = None) -> None:
    """A refusal in the catalog's fixed words (never an exception's text)."""
    from tabrion_studio.problems import CATALOG

    entry = CATALOG.get(code)
    if entry is None:
        print(f"tabrion-studio diagnose: {code}", file=err)
    else:
        hint = note if note is not None else entry.hint
        print(f"tabrion-studio diagnose: {entry.title}. {entry.detail}{f' {hint}' if hint else ''} ({code})", file=err)


def _say_db(err: TextIO, error: Any) -> None:
    from tabrion_studio.problems import db_problem

    found = db_problem(error)
    hint = f" {found.hint}" if found.hint else ""
    print(f"tabrion-studio diagnose: {found.title}. {found.detail}{hint} ({found.code})", file=err)


def main(argv: Sequence[str], out: TextIO, err: TextIO) -> int:
    args = build_parser().parse_args(list(argv))
    project = Path(args.project).expanduser()
    if not project.is_dir():
        print(f"tabrion-studio diagnose: not a folder: {project}", file=err)
        return EXIT_USAGE
    project = project.resolve()

    from tabrion_studio.logs import configure_logging

    # Warnings and errors only, to standard error, through the redaction (no log file: this is no Studio instance).
    logs = configure_logging(None, "diagnose", console=err)
    try:
        return _run(args, project, out, err)
    finally:
        logs.close()


def _run(args: argparse.Namespace, project: Path, out: TextIO, err: TextIO) -> int:
    from tabrion.errors import TabrionError

    from tabrion_studio.api.config import Configuration, ProfileEntry
    from tabrion_studio.config.project import ProjectConfig, TrustError
    from tabrion_studio.config.secrets import SecretError
    from tabrion_studio.config.user import UserConfig
    from tabrion_studio.instance import project_key
    from tabrion_studio.paths import STATE_DIR_ENV, default_config_dir, default_state_dir
    from tabrion_studio.services.connections import ConnectionRegistry, HealthError, RegistryError
    from tabrion_studio.services.diagnosis_report import DiagnosisReports, ReportError
    from tabrion_studio.services.diagnostics import Diagnostics
    from tabrion_studio.services.readonly import StatementRefused

    state_dir: Path = args.state_dir if args.state_dir is not None else default_state_dir()
    explicit_state = args.state_dir is not None or bool(os.environ.get(STATE_DIR_ENV))
    config_dir: Path = (
        args.config_dir if args.config_dir is not None else default_config_dir(state_dir if explicit_state else None)
    )
    configuration = Configuration(
        project=ProjectConfig(project, state_dir, project_key(project), config_dir.expanduser().resolve()),
        user=UserConfig(config_dir.expanduser().resolve()),
    )
    project_state = configuration.load_project()
    entries = configuration.entries(project_state)
    wanted = str(args.profile)
    matches: list[ProfileEntry] = [entry for entry in entries if entry.id == wanted]
    if not matches:
        matches = [entry for entry in entries if entry.profile.name == wanted]
    if len(matches) > 1:
        names = ", ".join(entry.id for entry in matches)
        print(f"tabrion-studio diagnose: more than one profile is named {wanted!r}; give its id: {names}", file=err)
        return EXIT_USAGE
    if not matches:
        _say(err, "studio.profile_not_found")
        if project_state.state in ("untrusted", "changed"):
            print(
                "The project's profiles are used only after you trust them in Studio (dialog D-01): run "
                "tabrion-studio in the project folder and review them.",
                file=err,
            )
        return EXIT_USAGE
    entry = matches[0]
    if entry.profile.environment == "prod" and not args.confirm_prod:
        # D-02 in this command's words; the registry checks it again on the profile it connects to.
        print(
            f"tabrion-studio diagnose: {entry.id} is marked as PROD. Studio stays read-only; the diagnosis is limited "
            "to the profile's statement timeout (15 s by default). Pass --confirm-prod to connect.",
            file=err,
        )
        return EXIT_USAGE

    registry = ConnectionRegistry(configuration)
    diagnostics = Diagnostics(registry)
    reports = DiagnosisReports(registry, diagnostics, clock=diagnostics.clock)
    try:
        connection = registry.open(entry.id, confirmed_environment="prod" if args.confirm_prod else None)
        report = reports.build(connection.id, args.format)
    except RegistryError as exc:
        _say(err, exc.code)
        return (
            EXIT_USAGE if exc.code in ("studio.prod_confirmation_required", "studio.profile_not_found") else EXIT_ERROR
        )
    except SecretError as exc:
        note = (
            "Store the password in the system keyring (Studio's connection manager), or reference it with env: or "
            "file: in the profile."
            if exc.code == "studio.secret_missing"
            else None
        )
        _say(err, exc.code, note=note)
        return EXIT_USAGE if exc.code == "studio.profile_not_found" else EXIT_ERROR
    except (TrustError, ReportError, StatementRefused) as exc:
        _say(err, exc.code)
        return EXIT_ERROR
    except HealthError as exc:
        _say_db(err, exc.classification)
        return EXIT_ERROR
    except TabrionError as exc:
        _say_db(err, exc)
        return EXIT_ERROR
    except Exception:
        # Through the log redaction (types and frames only, never the message), like the server's internal error.
        _logger.error("diagnosis report failed", exc_info=True, extra={"event": "studio.diagnosis_report_failed"})
        print("tabrion-studio diagnose: the report could not be made (an unexpected error, see above).", file=err)
        return EXIT_ERROR
    finally:
        registry.close_all()
    if args.output is None:
        out.write(report.body.decode("utf-8"))
        out.flush()
    else:
        try:
            Path(args.output).expanduser().write_bytes(report.body)
        except OSError as exc:
            print(
                f"tabrion-studio diagnose: cannot write {args.output}: {exc.strerror or type(exc).__name__}", file=err
            )
            return EXIT_ERROR
        print(f"Report written to {args.output} ({report.filename}).", file=err)
    _logger.info("diagnosis report", extra={"event": "studio.diagnosis_report_cli"})
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:], sys.stdout, sys.stderr))
