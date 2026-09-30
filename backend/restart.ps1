<#
Restart the backend, and say plainly whether it came up.

Started by hand, the backend printed a screen of log lines and left the reader
to work out whether that meant it was running. This ends on one line that
says so, in green or red:

  1. Stops this project's backend: whatever listens on port 8000, and any
     python.exe started from this folder (a restart otherwise fails with
     WinError 10013 on the port the old one still holds).
  2. Starts main.py in the background. Its output goes to backend.log and
     backend.err.log, next to this file; the previous run's are kept as
     backend.prev.log and backend.err.prev.log.
  3. Waits for /api/health, then opens and closes one browser page, which is
     what failed when Playwright could not start its driver.

A run or an execution in flight is let finish first; -Force restarts anyway.

Run it from anywhere:
    powershell -ExecutionPolicy Bypass -File backend\restart.ps1
    powershell -ExecutionPolicy Bypass -File backend\restart.ps1 -Force  # now, whatever runs
    powershell -ExecutionPolicy Bypass -File backend\restart.ps1 -Stop   # stop only
#>
param(
    [switch]$Stop,
    # Restart even if a run or an execution is in flight, which kills it.
    [switch]$Force,
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = 'Stop'
$backend = $PSScriptRoot
$python = Join-Path $backend '.venv\Scripts\python.exe'
$out = Join-Path $backend 'backend.log'
$err = Join-Path $backend 'backend.err.log'
$base = 'http://127.0.0.1:8000'

function Stop-Backend {
    $ids = @()
    $ids += Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess
    $ids += Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($backend) } |
        Select-Object -ExpandProperty ProcessId
    $ids = $ids | Where-Object { $_ -and $_ -ne $PID } | Sort-Object -Unique
    foreach ($id in $ids) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
    # The port is let go a moment after the process that held it.
    $until = (Get-Date).AddSeconds(15)
    while ((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) -and
           (Get-Date) -lt $until) {
        Start-Sleep -Milliseconds 300
    }
    return @($ids).Count
}

function Test-Busy {
    try {
        $health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 3
        return [bool]$health.busy
    } catch {
        return $false  # Not answering: nothing in flight to protect.
    }
}

# A restart kills whatever is in flight, so a run is let finish first.
if (-not $Stop -and -not $Force -and (Test-Busy)) {
    Write-Host "A run or an execution is in progress; restarting once it ends (-Force to restart now)..."
    $until = (Get-Date).AddMinutes(30)
    while ((Test-Busy) -and (Get-Date) -lt $until) {
        Start-Sleep -Seconds 3
    }
}

$stopped = Stop-Backend
Write-Host "Stopped $stopped backend process(es)."
if ($Stop) { exit 0 }

if (-not (Test-Path $python)) {
    Write-Host "Backend did not start: $python is missing. Create the venv first (see README)." -ForegroundColor Red
    exit 1
}

# The last run's output is kept one restart back: when the backend died on its
# own, starting it again must not erase the reason it died.
foreach ($file in @($out, $err)) {
    if (Test-Path $file) {
        try {
            Move-Item -Force $file ($file -replace '\.log$', '.prev.log') -ErrorAction Stop
        } catch {
            Write-Host "Could not keep $([IO.Path]::GetFileName($file)): $($_.Exception.Message)"
        }
    }
}

$env:PYTHONUNBUFFERED = '1'
Start-Process -FilePath $python -ArgumentList 'main.py' -WorkingDirectory $backend `
    -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err | Out-Null

function Show-Log {
    foreach ($file in @($err, $out)) {
        if (Test-Path $file) {
            Write-Host "--- $([IO.Path]::GetFileName($file)) (last lines) ---"
            Get-Content $file -Tail 25
        }
    }
}

# 1. The API answers.
$until = (Get-Date).AddSeconds($TimeoutSeconds)
$up = $false
while ((Get-Date) -lt $until) {
    try {
        $answer = Invoke-WebRequest -UseBasicParsing -Uri "$base/api/health" -TimeoutSec 3
        if ($answer.StatusCode -eq 200) { $up = $true; break }
    } catch {
        Start-Sleep -Milliseconds 700
    }
}
if (-not $up) {
    Write-Host "Backend did not start within $TimeoutSeconds s." -ForegroundColor Red
    Show-Log
    exit 1
}

# 2. A browser page opens — the part that failed with NotImplementedError when
# the server ran on an event loop that cannot start Playwright's driver.
try {
    $page = Invoke-RestMethod -Method Post -Uri "$base/api/web/session" -ContentType 'application/json' `
        -Body '{"url": "http://127.0.0.1:8000/api/health", "headless": true}' -TimeoutSec 60
    Invoke-RestMethod -Method Delete -Uri "$base/api/session/$($page.sessionId)" -TimeoutSec 30 | Out-Null
} catch {
    Write-Host "Backend is up at $base, but it could not open a browser page: $($_.Exception.Message)" -ForegroundColor Red
    Show-Log
    exit 1
}

Write-Host "Backend is up at $base, and it opens browser pages. Log: $out, $err" -ForegroundColor Green
exit 0
