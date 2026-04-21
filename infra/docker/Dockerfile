# ResearchOS Docker 镜像 - 方案 D1（容器内直接执行）
#
# 这个镜像包含：
# - Python 3.11 运行时
# - ResearchOS runtime 及其依赖
# - 常用 ML 库（PyTorch、Transformers 等）
# - LaTeX 完整工具链
# - MCP 服务器
#
# 构建命令：
#   cd /home/liangmengkun/ResearchOS
#   docker build -t researchos/system:latest -f infra/docker/Dockerfile .
#
# 运行命令：
#   docker run --rm -it \
#     -v $(pwd)/workspace:/workspace \
#     -e OPENAI_API_KEY=$OPENAI_API_KEY \
#     -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
#     --gpus all \
#     researchos/system:latest \
#     run --workspace /workspace

FROM nvidia/cuda:12.4.0-runtime-ubuntu22.04

LABEL maintainer="ResearchOS Team"
LABEL description="ResearchOS runtime with ML dependencies and LaTeX toolchain"
LABEL version="0.1.0"

# 设置环境变量，避免交互式提示
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# 安装系统依赖
# - python3.11: Python 运行时
# - git, curl, wget: 版本控制和下载工具
# - texlive-full, latexmk: LaTeX 完整工具链
# - ripgrep: 快速文本搜索（某些工具可能需要）
# - nodejs, npm: MCP 服务器依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    git \
    curl \
    wget \
    texlive-full \
    latexmk \
    ripgrep \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY pyproject.toml ./
COPY requirements.txt requirements-llm.txt requirements-dev.txt ./

# 安装 Python 依赖（ResearchOS runtime）
# 注意：这里先安装依赖，后面再安装 ResearchOS 本身（利用 Docker 缓存）
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-llm.txt && \
    pip install --no-cache-dir -r requirements-dev.txt

# 安装常用 ML 依赖（实验代码常用）
# 这些包会被 Agent 生成的实验代码使用
RUN pip install --no-cache-dir \
    torch==2.6.0 \
    transformers==4.45.0 \
    datasets==2.20.0 \
    numpy \
    pandas \
    scikit-learn \
    matplotlib \
    seaborn \
    tqdm \
    wandb \
    accelerate

# 安装 MCP 服务器
# @modelcontextprotocol/server-arxiv: arXiv 论文搜索
# @modelcontextprotocol/server-filesystem: 本地文件系统访问
# @modelcontextprotocol/server-github: GitHub 仓库和代码搜索
RUN npm install -g \
    @modelcontextprotocol/server-arxiv \
    @modelcontextprotocol/server-filesystem \
    @modelcontextprotocol/server-github

# 复制 ResearchOS 源代码
COPY researchos /app/researchos
COPY config /app/config
COPY scripts /app/scripts

# 安装 ResearchOS（可编辑模式，方便开发）
RUN pip install -e .

# 创建 workspace 挂载点
RUN mkdir -p /workspace

# 设置容器标识环境变量（用于环境检测）
ENV CONTAINER_ID=researchos-docker

# 入口点：使用 Python 模块方式启动 CLI
ENTRYPOINT ["python", "-m", "researchos.cli"]

# 默认命令：显示帮助信息
CMD ["--help"]
