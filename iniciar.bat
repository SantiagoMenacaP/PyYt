@echo off
REM Lanzador del Descargador de YouTube
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Preparando el entorno por primera vez...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo No se pudo iniciar la aplicacion. Revisa los mensajes anteriores.
pause
exit /b 1
