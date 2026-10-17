@echo off
REM PIDI one-time setup (Windows). Needs Python 3.11 or 3.12 (python.org or: winget install Python.Python.3.12)
REM Takes a few minutes the first time (MediaPipe + OpenCV are big). Don't press Ctrl+C while it runs.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Could not create .venv - install Python 3.12 first.
  pause
  exit /b 1
)
echo Installing packages... please wait
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
".venv\Scripts\python.exe" -m pidi --doctor
pause
