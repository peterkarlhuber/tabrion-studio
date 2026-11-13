@rem Shared settings of the Tabrion Studio QA kit (Windows). Called by the four scripts; not run directly.
@rem Everything the kit installs or writes stays inside this folder.
set "KIT_ROOT=%~dp0.."
for %%I in ("%KIT_ROOT%") do set "KIT_ROOT=%%~fI"
set "KIT=%KIT_ROOT%\_kit"
set "ENV_DIR=%KIT%\env"
rem Studio keeps its settings, trust records and logs in a private folder of the user profile: on Windows it accepts
rem a state folder outside the profile only with a private ACL, which an unpacked folder elsewhere does not have.
set "STATE_DIR=%LOCALAPPDATA%\TabrionStudio-QA\studio-daten"
set "PROJECT=%KIT_ROOT%\Beispielprojekt"
set "STUDIO=%ENV_DIR%\Scripts\tabrion-studio.exe"
set "UV=%KIT%\bin\windows-x64\uv.exe"
rem Settings of a uv the user may have configured elsewhere must not redirect the kit.
for %%V in (UV_INDEX UV_INDEX_URL UV_DEFAULT_INDEX UV_EXTRA_INDEX_URL UV_FIND_LINKS UV_PYTHON_INSTALL_MIRROR UV_PYPY_INSTALL_MIRROR UV_PYTHON_DOWNLOADS_JSON_URL UV_PYTHON_DOWNLOADS UV_OFFLINE UV_CONSTRAINT UV_OVERRIDE UV_PYTHON UV_INSECURE_HOST PIP_INDEX_URL PIP_EXTRA_INDEX_URL) do set "%%V="
set "UV_CACHE_DIR=%KIT%\cache"
set "UV_PYTHON_INSTALL_DIR=%KIT%\python"
set "UV_NO_CONFIG=1"
set "UV_NO_PROGRESS=1"
if not defined UV_PYTHON_PREFERENCE set "UV_PYTHON_PREFERENCE=only-managed"
