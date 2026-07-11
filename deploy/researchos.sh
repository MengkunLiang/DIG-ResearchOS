#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/compose.yaml"

export RESEARCHOS_HOST_WORKSPACE_ROOT="${RESEARCHOS_HOST_WORKSPACE_ROOT:-$REPO_ROOT/workspace}"
if command -v id >/dev/null 2>&1; then
  export RESEARCHOS_UID="${RESEARCHOS_UID:-$(id -u)}"
  export RESEARCHOS_GID="${RESEARCHOS_GID:-$(id -g)}"
fi

usage() {
  cat <<'USAGE'
Usage:
  ./researchos.sh doctor [extra args...]
  ./researchos.sh init <project> --topic "..." [extra args...]
  ./researchos.sh run <project> [extra args...]
  ./researchos.sh resume <project> [extra args...]
  ./researchos.sh run-task <project> <task> [extra args...]
  ./researchos.sh pull
USAGE
}

require_deploy_files() {
  local missing=0
  if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "[WARN] deploy/.env not found; provider secrets will only come from the shell or root .env."
    echo "       Optional setup: cp deploy/.env.example deploy/.env"
  fi
  if [ ! -f "$REPO_ROOT/config/user_settings.yaml" ]; then
    echo "[ERROR] config/user_settings.yaml not found."
    echo "        Docker Mode uses the same root config as Native Mode."
    missing=1
  fi
  if [ "$missing" -ne 0 ]; then
    exit 2
  fi
}

validate_project() {
  local project="$1"
  if [[ ! "$project" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "[ERROR] Invalid project name: $project" >&2
    echo "Use letters, numbers, dot, underscore, or dash; start with a letter or number." >&2
    exit 2
  fi
}

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

cmd="${1:-}"
if [ -z "$cmd" ]; then
  usage
  exit 2
fi
shift || true

mkdir -p "$REPO_ROOT/workspace"

case "$cmd" in
  doctor)
    require_deploy_files
    compose run --rm researchos doctor "$@"
    ;;
  init)
    project="${1:-}"
    [ -n "$project" ] || { usage; exit 2; }
    validate_project "$project"
    shift
    require_deploy_files
    compose run --rm researchos init-workspace \
      --workspace "/app/workspace/$project" \
      --project-id "$project" \
      "$@"
    ;;
  run)
    project="${1:-}"
    [ -n "$project" ] || { usage; exit 2; }
    validate_project "$project"
    shift
    require_deploy_files
    compose run --rm researchos run --workspace "/app/workspace/$project" "$@"
    ;;
  resume)
    project="${1:-}"
    [ -n "$project" ] || { usage; exit 2; }
    validate_project "$project"
    shift
    require_deploy_files
    compose run --rm researchos resume --workspace "/app/workspace/$project" "$@"
    ;;
  run-task)
    project="${1:-}"
    task="${2:-}"
    [ -n "$project" ] && [ -n "$task" ] || { usage; exit 2; }
    validate_project "$project"
    shift 2
    require_deploy_files
    compose run --rm researchos run-task "$task" --workspace "/app/workspace/$project" "$@"
    ;;
  pull)
    compose pull
    ;;
  *)
    usage
    exit 2
    ;;
esac
