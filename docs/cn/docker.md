# Native、Docker 与 LaTeX

> [中文](../cn/docker.md) | [English](../en/docker.md)

ResearchOS 只有一个运行时契约。原生和 Docker Compose 执行相同的 CLI、制品、验证器、人类闸门与状态机。

它们也执行相同的公开 Skill 契约、集成工作流会话、提供者上下文抽象批处理以及 Survey 证据闸门。不要将原生命令与容器化命令作为同一个工作区的并发写入者。

| 模式 | 工作区路径 | TeX 位置 | 使用场景 |
| --- | --- | --- | --- |
| 原生 | 主机路径，例如 `workspace/project-a` | 主机 TeX，继而允许的 Docker 回退 | 开发与直接本地使用 |
| Docker Compose | `/app/workspace/project-a` | ResearchOS 镜像内的 TeX | 可复现的 CLI 环境 |

请勿将两种模式作为同一工作区的并发写入者。

## 原生 TeX

`latex.default_backend: auto` 依次选择：

1. 本地 `latexmk`。
2. 本地 `tectonic`。
3. 当启用 Docker 回退时，使用允许列表中的 `latex.docker_image`。

```bash
python -m researchos.cli doctor --workspace ./workspace/project-a
```

在 Ubuntu/Debian 上安装主机 TeX：

```bash
sudo apt-get update
sudo apt-get install -y \
  latexmk texlive-latex-base texlive-latex-extra \
  texlive-fonts-recommended texlive-xetex texlive-lang-chinese
```

macOS 需要 MacTeX 或 BasicTeX 以及 `latexmk`。Windows 需要 MiKTeX 或 TeX Live，并将 `latexmk`、`pdflatex`、`xelatex` 和 `bibtex` 加入 `PATH`。

## Compose

```bash
cp .env.example .env
mkdir -p workspace
docker compose -f deploy/compose.yaml build researchos
docker compose -f deploy/compose.yaml run --rm researchos doctor
```

```bash
docker compose -f deploy/compose.yaml run --rm researchos \
  run --workspace /app/workspace/project-a

docker compose -f deploy/compose.yaml run --rm researchos \
  resume --workspace /app/workspace/project-a
```

Compose 服务不挂载 Docker 套接字，也不使用 Docker-in-Docker。镜像本身必须包含 TeX。在 Linux 上，当需要主机可写输出时，在 `.env` 中设置 `RESEARCHOS_UID=$(id -u)` 和 `RESEARCHOS_GID=$(id -g)`。

## 为何 TeX 不在 requirements.txt 中

`requirements.txt` 安装 Python 包，其中包括用于一个确定性 Survey 分类图的 matplotlib。TeX Live、`latexmk` 和字体是系统依赖项；请通过主机包管理器安装，或将其固化到 `infra/docker/Dockerfile` 中。

## 修复与恢复

| `doctor` / 预检结果 | 修复 |
| --- | --- |
| `latexmk_found_on_current_path` | 继续。 |
| `docker_tex_image_verified` | 继续使用已配置的 Docker 回退。 |
| Docker 守护进程/镜像不可用 | 启动 Docker 并构建配置的镜像，或安装主机 TeX。 |
| 镜像缺少 TeX 命令 | 从 `infra/docker/Dockerfile` 重新构建 `researchos/system:latest`。 |
| `.tex` 文件中的编译错误 | 阅读编译报告/日志，修复出错的源文件或资源，然后 `resume`。 |

绝不要通过增加 LLM 重试来解决 TeX 预检失败。运行时会在写入更多文本之前暂停，以便首先修复环境。
