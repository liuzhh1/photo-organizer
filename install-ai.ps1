param(
    [string]$CondaEnv = 'matanyone2',
    [string]$InstallerEnv = 'Py3.12',
    [string]$IndexUrl = 'https://pypi.tuna.tsinghua.edu.cn/simple'
)
$ErrorActionPreference = 'Stop'
$condaCommand = Get-Command conda -ErrorAction SilentlyContinue
if (-not $condaCommand) {
    foreach ($candidate in @($env:CONDA_EXE, "$env:USERPROFILE\miniconda3\Scripts\conda.exe", "$env:USERPROFILE\anaconda3\Scripts\conda.exe")) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $condaCommand = Get-Item -LiteralPath $candidate; break }
    }
}
if (-not $condaCommand) { throw 'Run from Anaconda PowerShell Prompt.' }
$condaExe = if ($condaCommand.Source) { $condaCommand.Source } else { $condaCommand.FullName }
Write-Host "Using existing Conda environment: $CondaEnv"
Write-Host 'AI packages go into project/.vendor. The shared environment will not be upgraded.'
$pythonVersion = (& $condaExe run -n $CondaEnv python -c "import sys; print(str(sys.version_info.major)+'.'+str(sys.version_info.minor))" | Out-String).Trim()
$pythonTag = $pythonVersion.Replace('.', '')
$vendorDirectory = Join-Path $PSScriptRoot ".vendor-py$pythonTag"
& $condaExe run --no-capture-output -n $InstallerEnv python -m pip install --upgrade --target $vendorDirectory --python-version $pythonVersion --platform win_amd64 --implementation cp --abi "cp$pythonTag" --only-binary=:all: -r (Join-Path $PSScriptRoot 'requirements-ai.txt') --index-url $IndexUrl
if ($LASTEXITCODE -ne 0) { throw 'AI dependency installation failed.' }
