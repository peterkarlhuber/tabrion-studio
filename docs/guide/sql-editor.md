# SQL editor

The **SQL editor** (the terminal icon in the activity bar, or "Go to SQL editor" in the command palette) runs
reading SQL statements on one of your connection profiles. Studio only reads: every statement runs in its own
read-only transaction, and there is no write mode.

## Running statements

1. Choose the **Connection** of the tab. A PROD profile asks before Studio first connects to it in this page session
   ("Production database"; a confirmation in **Connections** counts too, and a reload asks again). Queries on it are
   limited to 15 seconds, or to the profile's own statement timeout when it has one (the question names the limit).
   Changing a tab's connection clears its results. When a profile was changed elsewhere (another browser tab, or
   `profiles.toml`) since the editor read it, Studio runs nothing, shows the profile as it is now and says so; run
   again, which asks first for a PROD profile.
2. Write SQL. The editor highlights PostgreSQL or SQLite, depending on the profile.
3. Press **Run** (⌘↵ / Ctrl+Enter): Studio runs the selection, or the statement the cursor is in. **Run script**
   (⇧⌘↵ / Shift+Ctrl+Enter) runs every statement of the tab, one after the other, and stops at the first error.

Studio runs only statements that read: `SELECT`, `WITH`, `VALUES`, `TABLE`, `EXPLAIN` of a query, `SHOW`, and
SQLite's `PRAGMA`. Anything else is refused before it reaches the database, and the database refuses a change too
("This connection is read-only").

Tab moves the focus out of the editor. In the editor, ⌘/ / Ctrl+/ comments lines out; use the header button for the
shortcut list there.

## Parameters

A `:name` placeholder gets a row in **Parameters**, with a type (Text, Number, Boolean, Null, JSON) and a value. A
placeholder without a value stops the run, and its field gets the focus. JSON is sent as text: cast it in the
statement (`:doc::jsonb`). A whole number beyond 2^53 does not fit a number; give it as text with a cast
(`:id::bigint`). Parameter values stay in the page: they are not saved with the draft and never logged.

## Results

- A result shows **1,000 rows** at first. When there are more, the status says "capped at 1,000 (more rows exist)",
  and **Load up to N rows** runs the statement again with the **Maximum rows per result** of the settings.
- One **Result** tab per statement that returned rows, and **Messages** with every statement of the run: its number,
  rows and time, or its error. **Go to statement** selects it in the editor.
- The grid is one stop for the Tab key. The arrow keys, Home and End, Ctrl+Home and Ctrl+End, Page Up and Page Down
  move the active cell; Enter (or a click) shows the whole value on the side: text and JSON as plain text, bytes as
  hex. ⌘C / Ctrl+C copies the active cell. NULL is shown in italics; a value the server cut (64 KB of text,
  32 KB of bytes) has a "⋯".
- The browser holds only the rows around what you see (at most 10,000 rows and about 64 MB), so a result of 100,000
  rows, or one of very long values, needs little memory.
- Enter moves the focus into the value panel; a click opens it and leaves the focus in the grid.
- **Cancel query** (⌘. / Ctrl+., or Esc in the results) stops a running statement within about a second:
  "Canceled after 4.2 s".

Studio keeps the last 16 results on the server. An older one says "no longer kept"; run the statement again.

## Export

**Export…** next to a finished result saves it as a file:

- **Format:** CSV (for spreadsheets) or JSON (for programs).
- **Rows:** the fetched rows of the result, or **Up to** a number of rows from 1 to 1,000,000.
- **Protect against spreadsheet formulas** (CSV only, on by default): a text that starts with `=`, `+`, `-` or `@`
  gets a leading `'`, so a spreadsheet does not run it as a formula.

Studio runs the statement again for the export, read-only and with the same parameters, so the file holds the rows
as they are now. If there are more rows than the limit, the file stops there; a JSON file says so, and Studio tells
you after the download. A CSV file cannot say it. **Cancel export** (or Esc) stops it, and nothing is saved. Studio
runs one export per connection and two at a time, and an export stops after 10 minutes. The browser holds the file
until it is saved, so a large export needs that much browser memory. Studio stops an export when the file passes
1 GB: the export is canceled, nothing is saved, and the dialog asks you to export fewer rows or columns. If the
connection breaks while the file arrives, nothing is saved either, and the dialog says that the file did not arrive
whole.

## Tabs and drafts

Each tab has its own connection and text. **New editor tab** is Alt+T (⌥T), **Close editor tab** is Alt+W (⌥W):
the browser keeps ⌘T and ⌘W for itself. The text of every tab is kept as a draft in this browser and comes back
when you open the editor again. A draft holds the SQL text exactly as typed (never parameter values or results), in
the browser's storage of this computer's user, so do not type secrets into the SQL itself; give them as parameters.
Drafts are kept per project: another project started on the same port does not see them. Studio's port changes
with every start unless you give `--port`, and the browser keeps drafts per port, so give a fixed port to keep
drafts across starts. Two browser tabs on the editor keep each other's new tabs; when both change the same tab, the
last change wins.

**Save as .sql** (⌘S / Ctrl+S) downloads the tab's text as a `.sql` file. Closing a tab whose text changed since it
was last saved asks first: **Save**, **Discard** or **Cancel**.
