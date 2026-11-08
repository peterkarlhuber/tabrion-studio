"""STU-003 launch flow: one-time token, redirect file, no token in a process argv (STU-T-005, -006; F-02, F-08).

The browser is never started with a URL that carries the launch token (review F-02): it gets the ``file://`` URI of a
0600 redirect file in the state directory. The file disappears when the token is used, when it expires and when the
server stops. ``--print-url`` is the only way to see a launch URL. Studio prints and opens only ``127.0.0.1`` URLs
(F-08).
"""

from __future__ import annotations

from collections.abc import Iterator
import contextlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit

import pytest

from tabrion_studio import cli, server
from tabrion_studio.instance import (
    ControlError,
    InstanceInfo,
    InstanceLock,
    InstancePaths,
    control,
    control_proof,
    read_info,
    validate_launch_url,
    validate_redirect_file,
    write_info,
)
from tabrion_studio.paths import ensure_private_dir, write_private_file
from tabrion_studio.security.launch import LAUNCH_TTL_SECONDS, LaunchTokens, redirect_page
from tests.support.fake_server import fake_studio
from tests.support.session import heartbeat, redeem, session_from, sign_in
from tests.support.studio import NO_KEYRING, studio_command

pytestmark = pytest.mark.security

POSIX = sys.platform != "win32"
LAUNCH_URL = re.compile(r"http://127\.0\.0\.1:(\d+)/launch#([A-Za-z0-9_-]{43})")


def _file_path(uri: str) -> Path:
    parts = urlsplit(uri)
    assert parts.scheme == "file", uri
    path = unquote(parts.path)
    if sys.platform == "win32" and re.match(r"/[A-Za-z]:", path):
        path = path[1:]
    return Path(path)


def _launch_url_in(redirect: Path) -> str:
    match = LAUNCH_URL.search(redirect.read_text(encoding="utf-8"))
    assert match is not None, redirect.read_text(encoding="utf-8")
    return match.group(0)


