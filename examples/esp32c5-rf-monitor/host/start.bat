@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   esp-sdr Test Host (PySide6 + WebView)
echo ==========================================
echo.

python -c "import PySide6, serial" >nul 2>&1
if errorlevel 1 (
    echo [FIRST RUN] Installing dependencies, please wait...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERROR] Dependency install failed.
        pause
        exit /b 1
    )
)

echo [START] Launching host app...
echo   - real board : python app.py --port COM56 --gain-index 60
echo   - no board   : python app.py --mock --scenario wifi_busy
echo.
python app.py %*

if errorlevel 1 (
    echo.
    echo [ERROR] App exited with error. See message above.
    pause
)