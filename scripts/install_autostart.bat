@echo off
title Cai dat Auto-Start Khong UAC
cd /d "%~dp0"
echo [*] Dang don dep va dong cac phien lam viec cu...
taskkill /f /im PowerMonitorPC.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":38472" ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1

echo [*] Dang xin quyen Admin 1 lan duy nhat de dang ky Task Scheduler...
powershell -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%~dp0register_tasks.ps1\"' -Verb RunAs"
exit
