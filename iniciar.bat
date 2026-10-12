@echo off
cd /d "%~dp0"
title Lin-AI prueba de campo

rem --- Buscar Python (lanzador "py" o "python") ---
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo.
  echo  ERROR: no encuentro Python en este equipo.
  echo  Esta aplicacion necesita Python 3 instalado para funcionar.
  echo  Avisa a Luis para que lo revise.
  echo.
  pause
  exit /b 1
)

rem --- Preparar el entorno la primera vez ---
if not exist ".venv\Scripts\python.exe" (
  echo Preparando la aplicacion por primera vez, espera un momento...
  %PY% -m venv .venv
  if errorlevel 1 goto fallo
)

rem --- Librerias: se comprueban en cada arranque (si ya estan, tarda un segundo) ---
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 echo AVISO: no se pudieron instalar todas las librerias; sigo con las que hay.

rem --- Si queda abierta una version anterior de Lin-AI en el puerto 8010, se cierra ---
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"127.0.0.1:8010 .*LISTENING" /c:"0.0.0.0:8010 .*LISTENING"') do (
  echo Cerrando una version anterior de Lin-AI que seguia abierta...
  taskkill /PID %%p /F >nul 2>&1
  timeout /t 1 /nobreak >nul
)

start "" http://127.0.0.1:8010
".venv\Scripts\python.exe" app.py
pause
exit /b 0

:fallo
echo.
echo  ERROR: no se pudo preparar la aplicacion (entorno o librerias).
echo  Revisa la conexion a internet y vuelve a abrir iniciar.bat.
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1
