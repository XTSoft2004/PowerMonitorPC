@echo off
title Power Monitor PC (Portable)
cd /d "%~dp0.."
echo ===================================================
echo   POWER MONITOR PC - PHIEN BAN PORTABLE DONG GOI
echo ===================================================
echo [*] Dang khoi chay LibreHardwareMonitor chay ngam Khung khay he thong...
if exist "LibreHardwareMonitor.NET.10\LibreHardwareMonitor.exe" (
    start "" "LibreHardwareMonitor.NET.10\LibreHardwareMonitor.exe"
)
echo [*] Dang khoi chay Monitor Engine va Khay He Thong (Tray Icon)...
if exist "PowerMonitorPC.exe" (
    start "" "PowerMonitorPC.exe"
) else (
    start "" pythonw.exe server.py
)
echo [*] Dang mo Web Dashboard localhost:38472...
timeout /t 2 /nobreak >nul
start http://localhost:38472
exit
