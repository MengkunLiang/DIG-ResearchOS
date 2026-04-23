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
#   bash infra/docker/run.sh init-workspace --workspace /workspace
#
#   # 运行完整 pipeline
#   bash infra/docker/run.sh run --workspace /workspace
#
#   # 单 task 调试
#   bash infra/docker/run.sh run-task --workspace /workspace --task hello --mock

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$SCRIPT_DIR"

# 镜像名称
IMAGE_NAME="${RESEARCHOS_IMAGE:-researchos/system:latest}"

# Workspace 目录（宿主机路径）
WORKSPACE_DIR="${RESEARCHOS_WORKSPACE:-$(pwd)/workspace}"

# 自动加载项目根目录的 .env，方便直接在 .env 里切换 provider。
# shell 中已显式设置的环境变量会覆盖 .env 中的值。
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . ".env"
    set +a
fi

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
if [ -z "$SILICONFLOW_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "提示: 未检测到 LLM API 密钥"
    echo "  - 请在 .env 中设置 SILICONFLOW_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY"
fi

# 创建 workspace 目录（如果不存在）
mkdir -p "$WORKSPACE_DIR"

# 检测 Docker 是否真的支持 GPU。
# 仅宿主机上 nvidia-smi 正常还不够；还需要 nvidia-container-toolkit
# 把 nvidia runtime 注册给 Docker。
GPU_FLAG=""
RUNTIME_FLAG=""
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q 'nvidia' && [ -d /proc/driver/nvidia/gpus ] && [ -n "$(ls -A /proc/driver/nvidia/gpus 2>/dev/null)" ]; then
        GPU_FLAG="--gpus all"
        echo "检测到宿主机 GPU，且 Docker 已配置 nvidia runtime，将使用 --gpus all"
    elif docker run --rm --help 2>&1 | grep -q "\-\-runtime" && command -v nvidia-container-runtime &> /dev/null; then
        if [ -d /proc/driver/nvidia/gpus ] && [ -n "$(ls -A /proc/driver/nvidia/gpus 2>/dev/null)" ]; then
            RUNTIME_FLAG="--runtime=nvidia"
            GPU_FLAG="--gpus all"
            echo "检测到宿主机 GPU，将回退使用 --runtime=nvidia --gpus all"
        else
            echo "检测到宿主机 GPU，但 /proc/driver/nvidia/gpus 未暴露给当前系统（常见于 LXC）；本次将以 CPU 模式启动容器"
        fi
    else
        if [ ! -d /proc/driver/nvidia/gpus ] || [ -z "$(ls -A /proc/driver/nvidia/gpus 2>/dev/null)" ]; then
            echo "检测到宿主机 GPU，但 /proc/driver/nvidia/gpus 未暴露给当前系统（常见于 LXC）；本次将以 CPU 模式启动容器"
        else
            echo "检测到宿主机 GPU，但 Docker 未配置 nvidia runtime；本次将以 CPU 模式启动容器"
        fi
    fi
else
    echo "未检测到宿主机 GPU，本次将以 CPU 模式启动容器"
fi

# 运行容器
# --rm: 容器退出后自动删除
# -it: 交互式终端
# -v: 挂载 workspace
# -e: 传递环境变量
# --gpus/--runtime: GPU 支持（如果可用）
echo "运行 ResearchOS 容器..."
echo "镜像: $IMAGE_NAME"
echo "Workspace: $WORKSPACE_DIR"
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
    OPENROUTER_API_KEY \
    OPENAI_API_KEY \
    OPENAI_BASE_URL \
    ANTHROPIC_API_KEY \
    S2_API_KEY \
    RESEARCHER_EMAIL \
    GITHUB_TOKEN \
    LOG_LEVEL \
    DEV_MODE \
    ENABLE_TRACE \
    RESEARCHOS_NO_BANNER
do
    env_value="${!env_name}"
    if [ -n "$env_value" ]; then
        DOCKER_ENV_ARGS+=(-e "$env_name=$env_value")
    fi
done

docker run --rm \
    "${DOCKER_TTY_ARGS[@]}" \
    -v "$WORKSPACE_DIR:/workspace" \
    "${DOCKER_ENV_ARGS[@]}" \
    ${RUNTIME_FLAG} \
    ${GPU_FLAG} \
    "$IMAGE_NAME" \
    "$@"
