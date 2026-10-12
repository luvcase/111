@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\screenbridge.exe (
  echo Run setup-windows.bat first.
  pause
  exit /b 1
)
echo 1. OpenAI mode - requires INTERVIEW_AI_KEY in .env
echo 2. Demo mode - does not identify images or call AI
set /p screenbridge_mode=Select 1 or 2 [1]:
set "screenbridge_demo="
if "%screenbridge_mode%"=="2" set "screenbridge_demo=--demo"
start "ScreenBridge Server" cmd /k ".venv\Scripts\screenbridge.exe serve %screenbridge_demo% --open"
echo Waiting for server...
.venv\Scripts\screenbridge.exe wait --timeout 30
if errorlevel 1 (
  echo Server did not start. Check the server window and .env.
  pause
  exit /b 1
)
if exist .runtime\phone-qr.png start "" ".runtime\phone-qr.png"
.venv\Scripts\screenbridge.exe desktop
pause
