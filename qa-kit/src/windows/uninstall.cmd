@echo off
rem Tabrion Studio QA kit: remove everything the kit installed or wrote (Python, libraries, Studio's settings,
rem trust records and logs). The example project and the scripts stay until you delete the folder.
setlocal
if not exist "%~dp0_kit\common.cmd" (
  echo FEHLER: Der Ordner _kit fehlt. Bitte das Testpaket neu entpacken.
  pause
  exit /b 1
)
call "%~dp0_kit\common.cmd"
chcp 65001 >nul

echo Deinstallieren entfernt Python, die Bibliotheken und alle Einstellungen und Protokolle
echo von Tabrion Studio aus diesem Ordner: "%KIT_ROOT%"
set "ANSWER="
set /p "ANSWER=Fortfahren? (j/n) "
if /i not "%ANSWER%"=="j" if /i not "%ANSWER%"=="ja" if /i not "%ANSWER%"=="y" (
  echo Abgebrochen, nichts wurde entfernt.
  echo.
  pause
  exit /b 0
)
if exist "%STUDIO%" "%STUDIO%" --stop "%PROJECT%" --state-dir "%STATE_DIR%" >nul 2>&1
if not exist "%KIT%\common.cmd" (
  echo FEHLER: Unerwarteter Ordner, es wurde nichts entfernt.
  pause
  exit /b 1
)
for %%D in ("%KIT%\env" "%KIT%\python" "%KIT%\cache") do if exist "%%~D" rmdir /s /q "%%~D"
if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\TabrionStudio-QA" rmdir /s /q "%LOCALAPPDATA%\TabrionStudio-QA"
echo.
echo Entfernt. Zum Schluss den Ordner einfach in den Papierkorb ziehen.
echo Studios Einstellungen unter AppData\Local\TabrionStudio-QA wurden ebenfalls entfernt.
echo.
pause
