@echo off
rem Tabrion Studio QA kit: install everything into this folder (needs internet once).
setlocal
if not exist "%~dp0_kit\common.cmd" (
  echo FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken.
  pause
  exit /b 1
)
call "%~dp0_kit\common.cmd"
chcp 65001 >nul

echo Tabrion Studio (lokale Testversion) wird installiert.
echo Alles bleibt in diesem Ordner: "%KIT_ROOT%"
echo.

if not exist "%UV%" goto :nouv
rem Files unpacked from a download carry the "from the internet" mark; clear it for the kit's own files.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath $env:KIT_ROOT -Recurse -File | Unblock-File" >nul 2>&1

if exist "%STUDIO%" "%STUDIO%" --stop "%PROJECT%" --state-dir "%STATE_DIR%" >nul 2>&1

echo [1/4] Python 3.13 bereitstellen ...
"%UV%" venv --clear --python 3.13 "%ENV_DIR%" || goto :fail_python

echo [2/4] Bibliotheken laden und pruefen (feste Versionen mit Pruefsummen) ...
"%UV%" pip install --python "%ENV_DIR%\Scripts\python.exe" --require-hashes --no-deps --only-binary :all: -r "%KIT%\runtime-lock.txt" || goto :fail_libs

echo [3/4] Tabrion Studio installieren ...
for %%W in ("%KIT%\wheels\*.whl") do (
  "%UV%" pip install --python "%ENV_DIR%\Scripts\python.exe" --no-deps "%%~fW" || goto :fail_studio
)

echo [4/4] Installation pruefen ...
"%UV%" pip check --python "%ENV_DIR%\Scripts\python.exe" || goto :fail_check
"%STUDIO%" --version || goto :fail_check

echo.
echo Fertig. Weiter mit "2 - Studio starten".
echo.
pause
exit /b 0

:nouv
echo FEHLER: Das Werkzeug im Ordner _kit fehlt. Bitte das Testpaket neu entpacken.
goto :end_fail
:fail_python
echo FEHLER: Python konnte nicht bereitgestellt werden. Besteht eine Internetverbindung?
goto :end_fail
:fail_libs
echo FEHLER: Die Bibliotheken konnten nicht installiert werden.
goto :end_fail
:fail_studio
echo FEHLER: Tabrion Studio konnte nicht installiert werden.
goto :end_fail
:fail_check
echo FEHLER: Die Pruefung der Installation ist fehlgeschlagen.
:end_fail
echo.
pause
exit /b 1
