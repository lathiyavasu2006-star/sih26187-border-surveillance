# Starts the whole SIH26187 prototype (backend -> frontend -> ML pipeline) in the right order and keeps
# one window with the console URL. Launched by START-PROTOTYPE.bat; STOP-PROTOTYPE.bat calls it with -Stop.
#
#   .\start_prototype.ps1            start everything, print the URL, stop it all when you press Q
#   .\start_prototype.ps1 -NoWait    start everything and leave it running (for scripts)
#   .\start_prototype.ps1 -Stop      stop whatever an earlier run started
param(
    [switch]$NoWait,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
$pidFile = Join-Path $logs 'prototype-pids.json'
$backendUrl = 'http://127.0.0.1:8000/ping'
$consoleUrl = 'http://localhost:5173'

if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }

function Write-Step($text) { Write-Host "  $text" -ForegroundColor Gray }
function Write-Ok($text) { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [WARN] $text" -ForegroundColor Yellow }
function Write-Fail($text) { Write-Host "  [FAIL] $text" -ForegroundColor Red }

function Test-Url($url) {
    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Test-Port($portNumber) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect('127.0.0.1', $portNumber)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

# Kills a service and everything it spawned (npm/node and python child processes).
function Stop-Tree($processId, $label) {
    if (-not $processId) { return }
    $running = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $running) { return }
    & taskkill.exe /PID $processId /T /F 2>&1 | Out-Null
    Write-Step "stopped $label (pid $processId)"
}

function Stop-Everything {
    if (-not (Test-Path $pidFile)) {
        Write-Warn 'Nothing was started by this launcher.'
        return
    }
    $saved = Get-Content $pidFile -Raw | ConvertFrom-Json
    foreach ($name in 'ml', 'frontend', 'backend') {
        Stop-Tree $saved.$name $name
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
    Write-Ok 'All three services stopped.'
}

if ($Stop) {
    Write-Host ''
    Write-Host '  SIH26187 - stopping the prototype' -ForegroundColor Cyan
    Stop-Everything
    Write-Host ''
    return
}

# Start one service as a child process with its output in logs\<name>.log.
function Start-Service-Process($name, $exe, $argumentList, $workingDirectory) {
    $out = Join-Path $logs "$name.log"
    $err = Join-Path $logs "$name.err.log"
    $process = Start-Process -FilePath $exe -ArgumentList $argumentList -WorkingDirectory $workingDirectory `
        -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -PassThru
    return $process
}

function Show-LogTail($logFiles) {
    foreach ($file in $logFiles) {
        if ((Test-Path $file) -and (Get-Item $file).Length -gt 0) {
            Write-Host "         --- $file" -ForegroundColor DarkGray
            Get-Content $file -Tail 8 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
        }
    }
}

function Wait-For($name, $check, $process, $timeoutSeconds, $logFiles) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($process -and $process.HasExited) {
            Write-Fail "$name stopped immediately (exit code $($process.ExitCode))."
            Show-LogTail $logFiles
            return $false
        }
        if (& $check) { return $true }
        Start-Sleep -Milliseconds 700
    }
    Write-Fail "$name did not come up within $timeoutSeconds seconds."
    Show-LogTail $logFiles
    return $false
}

Write-Host ''
Write-Host '  ===============================================================' -ForegroundColor Cyan
Write-Host '   SIH26187 - AI Border Surveillance System (MHA / SSB)' -ForegroundColor Cyan
Write-Host '   Starting: database check -> backend -> console -> ML pipeline' -ForegroundColor Cyan
Write-Host '  ===============================================================' -ForegroundColor Cyan
Write-Host ''

# ---------------------------------------------------------------- prerequisites
$python = 'C:\pythonjarvis\python.exe'
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if (-not $found) { Write-Fail 'Python not found. Install Python 3.11 or fix the path in this script.'; exit 1 }
    $python = $found.Source
}
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Write-Fail 'Node.js not found. Install Node 20+ and try again.'; exit 1 }
$viteEntry = Join-Path $root 'frontend\node_modules\vite\bin\vite.js'
if (-not (Test-Path $viteEntry)) { Write-Fail 'Frontend packages are missing. Run "npm install" inside the frontend folder.'; exit 1 }

if (Test-Port 5433) { Write-Ok 'PostgreSQL is reachable on port 5433' }
else { Write-Fail 'PostgreSQL is not running on port 5433. Start the PostgreSQL service first.'; exit 1 }

# A previous run of this launcher must not leave duplicate servers behind (Windows lets several bind :8000).
if (Test-Path $pidFile) {
    Write-Step 'Cleaning up the previous run...'
    Stop-Everything
    Start-Sleep -Seconds 1
}

$started = @{ backend = $null; frontend = $null; ml = $null }
$ok = $true

# ---------------------------------------------------------------- 1. backend
if (Test-Url $backendUrl) {
    Write-Warn 'Backend was already running on port 8000 - using it (this launcher will not stop it).'
} else {
    Write-Step 'Starting the backend API on http://127.0.0.1:8000 ...'
    $backend = Start-Service-Process 'backend' $python @('-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000') $root
    $started.backend = $backend.Id
    $ok = Wait-For 'Backend' { Test-Url $backendUrl } $backend 90 @((Join-Path $logs 'backend.err.log'), (Join-Path $logs 'backend.log'))
    if ($ok) { Write-Ok "Backend API ready (pid $($backend.Id))" }
}

# ---------------------------------------------------------------- 2. frontend
if ($ok) {
    if (Test-Url $consoleUrl) {
        Write-Warn 'Console was already running on port 5173 - using it (this launcher will not stop it).'
    } else {
        Write-Step "Starting the operator console on $consoleUrl ..."
        $frontend = Start-Service-Process 'frontend' $node.Source @($viteEntry) (Join-Path $root 'frontend')
        $started.frontend = $frontend.Id
        $ok = Wait-For 'Console' { Test-Url $consoleUrl } $frontend 90 @((Join-Path $logs 'frontend.err.log'), (Join-Path $logs 'frontend.log'))
        if ($ok) { Write-Ok "Operator console ready (pid $($frontend.Id))" }
    }
}

# ---------------------------------------------------------------- 3. ML pipeline (needs the backend up: it exits with code 4 otherwise)
if ($ok) {
    Write-Step 'Starting the ML pipeline (YOLOv8x TensorRT + ByteTrack) - the model takes a few seconds to load...'
    $ml = Start-Service-Process 'ml' $python @('-m', 'ml.main') $root
    $started.ml = $ml.Id
    # The pipeline logs to stdout; "pipeline started" appears once the model is loaded and the camera is open.
    $mlLogs = @((Join-Path $logs 'ml.log'), (Join-Path $logs 'ml.err.log'))
    $ok = Wait-For 'ML pipeline' {
        foreach ($file in $mlLogs) {
            if ((Test-Path $file) -and (Select-String -Path $file -Pattern 'pipeline started' -Quiet)) { return $true }
        }
        return $false
    } $ml 240 $mlLogs
    if ($ok) { Write-Ok "ML pipeline running (pid $($ml.Id))" }
}

$started | ConvertTo-Json | Set-Content -Path $pidFile -Encoding utf8

if (-not $ok) {
    Write-Host ''
    Write-Fail 'Startup failed - stopping what had already started.'
    Stop-Everything
    Write-Host ''
    if (-not $NoWait) { Read-Host '  Press Enter to close' }
    return
}

Write-Host ''
Write-Host '  ===============================================================' -ForegroundColor Cyan
Write-Host '   PROTOTYPE IS RUNNING' -ForegroundColor Green
Write-Host ''
Write-Host "   Open the console:  $consoleUrl" -ForegroundColor White
Write-Host '   (Ctrl+Click the link above, or press O to open it here)' -ForegroundColor DarkGray
Write-Host ''
Write-Host '   API docs:          http://127.0.0.1:8000/docs' -ForegroundColor DarkGray
Write-Host "   Logs:              $logs\backend.log, frontend.log, ml.log" -ForegroundColor DarkGray
Write-Host '  ===============================================================' -ForegroundColor Cyan
Write-Host ''

if ($NoWait) {
    Write-Host '  Leaving everything running. Stop it with STOP-PROTOTYPE.bat' -ForegroundColor Yellow
    Write-Host ''
    return
}

Write-Host '  Press Q to stop all three services and close, or O to open the console.' -ForegroundColor Yellow
Write-Host ''

try {
    while ($true) {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq 'Q') { break }
            if ($key.Key -eq 'O') { Start-Process $consoleUrl }
        }
        # A service that dies on its own (camera unplugged, port taken) must not look healthy.
        foreach ($name in 'backend', 'frontend', 'ml') {
            $servicePid = $started.$name
            if ($servicePid -and -not (Get-Process -Id $servicePid -ErrorAction SilentlyContinue)) {
                Write-Warn "$name stopped unexpectedly - see $logs\$name.err.log"
                $started.$name = $null
            }
        }
        Start-Sleep -Milliseconds 400
    }
} finally {
    Write-Host ''
    Write-Host '  Shutting down...' -ForegroundColor Cyan
    Stop-Everything
    Write-Host ''
    Start-Sleep -Milliseconds 800
}
