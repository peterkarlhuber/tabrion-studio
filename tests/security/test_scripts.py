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
import threading
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
        # Windows device names, with or without an extension, in any case, also as a folder (F-2).
        "nul.sql",
        "NUL.SQL",
        "con.sql",
        "Aux.sql",
        "prn.backup.sql",
        "com1.sql",
        "COM9.sql",
        "lpt0.sql",
        "com\u00b9.sql",
        "lpt\u00b2.sql",
        "conin$.sql",
        "CONOUT$.sql",
        "nul /a.sql",
        "con/a.sql",
        "sql/aux/a.sql",
        # 8.3 short names and names Win32 trims (F-3).
        "GIT~1/hidden.sql",
        "TABRIO~1/x.sql",
        "sql~2.sql",
        "sql./a.sql",
        "sql /a.sql",
        "a.sql.",
        # Format characters: bidi overrides and isolates, zero-width characters (F-9).
        "evil\u202eloc.sql",
        "a\u2066b.sql",
        "a\u200bb.sql",
        "a\u200fb.sql",
        "\ufeffa.sql",
        # Characters that look blank but are not format characters (D-5 of the delta review).
        "a\u3164b.sql",
        "\u3164.sql",
        "a\u115fb.sql",
        "a\u1160b.sql",
        "a\uffa0b.sql",
        "a\u2800b.sql",
        "a\u034fb.sql",
        "a\u17b4b.sql",
        "a\u00a0b.sql",
        "a\u2007b.sql",
        "sql\u3164/a.sql",
        # E-1: a ZWNJ or ZWJ out of place (start, end, doubled, next to a blank, a dot or a digit, between symbols that
        # are no emoji sequence), U+3000 leading or trailing like U+0020.
        "\u200ca.sql",
        "a\u200c.sql",
        "a\u200d.sql",
        "\u200da.sql",
        "a\u200c\u200cb.sql",
        "a\u200d\u200db.sql",
        "a\u200c b.sql",
        "a \u200db.sql",
        "a\u200c.b.sql",
        "1\u200c2.sql",
        "\u2606\u200c\u2606.sql",
        "sql\u200c/a.sql",
        "\u3000a.sql",
        "a.sql\u3000",
        "sql\u3000/a.sql",
        "\u3000/a.sql",
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
        with pytest.raises(ScriptError, match=r"studio\.script_link"):
            scripts.read(path)
        with pytest.raises(ScriptError, match=r"studio\.script_link"):
            scripts.save(path, "SELECT 'replaced'", None, overwrite=True)
    assert (outside / "secret.sql").read_bytes() == b"SELECT 'outside';"
    assert not (outside / "new.sql").exists()
    assert (project / "top.sql").read_bytes() == b"SELECT 3;\n"


def test_names_like_devices_are_fine_as_part_of_a_name(project: Path) -> None:
    scripts = Scripts(project)
    # Names with a plain space, accents (also decomposed, as macOS stores them) and other scripts stay valid.
    for path in (
        "console.sql",
        "nullable.sql",
        "com10.sql",
        "lpt.sql",
        "my_aux.sql",
        "a~b.sql",
        "a b.sql",
        "caf\u00e9.sql",
        "re\u0301sume\u0301.sql",
        "\ud55c\uae00.sql",
        # E-1: Persian with a ZWNJ, an Indic conjunct and emoji sequences with a ZWJ, Japanese with U+3000.
        "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645.sql",
        "\u0915\u094d\u200d\u0937.sql",
        "\U0001f468\u200d\U0001f4bb.sql",
        "\U0001f468\U0001f3fd\u200d\U0001f4bb.sql",
        "\u2764\ufe0f\u200d\U0001f525.sql",
        "\u58f2\u4e0a\u3000\u96c6\u8a08.sql",
    ):
        scripts.save(path, "SELECT 1", None)
        assert scripts.read(path)["content"] == "SELECT 1"


