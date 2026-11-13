# Installation

There is no release yet, and Studio is not published to PyPI. You install it from a commit of the Git repository:
the commit SHA is the version you pin (owner decision 2026-11-08). A tag, once there is one, names such a commit, and
you install it the same way. Publishing Studio anywhere (PyPI, a GitHub Release) needs the owner's approval G-R for
that version and, before the first public release, a human security review
([Release checklist](../development/release-checklist.md)).

## Requirements

- Python 3.11 or later, on Linux with glibc, macOS 13 or later, or Windows 10 or later. CI tests the current
  Ubuntu, macOS and Windows runners; older versions in this range are not tested.
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
~/.venvs/tabrion-studio/bin/python -m pip install --upgrade "pip>=26.1"
~/.venvs/tabrion-studio/bin/python -m pip install --require-hashes --no-deps --only-binary :all: -r constraints/runtime-lock.txt
~/.venvs/tabrion-studio/bin/python -m pip install --no-deps . -r constraints/tabrion-integration.txt
~/.venvs/tabrion-studio/bin/python -m pip check
```

On Windows, in PowerShell (the virtual environment's programs are in `Scripts`, not `bin`):

```powershell
git clone https://github.com/peterkarlhuber/tabrion-studio.git
cd tabrion-studio
git checkout <commit SHA>
python scripts\build_frontend.py
python -m venv $env:USERPROFILE\.venvs\tabrion-studio
$py = "$env:USERPROFILE\.venvs\tabrion-studio\Scripts\python.exe"
& $py -m pip install --upgrade "pip>=26.1"
& $py -m pip install --require-hashes --no-deps --only-binary :all: -r constraints\runtime-lock.txt
& $py -m pip install --no-deps . -r constraints\tabrion-integration.txt
& $py -m pip check
```

The first install takes every runtime dependency from `constraints/runtime-lock.txt`: exact versions, and pip checks
the SHA-256 of every file it downloads (`--require-hashes`; wheels only, so no dependency is built on your
computer). The second installs Studio and Tabrion without dependencies, so the runtime dependencies come only from
the lock, and `pip check` confirms that the lock was complete. pip still builds Studio and Tabrion themselves from
source, with their build backends fetched from PyPI without hashes (build time only; nothing of it is installed). CI installs the same way and tests exactly these versions (security review F-6,
[CI](../development/ci.md#hashed-runtime-pins)). The lock is one tested set inside the ranges of `pyproject.toml`;
`pip install . -c constraints/tabrion-integration.txt` works too, but resolves the dependencies afresh from PyPI,
without hashes.

Tabrion is not on PyPI either. `constraints/tabrion-integration.txt` pins the Tabrion commit this Studio commit is
built and tested against (Tabrion v0.1.0) by its full SHA (pip cannot hash a Git checkout, so Tabrion is not in the
lock), so always install with that file. A plain `pip install .` fails, because pip cannot find Tabrion.

The install needs pip 26.1 or later. Older pip versions (24.0 to 26.0, for example the pip 24.0 of a fresh
Ubuntu 24.04 virtual environment) stop with `AssertionError: Candidate is not for this requirement`, because they
cannot combine the constraint with the `tabrion[postgres,sqlalchemy]` extras. The upgrade step above avoids that. A
virtual environment of Python 3.13 or later already comes with a recent enough pip; the upgrade step does no harm
there.

The wheel contains the built frontend. A build without it stops with a message that names
`scripts/build_frontend.py`. For the same reason, installing straight from a Git URL (`pip install git+https://…@<SHA>`,
`uv tool install git+https://…@<tag>` or `pipx install git+https://…`) does not work: the built frontend is not in
Git. Build it once and install the folder or a wheel, as below.

### Or: build a wheel first

In the checked-out folder, you can also build a wheel once and install that, for example to install the same build
on several computers. This is the way the clean-install test of STU-017 installs Studio
([Documentation checklist](../development/docs-checklist.md)):

```bash
python scripts/build_frontend.py
python -m venv ~/.venvs/tabrion-build
~/.venvs/tabrion-build/bin/python -m pip install "build==1.6.1"
PIP_CONSTRAINT=build-constraints.txt PIP_REQUIRE_HASHES=1 ~/.venvs/tabrion-build/bin/python -m build --wheel --outdir dist
# then, on each computer, with the wheel and the constraints folder copied there:
python -m venv ~/.venvs/tabrion-studio
~/.venvs/tabrion-studio/bin/python -m pip install --upgrade "pip>=26.1"
~/.venvs/tabrion-studio/bin/python -m pip install --require-hashes --no-deps --only-binary :all: -r constraints/runtime-lock.txt
~/.venvs/tabrion-studio/bin/python -m pip install --no-deps dist/tabrion_studio-*.whl -r constraints/tabrion-integration.txt
~/.venvs/tabrion-studio/bin/python -m pip check
```

