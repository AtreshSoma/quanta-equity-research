@echo off
:: QUANTA Web - one-click launcher. Double-click this file (or the desktop shortcut).
:: Delegates to start.ps1 so the double-click path gets the same dependency
:: check, .env handling and SEC-contact warning as the terminal path.
title QUANTA Elite
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
pause
