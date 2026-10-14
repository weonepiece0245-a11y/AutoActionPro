@echo off
title AI Assistant
cd /d "%~dp0"

rem ---- 1. Find Python: prefer the py launcher, then python ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto no_python

rem ---- 2. Install dependencies on first run ----
%PY% -c "import anthropic" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo First run: installing dependencies, please wait...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed
:deps_ok

rem ---- 3. No .env and no environment variable: create .env and open it in Notepad ----
if exist ".env" goto run
if defined ANTHROPIC_API_KEY goto run
copy ".env.example" ".env" >nul
echo.
echo Created config file .env. It will now open in Notepad.
echo Replace the placeholder after "ANTHROPIC_API_KEY=" with your API key,
echo then save and close Notepad. The assistant will start automatically.
echo.
pause
notepad ".env"

:run
cls
%PY% assistant.py %*
echo.
pause
exit /b

:no_python
echo Python was not found.
echo Download it from https://www.python.org/downloads/
echo During setup, make sure to check "Add python.exe to PATH",
echo then double-click this file again.
echo.
pause
exit /b 1

:pip_failed
echo.
echo Failed to install dependencies. Please check your network and try again.
echo You can also run manually: python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause
exit /b 1
