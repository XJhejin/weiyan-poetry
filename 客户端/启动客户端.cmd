@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "未言诗集.exe" (
  start "" "未言诗集.exe"
  exit /b
)
python main.py
if errorlevel 1 pause
