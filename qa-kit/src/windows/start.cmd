@echo off
rem Tabrion Studio QA kit: start Studio for the example project and open the browser.
setlocal
if not exist "%~dp0_kit\common.cmd" (
  echo FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken.
  pause
  exit /b 1
)
call "%~dp0_kit\common.cmd"
chcp 65001 >nul

if not exist "%STUDIO%" (
  echo Tabrion Studio ist noch nicht installiert. Bitte zuerst "1 - Installieren" ausfuehren.
  echo.
  pause
  exit /b 1
)
echo Tabrion Studio startet und oeffnet den Browser.
echo Dieses Fenster bitte offen lassen, solange du testest.
echo Beenden: "3 - Studio beenden" oder dieses Fenster schliessen.
echo.
"%STUDIO%" "%PROJECT%" --state-dir "%STATE_DIR%"
echo.
echo Tabrion Studio wurde beendet.
pause
