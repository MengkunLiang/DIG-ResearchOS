#!/bin/bash
# ResearchOS Docker 容器运行脚本
#
# 用法：
#   bash infra/docker/run.sh [COMMAND] [ARGS...]
#
# 示例：
#   # 显示帮助
#   bash infra/docker/run.sh --help
#
#   # 初始化 workspace
#   bash infra/docker/run.sh init-workspace --workspace /app/workspaces/dev
#
#   # 运行完整 pipeline
#   bash infra/docker/run.sh run --workspace /app/workspaces/dev
#
#   # 单 task 调试
#   bash infra/docker/run.sh run-task HELLO --workspace /app/workspaces/dev

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$SCRIPT_DIR"

load_env_defaults() {
    local env_file="$1"
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%$'\r'}"
        if [ -z "$line" ] || [[ "$line" == \#* ]]; then
            continue
        fi
        if [[ "$line" == export\ * ]]; then
            line="${line#export }"
        fi
        if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            if [ -z "${!key+x}" ]; then
                if [[ "$value" == \"*\" && "$value" == *\" ]]; then
                    value="${value:1:${#value}-2}"
                elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
                    value="${value:1:${#value}-2}"
                fi
                export "$key=$value"
            fi
        fi
    done < "$env_file"
}

# 自动加载项目根目录的 .env，方便直接在 .env 里切换 provider。
# shell 中已显式设置的环境变量会覆盖 .env 中的值。
if [ -f ".env" ]; then
    load_env_defaults ".env"
fi

# 镜像名称。必须在加载 .env 之后计算。
IMAGE_NAME="${RESEARCHOS_IMAGE:-researchos/system:latest}"

# Workspace 目录（宿主机路径）。必须在加载 .env 之后计算，
# 这样 .env 中的 RESEARCHOS_WORKSPACE 才会生效。
WORKSPACE_DIR="${RESEARCHOS_WORKSPACE:-$(pwd)/workspaces}"
HOST_WORKSPACE_HINT="${RESEARCHOS_HOST_WORKSPACE_ROOT:-./workspaces}"
HOST_UID="${RESEARCHOS_UID:-$(id -u 2>/dev/null || echo 1000)}"
HOST_GID="${RESEARCHOS_GID:-$(id -g 2>/dev/null || echo 1000)}"

# 检查 Docker 是否可用
if ! command -v docker &> /dev/null; then
    echo "错误: Docker 未安装或不在 PATH 中"
    exit 1
fi

# 检查镜像是否存在
if ! docker images "$IMAGE_NAME" --format "{{.Repository}}:{{.Tag}}" | grep -q "$IMAGE_NAME"; then
    echo "错误: 镜像 $IMAGE_NAME 不存在"
    echo "请先运行: bash infra/docker/build.sh"
    exit 1
fi

# 检查环境变量。
# 当前推荐把 API key 放在 .env 中，再由本脚本自动透传到容器。
if [ -z "$SILICONFLOW_API_KEY" ] && [ -z "$DEEPSEEK_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "提示: 未检测到 LLM API 密钥"
    echo "  - 请在 .env 中设置 SILICONFLOW_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY"
fi

# 创建 workspace 目录（如果不存在）
mkdir -p "$WORKSPACE_DIR"

# 运行容器
# --rm: 容器退出后自动删除
# -it: 交互式终端
# -v: 挂载 workspace
# -e: 传递环境变量
echo "运行 ResearchOS 容器..."
echo "镜像: $IMAGE_NAME"
echo "Workspace: $WORKSPACE_DIR"
echo "User: $HOST_UID:$HOST_GID"
echo "命令: $@"
echo ""

DOCKER_TTY_ARGS=()
if [ -t 0 ] && [ -t 1 ]; then
    DOCKER_TTY_ARGS=(-it)
else
    echo "当前不是交互式终端，将以非 TTY 模式运行容器"
fi

DOCKER_ENV_ARGS=()
for env_name in \
    SILICONFLOW_API_KEY \
    SILICONFLOW_BASE_URL \
    DEEPSEEK_API_KEY \
    DEEPSEEK_BASE_URL \
    OPENROUTER_API_KEY \
    OPENAI_API_KEY \
    OPENAI_BASE_URL \
    ANTHROPIC_API_KEY \
    S2_API_KEY \
    ELSEVIER_API_KEY \
    ELSEVIER_INSTTOKEN \
    RESEARCHER_EMAIL \
    GITHUB_TOKEN
do
    env_value="${!env_name}"
    if [ -n "$env_value" ]; then
        DOCKER_ENV_ARGS+=(-e "$env_name=$env_value")
    fi
done

DOCKER_MOUNT_ARGS=()
if [ -d "$(pwd)/config" ]; then
    DOCKER_MOUNT_ARGS+=(-v "$(pwd)/config:/app/config:ro")
fi

docker run --rm \
    "${DOCKER_TTY_ARGS[@]}" \
    --user "$HOST_UID:$HOST_GID" \
    -v "$WORKSPACE_DIR:/app/workspaces" \
    "${DOCKER_MOUNT_ARGS[@]}" \
    -e "RESEARCHOS_WORKSPACE_ROOT=/app/workspaces" \
    -e "RESEARCHOS_HOST_WORKSPACE_ROOT=$HOST_WORKSPACE_HINT" \
    -e "RESEARCHOS_CONFIG=/app/config/user_settings.yaml" \
    -e "RESEARCHOS_RUNTIME_CONFIG=/app/config/runtime.yaml" \
    "${DOCKER_ENV_ARGS[@]}" \
    "$IMAGE_NAME" \
    "$@"
