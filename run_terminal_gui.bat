@echo off
REM Launch GUI + terminal typing in this console (voice wake word still works)
REM First run: creates .venv, installs deps, downloads models
cd /d "%~dp0"
setlocal EnableExtensions

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 setup_env.py
) else (
  where python >nul 2>&1
  if %ERRORLEVEL%==0 (
    python setup_env.py
  ) else (
    echo [run] ERROR: Python 3.10+ is required. Install from https://www.python.org/downloads/
    exit /b 1
  )
)
if errorlevel 1 exit /b 1

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [run] ERROR: .venv not found after setup.
  exit /b 1
)

"%~dp0.venv\Scripts\python.exe" main.py --both %*
endlocal
