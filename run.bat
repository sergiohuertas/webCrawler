@echo off
REM Ejecuta el crawler con el Python del entorno virtual (.venv).
REM Uso:  run.bat https://www.directoalpaladar.com/ --max-pages 10
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creando entorno virtual...
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv .venv || goto :err
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :err
)

".venv\Scripts\python.exe" crawler.py %*
goto :eof

:err
echo.
echo No se ha podido preparar el entorno. Comprueba que Python 3.12 esta instalado.
exit /b 1
