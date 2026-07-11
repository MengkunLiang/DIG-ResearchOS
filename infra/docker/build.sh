#!/bin/bash
# ResearchOS Docker 镜像构建脚本
#
# 用法：
#   cd /mnt/data/DIG-ResearchOS
#   bash infra/docker/build.sh [TAG]
#
# 参数：
#   TAG: 镜像标签（默认：latest）
#
# Optional package index:
#   PIP_INDEX_URL=https://pypi.org/simple bash infra/docker/build.sh

set -e  # 遇到错误立即退出

# 获取脚本所在目录（ResearchOS 根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$SCRIPT_DIR"

# 镜像标签
TAG="${1:-latest}"
IMAGE_NAME="researchos/system:${TAG}"
echo "=========================================="
echo "ResearchOS 可选轻量 Docker 镜像构建"
echo "=========================================="
echo "工作目录: $SCRIPT_DIR"
echo "镜像名称: $IMAGE_NAME"
echo "Dockerfile: infra/docker/Dockerfile"
echo "=========================================="
echo ""

# 显示 Python 包源配置
echo "[Python 包源检查]"
if [ -n "${PIP_INDEX_URL:-}" ]; then
    echo "  PIP_INDEX_URL: $PIP_INDEX_URL"
else
    echo "  PIP_INDEX_URL: 使用 pip 默认源"
fi
echo ""

# 检查 Docker 是否可用
if ! command -v docker &> /dev/null; then
    echo "错误: Docker 未安装或不在 PATH 中"
    exit 1
fi

# 检查必需文件
if [ ! -f "infra/docker/Dockerfile" ]; then
    echo "错误: Dockerfile 不存在"
    exit 1
fi

if [ ! -f "pyproject.toml" ]; then
    echo "错误: pyproject.toml 不存在"
    exit 1
fi

# Docker build context is the repository root, so the root .dockerignore is
# the canonical file. Keeping a second ignore file under infra/docker can hide
# package data such as docs, skills, and prompt Markdown from the image.
if [ ! -f ".dockerignore" ]; then
    echo "错误: 根目录 .dockerignore 不存在"
    exit 1
fi

# 开始构建
echo "开始构建镜像..."
echo ""

# 使用 BuildKit 加速构建（如果可用）
export DOCKER_BUILDKIT=1

BUILD_ARGS=()
if [ -n "${PIP_INDEX_URL:-}" ]; then
    BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
fi

# 显示构建命令
echo "构建命令: docker build ${BUILD_ARGS[@]} --file infra/docker/Dockerfile --tag $IMAGE_NAME --progress=plain ."
echo ""

# 构建镜像
docker build \
    "${BUILD_ARGS[@]}" \
    --file infra/docker/Dockerfile \
    --tag "$IMAGE_NAME" \
    --progress=plain \
    .

echo ""
echo "=========================================="
echo "构建完成！"
echo "=========================================="
echo "镜像名称: $IMAGE_NAME"
echo ""
echo "查看镜像信息："
docker images "$IMAGE_NAME"
echo ""
echo "镜像大小："
docker inspect "$IMAGE_NAME" --format='{{.Size}}' | awk '{print $1/1024/1024/1024 " GB"}'
echo ""
echo "运行示例："
echo "  docker run --rm -it \\"
echo "    -v \$(pwd)/workspaces:/app/workspaces \\"
echo "    -e OPENAI_API_KEY=\$OPENAI_API_KEY \\"
echo "    -e OPENAI_BASE_URL=\$OPENAI_BASE_URL \\"
echo "    $IMAGE_NAME \\"
echo "    doctor --workspace /app/workspaces/dev-doctor"
echo ""
