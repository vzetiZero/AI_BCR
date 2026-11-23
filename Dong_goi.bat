@echo off
rem =====================================================================
rem  AI_BCR - Dong goi ban PORTABLE (chay ngay, khong can cai gi)
rem  Tao ra dist\AI_BCR_Portable.zip: Python + thu vien + Electron + Chrome
rem =====================================================================
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo   AI_BCR - Dong goi portable
echo   (Python runtime + Electron UI + Chrome, tat ca trong 1 ZIP)
echo ============================================================
echo.
echo   Can mang Internet cho lan dong goi dau (tai Python/Chrome).
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dong-goi-portable.ps1" %*
if errorlevel 1 (
  echo.
  echo [LOI] Dong goi that bai. Xem thong bao phia tren.
  pause
  exit /b 1
)

echo.
pause
endlocal
exit /b 0
