# Installation

There is no release yet, and Studio is not published to PyPI. You install it from a commit of the Git repository:
the commit SHA is the version you pin (owner decision 2026-11-08).

## Requirements

- Python 3.11 or later, on Linux, macOS or Windows.
- To **build** the frontend once: Node.js 22.22 or later with npm. Studio itself runs without Node.
- A current Chrome, Edge, Firefox or Safari.
- Optional: an operating system keyring (macOS Keychain, Windows Credential Manager, Secret Service or KWallet on
  Linux) to save database passwords. Without one, Studio asks for the password once per session.

## Install from a commit

Pick the commit you want, for example the head of `master` after a check you trust, and note its full SHA.

```bash
git clone https://github.com/peterkarlhuber/tabrion-studio.git
cd tabrion-studio
git checkout <commit SHA>
python scripts/build_frontend.py      # npm ci + npm run build; copies the frontend into the package
python -m venv ~/.venvs/tabrion-studio
~/.venvs/tabrion-studio/bin/python -m pip install .   # Windows: %USERPROFILE%\.venvs\tabrion-studio\Scripts\python
```

The wheel contains the built frontend. A build without it stops with a message that names
`scripts/build_frontend.py`. For the same reason, `pip install git+https://…@<SHA>` does not work: the built frontend
is not in Git.

A tool such as `pipx` works too (`pipx install .` after the frontend build). Put the command on your `PATH`, or call
it with the full path of the virtual environment. Check the installation:

```bash
~/.venvs/tabrion-studio/bin/tabrion-studio --version   # or just tabrion-studio --version once it is on your PATH
```

## Update

Check out the new commit, build the frontend again and reinstall:

```bash
git fetch && git checkout <new commit SHA>
python scripts/build_frontend.py
~/.venvs/tabrion-studio/bin/python -m pip install --force-reinstall --no-deps .
```

Stop a running Studio first (`tabrion-studio --stop` in the project folder); a running server keeps the old code.

## Uninstall

```bash
~/.venvs/tabrion-studio/bin/python -m pip uninstall tabrion-studio
```

Studio's own files stay where they are. Remove them by hand if you want them gone:

| | Linux | macOS | Windows |
|---|---|---|---|
| Settings and your profiles | `~/.config/tabrion-studio` | `~/Library/Application Support/tabrion-studio` | `%LOCALAPPDATA%\tabrion-studio` |
| State (running instances, trust records) | `~/.local/state/tabrion-studio` | `~/Library/Application Support/tabrion-studio` | `%LOCALAPPDATA%\tabrion-studio` |
| Logs | `~/.local/state/tabrion-studio/log` | `~/Library/Logs/tabrion-studio` | `%LOCALAPPDATA%\tabrion-studio\Logs` |

Passwords saved in the keyring are entries of the service `tabrion-studio`; remove them in your keyring's own tool,
or with "Remove saved password" in Studio before you uninstall. A project's `.tabrion/studio.toml` belongs to the
project and is never written by Studio.
