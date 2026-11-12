@echo off
:: Aurora v2 — One-shot launcher (Windows)
:: Starts the Python backend then launches the Electron UI.
:: See AGENTS.md rule #6: we track both processes and shut them down cleanly.

title Aurora v2

set AURORA_HOME=%~dp0
set AURORA_HOME=%AURORA_HOME:~0,-1%

echo [aurora] Home: %AURORA_HOME%

:: ── Select Python executable ───────────────────────────────────
if exist "%AURORA_HOME%\venv\Scripts\python.exe" (
    set "PY_EXE=%AURORA_HOME%\venv\Scripts\python.exe"
    call "%AURORA_HOME%\venv\Scripts\activate.bat"
) else (
    set "PY_EXE=python"
    echo [aurora] NOTE: Using system python (venv not found at %AURORA_HOME%\venv)
)

:: ── Start Python backend (detached) ──────────────────────────
echo [aurora] Starting backend via %PY_EXE%...
start "Aurora Backend" /min "%PY_EXE%" -m aurora_core
:: Give the backend a moment to bind the port before Electron tries to connect
timeout /t 3 /nobreak >nul

:: ── Start Electron ────────────────────────────────────────────
echo [aurora] Starting Electron UI...
call npm start --prefix "%AURORA_HOME%"

:: ── Cleanup: kill the backend window when Electron exits ─────
echo [aurora] Electron closed — stopping backend...
taskkill /FI "WindowTitle eq Aurora Backend" /F >nul 2>&1
echo [aurora] Done.
