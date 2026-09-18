[CmdletBinding()]
param(
    [string]$End = '',
    [string]$Dataset = 'all',
    [string]$Symbols = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$syncPython = Join-Path $PSScriptRoot '.venv-win\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $syncPython)) {
    throw 'Project Python environment missing: .venv-win'
}
$syncArguments = @('-m', 'datahub.sync', 'run', '--dataset', $Dataset)
if ($End) { $syncArguments += @('--end', $End) }
if ($Symbols) { $syncArguments += @('--symbols', $Symbols) }
& $syncPython @syncArguments
exit $LASTEXITCODE
