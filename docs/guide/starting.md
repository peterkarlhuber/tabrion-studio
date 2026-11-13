# Starting and stopping

```bash
tabrion-studio [PROJECT] [--port N] [--no-browser] [--print-url] [--idle-timeout MINUTES]
               [--state-dir DIR] [--log-dir DIR] [--config-dir DIR]
tabrion-studio --stop [PROJECT]
tabrion-studio --version
tabrion-studio diagnose [PROJECT] --profile PROFILE [--format markdown|json] [--output FILE] [--confirm-prod]
```

`tabrion-studio diagnose` writes the diagnosis report of one profile without starting Studio; its options are in
[Diagnostics](diagnostics.md#the-report-from-the-command-line).

## Start

Run `tabrion-studio` in your project folder, or name the folder: `tabrion-studio ~/src/parts`. Studio

1. starts a local server on `127.0.0.1` at a free port and prints its address, for example
   `http://127.0.0.1:53817/`;
2. opens your default browser with a **one-time sign-in link**. The link goes through a private file, so it never
   shows up in the process list, and it works once, within 2 minutes;
3. shows the start page. If the project has a `.tabrion/studio.toml`, the trust dialog comes first (see
   [Connections](connections.md)).

The terminal keeps running; Studio's output and its log path are shown there.

**One Studio per project folder.** Running `tabrion-studio` again for the same folder, also from another terminal,
starts no second server: it opens a new signed-in tab of the running one. Different folders get their own Studio on
their own port.

**A new tab is not signed in.** Studio's sign-in belongs to the tab it opened. A tab you open by hand, or by copying
the address, shows "Not signed in". Run `tabrion-studio` again to get a signed-in tab.

## Options

| Option | What it does |
|---|---|
| `PROJECT` | the project folder; default: the current folder |
| `--port N` | listen on this port of `127.0.0.1`; default: a free port. Studio never listens on another address |
| `--no-browser` | do not open the browser (and issue no sign-in link) |
| `--print-url` | also print a one-time sign-in link; the browser still opens unless you add `--no-browser`. Use `--no-browser --print-url` for a browser you start yourself. Anyone who sees the link can use it once; do not share it |
| `--idle-timeout MINUTES` | stop after this many minutes without an open Studio tab; default 30, `0` = never |
| `--state-dir DIR` | the folder for running instances and trust records; also `TABRION_STUDIO_STATE_DIR` |
| `--log-dir DIR` | the log folder; also `TABRION_STUDIO_LOG_DIR`; default: the per-user log folder, or `DIR/logs` with `--state-dir` |
| `--config-dir DIR` | the folder of `settings.toml` and `profiles.toml`; also `TABRION_STUDIO_CONFIG_DIR`; default: the per-user configuration folder, or `DIR/config` with `--state-dir` |
| `--stop` | stop the Studio that runs for the project folder |
| `--version` | print the version |

The default folders are listed under [Installation](installation.md#uninstall). Studio creates them private (only
your user can open them) and refuses a folder that other users can write to.

## Stop

- **Ctrl+C** in the terminal (Ctrl+Break on Windows), or close the terminal;
- `tabrion-studio --stop` in the project folder (or `tabrion-studio --stop PROJECT`): it prints
  "Tabrion Studio for … stopped.";
- automatically after 30 minutes without an open Studio tab (`--idle-timeout`). An open tab tells the server every
  minute that it is still there.

When the server stops, every sign-in ends. A tab that is still open says "Your session has ended. Run
`tabrion-studio` again to open Studio."

## Logs

Studio writes a rotating log file per project folder, `studio-<key>.log` in the log folder, as JSON lines. The
command prints its path at the start. The log has routes, codes and durations, never passwords, tokens, parameter
values or the text of database errors. If an error page shows a log ID (12 hex digits), the matching log entry has
the same ID.

Studio does not write SQL statements to the log in this version; see [Settings](settings.md).
