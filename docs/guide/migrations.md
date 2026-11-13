# Migration status

The **Migrations** view shows which migrations a database has and which ones the project's files still have to
apply. Studio only reads here: it never runs a migration, and there is no button that does.

## Showing the status

1. Choose the **Connection profile** and press **Show status**. Studio opens the profile's read-only connection. A
   PROD profile asks "Production database" first, once per page session (a confirmation in **Connections**, the
   explorer, the SQL editor or the diagnostics view counts too; a reload asks again). **Cancel** opens nothing.
2. The result names the profile's environment and lists every migration format Studio found:
   - **SQLite user_version:** the version tabrion's SQLite migrations keep in the file (`PRAGMA user_version`).
   - **Alembic** (the project folder has an `alembic.ini` with a `script_location`): the **Version in the database**
     (from `alembic_version`), the **Latest revisions (heads)** of the project's files, the revisions **Not yet
     applied**, and the revisions the database names that the files do not contain.
   - **Nodestro, Axentra and Orkexa** (PostgreSQL): each version table with its state (up to date, steps not yet
     applied, versions the files do not know, an applied file changed after it ran, no files) and its open steps.
     A table the connection's role may not read says why.

When there are revisions to apply, the page says how many; run your project's own command for that (for example
`alembic upgrade head`) outside Studio.

## The migration code and trust

Alembic reads its revisions by importing the project's revision files, which runs their code. Studio does that only
after you trusted these files:

- Without trust, the **Migration code** section says that the files are not trusted, and the status shows the version
  in the database but "unknown" for the revisions.
- **Review files** opens "Run the project's migration code?". It says what trusting means (the code runs as you, in a
  separate process for at most 30 seconds, with the open connection's address and password) and lists
  `alembic.ini`, the `script_location` and every revision file. The focus starts on **Don't trust**; Escape rejects.
  **Trust and run** trusts exactly the files shown. If they changed meanwhile, the dialog shows the new list first.
- When `alembic.ini` or a revision file changes later, the section says so and Studio asks again before it runs any
  of them. **Stop trusting** takes the trust back.
- A folder Studio cannot check (a `script_location` outside the project, a revision file that is a link, too many
  files) cannot be trusted; the section names the reason.

## When something goes wrong

The page says what happened in its own words, for example that another status is still being read ("Try again when
it has finished"), that reading the revisions took longer than 30 seconds, or that Alembic could not read them (run
`alembic heads` in the project to see the error). The details of each format are in
[Migration status and code trust](../architecture/migrations.md).
