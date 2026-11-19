"""STU-XSD-1: the Schema Designer's graphical mode has no route of its own and the server checks every edit, whichever
view sent it (G-S3 is not granted; GS3-23, TX-31; STU-ADR-0007 sections 2 and 3).

1. **No route of its own.** Every Schema Designer route is one of the table view's (``WRITE_ROUTES``); none names a
   canvas, a diagram, a layout or a position. The page's diagram (``SchemaCanvas.tsx``, ``schemaCanvasModel.ts``)
   never calls the server: it takes the draft and hands a chosen table or a drawn key to the table view's forms
   (static check of the two files).
2. **No view marker.** A request cannot tell the server which view sent it: a ``view``, ``source``, ``mode`` or
   ``canvas`` key is refused before the body is read, in the request and in the edit, so there is nothing a check
   could depend on.
3. **The same checks.** A foreign key as the diagram draws it (one column to one column, filled into the "New
   constraint" form) is refused for the same reasons as one typed in the table view: a column or a table that is not
   there, a key to a dropped table; each refusal is a fixed catalog problem and leaves the draft as it was. A valid
   key is the same edit operation from either view and gives the same draft.

The browser part (axe, the table alternative, a drawn line in Chromium, Firefox and WebKit) is in
``frontend/e2e/schema-designer-backend.spec.ts`` and ``SchemaCanvas.test.tsx``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
from typing import Any

import pytest

from tabrion_studio.api.write import WRITE_ROUTES
from tabrion_studio.write.connection import defensive_available
from tests.security.test_schema_designer_draft import EDITS, ROOT, SCHEMA_DESIGNER, Designer, _refused
from tests.support.write import WriteEnv

pytestmark = pytest.mark.security

FRONTEND = ROOT / "frontend" / "src" / "views" / "write"
#: The table view's routes (STU-CR-4, STU-CR-5, STU-XSD-2, STU-XSD-3): the only ones of the area.
TABLE_VIEW_ROUTES = frozenset(
    {
        "/api/write/schema-designer/draft",
        "/api/write/schema-designer/draft/view",
        "/api/write/schema-designer/draft/edits",
        "/api/write/schema-designer/draft/preview",
        "/api/write/schema-designer/plan/confirm",
        "/api/write/schema-designer/plan/run",
        "/api/write/schema-designer/compare",
        "/api/write/schema-designer/revision",
    }
)


@pytest.fixture
def designer(tmp_path: Path, static_dir: Path) -> Iterator[Designer]:
    if not defensive_available():
        pytest.skip("Python 3.11 refuses a SQLite write connection")
    env = WriteEnv(tmp_path, static_dir, SCHEMA_DESIGNER)
    try:
        env.create_read()
        connection_id = env.open()
        assert env.create_write().status_code == 201
        assert env.unlock(connection_id).status_code == 200
        yield Designer(env, connection_id)
    finally:
        env.close()


# --- 1. no route of its own ----------------------------------------------------------------------------------------


def test_the_graphical_mode_has_no_route_of_its_own() -> None:
    """TX-31: every Schema Designer route is one the table view uses; none is the diagram's."""
    paths = {path for path, _ in WRITE_ROUTES if path.startswith("/api/write/schema-designer")}
    assert paths, "the Schema Designer routes are missing from WRITE_ROUTES"
    assert paths <= TABLE_VIEW_ROUTES
    for path in paths:
        assert not re.search(r"canvas|diagram|graph|layout|position|\ber\b", path), path


def test_the_diagram_never_calls_the_server() -> None:
    """TX-31: the canvas and its model take the draft and hand edits to the table view's forms; they send nothing."""
    for name in ("SchemaCanvas.tsx", "schemaCanvasModel.ts"):
        text = (FRONTEND / name).read_text(encoding="utf-8")
        assert "fetch" not in text, name
        assert not re.search(r"['\"`]/api/", text), name
        # Only types come from the API modules: no request function (editDraft, previewDraft, ...) is imported.
        imports = re.findall(r"^import\b[^;]*?from '\.\./\.\./api/[^']+';", text, flags=re.MULTILINE | re.DOTALL)
        assert imports, name
        for statement in imports:
            assert statement.startswith("import type "), (name, statement)
        assert "editDraft" not in text and "send(" not in text, name


