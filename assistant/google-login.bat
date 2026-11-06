@echo off
rem Sign in to Google for Friday and test the connection
title Google Login
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found.
  pause
  exit /b 1
)
%PY% -c "import googleapiclient, google_auth_oauthlib" >nul 2>nul
if errorlevel 1 (
  echo Installing Google libraries, please wait...
  %PY% -m pip install -r requirements.txt
)
%PY% friday_google.py
echo.
pause
