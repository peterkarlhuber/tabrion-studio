# Troubleshooting

## In the browser

| You see | Why | What to do |
|---|---|---|
| "Not signed in. Run `tabrion-studio` in your project folder to open Studio." | the tab was not opened by Studio (a new tab, a bookmark, a copied address) | run `tabrion-studio` again; it opens a signed-in tab |
| "This sign-in link was already used." or "… has expired." | a sign-in link works once, within 2 minutes | run `tabrion-studio` again |
| "Your session has ended." | Studio stopped or restarted, or another local server replaced Studio's cookie | run `tabrion-studio` again |
| "The local server does not answer." | Studio stopped (Ctrl+C, `--stop`, the idle timeout) | run `tabrion-studio` again |
| the browser does not open, or shows a "file not found" page | a browser in a sandbox (on Linux, for example the Firefox snap or a Flatpak) cannot read Studio's private sign-in file; Studio prints a hint when it detects one | start with `tabrion-studio --no-browser --print-url` and open the printed link yourself, or use `--state-dir` with a folder the browser can read |
| an address with `localhost` or `[::1]` is refused | Studio answers only `127.0.0.1` | use the address Studio printed |
| "Kept in this browser only: your settings.toml has issues …" | Studio does not change a file with issues, or one written by hand, from the general settings | fix `settings.toml`, or rewrite it from the Settings view |
| "Your profiles.toml has issues, so Studio does not change it." | the file has an error or an unknown key | fix the listed issues in the file |
| the keyring choice says "not available on this computer" | no usable keyring (on Linux: no Secret Service or KWallet running) | use "Only this session", a variable or a file, or start a keyring service |
| "Project profiles not loaded" | `.tabrion/studio.toml` has errors | fix the listed issues in the project file |

## In the terminal

| Message | What to do |
|---|---|
| `tabrion-studio: not a folder: …` | name an existing project folder |
| `tabrion-studio: cannot use the state directory …` | the folder is not private (another user owns it, or others may write to it) or is a link; fix its owner and mode (`chmod 700`), or choose another one with `--state-dir` |
| `tabrion-studio: cannot listen on 127.0.0.1:<port>: …` | the port given with `--port` is in use (also on `[::1]`); choose another one or leave `--port` out |
| `another Tabrion Studio holds the lock for … but does not answer` | a Studio for this folder is starting or hangs; wait a moment, or end that process |
| `the running Tabrion Studio did not answer` or a failed server proof | the program on the recorded port is not that Studio; Studio sends it nothing. Stop the old process and start again |
| `No Tabrion Studio is running for …` (after `--stop`) | nothing to stop |

## Logs

The log file's path is printed at the start (`studio-<key>.log` in the log folder, see
[Starting and stopping](starting.md#logs)). An error page that shows a log ID has an entry with the same ID in that
file. The log holds no passwords or data, so you can attach the matching lines to a bug report; check them first.
