# Query history and favorites

## Query history

**Query history** (the clock icon in the activity bar, ⌘Y on a Mac or Ctrl+H elsewhere) lists the statements you
ran in the SQL editor, newest first: the profile with its environment, when the statement ran, whether it finished
(with its row count), failed or was canceled, and how long it took.

Studio keeps the **statement** and the **names** of its `:name` parameters, never their values. **Open in editor**
opens the statement in a new editor tab. It does not run there, and the parameter panel asks for the values again.
**Copy** puts the statement on the clipboard.

- **Profile** shows one profile's statements. The search finds statements that contain the text you type.
- **Delete** removes one entry right away. **Delete history** removes every entry shown (all of them, or the
  chosen profile's) after you confirm; the default is Cancel.
- The history is kept in Studio's state database (`state.sqlite` in the state folder, readable by your user only),
  never in the browser. Deleted entries are overwritten in the file.
- **Settings › Query history** turns the history off and sets how many days entries are kept (30 by default).
  Studio keeps at most 5,000 entries, and at most 64 KB of each statement.

A statement is your own SQL text, so a literal value you type into it (`where email = 'a@b.example'`) is in the
history. Give such values as parameters (`where email = :email`): parameter values are never kept.

## Favorites and recently opened connections

In **Connections**, **Favorite** (the star in a profile's details) marks a profile. The start page then lists your
favorites and the connections you opened last. **Connect** there opens the connection manager on that profile and
connects the usual way: a PROD profile asks "Production database" first, and a missing password opens the password
dialog. Favorites name a profile only, never its target or password. A project's profiles are favorites in that
project only.
