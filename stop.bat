@echo off
REM Stop the voice assistant and unload Ollama models to free RAM
cd /d "%~dp0"
setlocal EnableExtensions

echo [stop] Stopping Bayan / assistant (main.py)...
REM Kill python processes whose command line includes this project's main.py
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='python3.exe'\" ^| ^
   Where-Object { $_.CommandLine -and ($_.CommandLine -like '*main.py*') } ^| ^
   ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM Fallback blunt kill if PowerShell filter missed something (comment out if too aggressive)
REM taskkill /F /IM python.exe /T >nul 2>&1

echo [stop] Unloading Ollama models from RAM...
where ollama >nul 2>&1
if %ERRORLEVEL%==0 (
  for /f "skip=1 tokens=1" %%M in ('ollama ps 2^>nul') do (
    if not "%%M"=="" (
      echo   -^> ollama stop %%M
      ollama stop %%M >nul 2>&1
    )
  )
) else (
  echo   (ollama not found in PATH — skipped)
)

echo [stop] Done. Assistant stopped; Ollama models unloaded.
endlocal
