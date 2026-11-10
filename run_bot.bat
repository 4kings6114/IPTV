@echo off
setlocal
cd /d "%~dp0"
if defined PYTHON goto custom_python
where py >nul 2>nul
if errorlevel 1 goto default_python
py -3 "%~dp0telegram_bot.py" %*
exit /b %errorlevel%
:default_python
python "%~dp0telegram_bot.py" %*
exit /b %errorlevel%
:custom_python
"%PYTHON%" "%~dp0telegram_bot.py" %*
exit /b %errorlevel%
