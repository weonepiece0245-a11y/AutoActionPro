@echo off
rem Set up the Discord bot so you can talk to Friday from your phone
title Discord Setup
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found.
  pause
  exit /b 1
)
%PY% -c "import discord" >nul 2>nul
if errorlevel 1 (
  echo Installing Discord library, please wait...
  %PY% -m pip install -r requirements.txt
)
%PY% friday_discord.py setup
echo.
pause