def _wait(condition: object, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:  # type: ignore[operator]
        time.sleep(0.02)
    assert condition(), "condition not met in time"  # type: ignore[operator]


@contextlib.contextmanager
def serving(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, *args: str
) -> Iterator[tuple[InstanceInfo, io.StringIO, list[str]]]:
    """Run ``tabrion-studio PROJECT`` in a thread with the browser replaced by a recorder; stop it afterwards."""
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    out = io.StringIO()
    codes: list[int] = []
    thread = threading.Thread(
        target=lambda: codes.append(cli.main([str(project), "--state-dir", str(state_dir), *args], out))
    )
    thread.start()
    paths = InstancePaths.for_project(state_dir, project.resolve())
    try:
        _wait(lambda: read_info(paths) is not None, 15)
        info = read_info(paths)
        assert info is not None
        _wait(lambda: "Press Ctrl+C" in out.getvalue())
        if "--no-browser" not in args:
            _wait(lambda: bool(opened))
        yield info, out, opened
    finally:
        cli.main([str(project), "--stop", "--state-dir", str(state_dir)], io.StringIO())
        thread.join(timeout=20)
    assert codes == [0]


# --------------------------------------------------------------------------------------------------------------------
# STU-T-006 (F-02): the browser gets a redirect file, never the token
# --------------------------------------------------------------------------------------------------------------------


def test_browser_is_opened_with_a_redirect_file(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serving(project, state_dir, monkeypatch) as (info, out, opened):
        assert len(opened) == 1
        uri = opened[0]
        assert uri.startswith("file://")
        assert "#" not in uri and "launch" in uri  # the file name, not the launch URL
        redirect = _file_path(uri)
        assert redirect.parent.resolve() == state_dir.resolve()
        url = _launch_url_in(redirect)
        token = url.rsplit("#", 1)[1]
        assert token not in uri
        assert token not in out.getvalue()  # nothing in the terminal either (no --print-url)
        assert "/launch#" not in out.getvalue()
        if POSIX:
            assert stat.S_IMODE(redirect.stat().st_mode) == 0o600
        # The page at /launch redeems the token; the redirect file goes away at once.
        session = session_from(info.port, *redeem(info.port, token))
        assert not redirect.exists()
        assert heartbeat(session) == 204
        assert redeem(info.port, token)[0] == 401  # once only


def test_second_start_opens_a_new_redirect_file(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with serving(project, state_dir, monkeypatch) as (info, _out, opened):
        assert cli.main([str(project), "--state-dir", str(state_dir)]) == cli.EXIT_OK
        assert len(opened) == 2 and opened[0] != opened[1]
        second = _file_path(opened[1])
        url = _launch_url_in(second)
        assert url.rsplit("#", 1)[1] not in opened[1]
        printed = capsys.readouterr().out
        assert f"already running for {project.resolve()}: {info.url}" in printed
        assert "/launch#" not in printed
        assert redeem(info.port, url.rsplit("#", 1)[1])[0] == 200
        assert not second.exists()
    # Stopping removes the redirect file of the first launch, whose token was never used.
    assert not _file_path(opened[0]).exists()
    assert list(state_dir.glob("*-launch-*.html")) == []


def test_print_url_is_the_explicit_opt_in(project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with serving(project, state_dir, monkeypatch, "--no-browser", "--print-url") as (info, out, opened):
        assert opened == []
        links = LAUNCH_URL.findall(out.getvalue())
        assert len(links) == 1 and int(links[0][0]) == info.port
        assert list(state_dir.glob("*-launch-*.html")) == []  # no browser, no redirect file
        assert redeem(info.port, links[0][1])[0] == 200
        reuse = io.StringIO()
        assert cli.main([str(project), "--no-browser", "--print-url", "--state-dir", str(state_dir)], reuse) == 0
        assert len(LAUNCH_URL.findall(reuse.getvalue())) == 1
        quiet = io.StringIO()
        assert cli.main([str(project), "--no-browser", "--state-dir", str(state_dir)], quiet) == 0
        assert "/launch#" not in quiet.getvalue() and info.url in quiet.getvalue()


def test_no_localhost_url_is_printed_or_opened(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # F-08: only http://127.0.0.1:<port>/ URLs, also in the redirect files and with --print-url.
    with serving(project, state_dir, monkeypatch, "--print-url") as (info, out, opened):
        assert cli.main([str(project), "--print-url", "--state-dir", str(state_dir)]) == cli.EXIT_OK
        texts = [out.getvalue(), capsys.readouterr().out, *(_file_path(u).read_text(encoding="utf-8") for u in opened)]
        for text in texts:
            # (the project path of this test contains the word, so look for it as a host)
            assert not re.search(r"//localhost\b|localhost:\d", text), text
            assert "[::1]" not in text
            for url in re.findall(r"https?://[^\s\"'<>]+", text):
                assert url.startswith(f"http://127.0.0.1:{info.port}/"), url
        assert len(opened) == 2


@pytest.mark.skipif(not POSIX, reason="the fake browser is a script with a shebang ($BROWSER)")
def test_browser_argv_contains_no_token(project: Path, state_dir: Path, tmp_path: Path) -> None:
    # STU-T-005/-006, F-02: a real `tabrion-studio` process starts the browser named in $BROWSER (Python's webbrowser
    # module). The fake browser records its argv, which is what `ps` and /proc/<pid>/cmdline show to other users.
    record = tmp_path / "browser-argv.jsonl"
    browser = tmp_path / "fake-browser"
    browser.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(record)!r}, 'a') as fh:\n"
        "    fh.write(json.dumps(sys.argv) + '\\n')\n",
        encoding="utf-8",
    )
    browser.chmod(0o700)
    env = {**os.environ, "BROWSER": str(browser), "PYTHONUNBUFFERED": "1", **NO_KEYRING}
    log = tmp_path / "studio.log"
    with log.open("wb") as fh:
        process = subprocess.Popen(
            studio_command(str(project), "--state-dir", str(state_dir)), stdout=fh, stderr=subprocess.STDOUT, env=env
        )
    try:
        _wait(lambda: record.exists() and record.read_text().count("\n") >= 1, 20)
        paths = InstancePaths.for_project(state_dir, project.resolve())
        info = read_info(paths)
        assert info is not None
        second = subprocess.run(
            studio_command(str(project), "--state-dir", str(state_dir)), env=env, capture_output=True, text=True
        )
        assert second.returncode == 0, second.stdout + second.stderr
        _wait(lambda: record.read_text().count("\n") >= 2, 20)
        calls = [json.loads(line) for line in record.read_text().splitlines()]
        assert len(calls) == 2
        tokens = []
        for argv in calls:
            assert len(argv) == 2, argv
            assert argv[1].startswith("file://"), argv
            assert "#" not in argv[1] and "token" not in argv[1]
            url = _launch_url_in(_file_path(argv[1]))
            tokens.append(url.rsplit("#", 1)[1])
        for token in tokens:
            assert all(token not in arg for argv in calls for arg in argv)
            assert token not in log.read_text() and token not in second.stdout + second.stderr
        # The recorded file is the real way in: its token opens a session, once.
        assert redeem(info.port, tokens[1])[0] == 200
        assert redeem(info.port, tokens[1])[0] == 401
    finally:
        subprocess.run(studio_command("--stop", str(project), "--state-dir", str(state_dir)), check=False)
        process.wait(timeout=20)
    assert list(state_dir.glob("*-launch-*.html")) == []


# --------------------------------------------------------------------------------------------------------------------
# Launch tokens and redirect files (unit)
# --------------------------------------------------------------------------------------------------------------------


class Clock:
    now = 50.0

    def __call__(self) -> float:
        return self.now


def test_launch_tokens_and_redirect_files(tmp_path: Path) -> None:
    clock = Clock()
    ensure_private_dir(tmp_path / "s")
    launches = LaunchTokens("http://127.0.0.1:5000", tmp_path / "s", "studio-k", clock=clock)
    plain = launches.issue(redirect=False)
    assert plain.redirect is None and LAUNCH_URL.fullmatch(plain.url)
    with_file = launches.issue(redirect=True)
    assert with_file.redirect is not None
    assert with_file.redirect.read_bytes() == redirect_page(with_file.url)
    if POSIX:
        assert stat.S_IMODE(with_file.redirect.stat().st_mode) == 0o600
    assert plain.url != with_file.url
    assert launches.redeem(with_file.url.rsplit("#", 1)[1]) == "ok"
    assert not with_file.redirect.exists()  # deleted once redeemed
    assert launches.redeem(with_file.url.rsplit("#", 1)[1]) == "used"
    expiring = launches.issue(redirect=True)
    clock.now += LAUNCH_TTL_SECONDS
    assert launches.sweep() == 2
    assert expiring.redirect is not None and not expiring.redirect.exists()  # deleted after the timeout
    assert launches.redeem(plain.url.rsplit("#", 1)[1]) == "expired"
    last = launches.issue(redirect=True)
    launches.close()
    assert last.redirect is not None and not last.redirect.exists()  # deleted when the server stops
    assert launches.redeem(last.url.rsplit("#", 1)[1]) == "expired"
    assert launches.pending == 0


def test_redirect_page_escapes_the_url() -> None:
    page = redirect_page('http://127.0.0.1:1/launch#a"><script>x</script>').decode()
    assert "<script>" not in page and "&quot;" in page


def test_leftover_redirect_files_are_removed_at_start(state_dir: Path, project: Path) -> None:
    paths = InstancePaths.for_project(state_dir, project)
    ensure_private_dir(state_dir)
    ours = state_dir / f"{paths.info.stem}-launch-0123456789abcdef.html"
    other = state_dir / "studio-0000000000000000-launch-0123456789abcdef.html"
    unrelated = state_dir / f"{paths.info.stem}-launch-notes.html"
    for path in (ours, other, unrelated):
        write_private_file(path, b"x")
    server.remove_redirect_files(paths)
    assert not ours.exists()
    assert other.exists() and unrelated.exists()


# --------------------------------------------------------------------------------------------------------------------
# The CLI accepts only this instance's launch URL and redirect file
# --------------------------------------------------------------------------------------------------------------------

TOKEN = "the-control-token"


def _info(project: Path, port: int) -> InstanceInfo:
    return InstanceInfo(4242, port, str(project), "feedfacecafebeef", TOKEN, "0.1.0.dev0", 1.0)


def test_validate_launch_url() -> None:
    info = _info(Path("/p"), 5000)
    good = "http://127.0.0.1:5000/launch#" + "a" * 43
    assert validate_launch_url(info, good) == good
    for bad in (
        "http://127.0.0.1:5000/",
        "http://127.0.0.1:5000/launch",
        "http://127.0.0.1:5000/launch#",
        good + "x",
        good[:-1],
        good.replace("5000", "5001"),
        good.replace("127.0.0.1", "localhost"),
        "http://127.0.0.1:5000/launch#" + "a" * 42 + " ",
        "http://127.0.0.1:5000/other#" + "a" * 43,
        None,
        5,
    ):
        with pytest.raises(ControlError):
            validate_launch_url(info, bad)


@pytest.fixture
def instance_files(project: Path, state_dir: Path) -> Iterator[InstancePaths]:
    paths = InstancePaths.for_project(state_dir, project.resolve())
    lock = InstanceLock(paths.lock)
    assert lock.acquire()
    try:
        yield paths
    finally:
        lock.release()


def test_validate_redirect_file(instance_files: InstancePaths, tmp_path: Path) -> None:
    url = "http://127.0.0.1:5000/launch#" + "b" * 43
    folder = instance_files.info.parent
    good = folder / f"{instance_files.info.stem}-launch-0123456789abcdef.html"
    write_private_file(good, redirect_page(url))
    assert validate_redirect_file(instance_files, url, str(good)) == good

    outside = tmp_path / good.name
    write_private_file(outside, redirect_page(url))
    wrong_name = folder / "studio-0000000000000000-launch-0123456789abcdef.html"
    write_private_file(wrong_name, redirect_page(url))
    wrong_content = folder / f"{instance_files.info.stem}-launch-fedcba9876543210.html"
    write_private_file(wrong_content, redirect_page("https://evil.example/"))
    bad_values: list[object] = [
        None,
        5,
        good.name,  # relative
        str(outside),
        str(wrong_name),
        str(wrong_content),
        str(folder / f"{instance_files.info.stem}-launch-1111111111111111.html"),  # missing
        str(folder / "x" / ".." / good.name),
    ]
    if POSIX:
        shared = folder / f"{instance_files.info.stem}-launch-2222222222222222.html"
        write_private_file(shared, redirect_page(url))
        shared.chmod(0o644)
        link = folder / f"{instance_files.info.stem}-launch-3333333333333333.html"
        link.symlink_to(good)
        bad_values += [str(shared), str(link)]
    for value in bad_values:
        with pytest.raises(ControlError):
            validate_redirect_file(instance_files, url, value)


def test_second_start_refuses_a_foreign_redirect_file(
    project: Path, state_dir: Path, instance_files: InstancePaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even a server that proves the token gets only this instance's private redirect file opened.
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    elsewhere = tmp_path / "evil.html"
    with fake_studio("feedfacecafebeef", lambda nonce: control_proof(TOKEN, nonce)) as fake:
        url = f"http://127.0.0.1:{fake.port}/launch#" + "c" * 43
        elsewhere.write_bytes(redirect_page(url))
        fake.launch_url = url
        fake.redirect = str(elsewhere)
        write_info(instance_files, _info(project, fake.port))
        assert cli.main([str(project), "--state-dir", str(state_dir)], io.StringIO()) == cli.EXIT_ERROR
        assert opened == []
        assert fake.posts[0].body == {"redirect": True}


def test_control_launch_asks_for_a_redirect_file_only_to_open_a_browser(project: Path) -> None:
    with fake_studio("feedfacecafebeef", lambda nonce: control_proof(TOKEN, nonce)) as fake:
        info = _info(project, fake.port)
        control(info, "launch", body={"redirect": False})
        control(info, "shutdown")
        assert [post.body for post in fake.posts] == [{"redirect": False}, {}]


def test_sign_in_helper_matches_the_real_server(
    project: Path, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with serving(project, state_dir, monkeypatch, "--no-browser") as (info, _out, _opened):
        session = sign_in(info)
        assert heartbeat(session) == 204


def test_relative_state_dir_still_opens_the_browser(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server names its redirect files by absolute path, so a second start with a relative --state-dir works.
    monkeypatch.chdir(tmp_path)
    with serving(project, Path("relative-state"), monkeypatch) as (_info, _out, opened):
        assert cli.main([str(project), "--state-dir", "relative-state"]) == cli.EXIT_OK
        assert len(opened) == 2
        assert _file_path(opened[1]).parent.resolve() == (tmp_path / "relative-state").resolve()
