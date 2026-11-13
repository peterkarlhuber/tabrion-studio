# Explorer

The **Explorer** shows the tables, views and other objects of a connection's database, with their details. Studio
only reads here: there is nothing that changes the database.

## Opening a database

1. Choose the **Connection profile** and press **Open**. A PROD profile asks "Production database" first, once per
   page session.
2. The tree lists the objects by schema and type. Type in **Filter objects** (⌘P / Ctrl+P) to find one by the start
   of its name, or of `schema.name`.
3. Select an object. Its details open on the right, in tabs: **Columns**, **Constraints**, **Indexes**,
   **Relationships**, **Data** (the rows, see [Data viewer](data-viewer.md)), on SQLite **SQLite** (`STRICT`,
   `WITHOUT ROWID`), then **DDL** and, for a PostgreSQL table, **Policies**. The arrow keys move between the tabs.

**Refresh** reads the catalog again on the open connection. It never opens a new one.

## ER diagram

**ER diagram** (next to **Objects**, once a database is open) draws the tables of one **Schema** as boxes with their
columns and a line for each foreign key, pointing at the table it references. PK and FK mark the key columns; a box
lists up to 20 columns and counts the rest.

- Drag the background to move, use the mouse wheel or **Zoom in**, **Zoom out** and **Fit to view** to zoom. The
  overview map in the corner shows where you are.
- A diagram shows at most 150 tables. Open **Tables** to choose which ones (filter by name, then **Show N tables**).
  A larger schema starts with its first 150 by name.
- Click a table's name, or reach it with Tab and press Enter, to show it in the tree with its details.
- The table under the diagram lists the same tables and keys as text, including keys to tables that are not drawn.
- **Save as SVG** and **Save as PNG** save the whole diagram, not only the part on the screen, in the page's light or
  dark colors. Nothing is sent anywhere to make the files.
- **Lay out again** arranges the boxes again and fits them into the view. The status line says how long the layout
  took.

## DDL

The **DDL** tab shows the statements that create the object:

- **PostgreSQL:** Studio rebuilds them from the database's catalog, in the form `pg_dump --schema-only
  --no-owner --no-privileges` writes: the table with its constraints, indexes, triggers, rules, comments and
  row-level security policies; a view or materialized view; a sequence. The owner is shown above the DDL, since the
  DDL leaves it out. Notes under it say what the DDL does not show. For example, an identity column's sequence is
  part of its table's DDL.
- **SQLite:** the text SQLite stored for the object, with its indexes and triggers.

A sequence has no tabs. Its DDL is shown on its own.

Control characters in the DDL are shown as `\uXXXX` and a backslash as `\\`, so nothing in a name or a string can
change how the text reads. Line breaks and tabs stay as they are. **Copy DDL** copies the exact text, without these
escapes. A DDL longer than 100,000 characters is shown only in part, and the tab says so. **Copy DDL** still copies
all of it.

## Policies (PostgreSQL)

The **Policies** tab of a PostgreSQL table shows its row-level security:

- whether row-level security is **on**, and whether it is **forced** for the table's owner;
- the owner and **your role**, the role of the connection;
- whether the policies **bind your role**, and why not if they do not. Row-level security may be off. Your role may
  be a superuser or have `BYPASSRLS`. Or it may own the table, or be a member of the role that owns it, while the
  security is not forced. A role the policies do not bind sees every row in the data viewer and the SQL editor; a
  bound role sees only the rows they allow. If no permissive policy applies to your role, directly or through a role
  it belongs to, it sees no rows;
- every policy: its name, **Permissive** or **Restrictive**, the command (`ALL`, `SELECT`, `INSERT`, `UPDATE`,
  `DELETE`), the roles (**All roles (PUBLIC)** for every role), and its `USING` and `WITH CHECK` expressions.

When row-level security is on and the table has no policy, the tab warns that every role the security binds sees no
rows.

## Row counts

- **PostgreSQL** shows the database's own **estimate** above the tabs ("About 4,200,000 rows"). For a partitioned
  table, it is the sum of its partitions. The estimate comes from the last `ANALYZE` and may be off.
- **SQLite** has no estimate. **Count rows** counts a table or view on request (`count(*)`) and shows the count
  and how long it took. A count that runs longer than 10 seconds, or longer than the profile's time limit, is
  stopped, and the page says so.
