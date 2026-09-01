@echo off
title Cai dat Auto-Start Khong UAC
cd /d "%~dp0"
echo [*] Dang xin quyen Admin 1 lan duy nhat de dang ky Task Scheduler...
powershell -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%~dp0register_tasks.ps1\"' -Verb RunAs"
exit
