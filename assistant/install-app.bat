@echo off
rem Install Friday as an app: icon, Desktop and Start menu shortcuts, optional start at login
title Install Friday
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found.
  pause
  exit /b 1
)
%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo Installing Pillow for the icon, please wait...
  %PY% -m pip install pillow
)
%PY% friday_app.py
echo.
pause
