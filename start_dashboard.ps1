[CmdletBinding()]
param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8501,
    [ValidateSet("Prompt", "Direct", "UpdateFirst")]
    [string]$Mode = "Prompt"
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCmd,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    if ($PythonCmd -eq "py -3") {
        & py -3 @Arguments
    } else {
        & $PythonCmd @Arguments
    }
}

function Test-PythonVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCmd
    )

    try {
        Invoke-Python -PythonCmd $PythonCmd -Arguments @("-c", "import sys; assert sys.version_info[:2] >= (3, 11)")
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-ProjectPython {
    $windowsVenvPython = Join-Path $projectRoot ".venv-win\Scripts\python.exe"
    if ((Test-Path $windowsVenvPython) -and (Test-PythonVersion -PythonCmd $windowsVenvPython)) {
        return $windowsVenvPython
    }
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if ((Test-Path $venvPython) -and (Test-PythonVersion -PythonCmd $venvPython)) {
        return $venvPython
    }
    return $null
}

function Get-BootstrapPythonCandidate {
    $candidates = @(
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python312\python.exe"),
        (Join-Path $env:ProgramFiles "Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python\Python312\python.exe")
    ) | Select-Object -Unique

    foreach ($path in $candidates) {
        if (-not $path -or -not (Test-Path $path)) {
            continue
        }
        if (Test-PythonVersion -PythonCmd $path) {
            return $path
        }
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher -and (Test-PythonVersion -PythonCmd "py -3")) {
        return "py -3"
    }

    throw "Python 3.11+ not found. Install Python 3.11 or newer."
}

function Ensure-ProjectVenv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BootstrapPythonCmd
    )

    $venvDir = Join-Path $projectRoot ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    Write-Host "[INFO] Creating local virtual environment at $venvDir"
    Invoke-Python -PythonCmd $BootstrapPythonCmd -Arguments @("-m", "venv", $venvDir)

    if (-not (Test-Path $venvPython)) {
        throw "Virtual environment creation succeeded but .venv\\Scripts\\python.exe was not found."
    }

    return $venvPython
}

function Test-ProjectDeps {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCmd
    )

    $probe = "import duckdb, pandas, streamlit, openpyxl, plotly, yaml, jinja2, macro_replay"
    Invoke-Python -PythonCmd $PythonCmd -Arguments @("-c", $probe)
    return $LASTEXITCODE -eq 0
}

function Install-ProjectDeps {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCmd
    )

    Write-Host "[INFO] Installing project dependencies..."
    Invoke-Python -PythonCmd $PythonCmd -Arguments @("-m", "pip", "install", "-e", ".", "--no-build-isolation")
    return $LASTEXITCODE -eq 0
}

function Open-BrowserWhenReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    $jobScript = {
        param($TargetUrl)

        for ($i = 0; $i -lt 60; $i++) {
            try {
                $response = Invoke-WebRequest -Uri $TargetUrl -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    Start-Process $TargetUrl
                    return
                }
            } catch {
            }
            Start-Sleep -Seconds 1
        }
    }

    Start-Job -ScriptBlock $jobScript -ArgumentList $Url | Out-Null
}

function Stop-ExistingDashboardProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TargetPort,

        [Parameter(Mandatory = $true)]
        [string]$ProjectPath
    )

    $normalizedProjectPath = [System.IO.Path]::GetFullPath($ProjectPath)

    try {
        $candidates = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'powershell.exe'" |
            Where-Object {
                $commandLine = $_.CommandLine
                if (-not $commandLine) {
                    return $false
                }

                $matchesProject = $commandLine -like "*$normalizedProjectPath*"
                $matchesStreamlit = $commandLine -like "*macro_replay\streamlit_app.py*"
                $matchesPort = $commandLine -like "*--server.port $TargetPort*" -or $commandLine -like "*-Port $TargetPort*"

                return ($matchesProject -and $matchesStreamlit) -or ($matchesProject -and $matchesPort)
            }
    } catch {
        Write-Host "[WARN] Cannot inspect existing dashboard processes; skipping process cleanup."
        $candidates = @()
    }

    foreach ($process in $candidates) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
            Write-Host "[INFO] Stopped existing dashboard process $($process.ProcessId)"
        } catch {
        }
    }
}

function Read-StartupMode {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DefaultMode
    )

    if ($DefaultMode -ne "Prompt") {
        return $DefaultMode
    }

    Write-Host ""
    Write-Host "OmniSignal startup options"
    Write-Host "  1. Start dashboard directly"
    Write-Host "  2. Update all data first, then ask whether to start dashboard"
    Write-Host ""

    while ($true) {
        $choice = Read-Host "Choose 1 or 2"
        switch ($choice.Trim()) {
            "1" { return "Direct" }
            "2" { return "UpdateFirst" }
            default { Write-Host "Please enter 1 or 2." }
        }
    }
}

function Confirm-StartDashboard {
    while ($true) {
        $choice = Read-Host "Data update finished. Start dashboard now? [Y/N]"
        switch ($choice.Trim().ToUpperInvariant()) {
            "Y" { return $true }
            "YES" { return $true }
            "N" { return $false }
            "NO" { return $false }
            default { Write-Host "Please enter Y or N." }
        }
    }
}

function Invoke-DataRefresh {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonCmd
    )

    Write-Host "[INFO] Updating all OmniSignal data before startup..."
    Invoke-Python -PythonCmd $PythonCmd -Arguments @(
        ".\scripts\fetch_all_indicators.py",
        "--mode",
        "incremental"
    )

    if ($LASTEXITCODE -ne 0) {
        throw "Data refresh failed. Check logs\\dashboard_refresh_all.json and provider-specific logs for details."
    }

    Write-Host "[INFO] Data update completed."
}

$python = Get-ProjectPython
if (-not $python) {
    $bootstrapPython = Get-BootstrapPythonCandidate
    $python = Ensure-ProjectVenv -BootstrapPythonCmd $bootstrapPython
}

$env:PYTHONPATH = $projectRoot

if (-not (Test-ProjectDeps -PythonCmd $python)) {
    if (-not (Install-ProjectDeps -PythonCmd $python)) {
        throw "Missing Python dependencies for this project, and automatic installation failed."
    }
    if (-not (Test-ProjectDeps -PythonCmd $python)) {
        throw "Project dependencies still cannot be imported after installation."
    }
}

$dashboardUrl = "http://$ListenHost`:$Port"
$startupMode = Read-StartupMode -DefaultMode $Mode

if ($startupMode -eq "UpdateFirst") {
    Stop-ExistingDashboardProcesses -TargetPort $Port -ProjectPath $projectRoot
    Invoke-DataRefresh -PythonCmd $python
    if (-not (Confirm-StartDashboard)) {
        Write-Host "[INFO] Dashboard startup skipped by user."
        exit 0
    }
}

Stop-ExistingDashboardProcesses -TargetPort $Port -ProjectPath $projectRoot
Write-Host "[INFO] Starting Streamlit dashboard on $dashboardUrl"
Open-BrowserWhenReady -Url $dashboardUrl

Invoke-Python -PythonCmd $python -Arguments @(
    "-m",
    "streamlit",
    "run",
    ".\macro_replay\streamlit_app.py",
    "--server.headless",
    "true",
    "--server.address",
    $ListenHost,
    "--server.port",
    $Port,
    "--server.fileWatcherType",
    "none",
    "--browser.gatherUsageStats",
    "false"
)
