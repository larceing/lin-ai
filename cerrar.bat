@echo off
title Cerrar Lin-AI
rem --- Apaga Lin-AI del todo: cierra el programa que escucha en el puerto 8010 ---
set CERRADO=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"127.0.0.1:8010 .*LISTENING" /c:"0.0.0.0:8010 .*LISTENING"') do (
  taskkill /PID %%p /T /F >NUL 2>&1
  set CERRADO=1
)
if "%CERRADO%"=="1" (
  echo Lin-AI se ha cerrado. Ya no gasta nada.
) else (
  echo Lin-AI no estaba abierto.
)
echo.
pause
