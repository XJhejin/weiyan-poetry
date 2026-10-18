@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 未言诗集 · 服务端管理
if exist "未言诗集服务端.exe" (
  start "" "未言诗集服务端.exe"
  exit /b
)
where python >nul 2>nul
if errorlevel 1 goto usepy
python server.py
goto done
:usepy
where py >nul 2>nul
if errorlevel 1 goto missing
py -3 server.py
goto done
:missing
echo 请从 GitHub Releases 下载完整服务端，解压后双击 exe 即可运行。
echo 源码开发需要 Python 3.12 及 requirements.txt 中的依赖。
:done
pause