@pytest.mark.skipif(not POSIX, reason="hard links need no privilege on POSIX")
def test_a_hard_linked_script_is_refused(project: Path, tmp_path: Path) -> None:
    """F-4: a hard link inside the project names a file whose other name may lie outside."""
    outside = tmp_path / "outside.sql"
    outside.write_bytes(b"SELECT 'outside';")
    os.link(outside, project / "linked.sql")
    scripts = Scripts(project)
    with pytest.raises(ScriptError, match=r"studio\.script_link"):
        scripts.read("linked.sql")
    with pytest.raises(ScriptError, match=r"studio\.script_link"):
        scripts.save("linked.sql", "SELECT 'mine'", None, overwrite=True)
    assert outside.read_bytes() == b"SELECT 'outside';"


@pytest.mark.skipif(not POSIX, reason="descriptor-anchored access is POSIX")
def test_a_folder_swapped_for_a_link_after_the_check_is_not_followed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-4 (07 TM-04): the path is checked, then a folder on it becomes a link; read and save still stay inside."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.sql").write_bytes(b"SELECT 'outside';")
    checked = Scripts._target

    def swap_after_check(self: Scripts, path: object) -> Any:
        result = checked(self, path)
        if (project / "sql").is_dir() and not (project / "sql").is_symlink():
            (project / "sql").rename(project / "moved")
            (project / "sql").symlink_to(outside)
        return result

    monkeypatch.setattr(Scripts, "_target", swap_after_check)
    with pytest.raises(ScriptError, match=r"studio\.script_link"):
        Scripts(project).read("sql/a.sql")
    (project / "sql").unlink()
    (project / "moved").rename(project / "sql")
    with pytest.raises(ScriptError, match=r"studio\.script_link"):
        Scripts(project).save("sql/a.sql", "SELECT 'replaced'", None, overwrite=True)
    assert (outside / "a.sql").read_bytes() == b"SELECT 'outside';"
    assert sorted(path.name for path in outside.iterdir()) == ["a.sql"]


def test_a_byte_order_mark_is_kept_on_save(project: Path) -> None:
    """F-5: the page never sees the mark; saving over the file keeps it, and a new file gets none."""
    (project / "bom.sql").write_bytes(b"\xef\xbb\xbfSELECT 1;\r\n")
    scripts = Scripts(project)
    read = scripts.read("bom.sql")
    assert read["content"] == "SELECT 1;\r\n"
    saved = scripts.save("bom.sql", read["content"], read["digest"])
    assert (project / "bom.sql").read_bytes() == b"\xef\xbb\xbfSELECT 1;\r\n"
    assert saved["digest"] == read["digest"]
    scripts.save("plain.sql", "SELECT 2;", None)
    assert (project / "plain.sql").read_bytes() == b"SELECT 2;"


def test_mixed_line_endings_are_kept_byte_exact(project: Path) -> None:
    mixed = b"SELECT 1;\r\nSELECT 2;\nSELECT 3;\rSELECT 4;"
    (project / "mixed.sql").write_bytes(mixed)
    scripts = Scripts(project)
    read = scripts.read("mixed.sql")
    assert read["content"].encode() == mixed
    scripts.save("mixed.sql", read["content"], read["digest"])
    assert (project / "mixed.sql").read_bytes() == mixed


def test_two_saves_of_one_script_never_both_pass_the_check(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-5: the digest check and the replace run under one lock; the second save sees the first one's file."""
    scripts = Scripts(project)
    read = scripts.read("sql/a.sql")
    entered = threading.Event()
    release = threading.Event()
    original = scripts_module._write_atomic

    def slow_write(*args: Any) -> None:
        entered.set()
        assert release.wait(5)
        original(*args)

    monkeypatch.setattr(scripts_module, "_write_atomic", slow_write)
    results: list[str] = []

    def save(text: str) -> None:
        try:
            scripts.save("sql/a.sql", text, read["digest"])
            results.append("saved")
        except ScriptError as exc:
            results.append(exc.code)

    first = threading.Thread(target=save, args=("SELECT 'first';",))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=save, args=("SELECT 'second';",))
    second.start()
    second.join(0.2)
    assert second.is_alive()  # waiting for the lock, not past the check
    release.set()
    first.join(5)
    second.join(5)
    assert sorted(results) == ["saved", "studio.script_stale"]
    assert (project / "sql" / "a.sql").read_bytes() == b"SELECT 'first';"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PermissionError(13, "denied"), "studio.script_permission_denied"),
        (OSError(30, "read-only file system"), "studio.script_permission_denied"),
        (OSError(28, "no space"), "studio.script_disk_full"),
        (OSError(5, "input/output error"), "studio.script_write_failed"),
    ],
)
def test_file_system_refusals_have_their_own_problem(
    project: Path, monkeypatch: pytest.MonkeyPatch, error: OSError, code: str
) -> None:
    """F-7: no refusal of the file system becomes studio.internal_error; the script on disk is unchanged."""

    def refuse(*args: Any) -> None:
        raise error

    monkeypatch.setattr(scripts_module, "_write_atomic", refuse)
    with pytest.raises(ScriptError) as refused:
        Scripts(project).save("sql/a.sql", "SELECT 'x'", None, overwrite=True)
    assert refused.value.code == code
    assert (project / "sql" / "a.sql").read_bytes() == b"SELECT 1;\n"


