@echo off
REM Start PIDI without a console window. Lives in the system tray (bottom-right).
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m pidi
