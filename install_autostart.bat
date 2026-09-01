@echo off
echo [*] Dang don dep va dong cac phien lam viec cu...
taskkill /f /im PowerMonitorPC.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":38472" ^| findstr "LISTENING"') do taskkill /f /pid %%a >nul 2>&1

cd /d "%~dp0scripts"
call install_autostart.bat
exit
