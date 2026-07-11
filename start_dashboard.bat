@echo off
title Sales Dashboard v2
cd /d "c:\Sales Dashboard"
echo ==================================================
echo Starting Sales Dashboard...
echo ==================================================
echo.
echo Opening browser...
start http://localhost:5000
echo.
echo Starting server (app_v2.py)...
python app_v2.py
pause
