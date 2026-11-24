@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "GIT=C:\Program Files\Git\cmd\git.exe"
if not exist "%GIT%" set "GIT=git"

echo Updating AI_BCR from origin/main...
"%GIT%" pull --ff-only origin main
if errorlevel 1 goto :failed

echo.
echo Update completed.
pause
exit /b 0

:failed
echo.
echo Update failed. Check your network, Git installation, and local changes.
pause
exit /b 1
