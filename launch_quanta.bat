@echo off
:: QUANTA Web — One-click launcher
:: Double-click this file (or the desktop shortcut) to start QUANTA
title QUANTA Elite — Starting...

cd /d "%~dp0"

:: Activate venv if it exists
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else if exist "..\..\.venv\Scripts\activate.bat" (
    call "..\..\.venv\Scripts\activate.bat"
) else if exist "..\.venv\Scripts\activate.bat" (
    call "..\.venv\Scripts\activate.bat"
)

:: Install Flask if missing (one-time)
python -c "import flask" 2>nul || pip install flask -q

set "WEBAPP_DIR="
if exist "%~dp0webapp\app.py" (
    set "WEBAPP_DIR=%~dp0webapp"
)

if not defined WEBAPP_DIR (
    echo Could not find the QUANTA web app directory.
    exit /b 1
)

echo.
echo  =============================================
echo   QUANTA Elite — Quantitative Trading Intelligence
echo   Starting web server on http://127.0.0.1:5500
echo   Browser will open automatically...
echo   Press Ctrl+C to stop.
echo  =============================================
echo.

python "%WEBAPP_DIR%\app.py"

pause
