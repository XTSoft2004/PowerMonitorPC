@echo off
title Tao Release Tu Dong Tren GitHub
cd /d "%~dp0"
echo =======================================================
echo   BAT DAU TẠO RELEASE TỰ ĐỘNG LÊN GITHUB
echo =======================================================
set /p tag="Nhap Version Tag (vi du: v1.0.0 hoac v1.1.0): "
if "%tag%"=="" (
    echo [!] LOI: Version Tag khong duoc de trong!
    pause
    exit /b 1
)

echo [*] Dang tao va day Tag %tag% len GitHub...
git add .
git commit -m "Release %tag%"
git tag %tag%
git push origin main --force
git push origin %tag% --force

echo =======================================================
echo [OK] HOAN TAT! GitHub Actions dang tu dong Build
echo      va Upload file PowerMonitorPC.zip len GitHub Release!
echo      Kiem tra tai: https://github.com/XTSoft2004/PowerMonitorPC/releases
echo =======================================================
pause
