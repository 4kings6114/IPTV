@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 bot_run.py %*
    goto finished
)
python --version >nul 2>&1
if not errorlevel 1 (
    python bot_run.py %*
    goto finished
)
echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/
echo Enable "Add Python to PATH", extract the ZIP, then open bot_run.bat again.
:finished
echo.
echo Bot stopped. Press any key to close this window.
pause >nul
popd
endlocal
