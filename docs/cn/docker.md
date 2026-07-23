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

macOS 需要 MacTeX 或 BasicTeX 以及 `latexmk`。

## Windows

### Docker Desktop：推荐

要稳定编译 T3.6 Survey 与 T9 投稿 PDF，推荐使用运行 Linux containers 的 Docker Desktop。项目提供的 `researchos/system:latest` 镜像已经包含 TeX Live、`latexmk`、pdfLaTeX、XeLaTeX、BibTeX 和中文 TeX 包，因此 Windows 主机无需安装本机 TeX。

在仓库根目录打开 PowerShell，先在主机配置模型，再构建镜像并使用 PowerShell 包装器：

```powershell
py -m researchos.cli configure-llm
New-Item -ItemType Directory -Force workspace
docker compose -f deploy/compose.yaml build researchos

cd deploy
.\researchos.ps1 doctor
.\researchos.ps1 init project-a -Topic "面向 LLM Agent 的记忆系统"
.\researchos.ps1 run project-a
```

Compose 会把 `config/` 以只读方式挂入容器。不要尝试在容器内运行 `configure-llm`；先修改 Windows 主机上的 `config/model_settings.yaml`，再启动或恢复 Compose。包装器会在需要时创建 `workspace/`，并直接使用主机文件。

### 原生 MiKTeX 或 TeX Live：支持

需要在 Windows 本机编译时，安装 MiKTeX 或 TeX Live。确保 `latexmk`、`pdflatex`、`xelatex` 和 `bibtex` 位于 Windows `PATH`，重新打开 PowerShell 后，在长任务前确认四个命令都可用：

```powershell
"latexmk", "pdflatex", "xelatex", "bibtex" |
  ForEach-Object { Get-Command $_ -ErrorAction Stop }

py -m researchos.cli doctor --workspace .\workspace\project-a
```

若有命令找不到，完成 TeX 安装或将对应 TeX binary 目录加入 `PATH`，重新打开终端后再次检查。使用 MiKTeX 时，启用缺失宏包自动安装，或预先安装目标 venue 模板需要的宏包。`tectonic` 可以作为轻量回退，但 `auto` 会在 Docker 前优先选它；对于依赖完整 BibTeX、字体或 venue 宏包的正式模板，不建议将它作为首选后端。

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
| Windows 的 `Get-Command` 检查失败 | 将 MiKTeX/TeX Live binary 目录加入 `PATH`，重新打开 PowerShell 后运行 `doctor`；或改用 Docker Desktop 路线。 |
| Docker 守护进程/镜像不可用 | 启动 Docker 并构建配置的镜像，或安装主机 TeX。 |
| 镜像缺少 TeX 命令 | 从 `infra/docker/Dockerfile` 重新构建 `researchos/system:latest`。 |
| `.tex` 文件中的编译错误 | 阅读编译报告/日志，修复出错的源文件或资源，然后 `resume`。 |

绝不要通过增加 LLM 重试来解决 TeX 预检失败。运行时会在写入更多文本之前暂停，以便首先修复环境。
