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
rem The shortcuts start Python directly, so install everything Friday needs now
%PY% -c "import anthropic, PIL, googleapiclient, google_auth_oauthlib, discord, speech_recognition, edge_tts, imageio_ffmpeg" >nul 2>nul
if errorlevel 1 (
  echo Installing what Friday needs, please wait...
  %PY% -m pip install -r requirements.txt
)
if not exist ".env" if not defined ANTHROPIC_API_KEY (
  copy ".env.example" ".env" >nul
  echo Fill in your API key in Notepad after "ANTHROPIC_API_KEY=", then save and close Notepad.
  notepad ".env"
)
%PY% friday_app.py
echo.
pause
