@echo off
chcp 65001 >nul
title AI 小助理
cd /d "%~dp0"

rem ---- 1. 找到 Python:优先用 py 启动器,其次 python ----
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY goto no_python

rem ---- 2. 第一次运行时自动安装依赖 ----
%PY% -c "import anthropic" >nul 2>nul
if not errorlevel 1 goto deps_ok
echo 第一次运行,正在安装依赖,请稍候...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto pip_failed
:deps_ok

rem ---- 3. 没有 .env 且没设环境变量时,创建 .env 并用记事本打开 ----
if exist ".env" goto run
if defined ANTHROPIC_API_KEY goto run
copy ".env.example" ".env" >nul
echo.
echo 已创建配置文件 .env,即将用记事本打开。
echo 请把“在这里填你的Key”换成你的 API Key,保存并关闭记事本后会自动启动。
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
echo 没有找到 Python。
echo 请到 https://www.python.org/downloads/ 下载安装,
echo 安装时务必勾选“Add python.exe to PATH”,装好后再双击本文件。
echo.
pause
exit /b 1

:pip_failed
echo.
echo 依赖安装失败,请检查网络后重试。
echo 也可以手动运行:python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause
exit /b 1
