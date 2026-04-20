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

# 检查环境变量
if [ -z "$UIUIAPI_API_KEY" ]; then
    echo "警告: UIUIAPI_API_KEY 环境变量未设置"
fi

if [ -z "$UIUIAPI_BASE_URL" ]; then
    echo "警告: UIUIAPI_BASE_URL 环境变量未设置"
fi

# 创建 workspace 目录（如果不存在）
mkdir -p "$WORKSPACE_DIR"

# 检测是否有 GPU
GPU_FLAG=""
if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    GPU_FLAG="--gpus all"
    echo "检测到 GPU，将使用 --gpus all"
else
    echo "未检测到 GPU 或 nvidia-docker2 未安装"
fi

# 运行容器
# --rm: 容器退出后自动删除
# -it: 交互式终端
# -v: 挂载 workspace
# -e: 传递环境变量
# --gpus: GPU 支持（如果可用）
echo "运行 ResearchOS 容器..."
echo "镜像: $IMAGE_NAME"
echo "Workspace: $WORKSPACE_DIR"
echo "命令: $@"
echo ""

docker run --rm -it \
    -v "$WORKSPACE_DIR:/workspace" \
    -e UIUIAPI_API_KEY="${UIUIAPI_API_KEY}" \
    -e UIUIAPI_BASE_URL="${UIUIAPI_BASE_URL}" \
    ${GPU_FLAG} \
    "$IMAGE_NAME" \
    "$@"
