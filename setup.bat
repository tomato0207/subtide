@echo off
chcp 65001 >nul
title Subtide 安裝程式
cd /d "%~dp0"

echo ============================================
echo   Subtide 安裝（第一次在這台電腦使用時執行）
echo ============================================
echo.

rem ---------- 1. Python 3.11 ----------
set "PY="
py -3.11 --version >nul 2>nul && set "PY=py -3.11"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
if defined PY goto :have_python

echo [1/5] 安裝 Python 3.11 ...
winget install --id Python.Python.3.11 -e --scope user --accept-source-agreements --accept-package-agreements --silent
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
) else (
    echo [錯誤] Python 安裝失敗，請到 https://www.python.org/downloads/ 手動安裝 3.11 版後再執行本程式。
    pause
    exit /b 1
)
:have_python
echo [1/5] Python OK

rem ---------- 2. Node.js（yt-dlp 解 YouTube 驗證需要） ----------
where node >nul 2>nul
if errorlevel 1 if not exist "%ProgramFiles%\nodejs\node.exe" (
    echo [2/5] 安裝 Node.js ...
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements --silent
)
echo [2/5] Node.js OK

rem ---------- 3. FFmpeg ----------
where ffmpeg >nul 2>nul
if not errorlevel 1 goto :have_ffmpeg
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*" goto :have_ffmpeg
echo [3/5] 安裝 FFmpeg ...
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent
:have_ffmpeg
echo [3/5] FFmpeg OK

rem ---------- 4. 顯示卡 ----------
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo [4/5] 警告：找不到 NVIDIA 顯示卡，轉錄會改用 CPU，速度會慢很多。
    echo        建議把 backend\.env 裡的 WHISPER_MODEL 改成 small。
) else (
    echo [4/5] NVIDIA 顯示卡 OK
)

rem ---------- 5. Python 套件 ----------
cd backend
if not exist ".env" (
    copy /y ".env.zh-transcribe" ".env" >nul
    echo [5/5] 已從 .env.zh-transcribe 建立設定檔 .env
)
if not exist "venv\Scripts\python.exe" (
    echo [5/5] 建立虛擬環境 ...
    %PY% -m venv venv
)
echo [5/5] 安裝套件（約 3GB，需要幾分鐘）...
"venv\Scripts\python.exe" -m pip install --upgrade pip -q
"venv\Scripts\python.exe" -m pip install -r requirements-windows.txt
if errorlevel 1 (
    echo [錯誤] 套件安裝失敗，請檢查網路後重新執行 setup.bat。
    pause
    exit /b 1
)

echo.
echo ============================================
echo   安裝完成！
echo   之後雙擊 start.bat 啟動後端，
echo   Chrome 擴充的安裝方式請看「使用說明.md」。
echo ============================================
pause
