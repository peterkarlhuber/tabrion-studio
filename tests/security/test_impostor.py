"""Review F-01: another program on the port of an info file never gets the control token or a browser tab.

Scenario: a Studio crashed or stopped, its info file is still there (or is being read in the moment of shutdown), and
another local program now listens on that port. It can answer the health check with the instance ID from the old
file, but it cannot prove that it knows the control token, so the client never sends the token and never opens a URL
from it. And even a server that does prove the token only gets its own ``http://127.0.0.1:<port>/launch#<token>`` and
this instance's private redirect file opened.
"""

from __future__ import annotations

from collections.abc import Iterator
import io
from pathlib import Path

import pytest

from tabrion_studio import cli, instance
from tabrion_studio.instance import InstanceInfo, InstanceLock, InstancePaths, control_proof
from tabrion_studio.paths import write_private_file
from tabrion_studio.security.launch import redirect_page
from tests.support.fake_server import FakeStudio, fake_studio

pytestmark = pytest.mark.security

TOKEN = "the-real-control-token"


def _info(project: Path, port: int) -> InstanceInfo:
    return InstanceInfo(
        pid=4242,
        port=port,
        project=str(project),
        instance_id="feedfacecafebeef",
        control_token=TOKEN,
        version="0.1.0.dev0",
        started_at=1.0,
    )


@pytest.fixture
def held_lock(project: Path, state_dir: Path) -> Iterator[InstancePaths]:
    """The project's lock held as if a Studio ran, so that the CLI talks to the instance named in the info file."""
    paths = InstancePaths.for_project(state_dir, project.resolve())
    lock = InstanceLock(paths.lock)
    assert lock.acquire()
    try:
        yield paths
    finally:
        lock.release()


def _wrong_proof(nonce: str) -> str:
    return control_proof("a-guessed-token", nonce)


def _replayed_proof(nonce: str) -> str:
    return control_proof(TOKEN, "0" * 32)  # a proof seen earlier, for another nonce


IMPOSTORS = {
    "wrong proof": _wrong_proof,
    "replayed proof": _replayed_proof,
    "no proof endpoint": lambda nonce: None,
    "echoes the nonce": lambda nonce: nonce,
}


@pytest.mark.parametrize("prove", IMPOSTORS.values(), ids=IMPOSTORS.keys())
def test_control_never_sends_the_token_to_an_impostor(project: Path, prove: object) -> None:
    with fake_studio("feedfacecafebeef", prove, launch_url="https://evil.example/") as fake:  # type: ignore[arg-type]
        info = _info(project, fake.port)
        for action in ("launch", "shutdown"):
            with pytest.raises(instance.ImpostorError):
                instance.control(info, action, timeout=2)
        assert fake.posts == []
        assert not fake.authorization_seen
        assert TOKEN not in "".join(r.path for r in fake.seen)


def _assert_impostor_refused(fake: FakeStudio, opened: list[str]) -> None:
    assert opened == []
    assert fake.posts == []
    assert not fake.authorization_seen


