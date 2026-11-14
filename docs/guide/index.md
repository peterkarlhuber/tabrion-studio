# User guide

This guide is for people who use Tabrion Studio. It describes what the current development version does.

!!! warning "Development version"
    What works: starting and signing in, the connection profiles and the project's trust dialog, saved passwords,
    the explorer with DDL, policies and the ER diagram, the data viewer, the SQL editor (reading statements only)
    with export, project scripts, drafts kept by Studio, formatting and completion, the query history and favorite
    connections, the migration status (Alembic, SQLite, Nodestro, Axentra and Orkexa), the diagnostics with the
    backup hints and the report, the vector data view, the settings, the keyboard and the command palette. Studio opens
    every connection read-only and sends only reading statements; the hard boundary is a database role without write
    privileges ([Security](security.md)). Nothing is released; you install Studio from a commit of the repository.

- [Installation](installation.md): from a Git commit or a wheel built from it, with Python 3.11 or later.
- [Starting and stopping](starting.md): the `tabrion-studio` command, the sign-in link, one Studio per project
  folder, the logs.
- [Connections](connections.md): your profiles, the project's profiles and the trust dialog, passwords.
- [Diagnostics](diagnostics.md): the findings of a diagnosis, the backup hints and the report without secrets.
- [Vector data](vector-data.md): vector columns, ANN indexes or exact search, embedding dimensions and models,
  stored embeddings, and the pgvector or sqlite-vec status, read-only.
- [Migration status](migrations.md): which migrations a database has and which are pending, and trusting the
  project's Alembic files.
- [Explorer](explorer.md): the objects of a database, their details, DDL and row-level security policies, row
  counts, and the ER diagram of a schema with its SVG and PNG export.
- [SQL editor](sql-editor.md): running reading statements, parameters, results, cancel, export as CSV or JSON,
  tabs and drafts, project scripts, format and completion.
- [Query history and favorites](history.md): the statements you ran (without parameter values), favorite
  and recently opened connections.
- [Data viewer](data-viewer.md): the rows of a table or view, page by page, with sorting and filters.
- [Settings and keyboard](settings.md): language, theme, density, query limits, the command palette and the
  shortcuts.
- [How Studio protects your data](security.md): what Studio does and does not do, in short.
- [Troubleshooting](troubleshooting.md): the messages you may see while installing, in the browser, in the views
  and in the terminal, and what to do.

The architecture pages in this site explain how each part works and how it is tested.
