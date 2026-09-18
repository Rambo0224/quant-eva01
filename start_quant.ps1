[CmdletBinding()]
param([int]$Port = 8600)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$quantPython = Join-Path $PSScriptRoot '.venv-win\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $quantPython)) {
    $quantPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $quantPython)) {
    throw 'Project Python environment missing. Install project dependencies first.'
}
Write-Host "Quant: http://127.0.0.1:$Port"
& $quantPython -m app.launcher panel --port $Port
exit $LASTEXITCODE
