@echo off
set "ASSISTANT_TITLE=Friday"
rem Start the personal assistant "Friday" (same setup steps as start.bat)
call "%~dp0start.bat" --persona friday %*
