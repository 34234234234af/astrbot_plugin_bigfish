@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   ============================================================
echo    bigfish relay + cloudflared quick tunnel
echo    Wait for the https://xxxx.trycloudflare.com address below,
echo    then paste it into the plugin config "public_base_url".
echo   ============================================================
echo.
python relay_server.py --port 8317 --tunnel
echo.
echo   [relay stopped] press any key to close.
pause >nul
