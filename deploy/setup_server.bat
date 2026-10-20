@echo off
setlocal
rem AIOpHub server setup for an embeddable (portable) Python 3.12.
rem Usage: setup_server.bat [portable Python folder]   default: ..\python next to this folder
rem Steps: pre-flight checks, configure python*._pth, check pip, install requirements, import check.

set "APP_DIR=%~dp0"
set "APP_DIR=%APP_DIR:~0,-1%"
set "PY_DIR=%~1"
if "%PY_DIR%"=="" set "PY_DIR=%APP_DIR%\..\python"
set "PY=%PY_DIR%\python.exe"
set "RC=0"

echo [1/5] Pre-flight checks
if not exist "%PY%" goto :no_python
if not exist "%APP_DIR%\requirements.txt" goto :no_requirements
if not exist "%APP_DIR%\.env" echo WARNING: .env is missing. Copy .env.example to .env and fill in the secrets before any sync.
"%PY%" --version

echo [2/5] Configuring the ._pth file: enable "import site" and add the application folder
"%PY%" "%APP_DIR%\scripts\configure_pth.py" "%APP_DIR%"
if errorlevel 1 goto :failed

echo [3/5] Checking pip
"%PY%" -m pip --version
if errorlevel 1 goto :no_pip

echo [4/5] Installing requirements.txt
"%PY%" -m pip install -r "%APP_DIR%\requirements.txt"
if errorlevel 1 goto :failed

echo [5/5] Import check
pushd "%APP_DIR%"
"%PY%" -c "import src.cli; print('AIOpHub import OK')"
set "RC=%ERRORLEVEL%"
popd
goto :end

:no_python
echo ERROR: python.exe not found at "%PY%".
echo Pass the portable Python folder as the first argument, e.g. setup_server.bat D:\python-3.12.8-embed-amd64
set "RC=1"
goto :end

:no_requirements
echo ERROR: requirements.txt not found in "%APP_DIR%". Unpack the whole AIOpHub_Deploy.zip.
set "RC=1"
goto :end

:no_pip
echo ERROR: pip is not installed in this portable Python.
echo Download https://bootstrap.pypa.io/get-pip.py on a machine with internet access,
echo copy it next to python.exe, run "%PY%" get-pip.py and then run this script again.
set "RC=1"
goto :end

:failed
set "RC=1"

:end
echo.
echo setup_server.bat finished with exit code %RC%
pause
exit /b %RC%
