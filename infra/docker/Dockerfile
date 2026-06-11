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
ENV RESEARCHOS_VENV=/opt/researchos-venv

# 清除代理环境变量（apt 和 Ubuntu 源不走代理）
# 只有 npm 需要代理
ENV HTTP_PROXY=
ENV HTTPS_PROXY=
ENV http_proxy=
ENV https_proxy=

# 安装系统依赖
# - python3.11: Python 运行时
# - git, curl, wget: 版本控制和下载工具
# - texlive-latex-base + 必要包: LaTeX 基础工具链（替代 texlive-full 节省 ~3GB）
# - texlive-lang-chinese + fonts-noto-cjk: 中文 survey/T8 草稿需要 ctex/xeCJK 与 CJK 字体
# - ripgrep: 快速文本搜索（某些工具可能需要）
# - nodejs, npm: MCP 服务器依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    git \
    curl \
    wget \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-xetex \
    texlive-lang-chinese \
    fonts-noto-cjk \
    fontconfig \
    latexmk \
    ripgrep \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 22.04 keeps /usr/bin/python3 and python3-pip on Python 3.10 even when
# python3.11 is installed. ResearchOS requires Python >=3.11, so all runtime
# Python commands are pinned to a dedicated 3.11 virtual environment.
RUN python3.11 -m venv "$RESEARCHOS_VENV" && \
    "$RESEARCHOS_VENV/bin/pip" install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip setuptools wheel

ENV PATH="$RESEARCHOS_VENV/bin:$PATH"

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY pyproject.toml ./
COPY requirements.txt requirements-llm.txt requirements-dev.txt ./

# 安装 Python 依赖（ResearchOS runtime）
# 注意：这里先安装依赖，后面再安装 ResearchOS 本身（利用 Docker 缓存）
# 使用清华镜像加速下载
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-llm.txt && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt

# 安装常用 ML 依赖（实验代码常用）
# 使用 CUDA 12.4 与基础镜像 nvidia/cuda:12.4.0 匹配
# 使用清华镜像加速下载
# 依赖版本不写死，让 pip 自动解析兼容版本
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    torch \
    torchvision \
    torchaudio \
    --index-url https://download.pytorch.org/whl/cu124 && \
    pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    numpy \
    pandas \
    scikit-learn \
    matplotlib \
    seaborn \
    tqdm \
    wandb \
    transformers \
    datasets \
    accelerate

# 安装 MCP 服务器（可选）
# 注意：@modelcontextprotocol/server-* 包目前不在公共 npm registry 中
# ResearchOS 使用内置的 MCP 适配层，不强制依赖外部 MCP 服务器
# 如果需要 MCP 服务器，可以在运行时通过配置文件指定
#
# 这里跳过 MCP 服务器安装，不影响核心功能
RUN echo "Skipping MCP server installation (not required for core functionality)"

# 复制 ResearchOS 源代码
COPY README.md /app/README.md
COPY researchos /app/researchos
COPY config /app/config
COPY scripts /app/scripts

# 安装 ResearchOS
# 注意：使用普通安装而非可编辑模式，因为 Docker 容器中不需要开发模式
RUN pip install --no-build-isolation .

# 创建 workspace 挂载点
RUN mkdir -p /workspace

# 设置容器标识环境变量（用于环境检测）
ENV CONTAINER_ID=researchos-docker

# 入口点：使用 Python 模块方式启动 CLI
ENTRYPOINT ["python", "-m", "researchos.cli"]

# 默认命令：显示帮助信息
CMD ["--help"]