def test_second_start_refuses_an_impostor(
    project: Path,
    state_dir: Path,
    held_lock: InstancePaths,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    with fake_studio("feedfacecafebeef", _wrong_proof, launch_url="https://evil.example/") as fake:
        instance.write_info(held_lock, _info(project, fake.port))
        assert cli.main([str(project), "--state-dir", str(state_dir)], io.StringIO()) == cli.EXIT_ERROR
        _assert_impostor_refused(fake, opened)
    assert "not the Tabrion Studio of this folder" in capsys.readouterr().err


def test_stop_refuses_an_impostor(
    project: Path, state_dir: Path, held_lock: InstancePaths, capsys: pytest.CaptureFixture[str]
) -> None:
    with fake_studio("feedfacecafebeef", _wrong_proof) as fake:
        instance.write_info(held_lock, _info(project, fake.port))
        assert cli.main([str(project), "--stop", "--state-dir", str(state_dir)], io.StringIO()) == cli.EXIT_ERROR
        _assert_impostor_refused(fake, [])
    assert "stop request failed" in capsys.readouterr().err


GOOD_TOKEN = "t" * 43


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",
        "http://localhost:{port}/launch#" + GOOD_TOKEN,
        "http://[::1]:{port}/launch#" + GOOD_TOKEN,
        "http://127.0.0.1:{other}/launch#" + GOOD_TOKEN,
        "http://127.0.0.1:{port}.evil.example/launch#" + GOOD_TOKEN,
        "http://127.0.0.1:{port}@evil.example/launch#" + GOOD_TOKEN,
        "javascript:alert(1)//http://127.0.0.1:{port}/launch#" + GOOD_TOKEN,
        "http://127.0.0.1:{port}/\\\\evil.example",
        "http://127.0.0.1:{port}/launch#" + GOOD_TOKEN + "\r\nX: y",
        "http://127.0.0.1:{port}/",
        "",
        None,
    ],
)
def test_launch_url_must_point_to_the_instance(
    project: Path,
    state_dir: Path,
    held_lock: InstancePaths,
    monkeypatch: pytest.MonkeyPatch,
    url: str | None,
) -> None:
    # Even a server that proves the token (here: one that read the old info file) only gets its own origin opened.
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    with fake_studio("feedfacecafebeef", lambda nonce: control_proof(TOKEN, nonce)) as fake:
        fake.launch_url = None if url is None else url.format(port=fake.port, other=fake.port + 1)  # type: ignore[assignment]
        instance.write_info(held_lock, _info(project, fake.port))
        assert cli.main([str(project), "--state-dir", str(state_dir)], io.StringIO()) == cli.EXIT_ERROR
        assert opened == []
        assert len(fake.posts) == 1  # the token went to a server that proved it knows it


def test_a_valid_launch_is_opened_through_its_redirect_file(
    project: Path, state_dir: Path, held_lock: InstancePaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    with fake_studio("feedfacecafebeef", lambda nonce: control_proof(TOKEN, nonce)) as fake:
        fake.launch_url = f"http://127.0.0.1:{fake.port}/launch#{GOOD_TOKEN}"
        redirect = held_lock.info.parent / f"{held_lock.info.stem}-launch-0123456789abcdef.html"
        write_private_file(redirect, redirect_page(fake.launch_url))
        fake.redirect = str(redirect)
        instance.write_info(held_lock, _info(project, fake.port))
        assert cli.main([str(project), "--state-dir", str(state_dir)], io.StringIO()) == cli.EXIT_OK
        assert opened == [redirect.resolve().as_uri()]
        assert GOOD_TOKEN not in opened[0]


def test_proof_and_token_travel_on_one_connection(project: Path) -> None:
    # Review R-4: the token is sent only on the connection that delivered the proof.
    with fake_studio("feedfacecafebeef", lambda nonce: control_proof(TOKEN, nonce)) as fake:
        info = _info(project, fake.port)
        instance.control(info, "shutdown", timeout=2)
        proof, post = fake.seen
        assert proof.path.startswith("/api/control/proof") and post.method == "POST"
        assert proof.connection == post.connection
        assert "authorization" not in {k.lower() for k in proof.headers}


def test_server_that_closes_after_the_proof_never_gets_the_token(project: Path) -> None:
    # Review R-4: the client never opens a second connection for the token; a closed connection ends the call.
    with fake_studio("feedfacecafebeef", lambda nonce: control_proof(TOKEN, nonce), keep_alive=False) as fake:
        info = _info(project, fake.port)
        with pytest.raises(instance.ControlError, match="closed the connection"):
            instance.control(info, "launch", timeout=2)
        assert fake.posts == []
        assert not fake.authorization_seen


def test_stale_info_file_is_removed_when_the_lock_is_free(
    project: Path, state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Nobody holds the lock: the info file is left from a crash. The next start owns it and removes it at once.
    paths = InstancePaths.for_project(state_dir, project.resolve())
    with fake_studio("feedfacecafebeef", _wrong_proof) as fake:
        instance.write_info(paths, _info(project, fake.port))
        assert cli.main([str(project), "--stop", "--state-dir", str(state_dir)]) == cli.EXIT_OK
        assert fake.seen == []
    assert not paths.info.exists()
    assert "No Tabrion Studio is running" in capsys.readouterr().out
