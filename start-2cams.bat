@echo off
setlocal
title BantayPlaka - 2 Camera Mode
echo.
echo  =============================================
echo   BantayPlaka - Entry/Exit Camera Runtime
echo  =============================================
echo.

set /p ENTRY_RTSP=Enter ENTRY camera RTSP URL: 
set /p EXIT_RTSP=Enter EXIT camera RTSP URL: 

if "%ENTRY_RTSP%"=="" (
  echo ENTRY RTSP is required.
  pause
  exit /b 1
)

if "%EXIT_RTSP%"=="" (
  echo EXIT RTSP is required.
  pause
  exit /b 1
)

echo.
echo [1/3] Starting Django server...
start "BantayPlaka - Django" cmd /k "venv\Scripts\activate.bat ^&^& set ""ENTRY_CAMERA_RTSP=%ENTRY_RTSP%"" ^&^& set ""EXIT_CAMERA_RTSP=%EXIT_RTSP%"" ^&^& python manage.py runserver"

timeout /t 3 /nobreak >nul

echo [2/3] Starting ENTRY camera ANPR (TIME_IN mapping)...
start "BantayPlaka - ENTRY CAM" cmd /k "venv\Scripts\activate.bat ^&^& python anpr_engine/anpr_engine.py --rtsp ""%ENTRY_RTSP%"" --camera-role ENTRY_CAM --frame-skip 1"

echo [3/3] Starting EXIT camera ANPR (TIME_OUT mapping)...
start "BantayPlaka - EXIT CAM" cmd /k "venv\Scripts\activate.bat ^&^& python anpr_engine/anpr_engine.py --rtsp ""%EXIT_RTSP%"" --camera-role EXIT_CAM --frame-skip 1"

echo.
echo  Open: http://127.0.0.1:8000
echo  Login as guard/admin to see logs and detection feed.
echo  Keep the 3 spawned windows open (Django + ENTRY CAM + EXIT CAM).
echo  Do NOT run an extra "python manage.py runserver" manually.
echo  You may close this launcher window after pressing any key.
echo.
pause

endlocal
