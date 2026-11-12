# SQL editor

The **SQL editor** (the terminal icon in the activity bar, or "Go to SQL editor" in the command palette) runs
reading SQL statements on one of your connection profiles. Studio only reads: every statement runs in its own
read-only transaction, and there is no write mode.

## Running statements

1. Choose the **Connection** of the tab. A PROD profile asks once per page before Studio connects to it ("Production
   database"); queries on it are limited to 15 seconds.
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
- The browser holds only the rows around what you see, so a result of 100,000 rows needs little memory.
- **Cancel query** (⌘. / Ctrl+., or Esc in the results) stops a running statement within about a second:
  "Canceled after 4.2 s".

Studio keeps the last 16 results on the server. An older one says "no longer kept"; run the statement again.

## Tabs and drafts

Each tab has its own connection and text. **New editor tab** is Alt+T (⌥T), **Close editor tab** is Alt+W (⌥W):
the browser keeps ⌘T and ⌘W for itself. The text of every tab is kept as a draft in this browser and comes back
when you open the editor again. Studio's port changes with every start unless you give `--port`, and the browser
keeps drafts per port, so give a fixed port to keep drafts across starts.

**Save as .sql** (⌘S / Ctrl+S) downloads the tab's text as a `.sql` file. Closing a tab whose text changed since it
was last saved asks first: **Save**, **Discard** or **Cancel**.
