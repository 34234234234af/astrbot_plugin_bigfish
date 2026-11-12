@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   ============================================================
echo    bigfish relay - starting on port 8317 (local network only)
echo    For public access, close this and run the tunnel .bat
echo   ============================================================
echo.
python relay_server.py --port 8317
echo.
echo   [relay stopped] press any key to close.
pause >nul
