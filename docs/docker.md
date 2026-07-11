# ResearchOS Docker Guide

Docker 现在是 ResearchOS 的可选运行方式，不是默认依赖。

当前主链默认在宿主机 Python 环境中运行。真实实验由外部执行器完成，ResearchOS 只负责编译 handoff 协议、等待 result pack、摄取、审计和写作。LaTeX 编译默认使用宿主机 `latexmk`，不再把 Docker 作为隐式 fallback。

## 1. Docker 的定位

当前 Docker 镜像 `researchos/system:latest` 是轻量 CLI 容器，适合：

- 隔离运行 ResearchOS CLI
- 复现一个干净的 Python 运行环境
- 在不污染宿主机 Python 环境的情况下调试 `run`、`resume`、`run-task`

它不是默认实验环境，也不是默认 LaTeX 环境。

默认镜像不包含：

- CUDA / NVIDIA runtime
- PyTorch / Transformers / WandB 等实验栈
- TeX Live / `latexmk`
- Node / MCP server

如果某个团队确实需要实验容器或 TeX 容器，应单独维护面向该任务的镜像，不要把它混入 ResearchOS 默认镜像。

相关文件：

- [deploy/compose.yaml](../deploy/compose.yaml)
- [deploy/researchos.sh](../deploy/researchos.sh)
- [deploy/researchos.ps1](../deploy/researchos.ps1)
- [infra/docker/Dockerfile](../infra/docker/Dockerfile)
- [infra/docker/build.sh](../infra/docker/build.sh)
- [infra/docker/run.sh](../infra/docker/run.sh)（低层兼容 wrapper）

## 2. 推荐默认安装

日常开发优先使用宿主机环境：

```bash
conda create -n researchos python=3.11 -y
conda activate researchos
pip install -r requirements.txt
pip install -e .
python -m researchos.cli selftest
```

`requirements.txt` 是唯一 Python 依赖文件，覆盖 ResearchOS runtime、LLM 路由、PDF/BibTeX 处理和测试依赖。它不再包含本地实验训练栈。

如果要跑 T3.6 survey 或 T9 submission 编译，需要在宿主机安装 TeX：

```bash
sudo apt-get update
sudo apt-get install -y \
  texlive-latex-base \
  texlive-latex-extra \
  texlive-fonts-recommended \
  texlive-xetex \
  texlive-lang-chinese \
  latexmk
```

macOS 使用 MacTeX 或 BasicTeX 加 `latexmk`；Windows 使用 MiKTeX 或 TeX Live，并确保 `latexmk` 在 PATH 中。

验证：

```bash
latexmk -version
python -m researchos.cli selftest
```

如果 `latex_compile` 报 `WAITING_ENVIRONMENT: latexmk is not installed`，安装 TeX 后直接 `resume` 即可。

## 3. 推荐 Docker Compose 入口

```bash
cd /mnt/data/DIG-ResearchOS
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

Compose 使用宿主机 bind mount：

```text
workspace -> /app/workspace
```

容器删除、镜像升级或 `docker compose down` 都不会删除 `workspace` 下的真实项目文件。

Linux 下，`deploy/researchos.sh` 会自动把 `${RESEARCHOS_UID}:${RESEARCHOS_GID}`
设置为当前用户，避免生成 root-owned workspace 文件。直接运行
`docker compose` 时默认使用 `0:0` 保证 root-owned checkout 也能写入；如果希望
直接 Compose 也按当前用户写入，可以在 `.env` 中设置
`RESEARCHOS_UID=$(id -u)` 和 `RESEARCHOS_GID=$(id -g)`。

## 4. 构建轻量 Docker 镜像

如果只想构建镜像而不走 Compose：

```bash
cd /mnt/data/DIG-ResearchOS
bash infra/docker/build.sh
```

默认镜像名是 `researchos/system:latest`。构建内容主要是 `python:3.11-slim-bookworm`、`git/curl/wget/ripgrep`、`requirements.txt` 和 ResearchOS 包本身。

## 5. 运行 CLI 容器

最小检查：

```bash
docker compose -f deploy/compose.yaml run --rm researchos --help
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

初始化 workspace：

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  init-workspace \
  --workspace /app/workspace/local-test2 \
  --project-id local-test2 \
  --topic "memory systems for llm agents"
