@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Create it and install requirements first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m waitress --listen=0.0.0.0:3942 --threads=8 --channel-timeout=120 app:app
