param(
    [string]$CondaEnv = 'matanyone2',
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$Foreground
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$baseUrl = "http://127.0.0.1:$Port"
function Get-FrameHealth {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2 -UseBasicParsing
        if ($health.app -eq 'frame-photo' -and $health.data -eq (Join-Path $projectRoot '.data')) { return $health }
    } catch { }
    return $null
}
if (Get-FrameHealth) {
    Write-Host "FRAME is already running: $baseUrl"
    if (-not $NoBrowser) { Start-Process $baseUrl }
    exit 0
}
$condaCommand = Get-Command conda -ErrorAction SilentlyContinue
if (-not $condaCommand) {
    $condaPaths = @($env:CONDA_EXE, "$env:USERPROFILE\miniconda3\Scripts\conda.exe", "$env:USERPROFILE\anaconda3\Scripts\conda.exe")
    foreach ($condaPath in $condaPaths) {
        if ($condaPath -and (Test-Path -LiteralPath $condaPath)) { $condaCommand = Get-Item -LiteralPath $condaPath; break }
    }
}
if (-not $condaCommand) { throw 'Conda was not found. Open Anaconda PowerShell Prompt and run this script.' }
$condaExe = if ($condaCommand.Source) { $condaCommand.Source } else { $condaCommand.FullName }
$environmentInfo = & $condaExe env list --json | Out-String | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot list existing Conda environments.' }
$environmentRoot = $environmentInfo.envs | Where-Object { (Split-Path $_ -Leaf) -eq $CondaEnv } | Select-Object -First 1
if ($CondaEnv -eq 'base') { $environmentRoot = Split-Path (Split-Path $condaExe -Parent) -Parent }
if (-not $environmentRoot) { throw "Existing Conda environment not found: $CondaEnv" }
$pythonExe = Join-Path $environmentRoot 'python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Python is missing in $environmentRoot" }
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
$env:TOKENIZERS_PARALLELISM = 'false'
$env:PYTHONUTF8 = '1'
$env:CONDA_PREFIX = $environmentRoot
$env:CONDA_DEFAULT_ENV = $CondaEnv
$env:PATH = "$environmentRoot;$(Join-Path $environmentRoot 'Library\bin');$(Join-Path $environmentRoot 'Scripts');$env:PATH"
$appFile = Join-Path $projectRoot 'app.py'
if ($Foreground) {
    Write-Host "Starting FRAME in foreground with existing Conda environment: $CondaEnv"
    & $pythonExe -u $appFile --port $Port --no-browser
    exit $LASTEXITCODE
}
$logDirectory = Join-Path $projectRoot '.data'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$logId = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdoutLog = Join-Path $logDirectory "server-$logId.log"
$stderrLog = Join-Path $logDirectory "server-$logId.error.log"
$logPrefix = Join-Path $logDirectory "server-$logId"
# Explicitly break away from temporary execution jobs; never inherit terminal handles.
$launchOutput = & $pythonExe (Join-Path $projectRoot 'launch_server.py') --port $Port --log-prefix $logPrefix
if ($LASTEXITCODE -ne 0) { throw 'Windows refused detached startup. Use -Foreground in your own PowerShell window.' }
$serverPid = [int]$launchOutput
$ready = $false
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    $health = Get-FrameHealth
    if ($health -and $health.pid -eq $serverPid) { $ready = $true; break }
    Start-Sleep -Milliseconds 300
}
if (-not $ready) {
    if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Tail 20 | Write-Host }
    throw "FRAME did not start. Check port $Port and logs: $logDirectory"
}
@{ pid = $serverPid; port = $Port; python = $pythonExe; stdout = $stdoutLog; stderr = $stderrLog } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory "server-$Port.json") -Encoding UTF8
Write-Host "FRAME is running in the background: $baseUrl"
Write-Host "Existing Conda environment: $CondaEnv | PID: $serverPid"
Write-Host "Logs: $logDirectory"
Write-Host 'The launcher can close. To stop the app, use stop.ps1.'
if (-not $NoBrowser) { Start-Process $baseUrl }
