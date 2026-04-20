#!/bin/bash
# ResearchOS Docker 镜像构建脚本
#
# 用法：
#   cd /home/liangmengkun/ResearchOS
#   bash infra/docker/build.sh [TAG]
#
# 参数：
#   TAG: 镜像标签（默认：latest）

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

# 开始构建
echo "开始构建镜像..."
echo ""

# 使用 BuildKit 加速构建（如果可用）
export DOCKER_BUILDKIT=1

# 构建镜像
# --file: 指定 Dockerfile 路径
# --tag: 镜像标签
# --progress: 显示构建进度
# .: 构建上下文为当前目录
docker build \
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
echo "    -e UIUIAPI_API_KEY=\$UIUIAPI_API_KEY \\"
echo "    -e UIUIAPI_BASE_URL=\$UIUIAPI_BASE_URL \\"
echo "    --gpus all \\"
echo "    $IMAGE_NAME \\"
echo "    --help"
echo ""
