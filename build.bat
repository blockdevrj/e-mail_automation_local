@echo off
REM ---- Builds dist\OutreachDesk.exe on Windows ----
REM Needs Python 3.10 or newer from https://www.python.org/downloads/ (tick "Add python.exe to PATH").
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from python.org and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)

echo Creating a clean build environment...
python -m venv .venv || goto :fail
call .venv\Scripts\activate.bat || goto :fail

echo Installing dependencies...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller || goto :fail

echo Building the .exe (this takes a minute or two)...
pyinstaller --noconfirm --clean OutreachDesk.spec || goto :fail

echo.
echo Done. Your app is: %~dp0dist\OutreachDesk.exe
echo Copy that single file anywhere and double-click it.
pause
exit /b 0

:fail
echo.
echo The build failed. Scroll up to read the error.
pause
exit /b 1
