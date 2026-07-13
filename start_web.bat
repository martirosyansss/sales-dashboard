@echo off
echo ============================================
echo Sales Management Web Application
echo ============================================
echo.
echo Installing dependencies...
python -m pip install Flask pyodbc --quiet
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to install dependencies
    echo Please make sure Python is installed
    pause
    exit /b 1
)

echo.
echo Starting web application...
echo.
echo ============================================
echo Web application will open at:
echo http://localhost:5000
echo ============================================
echo.
echo Press Ctrl+C to stop the server
echo.

python app_v2.py

pause