def test_windows_lock_and_disk_full_errors() -> None:
    class WinError(OSError):
        def __init__(self, winerror: int) -> None:
            super().__init__(13, "windows")
            self.winerror = winerror

    assert scripts_module._os_code(WinError(32)) == "studio.script_locked"
    assert scripts_module._os_code(WinError(33)) == "studio.script_locked"
    assert scripts_module._os_code(WinError(112)) == "studio.script_disk_full"
    assert scripts_module._os_code(WinError(5)) == "studio.script_permission_denied"


@pytest.mark.skipif(not POSIX, reason="file modes")
def test_a_folder_studio_may_not_write_is_a_clear_refusal(project: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root may write anywhere")
    (project / "sql").chmod(0o500)
    try:
        with pytest.raises(ScriptError, match=r"studio\.script_permission_denied"):
            Scripts(project).save("sql/new.sql", "SELECT 1", None)
    finally:
        (project / "sql").chmod(0o700)


@pytest.mark.skipif(not POSIX, reason="symbolic links")
def test_the_listing_counts_the_links_it_skips(project: Path, tmp_path: Path) -> None:
    (project / "linked").symlink_to(project / "sql")
    (project / "link.sql").symlink_to(project / "top.sql")
    (project / "GIT~1").mkdir()
    (project / "GIT~1" / "x.sql").write_bytes(b"SELECT 1")
    listed = Scripts(project).list()
    assert listed["skipped_links"] == 2
    assert [item["path"] for item in listed["scripts"]] == ["sql/a.sql", "sql/reports/b.SQL", "top.sql"]


def test_the_listing_counts_the_names_it_refuses(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """E-1: a folder or ``.sql`` file with a name Studio refuses is left out and counted, so the page can say why."""
    (project / "a.sql.").mkdir()  # a folder whose name ends in a dot (not on Windows: Win32 drops the dot)
    (project / "x\u200by.sql").write_bytes(b"SELECT 1")  # a zero-width space
    (project / "sql" / "com1.sql").write_bytes(b"SELECT 1")  # a device name
    (project / "notes\u200b.txt").write_bytes(b"not a script")  # not counted: not a script
    (project / ".hidden.sql").write_bytes(b"SELECT 1")  # not counted: hidden
    (project / "\u0645\u06cc\u200c\u062e.sql").write_bytes(b"SELECT 1")  # listed: a ZWNJ between letters
    listed = Scripts(project).list()
    expected = 2 if sys.platform == "win32" else 3
    assert listed["skipped_invalid"] == expected
    assert [item["path"] for item in listed["scripts"]] == [
        "sql/a.sql",
        "sql/reports/b.SQL",
        "top.sql",
        "\u0645\u06cc\u200c\u062e.sql",
    ]
    # A path longer than the limit is left out and counted too.
    monkeypatch.setattr(scripts_module, "MAX_PATH_CHARS", 12)
    listed = Scripts(project).list()
    assert "sql/reports/b.SQL" not in [item["path"] for item in listed["scripts"]]
    assert listed["skipped_invalid"] == expected + 1


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
