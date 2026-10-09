@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
	echo Creating virtual environment...
	python -m venv .venv
	if errorlevel 1 goto setup_failed
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed
".venv\Scripts\python.exe" -m waitress --listen=0.0.0.0:5005 app:app
goto end

:setup_failed
echo Failed to prepare Python dependencies. Check that Python is installed and try again.

:end
pause