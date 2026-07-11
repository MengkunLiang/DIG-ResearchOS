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
$env:RESEARCHOS_HOST_WORKSPACE_ROOT = if ($env:RESEARCHOS_HOST_WORKSPACE_ROOT) { $env:RESEARCHOS_HOST_WORKSPACE_ROOT } else { Join-Path $RepoRoot "workspaces" }
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
  if (-not (Test-Path (Join-Path $ScriptDir ".env"))) {
    Write-Host "[WARN] deploy/.env not found; provider secrets will only come from the shell or root .env."
    Write-Host "       Optional setup: Copy-Item deploy/.env.example deploy/.env"
  }
  if (-not (Test-Path (Join-Path $RepoRoot "config/user_settings.yaml"))) {
    Write-Host "[ERROR] config/user_settings.yaml not found."
    Write-Host "        Docker Mode uses the same root config as Native Mode."
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
  & docker compose -f $ComposeFile @ArgsList
  exit $LASTEXITCODE
}

function NonEmpty([object[]]$Items) {
  $out = @()
  foreach ($item in $Items) {
    if ($null -ne $item -and "$item" -ne "") { $out += "$item" }
  }
  return $out
}

New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "workspaces") | Out-Null

switch ($Command) {
  "doctor" {
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "doctor", $Project, $Task) + $Rest))
  }
  "init" {
    if (-not $Project) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    $argsList = @("run", "--rm", "researchos", "init-workspace", "--workspace", "/app/workspaces/$Project", "--project-id", $Project)
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
    Compose (NonEmpty (@("run", "--rm", "researchos", "run", "--workspace", "/app/workspaces/$Project", $Task) + $Rest))
  }
  "resume" {
    if (-not $Project) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "resume", "--workspace", "/app/workspaces/$Project", $Task) + $Rest))
  }
  "run-task" {
    if (-not $Project -or -not $Task) { Show-Usage; exit 2 }
    Validate-Project $Project
    Require-DeployFiles
    Compose (NonEmpty (@("run", "--rm", "researchos", "run-task", $Task, "--workspace", "/app/workspaces/$Project") + $Rest))
  }
  "pull" {
    Compose @("pull")
  }
  default {
    Show-Usage
    exit 2
  }
}
