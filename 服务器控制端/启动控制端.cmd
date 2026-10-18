@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "未言诗集服务器控制端.exe" (
    start "" "未言诗集服务器控制端.exe"
) else (
    python main.py
)
