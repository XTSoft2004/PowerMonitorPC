# PowerShell script để đăng ký Task Scheduler không bị lỗi escaping của Batch
$curDir = $PSScriptRoot
$lhmPath = Join-Path $curDir "LibreHardwareMonitor.NET.10\LibreHardwareMonitor.exe"
$lhmCfgSource = Join-Path $curDir "LibreHardwareMonitor.NET.10\LibreHardwareMonitor.config"
$lhmAppDataDir = Join-Path $env:APPDATA "LibreHardwareMonitor"
$lhmAppDataCfg = Join-Path $lhmAppDataDir "LibreHardwareMonitor.config"

# Tự động dọn dẹp các tiến trình server cũ đang chạy trước khi cài đặt
try {
    Get-Process "PowerMonitorPC" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $connections = Get-NetTCPConnection -LocalPort 38472 -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $connections) {
        if ($conn.OwningProcess) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "[+] Da don dep cac tien trinh server cu" -ForegroundColor Green
} catch {}

# Tự động nạp cấu hình Remote Web Server (cổng 8085) vào AppData
try {
    if (-not (Test-Path $lhmAppDataDir)) {
        New-Item -ItemType Directory -Force -Path $lhmAppDataDir | Out-Null
    }
    Copy-Item -Force -Path $lhmCfgSource -Destination $lhmAppDataCfg | Out-Null
    Write-Host "[+] Da napa cau hinh Remote Web Server 8085 cho LibreHardwareMonitor" -ForegroundColor Green
} catch {
    Write-Host "[!] Co loi khi chep cau hinh LHM: $_" -ForegroundColor Yellow
}

# 1. Đăng ký LibreHardwareMonitor (Silent Admin)
try {
    $actionLHM = New-ScheduledTaskAction -Execute $lhmPath
    $triggerLHM = New-ScheduledTaskTrigger -AtLogOn
    $principalLHM = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
    Register-ScheduledTask -TaskName 'PowerMonitor_LHM_SilentAdmin' -Action $actionLHM -Trigger $triggerLHM -Principal $principalLHM -Force | Out-Null
    Write-Host "[+] Da dang ky Task Scheduler cho LibreHardwareMonitor (Silent Admin)" -ForegroundColor Green
    
    # Khởi động LHM ngay lập tức qua Task Scheduler
    Start-ScheduledTask -TaskName 'PowerMonitor_LHM_SilentAdmin' -ErrorAction SilentlyContinue
} catch {
    Write-Host "[!] Loi dang ky Task LHM: $_" -ForegroundColor Red
}

# 2. Đăng ký Power Monitor Server (Hỗ trợ Standalone Portable EXE)
try {
    $exePath = Join-Path $curDir "PowerMonitorPC.exe"
    if (Test-Path $exePath) {
        $actionServer = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $curDir
    } else {
        $actionServer = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "server.py" -WorkingDirectory $curDir
    }
    $triggerServer = New-ScheduledTaskTrigger -AtLogOn
    $principalServer = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
    Register-ScheduledTask -TaskName 'PowerMonitorPC_AutoRun' -Action $actionServer -Trigger $triggerServer -Principal $principalServer -Force | Out-Null
    Write-Host "[+] Da dang ky Task Scheduler cho Power Monitor Server (Portable EXE)" -ForegroundColor Green
    
    # Khởi động Server ngay lập tức qua Task Scheduler
    Start-ScheduledTask -TaskName 'PowerMonitorPC_AutoRun' -ErrorAction SilentlyContinue
} catch {
    Write-Host "[!] Loi dang ky Task Server: $_" -ForegroundColor Red
}

# Mở trình duyệt Web Dashboard
Start-Process "http://localhost:38472"
