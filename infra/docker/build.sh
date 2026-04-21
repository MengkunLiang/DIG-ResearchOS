#!/bin/bash
# ResearchOS Docker 镜像构建脚本
#
# 用法：
#   cd /home/liangmengkun/ResearchOS
#   bash infra/docker/build.sh [TAG]
#
# 参数：
#   TAG: 镜像标签（默认：latest）
#
# 代理配置：
#   如果需要通过代理构建，设置 HTTP_PROXY 和 HTTPS_PROXY 环境变量
#   例如：HTTP_PROXY=http://proxy.example.com:8080 bash infra/docker/build.sh

set -e  # 遇到错误立即退出

# 获取脚本所在目录（ResearchOS 根目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$SCRIPT_DIR"

# 镜像标签
TAG="${1:-latest}"
IMAGE_NAME="researchos/system:${TAG}"

echo "=========================================="
echo "ResearchOS Docker 镜像构建"
echo "=========================================="
echo "工作目录: $SCRIPT_DIR"
echo "镜像名称: $IMAGE_NAME"
echo "Dockerfile: infra/docker/Dockerfile"
echo "=========================================="
echo ""

# 显示网络配置
echo "[网络配置检查]"
if [ -n "$HTTP_PROXY" ]; then
    echo "  HTTP_PROXY: $HTTP_PROXY"
else
    echo "  HTTP_PROXY: 未设置"
fi
if [ -n "$HTTPS_PROXY" ]; then
    echo "  HTTPS_PROXY: $HTTPS_PROXY"
else
    echo "  HTTPS_PROXY: 未设置"
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

# 处理 .dockerignore（如果存在于 infra/docker/ 但不存在于根目录）
if [ -f "infra/docker/.dockerignore" ] && [ ! -f ".dockerignore" ]; then
    echo "复制 .dockerignore 到项目根目录..."
    cp infra/docker/.dockerignore .dockerignore
fi

# 开始构建
echo "开始构建镜像..."
echo ""

# 使用 BuildKit 加速构建（如果可用）
export DOCKER_BUILDKIT=1

# 构建参数
BUILD_ARGS=()

# 添加代理支持
if [ -n "$HTTP_PROXY" ]; then
    BUILD_ARGS+=("--build-arg" "HTTP_PROXY=$HTTP_PROXY")
fi
if [ -n "$HTTPS_PROXY" ]; then
    BUILD_ARGS+=("--build-arg" "HTTPS_PROXY=$HTTPS_PROXY")
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
echo "    -v \$(pwd)/workspace:/workspace \\"
echo "    -e OPENAI_API_KEY=\$OPENAI_API_KEY \\"
echo "    -e OPENAI_BASE_URL=\$OPENAI_BASE_URL \\"
echo "    --gpus all \\"
echo "    $IMAGE_NAME \\"
echo "    --help"
echo ""