```

运行或恢复：

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/local-test2
docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/local-test2
```

宿主机 `workspace` 会挂载到容器内 `/app/workspace`。容器写入 `/app/workspace/local-test2/...`，宿主机会在 `workspace/local-test2/...` 看到同一份文件。

低层 `infra/docker/run.sh` 仍保留给直接 `docker run` 调试，也使用相同的 `/app/workspace` 容器路径。

## 6. 环境变量

`infra/docker/run.sh` 会读取项目根目录 `.env`，并把常用变量透传给容器：

- `SILICONFLOW_API_KEY`
- `SILICONFLOW_BASE_URL`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `ANTHROPIC_API_KEY`
- `S2_API_KEY`
- `RESEARCHER_EMAIL`
- `GITHUB_TOKEN`

推荐：

```bash
cp .env.example .env
```

然后在 `.env` 中填写实际 key。

## 7. 与 LaTeX 的关系

T3.6 和 T9 使用 `latex_compile` 工具。当前行为是：

- 检测当前环境是否有 `latexmk`
- 有则在当前环境直接编译
- 如果没有 `latexmk` 但有 `tectonic`，可使用本机 `tectonic`
- 没有则返回 `WAITING_ENVIRONMENT`
- 不再自动改走 Docker；只有显式启用 Docker backend/fallback 时才会考虑 Docker

这意味着如果你在宿主机运行 ResearchOS，就在宿主机安装 TeX；如果你坚持在 Docker 里运行并需要编译 LaTeX，就需要自行扩展 Dockerfile 安装 TeX、显式启用项目自有 Docker backend，或在容器外编译。

最小 TeX 验证：

```bash
cd workspace/your-project/submission/bundle
latexmk -pdf -interaction=nonstopmode main.tex
```

中文稿通常需要 `xelatex`、`texlive-lang-chinese` 和可用 CJK 字体。

## 8. 与实验的关系

当前主链不在 ResearchOS runtime 中本地跑真实实验。默认路径是：

```text
T5-HANDOFF -> T5-EXECUTOR-GATE -> T5-EXTERNAL-WAIT/T5-DRY-RUN -> T7-INGEST -> T7-AUDIT -> T7-CLAIMS
```

ResearchOS 生成外部执行器需要的协议和材料，真实实验由 Codex CLI、Claude Code、manual executor 或团队自定义执行器在隔离目录中完成。

`docker_exec` 工具仍保留给 legacy 内部实验调试和高级自定义场景，但不是默认主链要求。需要 GPU/CUDA/PyTorch 时，由外部执行器或项目自定义实验环境自行配置。

## 9. 常见问题

### 镜像不存在

```text
错误: 镜像 researchos/system:latest 不存在
```

先构建：

```bash
bash infra/docker/build.sh
```

### 容器内没有 latexmk

这是当前默认设计。默认 Docker 镜像不提供 TeX。请在宿主机安装 TeX，或维护自己的 TeX 镜像。

### 容器内没有 GPU

这是当前默认设计。默认 Docker 镜像不声明 GPU，也不会自动加 `--gpus all`。真实实验的 GPU 环境由外部执行器或项目实验环境负责。

### `ModuleNotFoundError: No module named 'researchos'`

不要执行 `pip install researchos`。这不是 PyPI 包修复路径。

在仓库根目录运行：

```bash
PYTHONPATH=/mnt/data/DIG-ResearchOS python -m researchos.cli ...
```

或者安装本地 editable 包：

```bash
pip install -e .
```

### 手动 docker run 看不到 workspace

通常是挂载路径错了。参考：

```bash
docker run --rm -it \
  -v "$(pwd)/workspace:/app/workspace" \
  researchos/system:latest \
  run --workspace /app/workspace/local-test2
```

## 10. 维护原则

- 默认文档和 CLI 不应要求 Docker。
- 默认 Docker 镜像保持轻量，只跑 ResearchOS CLI。
- LaTeX 依赖由宿主机 TeX 环境承担。
- 实验依赖由外部执行器或项目自定义环境承担。
- 如果新增 Docker 能力，文档必须明确它是可选扩展，不能写成主链硬依赖。
