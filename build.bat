@echo off
title Build Portable Release
cd /d "%~dp0"
echo =======================================================
echo   BAT DAU DONG GOI PORTABLE RELEASE (ZIP)
echo =======================================================

echo [*] Dang don dep va dung cac tien trinh Dang chay...
taskkill /F /IM PowerMonitorPC.exe /T >nul 2>&1
powershell -Command "Stop-Process -Name 'PowerMonitorPC' -ErrorAction SilentlyContinue -Force" >nul 2>&1
timeout /t 2 /nobreak >nul

echo [*] Xoa cac thu muc build cu...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist PowerMonitorPC.spec del /f /q PowerMonitorPC.spec

echo [*] Dang bien dich Standalone Portable EXE qua PyInstaller...
taskkill /F /IM PowerMonitorPC.exe /T >nul 2>&1
pyinstaller --noconfirm --onedir --windowed --name PowerMonitorPC --add-data "templates;templates" --add-data "static;static" --add-data "config.json;." server.py

if not exist dist\PowerMonitorPC\PowerMonitorPC.exe (
    echo [!] LOI: Bien dich PyInstaller that bai!
    exit /b 1
)

echo [*] Dang chep LibreHardwareMonitor vao thu muc Portable...
if exist LibreHardwareMonitor.NET.10 (
    powershell -Command "Copy-Item -Path 'LibreHardwareMonitor.NET.10' -Destination 'dist\PowerMonitorPC\LibreHardwareMonitor.NET.10' -Recurse -Force"
)

echo [*] Dang chep Kich ban Khoi chay Chay_Ngay.bat...
if exist scripts\Chay_Ngay.bat (
    copy /y scripts\Chay_Ngay.bat dist\PowerMonitorPC\Chay_Ngay.bat
) else if exist Chay_Ngay.bat (
    copy /y Chay_Ngay.bat dist\PowerMonitorPC\Chay_Ngay.bat
)

echo [*] Dang nen thu muc thanh file Release ZIP Portable...
if exist PowerMonitorPC.zip del /f /q PowerMonitorPC.zip
powershell -Command "Compress-Archive -Path 'dist\PowerMonitorPC\*' -DestinationPath 'PowerMonitorPC.zip' -Force"

echo =======================================================
echo [OK] HOAN TAT! File Portable ZIP da duoc tao tai:
echo      PowerMonitorPC.zip
echo =======================================================
