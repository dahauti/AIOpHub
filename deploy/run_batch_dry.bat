@echo off
setlocal
rem One-click DRY RUN of the batch sync: reads BC and Frontu, never writes to Frontu.
rem Usage: run_batch_dry.bat [portable Python folder]   default: ..\python next to this folder

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"
set "PY_DIR=%~1"
if "%PY_DIR%"=="" set "PY_DIR=%APP_DIR%\..\python"
set "PY=%PY_DIR%\python.exe"

if not exist "%PY%" goto :no_python
if not exist "%APP_DIR%\.env" goto :no_env

rem Environment variables override .env, so this run is always a dry run.
set "SYNC_DRY_RUN=true"
cd /d "%APP_DIR%"
echo Running: "%PY%" -m src.cli sync --batch   with SYNC_DRY_RUN=true
"%PY%" -m src.cli sync --batch
set "RC=%ERRORLEVEL%"
goto :end

:no_python
echo ERROR: python.exe not found at "%PY%". Pass the portable Python folder as the first argument.
set "RC=1"
goto :end

:no_env
echo ERROR: .env not found in "%APP_DIR%". Copy .env.example to .env and fill in the secrets.
set "RC=1"

:end
echo.
echo run_batch_dry.bat finished with exit code %RC%
echo Exit code 0 also covers "skipped: another run holds the module lock". Details: logs\app.log
pause
exit /b %RC%
