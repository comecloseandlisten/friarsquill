@echo off
title VTS Setup

echo.
echo  ========================================
echo    VTS - Setup Prerequisites
echo  ========================================
echo.

:: --- Check admin ---
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [!] Run as Administrator.
    echo     Right-click - Run as administrator
    echo.
    pause
    exit /b 1
)

:: --- Python ---
echo [1/5] Checking Python...
where python >nul 2>&1
if %errorLevel% neq 0 (
    echo       Installing Python 3.12...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
) else (
    python --version
)

:: --- Node.js ---
echo [2/5] Checking Node.js...
where node >nul 2>&1
if %errorLevel% neq 0 (
    echo       Installing Node.js LTS...
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
) else (
    node --version
)

:: --- ffmpeg ---
echo [3/5] Checking ffmpeg...
where ffmpeg >nul 2>&1
if %errorLevel% neq 0 (
    echo       Installing ffmpeg...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
) else (
    echo       ffmpeg found
)

:: --- Refresh PATH ---
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "PATH=%%B;%PATH%"
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "PATH=%%B;%PATH%"

:: --- pip ---
echo [4/5] Installing Python packages...
python -m pip install --upgrade pip 2>nul
python -m pip install -r "%~dp0backend\requirements.txt"
if %errorLevel% neq 0 (
    echo [!] pip install failed.
) else (
    echo       OK
)

:: --- GPU acceleration (llama-cpp-python with CUDA) ---
echo [4b]  Installing GPU-accelerated llama-cpp-python...
python -c "from llama_cpp import Llama; l=Llama.__init__.__doc__" >nul 2>&1
python -m pip install llama-cpp-python --force-reinstall --no-cache-dir --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 2>nul
if %errorLevel% neq 0 (
    echo       [!] CUDA build not available, falling back to CPU
    echo       To use GPU: install CUDA toolkit, then re-run setup
) else (
    echo       OK - CUDA support enabled
)

:: --- npm ---
echo [5/5] Installing Node packages...
cd /d "%~dp0"
call npm install
if %errorLevel% neq 0 (
    echo [!] npm install failed.
) else (
    echo       OK
)

echo.
echo  ========================================
echo    Setup complete!
echo.
echo    Run:  npm start
echo.
echo    First launch downloads models (~2 GB)
echo  ========================================
echo.
pause
