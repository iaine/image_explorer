@echo off
rem Double-click friendly wrapper around build_windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
if errorlevel 1 (
    echo.
    echo Build FAILED.
) else (
    echo.
    echo Build finished.
)
pause
