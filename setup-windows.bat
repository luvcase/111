@echo off
setlocal
cd /d "%~dp0"
py -3 -m pip install uv==0.12.23
if errorlevel 1 goto failed
py -3 -m uv sync --frozen --extra desktop --python 3.12
if errorlevel 1 goto failed
if not exist .env copy .env.example .env >nul
echo Setup complete. Edit .env, then run start-windows.bat.
pause
exit /b 0
:failed
echo Setup failed. Check the error above. Python 3.12 and Internet access are required.
pause
exit /b 1
