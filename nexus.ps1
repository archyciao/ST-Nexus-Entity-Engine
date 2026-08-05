param(
    [ValidateSet("setup", "test", "check-registry", "python")]
    [string]$Action = "check-registry",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ActionArgs
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$utf8Output = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8Output
$OutputEncoding = $utf8Output
$ProjectRoot = [System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$VenvPath = Join-Path $ProjectRoot ".venv"
$RootMarker = Join-Path $VenvPath ".nexus-root"
$ProjectHashMarker = Join-Path $VenvPath ".nexus-pyproject-sha256"
$PyProjectPath = Join-Path $ProjectRoot "pyproject.toml"

function Get-ProjectHash {
    $stream = [System.IO.File]::OpenRead($PyProjectPath)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            $bytes = $sha256.ComputeHash($stream)
            return ([System.BitConverter]::ToString($bytes)).Replace("-", "")
        } finally {
            $sha256.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Remove-StaleVenv {
    if (-not (Test-Path -LiteralPath $VenvPath)) {
        return
    }

    $recordedRoot = if (Test-Path -LiteralPath $RootMarker) {
        (Get-Content -LiteralPath $RootMarker -Raw).Trim()
    } else {
        ""
    }
    if ($recordedRoot -eq $ProjectRoot) {
        return
    }

    $resolvedVenv = [System.IO.Path]::GetFullPath($VenvPath).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $expectedVenv = [System.IO.Path]::Combine($ProjectRoot, ".venv")
    if ($resolvedVenv -ne $expectedVenv) {
        throw "Refusing to remove an environment outside the project: $resolvedVenv"
    }

    Write-Host "A stale Python environment was found and will be rebuilt."
    Remove-Item -LiteralPath $resolvedVenv -Recurse -Force
}

function Write-EnvironmentMarkers {
    Set-Content -LiteralPath $RootMarker -Value $ProjectRoot -Encoding UTF8
    $projectHash = Get-ProjectHash
    Set-Content -LiteralPath $ProjectHashMarker -Value $projectHash -Encoding ASCII
}

function Invoke-Uv {
    param([string[]]$Arguments)

    & uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Find-BasePython {
    $candidates = @(
        @{ Command = "py"; Prefix = @("-3.12") },
        @{ Command = "py"; Prefix = @("-3.11") },
        @{ Command = "python"; Prefix = @() },
        @{ Command = "python3"; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $commandName = [string]$candidate.Command
        $prefixArgs = [string[]]$candidate.Prefix
        if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
            continue
        }
        & $commandName @prefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return $candidate
        }
    }
    throw "Python 3.11+ was not found. The future Tavern build will not require Python, but the current source tools require Python or uv."
}

function Ensure-PipEnvironment {
    $basePython = Find-BasePython
    $baseCommand = [string]$basePython.Command
    $basePrefix = [string[]]$basePython.Prefix
    $venvPython = Join-Path $VenvPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $baseCommand @basePrefix -m venv $VenvPath
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    $currentHash = Get-ProjectHash
    $installedHash = if (Test-Path -LiteralPath $ProjectHashMarker) {
        (Get-Content -LiteralPath $ProjectHashMarker -Raw).Trim()
    } else {
        ""
    }
    if ($installedHash -ne $currentHash) {
        & $venvPython -m pip install --disable-pip-version-check --editable $ProjectRoot
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    Write-EnvironmentMarkers
    return $venvPython
}

Set-Location -LiteralPath $ProjectRoot
Remove-StaleVenv

$uvAvailable = [bool](Get-Command uv -ErrorAction SilentlyContinue)
if ($uvAvailable) {
    Invoke-Uv -Arguments @("sync", "--locked")
    Write-EnvironmentMarkers
    if ($Action -eq "setup") {
        Write-Host "Runtime environment is ready: $VenvPath"
        exit 0
    }
    $runner = @("run", "--locked", "python")
} else {
    $venvPython = Ensure-PipEnvironment
    if ($Action -eq "setup") {
        Write-Host "Runtime environment is ready: $VenvPath"
        exit 0
    }
    $runner = @($venvPython)
}

switch ($Action) {
    "test" {
        $commandArgs = @("-m", "unittest", "discover", "-s", "tests", "-v") + $ActionArgs
    }
    "check-registry" {
        $commandArgs = @("-m", "world_simulator_schema.cli", "check-registry") + $ActionArgs
    }
    "python" {
        $commandArgs = $ActionArgs
    }
}

if ($uvAvailable) {
    Invoke-Uv -Arguments ($runner + $commandArgs)
} else {
    & $runner[0] @commandArgs
    exit $LASTEXITCODE
}
