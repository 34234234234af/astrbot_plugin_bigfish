@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing python dependencies for the relay...
python -m pip install --upgrade aiohttp
echo.
echo Done. press any key to close.
pause >nul
