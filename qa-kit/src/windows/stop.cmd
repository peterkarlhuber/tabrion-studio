@echo off
rem Tabrion Studio QA kit: stop the running Studio of the example project.
setlocal
if not exist "%~dp0_kit\common.cmd" (
  echo FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken.
  pause
  exit /b 1
)
call "%~dp0_kit\common.cmd"
chcp 65001 >nul

if exist "%STUDIO%" (
  "%STUDIO%" --stop "%PROJECT%" --state-dir "%STATE_DIR%" || echo Es lief kein Tabrion Studio.
) else (
  echo Tabrion Studio ist nicht installiert.
)
echo.
pause
