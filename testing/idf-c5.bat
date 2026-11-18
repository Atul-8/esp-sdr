@echo off
rem ESP32-C5 build/flash helper for this machine.
rem - Bash 里调用：cmd.exe //c "testing\idf-c5.bat build"
rem - 只允许 COM56（见仓库根 AGENTS.md）
setlocal
set "MSYSTEM="
set "IDF_PATH=D:\esp\v6.1\esp-idf"
set "IDF_PYTHON_ENV_PATH=D:\Espressif\python_env\idf6.1_py3.12_env"
set "PORT=COM56"

call "%IDF_PATH%\export.bat"
if errorlevel 1 (
    echo [fail] export.bat failed
    exit /b 1
)

cd /d "%~dp0.."

if /i "%~1"=="version"  ( idf.py --version & goto :end )
if /i "%~1"=="settarget" ( idf.py set-target esp32c5 & goto :end )
if /i "%~1"=="build"    ( idf.py build & goto :end )
if /i "%~1"=="flash"    ( idf.py -p %PORT% flash & goto :end )
if /i "%~1"=="efuse"    ( idf.py -p %PORT% efuse-summary & goto :end )

echo usage: idf-c5.bat [version^|settarget^|build^|flash^|efuse]
exit /b 2

:end
exit /b %errorlevel%