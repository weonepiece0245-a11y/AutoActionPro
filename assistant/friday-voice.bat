@echo off
rem Talk to "Friday" by voice (installs voice dependencies on first run)
set "ASSISTANT_VOICE=1"
call "%~dp0start.bat" --persona friday --voice %*
