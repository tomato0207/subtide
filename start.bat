@echo off
chcp 65001 >nul
title Subtide 後端（中文轉錄）
cd /d "%~dp0backend"

if not exist "venv\Scripts\python.exe" (
    echo [錯誤] 找不到虛擬環境 backend\venv，請先完成安裝。
    pause
    exit /b 1
)

rem 確保找得到 FFmpeg（winget 安裝的位置）
where ffmpeg >nul 2>nul
if errorlevel 1 (
    for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*") do (
        for /d %%B in ("%%D\ffmpeg-*") do set "PATH=%%B\bin;%PATH%"
    )
)

rem 確保找得到 Node.js（yt-dlp 解 YouTube 驗證需要）
where node >nul 2>nul
if errorlevel 1 if exist "%ProgramFiles%\nodejs\node.exe" set "PATH=%ProgramFiles%\nodejs;%PATH%"

set "PYTHONPATH=%~dp0"
set "PYTHONIOENCODING=utf-8"
set KMP_DUPLICATE_LIB_OK=TRUE

echo ============================================
echo   Subtide 後端啟動中： http://localhost:5001
echo   第一次使用會下載 Whisper 模型（約 1.6 GB）
echo   關閉此視窗即停止後端
echo ============================================
echo.

"venv\Scripts\python.exe" app.py
echo.
echo 後端已停止。
pause
