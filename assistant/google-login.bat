@echo off
rem Sign in to Google (Calendar + Gmail) for Friday and test the connection
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
if not exist "credentials.json" (
  echo.
  echo credentials.json was not found in this folder.
  echo Download it from Google Cloud Console and put it next to this file, then run again.
  echo.
  pause
  exit /b 1
)
echo A browser window will open. Sign in with your Google account and click Allow.
%PY% friday_google.py
echo.
pause
