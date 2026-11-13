"""STU-020: SQL scripts in the project folder (03 section 2 C "nur ``.sql``", threat T-08).

A script path from the page is judged as text and then on the file system: only ``.sql`` files inside the project,
never through a link, a hidden folder, ``..``, a drive or a network path. Saving is atomic and bound to the bytes the
page read (``studio.script_stale``, ``studio.script_exists``).
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
from typing import Any

import pytest

from tabrion_studio.services import scripts as scripts_module
from tabrion_studio.services.scripts import MAX_SCRIPT_BYTES, ScriptError, Scripts, script_digest
from tests.support.access import ORIGIN, make_studio

pytestmark = pytest.mark.security

# Every file here is written and read as bytes: a text-mode write would turn "\n" into "\r\n" on Windows, and the
# service is byte-exact on every system (test_line_endings_are_kept_byte_exact).

POSIX = sys.platform != "win32"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    (folder / "sql" / "reports").mkdir(parents=True)
    (folder / "sql" / "a.sql").write_bytes(b"SELECT 1;\n")
    (folder / "sql" / "reports" / "b.SQL").write_bytes(b"SELECT 2;\n")
    (folder / "top.sql").write_bytes(b"SELECT 3;\n")
    (folder / "notes.txt").write_bytes(b"not a script")
    (folder / ".git").mkdir()
    (folder / ".git" / "hidden.sql").write_bytes(b"SELECT 'hidden';")
    (folder / "node_modules" / "pkg").mkdir(parents=True)
    (folder / "node_modules" / "pkg" / "x.sql").write_bytes(b"SELECT 'tool';")
    return folder


def test_list_finds_the_project_scripts_only(project: Path) -> None:
    listed = Scripts(project).list()
    assert [item["path"] for item in listed["scripts"]] == ["sql/a.sql", "sql/reports/b.SQL", "top.sql"]
    assert listed["truncated"] is False
    assert listed["scripts"][0]["size"] == len("SELECT 1;\n")


@pytest.mark.skipif(not POSIX, reason="symbolic links")
def test_list_follows_no_link(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.sql").write_bytes(b"SELECT 'outside';")
    (project / "linked").symlink_to(outside)
    (project / "link.sql").symlink_to(outside / "secret.sql")
    paths = [item["path"] for item in Scripts(project).list()["scripts"]]
    assert "linked/secret.sql" not in paths
    assert "link.sql" not in paths


def test_list_stops_at_its_limits(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scripts_module, "MAX_SCRIPTS", 2)
    listed = Scripts(project).list()
    assert listed["truncated"] is True
    assert len(listed["scripts"]) == 2
    monkeypatch.setattr(scripts_module, "MAX_SCRIPTS", 1000)
    monkeypatch.setattr(scripts_module, "MAX_DEPTH", 2)
    assert "sql/reports/b.SQL" not in [item["path"] for item in Scripts(project).list()["scripts"]]


def test_read_a_script(project: Path) -> None:
    (project / "bom.sql").write_bytes(b"\xef\xbb\xbfSELECT 'bom';")
    read = Scripts(project).read("sql/a.sql")
    assert read == {
        "path": "sql/a.sql",
        "content": "SELECT 1;\n",
        "digest": script_digest(b"SELECT 1;\n"),
    }
    assert Scripts(project).read("bom.sql")["content"] == "SELECT 'bom';"


@pytest.mark.parametrize(
    "path",
    [
        "",
        "../x.sql",
        "sql/../../x.sql",
        "/etc/x.sql",
        "sql\\a.sql",
        "C:x.sql",
        "C:/x.sql",
        "//host/share/x.sql",
        "/net/host/x.sql",
        ".git/hidden.sql",
        "sql/./a.sql",
        "sql//a.sql",
        "notes.txt",
        ".sql",
        "sql/",
        "a\x00.sql",
        "a\n.sql",
        " a.sql",
        "a.sql ",
        "x" * 600 + ".sql",
        "sql/a.sql:stream",
    ],
)
def test_refused_paths(project: Path, path: str) -> None:
    scripts = Scripts(project)
    with pytest.raises(ScriptError) as refused:
        scripts.read(path)
    assert refused.value.code == "studio.script_invalid", path
    with pytest.raises(ScriptError) as refused:
        scripts.save(path, "SELECT 1", None)
    assert refused.value.code == "studio.script_invalid", path


def test_read_refusals(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = Scripts(project)
    with pytest.raises(ScriptError, match=r"studio\.script_not_found"):
        scripts.read("missing.sql")
    (project / "latin1.sql").write_bytes("SELECT 'é'".encode("latin-1"))
    with pytest.raises(ScriptError, match=r"studio\.script_unreadable"):
        scripts.read("latin1.sql")
    (project / "big.sql").write_bytes(b"-" * (MAX_SCRIPT_BYTES + 1))
    with pytest.raises(ScriptError, match=r"studio\.script_too_large"):
        scripts.read("big.sql")
    (project / "folder.sql").mkdir()
    with pytest.raises(ScriptError, match=r"studio\.script_invalid"):
        scripts.read("folder.sql")


@pytest.mark.skipif(not POSIX, reason="symbolic links")
def test_no_link_is_followed_on_the_way(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.sql").write_bytes(b"SELECT 'outside';")
    (project / "out.sql").symlink_to(outside / "secret.sql")
    (project / "inside.sql").symlink_to(project / "top.sql")
    (project / "outdir").symlink_to(outside)
    (project / "indir").symlink_to(project / "sql")
    scripts = Scripts(project)
    for path in ("out.sql", "inside.sql", "outdir/secret.sql", "indir/a.sql", "outdir/new.sql"):
        with pytest.raises(ScriptError, match=r"studio\.script_invalid"):
            scripts.read(path)
        with pytest.raises(ScriptError, match=r"studio\.script_invalid"):
            scripts.save(path, "SELECT 'replaced'", None, overwrite=True)
    assert (outside / "secret.sql").read_bytes() == b"SELECT 'outside';"
    assert not (outside / "new.sql").exists()
    assert (project / "top.sql").read_bytes() == b"SELECT 3;\n"


@pytest.mark.skipif(not POSIX, reason="file modes")
def test_save_new_and_replace(project: Path) -> None:
    scripts = Scripts(project)
    saved = scripts.save("sql/new.sql", "SELECT 'new';\n", None)
    assert saved == {"path": "sql/new.sql", "digest": script_digest(b"SELECT 'new';\n")}
    assert (project / "sql" / "new.sql").read_bytes() == b"SELECT 'new';\n"
    umask = os.umask(0)
    os.umask(umask)
    assert stat.S_IMODE((project / "sql" / "new.sql").stat().st_mode) == 0o666 & ~umask
    (project / "sql" / "new.sql").chmod(0o640)
    again = scripts.save("sql/new.sql", "SELECT 'two';\n", saved["digest"])
    assert stat.S_IMODE((project / "sql" / "new.sql").stat().st_mode) == 0o640
    assert again["digest"] == script_digest(b"SELECT 'two';\n")
    assert [p.name for p in (project / "sql").iterdir() if p.name.startswith(".")] == []


def test_line_endings_are_kept_byte_exact(project: Path) -> None:
    """Reading and saving never translate line endings: a CRLF script reads as CRLF, its digest is that of its bytes,
    and what the page saves is written as it is, on every system."""
    crlf = b"SELECT 1;\r\nSELECT 2;\r\n"
    (project / "crlf.sql").write_bytes(crlf)
    scripts = Scripts(project)
    read = scripts.read("crlf.sql")
    assert read["content"] == "SELECT 1;\r\nSELECT 2;\r\n"
    assert read["digest"] == script_digest(crlf)
    sizes = {item["path"]: item["size"] for item in Scripts(project).list()["scripts"]}
    assert sizes["crlf.sql"] == len(crlf)
    saved = scripts.save("crlf.sql", "SELECT 3;\r\n", read["digest"])
    assert (project / "crlf.sql").read_bytes() == b"SELECT 3;\r\n"
    assert saved["digest"] == script_digest(b"SELECT 3;\r\n")
    scripts.save("lf.sql", "SELECT 4;\n", None)
    assert (project / "lf.sql").read_bytes() == b"SELECT 4;\n"


def test_save_is_bound_to_what_the_page_read(project: Path) -> None:
    scripts = Scripts(project)
    read = scripts.read("sql/a.sql")
    (project / "sql" / "a.sql").write_bytes(b"SELECT 'changed on disk';")
    with pytest.raises(ScriptError, match=r"studio\.script_stale"):
        scripts.save("sql/a.sql", "SELECT 'mine';", read["digest"])
    with pytest.raises(ScriptError, match=r"studio\.script_exists"):
        scripts.save("sql/a.sql", "SELECT 'mine';", None)
    with pytest.raises(ScriptError, match=r"studio\.script_stale"):
        scripts.save("sql/gone.sql", "SELECT 'mine';", read["digest"])
    scripts.save("sql/a.sql", "SELECT 'mine';", None, overwrite=True)
    assert (project / "sql" / "a.sql").read_bytes() == b"SELECT 'mine';"


def test_save_refusals(project: Path) -> None:
    scripts = Scripts(project)
    with pytest.raises(ScriptError, match=r"studio\.script_folder_missing"):
        scripts.save("nowhere/x.sql", "SELECT 1", None)
    assert not (project / "nowhere").exists()
    with pytest.raises(ScriptError, match=r"studio\.script_too_large"):
        scripts.save("big.sql", "-" * (MAX_SCRIPT_BYTES + 1), None)
    with pytest.raises(ScriptError, match=r"studio\.script_unreadable"):
        scripts.save("bad.sql", "SELECT '\ud800'", None)
    for content, digest, overwrite in ((1, None, False), ("x", 1, False), ("x", None, "yes")):
        with pytest.raises(ScriptError, match=r"studio\.invalid_request"):
            scripts.save("x.sql", content, digest, overwrite=overwrite)


def test_script_routes(static_dir: Path, project: Path) -> None:
    studio = make_studio(static_dir, scripts=Scripts(project))
    headers: dict[str, Any] = {**studio.sign_in(), "Origin": ORIGIN}
    listed = studio.client.get("/api/scripts", headers=headers)
    assert [item["path"] for item in listed.json()["scripts"]] == ["sql/a.sql", "sql/reports/b.SQL", "top.sql"]
    read = studio.client.get("/api/scripts/file", params={"path": "sql/a.sql"}, headers=headers).json()
    assert read["content"] == "SELECT 1;\n"
    saved = studio.client.put(
        "/api/scripts/file",
        json={"path": "sql/a.sql", "content": "SELECT 10;", "digest": read["digest"]},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    stale = studio.client.put(
        "/api/scripts/file",
        json={"path": "sql/a.sql", "content": "SELECT 11;", "digest": read["digest"]},
        headers=headers,
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "studio.script_stale")
    refused = studio.client.get("/api/scripts/file", params={"path": "../x.sql"}, headers=headers)
    assert (refused.status_code, refused.json()["code"]) == (400, "studio.script_invalid")
    assert "x.sql" not in refused.text
    # A script larger than the 16 KB of an ordinary form passes the body cap in front of the routes.
    large = "SELECT 1;\n" * 20_000
    saved_large = studio.client.put(
        "/api/scripts/file", json={"path": "large.sql", "content": large, "digest": None}, headers=headers
    )
    assert saved_large.status_code == 200, saved_large.text
    assert (project / "large.sql").read_bytes() == large.encode()
    unknown = studio.client.put(
        "/api/scripts/file", json={"path": "a.sql", "content": "", "digest": None, "mode": 1}, headers=headers
    )
    assert unknown.json()["code"] == "access.bad_request"
