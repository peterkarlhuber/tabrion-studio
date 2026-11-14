"""A plan executor for the tests of the plan framework (STU-XW-3). Studio itself registers none until the Data Editor
(STU-026) brings Tabrion's guided write transaction; this one updates or deletes rows of one table by id, with bound
values and schema-qualified, quoted names, and counts the rows it changed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy
import tabrion

from tabrion_studio.config.write_areas import Area
from tabrion_studio.write.connection import WriteError
from tabrion_studio.write.plan import Plan, PlanContent, Step, Target
from tabrion_studio.write.unlock import AuditedRun


class RowExecutor:
    """``{"schema": "main", "table": "parts", "rows": [{"id": 1, "material": "Iron"}], "delete": false}``."""

    def __init__(self, name: str = "test.rows", area: Area = Area.DATA_EDITOR) -> None:
        self.name = name
        self.area = area
        self.ran: list[str] = []

    def build(self, request: Mapping[str, Any], engine: str) -> PlanContent:
        rows = request.get("rows")
        if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
            raise WriteError("studio.plan_invalid")
        delete = request.get("delete") is True
        schema = request.get("schema", "main")
        table = request.get("table", "parts")
        steps = tuple(
            Step(
                "delete_row" if delete else "update_row",
                0,
                destructive=delete,
                rows=1,
                data={"id": row.get("id"), "material": row.get("material")},
            )
            for row in rows
        )
        return PlanContent((Target(schema, table),), steps, risks=("test.note",))

    def run_step(self, transaction: tabrion.Transaction, plan: Plan, step: Step, run: AuditedRun) -> None:
        target = plan.content.targets[step.target]
        quote = transaction.connection.dialect.identifier_preparer.quote_identifier
        name = f"{quote(target.schema)}.{quote(target.table)}"
        if step.action == "delete_row":
            changed = transaction.execute(
                sqlalchemy.text(f"DELETE FROM {name} WHERE id = :id"),  # noqa: S608 - quoted names
                {"id": step.data["id"]},
            )
            run.count("deleted", changed)
        else:
            changed = transaction.execute(
                sqlalchemy.text(f"UPDATE {name} SET material = :m WHERE id = :id"),  # noqa: S608 - quoted names
                {"m": step.data["material"], "id": step.data["id"]},
            )
            run.count("updated", changed)
        self.ran.append(step.action)