# --- 2. no view marker ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["view", "source", "mode", "canvas"])
def test_a_request_cannot_say_which_view_sent_it(designer: Designer, key: str) -> None:
    """GS3-23: the edit route takes exactly its keys; a view marker is refused in the request and in the edit."""
    assert designer.start().status_code == 201
    edit = {"op": "add_column", "table": "parts", "column": {"name": "x", "type": "TEXT"}}
    body = {"connection_id": designer.connection_id, "revision": 0, "edit": edit, key: "diagram"}
    _refused(designer.env.send("POST", EDITS, body), 400, "access.bad_request")
    _refused(
        designer.edit({**edit, key: "diagram"}), 400, "studio.schema_edit_invalid", [("edit.op", "edit.unknown_key")]
    )
    assert designer.view().json()["revision"] == 0


# --- 3. the same checks --------------------------------------------------------------------------------------------


def _drawn(table: str, column: str, referred_table: str, referred_column: str) -> dict[str, Any]:
    """A foreign key as the diagram fills it into the form: one column to one column, no name, no action."""
    return {
        "op": "add_constraint",
        "table": table,
        "constraint": {
            "kind": "foreign_key",
            "columns": [column],
            "referred_table": referred_table,
            "referred_columns": [referred_column],
        },
    }


def _with_orders(designer: Designer) -> None:
    """The draft with a new table ``orders (id, part_id)`` beside ``parts``, and a table ``gone`` dropped again."""
    assert designer.start().status_code == 201
    designer.ok(
        {
            "op": "add_table",
            "table": "orders",
            "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
            "primary_key": ["id"],
        }
    )
    designer.ok({"op": "add_column", "table": "orders", "column": {"name": "part_id", "type": "INTEGER"}})
    designer.ok(
        {
            "op": "add_table",
            "table": "gone",
            "columns": [{"name": "id", "type": "INTEGER", "nullable": False}],
            "primary_key": ["id"],
        }
    )
    designer.ok({"op": "drop_table", "table": "gone"})


INVALID_DRAWN: list[tuple[str, dict[str, Any], tuple[str, str]]] = [
    ("column not there", _drawn("orders", "nope", "parts", "id"), ("edit.constraint_columns", "edit.column_missing")),
    (
        "referred column not there",
        _drawn("orders", "part_id", "parts", "nope"),
        ("edit.referred_columns", "edit.column_missing"),
    ),
    (
        "referred table not there",
        _drawn("orders", "part_id", "nope", "id"),
        ("edit.referred_table", "edit.table_missing"),
    ),
    ("table not there", _drawn("nope", "part_id", "parts", "id"), ("edit.table", "edit.table_missing")),
    (
        "referred table dropped",
        _drawn("orders", "part_id", "gone", "id"),
        ("edit.referred_table", "edit.table_missing"),
    ),
]


@pytest.mark.parametrize(
    ("edit", "issue"), [case[1:] for case in INVALID_DRAWN], ids=[case[0] for case in INVALID_DRAWN]
)
def test_a_drawn_key_is_checked_like_a_typed_one(
    designer: Designer, edit: dict[str, Any], issue: tuple[str, str]
) -> None:
    """GS3-23: the server refuses each invalid key the diagram can propose, with the table view's catalog problem."""
    _with_orders(designer)
    before = designer.view().json()
    response = designer.edit(edit)
    _refused(response, 400, "studio.schema_edit_invalid", [issue])
    # No name of the request comes back, and the draft is as it was.
    assert "nope" not in response.text and "gone" not in response.text
    assert designer.view().json() == before


def test_a_valid_drawn_key_is_the_table_views_edit(designer: Designer) -> None:
    """GS3-23: the key the diagram fills in is the table view's ``add_constraint``, planned like every other edit."""
    _with_orders(designer)
    after = designer.ok(_drawn("orders", "part_id", "parts", "id"))
    orders = next(table for table in after["tables"] if table["name"] == "orders")
    assert [(key["columns"], key["referred_table"], key["referred_columns"]) for key in orders["foreign_keys"]] == [
        (["part_id"], "parts", ["id"])
    ]
    # It goes on through the same plan preview as every other edit (nothing runs).
    preview = designer.preview()
    assert preview.status_code == 200, preview.text
    assert any(step["target"].endswith("orders") for step in preview.json()["steps"]), preview.text