On Windows, in PowerShell. Neither PowerShell nor `cmd.exe` expands `*` for pip, so name the wheel's full file name
(the one `python -m build` printed, for example `tabrion_studio-0.1.0.dev0-py3-none-any.whl`):

```powershell
python scripts\build_frontend.py
python -m venv $env:USERPROFILE\.venvs\tabrion-build
& "$env:USERPROFILE\.venvs\tabrion-build\Scripts\python.exe" -m pip install "build==1.6.1"
$env:PIP_CONSTRAINT = "build-constraints.txt"; $env:PIP_REQUIRE_HASHES = "1"
& "$env:USERPROFILE\.venvs\tabrion-build\Scripts\python.exe" -m build --wheel --outdir dist
Remove-Item Env:PIP_CONSTRAINT, Env:PIP_REQUIRE_HASHES
python -m venv $env:USERPROFILE\.venvs\tabrion-studio
$py = "$env:USERPROFILE\.venvs\tabrion-studio\Scripts\python.exe"
& $py -m pip install --upgrade "pip>=26.1"
& $py -m pip install --require-hashes --no-deps --only-binary :all: -r constraints\runtime-lock.txt
& $py -m pip install --no-deps dist\<wheel file name> -r constraints\tabrion-integration.txt
& $py -m pip check
```

The wheel needs the same two files as `pip install .`; without the Tabrion pin pip stops with `No matching
distribution found for tabrion`. `PIP_CONSTRAINT` and `PIP_REQUIRE_HASHES` pin the build backend to the hashed versions of
`build-constraints.txt`, so the same sources give the same wheel. In bash they apply to the build command only; in
PowerShell they stay set, so the block removes them before installing (the install would require hashes too).

### pipx or uv

`pipx` and `uv tool` install Studio into an environment of their own and put the `tabrion-studio` command on your
`PATH`. Give them the wheel built above and both constraints files, so that they take the lock's versions and the
Tabrion pin:

```bash
uv tool install dist/tabrion_studio-*.whl -c constraints/runtime-lock.txt -c constraints/tabrion-integration.txt
# or
pipx install dist/tabrion_studio-*.whl --pip-args "-c constraints/tabrion-integration.txt -c constraints/runtime-lock.txt"
```

These two take the lock as constraints, so they install the lock's versions; the guide's main path above, pip with
`--require-hashes`, is the one that checks every file's SHA-256. Both commands were tested on Linux on 2026-11-13
([Release checklist](../development/release-checklist.md#clean-install-2026-11-13-linux)); on macOS and Windows they
are not tested yet. To update, build the new wheel and run the same command with `--force` (`uv tool install
--force`, `pipx install --force`); to uninstall, `uv tool uninstall tabrion-studio` or `pipx uninstall
tabrion-studio`.

### Check the installation

Put the command on your `PATH`, or call it with the full path of the virtual environment:

```bash
~/.venvs/tabrion-studio/bin/tabrion-studio --version   # or just tabrion-studio --version once it is on your PATH
```

On Windows: `& "$env:USERPROFILE\.venvs\tabrion-studio\Scripts\tabrion-studio.exe" --version`.

The Windows commands above are not yet run by hand in a recorded test; the automated clean-install test runs on
Windows in CI, but with its own commands ([Documentation checklist](../development/docs-checklist.md)).

## Update

Check out the new commit, build the frontend again and reinstall:

```bash
git fetch && git checkout <new commit SHA>
python scripts/build_frontend.py
~/.venvs/tabrion-studio/bin/python -m pip install --require-hashes --no-deps --only-binary :all: -r constraints/runtime-lock.txt
~/.venvs/tabrion-studio/bin/python -m pip install --force-reinstall --no-deps . -r constraints/tabrion-integration.txt
~/.venvs/tabrion-studio/bin/python -m pip check
```

On Windows, run the same three commands with `& $py` as above and `\` in the paths.

The first command moves the dependencies to the pins of the new commit. The second names the Tabrion pin itself, so
a newer Studio commit that moves it also installs the new Tabrion. A package that the new lock no longer lists stays
installed; that is harmless, and a fresh virtual environment drops it.

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
