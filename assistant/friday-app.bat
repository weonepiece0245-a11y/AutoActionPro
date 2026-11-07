@echo off
rem Start Friday with its own window (no black console). Installs dependencies on first run.
cd /d "%~dp0"
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found. Download it from https://www.python.org/downloads/
  pause
  exit /b 1
)
set "PYW="
where pyw >nul 2>nul && set "PYW=pyw -3"
if not defined PYW where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW set "PYW=%PY%"

%PY% -c "import anthropic, PIL, googleapiclient, google_auth_oauthlib, discord, speech_recognition, edge_tts, imageio_ffmpeg" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo First run: installing dependencies, please wait...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo Failed to install dependencies. Please check your network and try again.
  pause
  exit /b 1
)
:deps_ok

if exist ".env" goto run
if defined ANTHROPIC_API_KEY goto run
copy ".env.example" ".env" >nul
echo Fill in your API key in Notepad after "ANTHROPIC_API_KEY=", then save and close Notepad.
notepad ".env"

:run
start "" %PYW% assistant.py --persona friday --web %*
