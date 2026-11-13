# Settings and keyboard

## Settings

The **Settings** view has three parts. Every setting is saved in `settings.toml` in Studio's configuration folder,
so it is kept for the next start.

| Part | Setting | Default |
|---|---|---|
| General | **Language:** English or Deutsch | the browser's language (German for a German browser, else English) |
| | **Theme:** System, Light or Dark | System |
| | **Density:** Compact or Normal | Compact |
| Queries | **Maximum rows per result** | 10,000 |
| | **Default statement timeout** in seconds | 30 |
| Security | **Write SQL statements to the log** | off |

The general settings are saved as soon as you choose them. The query and security settings are saved with
**Save settings**. **Maximum rows per result** applies to the next statement you run in the SQL editor. The
**default statement timeout** is set when a connection opens: for a connection that is already open, **Disconnect**
and **Connect** again. A profile with its own statement timeout keeps it; a PROD profile without one stops after 15
seconds.

**Write SQL statements to the log** is saved, but in this version Studio does not write SQL statements to the log
either way (docs/TODO.md, TD-013). Once it does, the log will contain the statements you run, which can include
data; parameter values are never logged.

If `settings.toml` has issues, or was written by hand, Studio does not change it from the general settings: your
choice then applies in this browser only, until the next start, and the view says so. Fix the file, or rewrite it
with "Rewrite file and save".

## Keyboard

| Keys (macOS) | Keys (Windows, Linux) | What it does |
|---|---|---|
| ⌘K | Ctrl+K | open the **command palette** |
| ⌘/ | Ctrl+/ | show the keyboard shortcuts |
| F6 | F6 | move to the next area: header, navigation, tabs, main area, status bar. In the explorer the main area is two areas (the tree, the details), in the SQL editor three (the SQL text, the parameters, the result) |
| Shift+F6 | Shift+F6 | move to the previous area |
| Esc | Esc | close the palette or a dialog |

F6 moves between areas, Tab between the controls inside one. The explorer's connection section and the SQL
editor's toolbar (connection, Run, Run script, Cancel, Save) are not areas of their own: reach the toolbar with
Shift+Tab from the SQL text, and the explorer's connection section with Shift+Tab (past "Refresh") from its filter.

The SQL editor has shortcuts of its own (run, run script, cancel, new and close tab, save); they are listed in
[SQL editor](sql-editor.md) and in the shortcut list.

The command palette shows the commands whose name contains every word you type: go to a view ("go settings"),
change the theme, density or language ("theme dark", "Deutsch"), or open the shortcut list. The arrow keys choose,
Enter runs, Esc closes. Theme, density and language chosen there are saved like in the settings.

The header has buttons for the palette and the shortcut list, so both work with a mouse too.
