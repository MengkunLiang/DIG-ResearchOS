# ResearchOS Docker 镜像 - 可复现 CLI 与 PDF 编译环境
#
# 这个镜像包含：
# - Python 3.11 运行时
# - ResearchOS runtime 及其依赖
# - latexmk、pdfLaTeX、XeLaTeX 与中文语言包（T3.6/T9 真实 PDF 验证）
#
# 构建命令：
#   cd /mnt/data/DIG-ResearchOS
#   docker build -t researchos/system:latest -f infra/docker/Dockerfile .
#
# 运行命令：
#   docker run --rm -it \
#     -v $(pwd)/workspace:/app/workspace \
#     -e OPENAI_API_KEY=$OPENAI_API_KEY \
#     -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
#     researchos/system:latest \
#     run --workspace /app/workspace/project-a

FROM python:3.11-slim-bookworm

LABEL maintainer="ResearchOS Team"
LABEL description="ResearchOS CLI runtime with reproducible LaTeX compilation"
LABEL version="0.1.0"

# 设置环境变量，避免交互式提示
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV RESEARCHOS_VENV=/opt/researchos-venv

# Keep the runtime image free of host proxy settings by default.
ENV HTTP_PROXY=
ENV HTTPS_PROXY=
ENV http_proxy=
ENV https_proxy=

ARG PIP_INDEX_URL=

# 安装系统依赖
# - git, curl, wget: 版本控制和下载工具
# - ripgrep: 快速文本搜索（某些工具可能需要）
# - TeX Live + latexmk: 容器内不依赖 Docker-in-Docker 即可完成 T3.6/T9 编译
# A bounded retry policy keeps first-time image builds diagnosable on flaky
# mirrors.  Disabling HTTP pipelining avoids a known class of proxy/mirror
# stalls that otherwise leaves `apt-get` silent indefinitely during TeX Live
# downloads.
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=45 -o Acquire::https::Timeout=45 -o Acquire::http::Pipeline-Depth=0 update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=45 -o Acquire::https::Timeout=45 -o Acquire::http::Pipeline-Depth=0 install -y --no-install-recommends \
    git \
    curl \
    wget \
    ripgrep \
    latexmk \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-xetex \
    texlive-lang-chinese \
    && rm -rf /var/lib/apt/lists/*

RUN if [ -n "$PIP_INDEX_URL" ]; then \
      python -m pip install --no-cache-dir -i "$PIP_INDEX_URL" --upgrade pip setuptools wheel; \
    else \
      python -m pip install --no-cache-dir --upgrade pip setuptools wheel; \
    fi

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY pyproject.toml ./
COPY requirements.txt ./

# 安装 Python 依赖（ResearchOS runtime + LLM/PDF/test）
# 注意：这里先安装依赖，后面再安装 ResearchOS 本身（利用 Docker 缓存）
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

# 复制 ResearchOS 源代码
COPY README.md /app/README.md
COPY README.zh-CN.md /app/README.zh-CN.md
COPY researchos /app/researchos
COPY config /app/config
COPY docs /app/docs
COPY skills /app/skills
COPY scripts /app/scripts
COPY latex_templete /app/latex_templete

# 安装 ResearchOS
# 注意：使用普通安装而非可编辑模式，因为 Docker 容器中不需要开发模式
RUN pip install --no-build-isolation .

# 创建 workspace bind mount 挂载点
RUN mkdir -p /app/workspace

# 设置容器标识环境变量（用于环境检测）
ENV CONTAINER_ID=researchos-docker
ENV RESEARCHOS_WORKSPACE_ROOT=/app/workspace

# 入口点：使用同一套 ResearchOS CLI
ENTRYPOINT ["researchos"]

# 默认命令：显示帮助信息
CMD ["--help"]
