# Data viewer

The **data viewer** shows the rows of a table, view or materialized view, one page at a time. It is the **Data** tab
of an object in the **Explorer**. Studio only reads here: there is nothing that changes data.

## Opening the data

1. In **Explorer**, choose a profile and press **Open**. A PROD profile asks first ("Production database"), unless you
   confirmed it in this page session already (in **Connections**, the SQL editor or the explorer).
2. Select a table, view or materialized view in the tree.
3. Press **Show data**, or choose the **Data** tab. The first page loads.

**SELECT … LIMIT 100 in the SQL editor** opens a new SQL editor tab with `SELECT * FROM <object> LIMIT 100;` on the
same profile. Nothing runs until you press **Run**. **Copy name** copies the object's name with its schema.

## Pages, sorting and filters

- **Rows per page:** 100 at first; 50, 250, 500 or 1,000 can be chosen. A table with many long text or JSON columns
  gets fewer rows per page, so that a page fits the browser's memory, and the tab says so.
- **Next** and **Previous** move between pages. The status says which rows are shown and whether more follow. There
  is no total count: counting a large table would read all of it.
- **Sort by** a column, ascending or descending. Studio pages by the table's primary key; a table without one is paged
  by its physical row position (`rowid` on SQLite, `ctid` on PostgreSQL), and a view without one by position. The tab
  says so, because rows can then move between pages when the table changes.
- **Filters:** **Add filter**, choose a column, an operator (=, ≠, <, ≤, >, ≥, contains, starts with, is NULL, is not
  NULL) and a value, then **Apply**. All filters must hold. The value is sent as a parameter, never as SQL, and the
  database compares it by the column's own type. `%` and `_` in a value are plain characters. On SQLite "contains"
  and "starts with" ignore the case of ASCII letters; on PostgreSQL they respect case. There can be up to 20 filters,
  and all of them together must fit in one request (about 12 KB once encoded; a character outside ASCII takes up to
  12 bytes): the form says when they do not.
- Changing the sort, the filters or the page size starts again at the first page. The tab reads its first page a
  moment after it opens, so moving across it with the arrow keys reads nothing.
- **Search this page** finds rows of the current page that contain a text (any column, ignoring case). It does not ask
  the database; use a filter for that.

## Cells

The grid works like the SQL editor's: one stop for the Tab key, the arrow keys move, Enter or a click shows the whole
cell on the side (text and JSON as plain text, bytes as hex), ⌘C / Ctrl+C copies it. A value longer than 64 KB is cut
on the page and marked with "⋯"; **Load in full** in the detail loads up to 10 MB of it. Rows of a view without a
primary key cannot be found again, so their long values cannot be loaded in full here: query them in the SQL editor.

## When a page is gone

Studio keeps only the most recent pages on the server. When one is no longer there, the tab says so and offers **Back
to the first page**. When the connection was closed (for example in **Connections**), the explorer closes it too and
asks you to open it again.
