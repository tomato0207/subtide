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

echo [1/6] 安裝 Python 3.11 ...
winget install --id Python.Python.3.11 -e --scope user --accept-source-agreements --accept-package-agreements --silent
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PY="%LOCALAPPDATA%\Programs\Python\Python311\python.exe""
) else (
    echo [錯誤] Python 安裝失敗，請到 https://www.python.org/downloads/ 手動安裝 3.11 版後再執行本程式。
    pause
    exit /b 1
)
:have_python
echo [1/6] Python OK

rem ---------- 2. Node.js（yt-dlp 解 YouTube 驗證需要） ----------
where node >nul 2>nul
if errorlevel 1 if not exist "%ProgramFiles%\nodejs\node.exe" (
    echo [2/6] 安裝 Node.js ...
    winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements --silent
)
echo [2/6] Node.js OK

rem ---------- 3. FFmpeg ----------
where ffmpeg >nul 2>nul
if not errorlevel 1 goto :have_ffmpeg
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*" goto :have_ffmpeg
echo [3/6] 安裝 FFmpeg ...
winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent
:have_ffmpeg
echo [3/6] FFmpeg OK

rem ---------- 4. 顯示卡 ----------
where nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo [4/6] 警告：找不到 NVIDIA 顯示卡，轉錄會改用 CPU，速度會慢很多。
    echo        建議把 backend\.env 裡的 WHISPER_MODEL 改成 small。
) else (
    echo [4/6] NVIDIA 顯示卡 OK
)

rem ---------- 5. Python 套件 ----------
cd backend
if not exist ".env" (
    copy /y ".env.zh-transcribe" ".env" >nul
    echo [5/6] 已從 .env.zh-transcribe 建立設定檔 .env
)
if not exist "venv\Scripts\python.exe" (
    echo [5/6] 建立虛擬環境 ...
    %PY% -m venv venv
)
echo [5/6] 安裝套件（約 3GB，需要幾分鐘）...
"venv\Scripts\python.exe" -m pip install --upgrade pip -q
"venv\Scripts\python.exe" -m pip install -r requirements-windows.txt
if errorlevel 1 (
    echo [錯誤] 套件安裝失敗，請檢查網路後重新執行 setup.bat。
    pause
    exit /b 1
)

echo [5/6] 安裝英漢字典（滑鼠查單字用，約 66MB）...
"venv\Scripts\python.exe" scripts\install_dictionary.py
if errorlevel 1 echo [5/6] 警告：字典下載失敗，滑鼠查單字將無法使用，可稍後重新執行 setup.bat。

rem ---------- 6. Ollama + 摘要模型（章節摘要用，可略過） ----------
set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "%OLLAMA%" (
    echo [6/6] 安裝 Ollama（章節摘要用）...
    winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements --silent
)
if exist "%OLLAMA%" (
    echo [6/6] 下載摘要模型 qwen2.5:7b（約 4.7GB）...
    "%OLLAMA%" pull qwen2.5:7b
) else (
    echo [6/6] 警告：Ollama 安裝失敗，章節摘要將無法使用（字幕不受影響）。
)

echo.
echo ============================================
echo   安裝完成！
echo   之後雙擊 start.bat 啟動後端，
echo   Chrome 擴充的安裝方式請看「使用說明.md」。
echo ============================================
pause
