@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Create it and install requirements first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m waitress --host=0.0.0.0 --port=3942 app:app
