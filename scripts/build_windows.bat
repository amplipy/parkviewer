@echo off
REM Build the ParkViewer standalone directory on Windows.
REM Requires: Python 3.10+ on PATH.
setlocal
cd /d "%~dp0.."

python -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scripts\make_icon.py
.venv\Scripts\pyinstaller --clean --noconfirm packaging\pyinstaller.spec
if errorlevel 1 exit /b 1

rem Ship the whole one-dir bundle. tar.exe (Win10+) auto-detects zip by extension.
del dist\ParkViewer-windows.zip 2>nul
tar -a -c -f dist\ParkViewer-windows.zip -C dist ParkViewer
echo Built: dist\ParkViewer\  (+ dist\ParkViewer-windows.zip)