<#
.SYNOPSIS
Creates or updates a complete ResearchOS Conda environment on Windows.

.DESCRIPTION
The script resolves this repository from its own location, so it is safe to
run from a different PowerShell working directory.  `environment.yml` owns
only Conda's Python toolchain; editable installation belongs here because a
local project path must be absolute.  This prevents the old half-installed
state where Conda succeeded but pip could not find `requirements.txt` or `.`.

The default package sources are Tsinghua University mirrors.  They are applied
only to this command and do not write global Conda or pip configuration.  Use
-UseOfficialPypi if the PyPI mirror is unavailable on the current network.
#>

[CmdletBinding()]
param(
    [string]$EnvironmentName = "researchos",
    [switch]$Recreate,
    [switch]$UseOfficialPypi,
    [switch]$SkipValidation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-Conda {
    param([string[]]$Arguments)

    & conda @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Conda command failed (exit $LASTEXITCODE): conda $($Arguments -join ' ')"
    }
}

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda was not found on PATH. Open an Anaconda/Miniconda PowerShell prompt, then run this script again."
}

$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EnvironmentFile = Join-Path $RepositoryRoot "environment.yml"
if (-not (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf)) {
    throw "environment.yml was not found at $EnvironmentFile"
}

$existingEnvironments = @(
    ((& conda env list --json | ConvertFrom-Json).envs) |
        Where-Object { (Split-Path -Leaf $_) -ieq $EnvironmentName }
)

Push-Location $RepositoryRoot
try {
    if ($Recreate -and $existingEnvironments.Count -gt 0) {
        Write-Host "[1/4] Removing existing Conda environment '$EnvironmentName'..."
        Invoke-Conda @("env", "remove", "--yes", "--name", $EnvironmentName)
        $existingEnvironments = @()
    }

    if ($existingEnvironments.Count -gt 0) {
        Write-Host "[1/4] Updating Conda environment '$EnvironmentName' from environment.yml..."
        Invoke-Conda @("env", "update", "--yes", "--prune", "--name", $EnvironmentName, "--file", $EnvironmentFile)
    }
    else {
        Write-Host "[1/4] Creating Conda environment '$EnvironmentName' from environment.yml..."
        Invoke-Conda @("env", "create", "--yes", "--name", $EnvironmentName, "--file", $EnvironmentFile)
    }

    if ($UseOfficialPypi) {
        $pipIndexArguments = @("--index-url", "https://pypi.org/simple")
        Write-Host "[2/4] Using the official PyPI index for this install."
    }
    else {
        $pipIndexArguments = @("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
        Write-Host "[2/4] Using the Tsinghua PyPI mirror for this install."
    }

    # Avoid `--no-capture-output`: plain `conda run` works with older Windows
    # Anaconda/Miniconda releases too, and Invoke-Conda still propagates the
    # underlying exit code and complete diagnostic output.
    $basePipArguments = @("run", "--name", $EnvironmentName, "python", "-m", "pip", "install")
    Invoke-Conda ($basePipArguments + $pipIndexArguments + @("--upgrade", "pip", "setuptools", "wheel"))

    # The package metadata is the single dependency authority for this local
    # editable installation.  The dev extra provides the supported pytest
    # tools; Docker continues to consume requirements.txt with the same
    # runtime LiteLLM pin.
    Write-Host "[3/4] Installing ResearchOS and its development dependencies..."
    $editableTarget = "${RepositoryRoot}[dev]"
    Invoke-Conda ($basePipArguments + $pipIndexArguments + @("--retries", "5", "--timeout", "60", "--prefer-binary", "--editable", $editableTarget))

    if (-not $SkipValidation) {
        Write-Host "[4/4] Checking the installed dependency graph and ResearchOS configuration..."
        Invoke-Conda @("run", "--name", $EnvironmentName, "python", "-m", "pip", "check")
        Invoke-Conda @("run", "--name", $EnvironmentName, "python", "-m", "researchos.cli", "validate-config", "--no-banner", "--no-color")
    }

    Write-Host "ResearchOS is ready. Activate it with: conda activate $EnvironmentName"
}
finally {
    Pop-Location
}
