@echo off
title Trading Assistant Launcher
color 0A

echo.
echo  ================================================
echo    Trading Assistant - Starting Up
echo  ================================================
echo.

:: Check venv exists
if not exist "venv\Scripts\activate.bat" (
    echo  ERROR: Virtual environment not found.
    echo  Please run: python -m venv venv
    echo  Then run:   pip install -r requirements.txt
    pause
    exit /b 1
)

:: Activate venv
call venv\Scripts\activate.bat
echo  [OK] Virtual environment activated

:: Check .env exists
if not exist ".env" (
    echo  ERROR: .env file not found.
    echo  Copy .env.example to .env and fill in your API keys.
    pause
    exit /b 1
)
echo  [OK] .env file found

echo.
echo  Starting Trading Assistant Bot...
echo  Starting Dashboard...
echo.

:: Start the bot in a new window
start "Trading Assistant Bot" cmd /k "call venv\Scripts\activate.bat && python main.py"

:: Wait 3 seconds for bot to initialize
timeout /t 3 /nobreak > nul

:: Start the dashboard in a new window
start "Trading Assistant Dashboard" cmd /k "call venv\Scripts\activate.bat && python -m streamlit run alerts\dashboard.py"

echo  ================================================
echo   Trading Assistant is running!
echo.
echo   Bot:       Running in separate window
echo   Dashboard: http://localhost:8501
echo   Discord:   Check your Trading Alerts server
echo.
echo   To stop: Close the Bot and Dashboard windows
echo  ================================================
echo.
echo  This window will close in 5 seconds...
timeout /t 5 /nobreak > nul