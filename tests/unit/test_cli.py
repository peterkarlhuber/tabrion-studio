from __future__ import annotations

import io
from pathlib import Path
import threading
import time

import pytest

from tabrion_studio import __version__, cli
from tabrion_studio.instance import InstancePaths, read_info


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"tabrion-studio {__version__} (tabrion ")


@pytest.mark.parametrize(
    "args",
    [["--port", "70000"], ["--port", "-1"], ["--idle-timeout", "-1"], ["--idle-timeout", "nan"], ["--host", "0.0.0.0"]],
)
def test_bad_arguments(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    # STU-T-025: there is no option to bind another address.
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2
    capsys.readouterr()


def test_project_must_be_a_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(tmp_path / "missing"), "--state-dir", str(tmp_path / "s")]) == cli.EXIT_USAGE
    assert "not a folder" in capsys.readouterr().err


def test_unusable_state_dir(tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert cli.main([str(project), "--no-browser", "--state-dir", str(blocker / "state")]) == cli.EXIT_ERROR
    assert "cannot use the state directory" in capsys.readouterr().err


def test_serve_reuse_and_stop_in_process(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    out = io.StringIO()
    codes: list[int] = []
    server = threading.Thread(target=lambda: codes.append(cli.main([str(project), "--state-dir", str(state_dir)], out)))
    server.start()
    paths = InstancePaths.for_project(state_dir, project)
    deadline = time.monotonic() + 15
    while read_info(paths) is None and time.monotonic() < deadline:
        time.sleep(0.02)
    info = read_info(paths)
    assert info is not None
    deadline = time.monotonic() + 5
    while not opened and time.monotonic() < deadline:  # the browser opens on a helper thread
        time.sleep(0.02)
    assert opened == [info.url]

    assert cli.main([str(project), "--state-dir", str(state_dir)]) == cli.EXIT_OK
    assert opened == [info.url, info.url]  # the second start opens the running instance
    assert cli.main([str(project), "--stop", "--state-dir", str(state_dir)]) == cli.EXIT_OK
    server.join(timeout=15)
    assert codes == [0]
    assert f"is running at {info.url}" in out.getvalue()
    assert "stopped (stop requested)" in out.getvalue()
    captured = capsys.readouterr().out
    assert "already running" in captured
    assert "stopped." in captured
