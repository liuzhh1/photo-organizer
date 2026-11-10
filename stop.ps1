param([ValidateRange(1, 65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 3 -UseBasicParsing
if ($health.app -ne 'frame-photo' -or $health.data -ne (Join-Path $PSScriptRoot '.data')) { throw 'This is not the FRAME instance for this project.' }
$state = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/state" -TimeoutSec 3 -UseBasicParsing
if ($state.job.running) { throw 'A task is running. Cancel it in the app and wait for it to finish before stopping.' }
$targetProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$health.pid)"
$expectedApp = Join-Path $PSScriptRoot 'app.py'
$expectedEntry = Join-Path $PSScriptRoot 'run_server.py'
if (-not $targetProcess -or ($targetProcess.CommandLine -notlike "*$expectedApp*" -and $targetProcess.CommandLine -notlike "*$expectedEntry*")) { throw 'Cannot verify the server process. No process was stopped.' }
Stop-Process -Id $targetProcess.ProcessId
Write-Host 'FRAME has stopped.'
