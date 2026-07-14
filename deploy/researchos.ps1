param(
  [Parameter(Position=0)]
  [string]$Command,
  [Parameter(Position=1)]
  [string]$Project,
  [Parameter(Position=2)]
  [string]$Task,
  [string]$Topic,
  [Parameter(ValueFromRemainingArguments=$true)]
  [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ComposeFile = Join-Path $ScriptDir "compose.yaml"
$env:RESEARCHOS_HOST_WORKSPACE_ROOT = if ($env:RESEARCHOS_HOST_WORKSPACE_ROOT) { $env:RESEARCHOS_HOST_WORKSPACE_ROOT } else { Join-Path $RepoRoot "workspace" }
if (-not $env:RESEARCHOS_UID) { $env:RESEARCHOS_UID = "1000" }
if (-not $env:RESEARCHOS_GID) { $env:RESEARCHOS_GID = "1000" }

function Show-Usage {
  Write-Host "Usage:"
  Write-Host "  .\researchos.ps1 doctor [extra args...]"
  Write-Host "  .\researchos.ps1 init <project> -Topic `"..." [extra args...]"
  Write-Host "  .\researchos.ps1 run <project> [extra args...]"
  Write-Host "  .\researchos.ps1 resume <project> [extra args...]"
  Write-Host "  .\researchos.ps1 run-task <project> <task> [extra args...]"
  Write-Host "  .\researchos.ps1 pull"
}

function Require-DeployFiles {
  $missing = $false
  if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
    Write-Host "[WARN] .env not found; provider secrets will only come from the shell."
    Write-Host "       Optional setup: Copy-Item .env.example .env"
  }
  if (-not (Test-Path (Join-Path $RepoRoot "config/model_settings.yaml"))) {
    Write-Host "[ERROR] config/model_settings.yaml not found."
    Write-Host "        Run on the host: python -m researchos.cli configure-llm"
    Write-Host "        Docker mounts config read-only, so setup must happen before this command."
    $missing = $true
  }
  if ($missing) {
    exit 2
  }
}

function Validate-Project([string]$Name) {
  if ($Name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    Write-Error "[ERROR] Invalid project name: $Name. Use letters, numbers, dot, underscore, or dash."
    exit 2
  }
}

function Compose([string[]]$ArgsList) {
  $composeArgs = @()
  $RootEnv = Join-Path $RepoRoot ".env"
  if (Test-Path $RootEnv) {
    $composeArgs += @("--env-file", $RootEnv)
  }
  $composeArgs += @("-f", $ComposeFile)
  & docker compose @composeArgs @ArgsList
  exit $LASTEXITCODE
}

function NonEmpty([object[]]$Items) {
  $out = @()
  foreach ($item in $Items) {
    if ($null -ne $item -and "$item" -ne "") { $out += "$item" }
  }
  return $out
}

New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "workspace") | Out-Null

switch ($Command) {
  "doctor" {
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "doctor", $Project, $Task) + $Rest))
  }
  "init" {
    if (-not $Project) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    $argsList = @("run", "--rm", "researchos", "init-workspace", "--workspace", "/app/workspace/$Project", "--project-id", $Project)
    if ($Topic) {
      $argsList += @("--topic", $Topic)
    }
    $argsList += @($Task) + $Rest
    Compose (NonEmpty $argsList)
  }
  "run" {
    if (-not $Project) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "run", "--workspace", "/app/workspace/$Project", $Task) + $Rest))
  }
  "resume" {
    if (-not $Project) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "resume", "--workspace", "/app/workspace/$Project", $Task) + $Rest))
  }
  "run-task" {
    if (-not $Project -or -not $Task) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "run-task", $Task, "--workspace", "/app/workspace/$Project") + $Rest))
  }
  "pull" {
    Compose @("pull")
  }
  default {
    Show-Usage
    exit 2
  }
}
