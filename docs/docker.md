# ResearchOS Docker Guide

本文档是 ResearchOS 当前唯一的 Docker 主文档。

之前分散在旧 Docker 使用说明和 Docker 测试报告中的有效信息已经并入本文档。

如果你之前看过旧文档，现在请以本文档为准。

本文档详细说明 ResearchOS 当前 Docker 方案的目标、使用方法、注意事项和常见问题。

当前仓库的 Docker 方案不是“可选附属品”，而是正式支持的运行模式之一，尤其适合：

- T9 LaTeX 编译和投稿打包
- legacy T5 / T7 内部实验调试
- 外部 executor 自行需要的隔离实验环境
- 环境一致性要求高的场景
- 需要避免宿主机依赖漂移的场景

---

## 1. 当前 Docker 方案的定位

ResearchOS 当前采用 **统一镜像** 模式，而不是“实验镜像一套、LaTeX 镜像一套”的多镜像模式。

当前统一镜像名称：

- `researchos/system:latest`

目标：

- 同一个镜像支持 legacy T5/T7 内部实验调试和外部 executor 隔离运行
- 同一个镜像支持 T9 论文编译与投稿打包
- 同一个镜像支持 CLI 命令和 runtime 工具

相关文件：

- [infra/docker/Dockerfile](../infra/docker/Dockerfile)
- [infra/docker/build.sh](../infra/docker/build.sh)
- [infra/docker/run.sh](../infra/docker/run.sh)
- [docker-compose.yml](../docker-compose.yml)

### 1.1 当前有两种 Docker 使用模式

ResearchOS 不是只能“整个系统都在 Docker 中”或“完全不用 Docker”。当前设计同时支持两种模式：

1. **整体系统在 Docker 中运行**
   使用 `bash infra/docker/run.sh ...` 启动统一镜像，容器入口仍然是 `python3 -m researchos.cli`。这种模式下 CLI、Agent、tool、LaTeX 和实验命令都在容器环境里执行，workspace 通过 bind mount 挂到 `/workspace`。

2. **宿主机运行 ResearchOS，部分阶段使用 Docker tool**
   宿主机 Python 进程运行 `researchos run/resume/run-task`。默认外部实验主链的 `T5-HANDOFF/T5-DRY-RUN/T7-*` 不要求 Docker；legacy T5/T7 内部实验调试、外部 executor 自行隔离运行，或 T9/T3.6-COMPILE 需要 LaTeX 编译时，`docker_exec` / `latex_compile` 可调用统一镜像执行具体命令。宿主机没有 `latexmk` 时，`latex_compile` 会自动走 Docker；如果 Docker 不可用，相关阶段会以 `WAITING_ENVIRONMENT` 暂停，修好环境后可以 `resume`。

容器内运行时会检测到自己已经在容器中，`latex_compile` 会直接调用容器内 `latexmk`，不会再做 Docker-in-Docker。

### 1.2 当前机器实测状态

在当前 `/mnt/data/DIG-ResearchOS` 环境中已经检查到：

- Docker CLI/daemon 可用，`Docker Root Dir` 已是 `/mnt/data/Docker`，不会继续默认占用 `/var/lib/docker`。
- 镜像 `researchos/system:latest` 已存在，且 `bash infra/docker/build.sh` 已实测可完成构建。
- 容器内 `latexmk` 可用，可以支撑 T3.6 survey 和 T9 投稿包的 TeX 编译；最小 `article` smoke test 已在 `workspace/docker-tex-smoke/main.tex` 上通过并生成 `main.pdf`。
- 宿主机 `nvidia-smi` 可用，但 `docker info` 的 runtimes 里没有 `nvidia`，`docker run --gpus all ... nvidia-smi` 当前失败，报错类似 `failed to discover GPU vendor from CDI: no known GPU vendor found`。

结论：当前 Docker 存储、镜像和 TeX 工具链可用；Docker GPU 还不可用，需要注册 NVIDIA Container Toolkit / runtime 或 CDI 后才能让容器使用 GPU。默认外部实验主链不依赖 ResearchOS 内部 Docker/GPU；legacy T5/T7 或外部 executor 如果强依赖 GPU，在 GPU runtime 修好前应预期以环境等待或 CPU 降级处理。

---

## 2. 避免占用系统盘

Docker 的镜像层、构建缓存、容器层和默认 named volume 默认会写入 daemon 的
`Docker Root Dir`，常见位置是 `/var/lib/docker`。这会快速占满系统盘。
ResearchOS 推荐把 Docker 数据根目录迁移到：

```text
/mnt/data/Docker
```

### 2.1 daemon 级迁移

在宿主机执行：

```bash
sudo mkdir -p /mnt/data/Docker
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{
  "data-root": "/mnt/data/Docker"
}
JSON
sudo systemctl restart docker
docker info --format 'Docker Root Dir: {{.DockerRootDir}}'
```

如果旧机器已经在 `/var/lib/docker` 有大量镜像和容器，先停止 Docker，再按你的运维策略
迁移或重新构建镜像。不要在 Docker daemon 正运行时直接移动 `/var/lib/docker`。

### 2.2 ResearchOS 脚本与 compose

`infra/docker/build.sh` 和 `infra/docker/run.sh` 会打印当前 `Docker Root Dir`，并在
未显式设置 `DOCKER_CONFIG` 时把 Docker CLI 配置放到：

```text
${RESEARCHOS_DOCKER_ROOT:-/mnt/data/Docker}/cli-config
```

`docker-compose.yml` 已将 ResearchOS 缓存和数据目录改为显式 bind mount：

```text
${RESEARCHOS_DOCKER_ROOT:-/mnt/data/Docker}/researchos_cache
${RESEARCHOS_DOCKER_ROOT:-/mnt/data/Docker}/researchos_data
```

这只能控制 compose 级缓存和数据；镜像层、BuildKit cache 和容器 writable layer 仍由
Docker daemon 的 `data-root` 控制。因此真正避免系统盘占用，必须优先配置
`/etc/docker/daemon.json`。

---

## 3. Docker 方案包含什么

当前 Dockerfile 基于：

- `nvidia/cuda:12.4.0-runtime-ubuntu22.04`

镜像中包含：

- Python 3.11
- ResearchOS runtime
- LLM 依赖
- 常用 ML 依赖
- LaTeX 工具链
- `latexmk`
- `ripgrep`
- Node.js / npm

这意味着它既能做：

- 论文编译
- shell 调试
- 研究代码运行
- MCP server 启动基础依赖

---

## 4. 核心设计原则

### 4.1 统一入口

容器入口点设置为：

```text
python3 -m researchos.cli
```

所以在容器里最终跑的不是某个 ad-hoc shell，而是同一套 CLI。

### 4.2 Workspace 挂载优先

容器运行时，宿主机的 workspace 挂载到容器内：

- 宿主机：`$WORKSPACE_DIR`
- 容器内：`/workspace`

这意味着：

- 所有产物都保留在宿主机
- 容器退出后文件不丢
- 便于本地继续调试同一 workspace

### 4.3 自动环境变量透传

`run.sh` 会自动读取项目根目录 `.env`，并把常用变量注入容器，例如：

- `SILICONFLOW_API_KEY`
- `SILICONFLOW_BASE_URL`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `ANTHROPIC_API_KEY`
- `S2_API_KEY`
- `RESEARCHER_EMAIL`
- `GITHUB_TOKEN`

---

## 5. 主要脚本与职责

### 5.1 `infra/docker/build.sh`

用途：

- 构建统一镜像
- 做基础检查
- 展示镜像大小和镜像信息

使用方式：

```bash
cd ResearchOS
bash infra/docker/build.sh
```

也可以自定义 tag：

```bash
bash infra/docker/build.sh dev
```

### 5.2 `infra/docker/run.sh`

用途：

- 启动容器
- 挂载 workspace
- 透传环境变量
- 自动判断 GPU 是否可用
- 执行任意 ResearchOS CLI 命令

使用方式：

```bash
cd ResearchOS
bash infra/docker/run.sh <command> [args...]
```

典型例子：

```bash
bash infra/docker/run.sh selftest
bash infra/docker/run.sh init-workspace --workspace /workspace/local-test2
bash infra/docker/run.sh run-task T3 --workspace /workspace/local-test2
bash infra/docker/run.sh run --workspace /workspace/local-test2
bash infra/docker/run.sh bash
```

### 5.3 `docker-compose.yml`

用途：

- 提供 compose 风格的统一服务配置
- 固定挂载、环境变量、工作目录和 GPU 能力声明

更适合：

- 团队内共享运行方式
- 固定化服务定义
- 长期环境维护

### 5.4 什么时候优先用 Docker

推荐优先使用 Docker 的场景：

- 你要跑 `T5` / `T7`，而且实验依赖较重
- 你要跑 `T9`，需要稳定的 LaTeX 编译环境
- 你在多台机器上切换，希望环境一致
- 你希望把宿主机 Python 环境和 ResearchOS 运行环境隔离

不一定非要用 Docker 的场景：

- 你只是在本地调试 `T1` / `T2` / `T3`
- 你只是在看日志、改 prompt、读文档
- 你暂时不需要 GPU 或 LaTeX

---

## 6. 构建镜像

### 6.1 标准构建

```bash
cd ResearchOS
bash infra/docker/build.sh
```

成功后可检查：

```bash
docker images | grep researchos
```

### 6.2 Dockerfile 做了什么

主要步骤：

1. 基于 CUDA runtime 镜像
2. 安装系统依赖
3. 安装 Python 核心依赖
4. 安装 LLM / dev 依赖
5. 安装常见 ML 包
6. 复制 `researchos/`、`config/`、`scripts/`
7. `pip install .`
8. 创建 `/workspace`
9. 设置 entrypoint

### 6.3 常见构建问题

#### 问题 1：Docker 未安装

表现：

```text
错误: Docker 未安装或不在 PATH 中
```

解决：

- 安装 Docker
- 确认 `docker` 可执行

#### 问题 2：镜像构建时间较长

原因：

- LaTeX + PyTorch + ML 依赖体积较大

建议：

- 第一次构建后尽量复用缓存
- 仅在依赖变化时重建

#### 问题 3：代理 / 网络问题

`build.sh` 会打印 `HTTP_PROXY` / `HTTPS_PROXY` 检查信息。若你所在环境需要代理，可在构建前显式设置。

---

## 7. 运行容器

### 7.1 最小运行

```bash
cd ResearchOS
bash infra/docker/run.sh --help
```

### 7.2 初始化 workspace

```bash
bash infra/docker/run.sh init-workspace --workspace /workspace/local-test2 --project-id local-test2 --topic "demo topic"
```

注意：

- 这里的 `/workspace/local-test2` 是容器内路径
- 实际落盘会回写到宿主机映射目录

### 7.3 跑完整流水线

```bash
bash infra/docker/run.sh run --workspace /workspace/local-test2
```

### 7.4 跑单阶段

```bash
bash infra/docker/run.sh run-task T2 --workspace /workspace/local-test2
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

### 7.5 手动 `docker run`（不用 `run.sh`）

如果你想完全显式地控制挂载和环境变量，也可以直接用 `docker run`。

初始化 workspace：

```bash
cd ResearchOS
docker run --rm -it \
  -v ./workspace:/workspace \
  researchos/system:latest \
  init-workspace --workspace /workspace/local-test2 --project-id local-test2 --topic "agent memory retrieval"
```

跑完整 pipeline：

```bash
cd ResearchOS
docker run --rm -it \
  -v ./workspace:/workspace \
  -e SILICONFLOW_API_KEY="$SILICONFLOW_API_KEY" \
  -e OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
  -e OPENAI_API_KEY="$OPENAI_API_KEY" \
  -e S2_API_KEY="$S2_API_KEY" \
  researchos/system:latest \
  run --workspace /workspace/local-test2
```

单 task 调试：

```bash
cd ResearchOS
docker run --rm -it \
  -v ./workspace:/workspace \
  -e SILICONFLOW_API_KEY="$SILICONFLOW_API_KEY" \
  researchos/system:latest \
  run-task T3 --workspace /workspace/local-test2
```

### 7.6 交互式进入容器

```bash
bash infra/docker/run.sh bash
```

进入后你可以：

- 检查 `python -m researchos.cli --help`
- 检查 `pdflatex --version`
- 检查 `nvidia-smi`

---

## 8. Workspace 映射与文件保存机制

这是 Docker 使用里最关键的一点。

### 8.1 映射关系

宿主机：

```text
./workspace
```

容器内：

```text
/workspace
```

### 8.2 这意味着什么

- 在容器内写入 `/workspace/local-test2/...`
- 等价于在宿主机写入 `./workspace/local-test2/...`

所以：

- 容器退出后产物保留
- 本地 IDE 里能直接打开同一套文件
- 单任务续跑和 pipeline resume 仍然可用

### 8.3 最常见的路径误解

最常见的误解是：

- 宿主机上你看到的是 `./workspace/local-test2`
- 容器里 agent 看到的是 `/workspace/local-test2`

这两个路径指向的是同一份数据。

所以：

- 在容器里写 `paper_notes/*.md`
- 宿主机 IDE 里会立刻看到更新

这不是复制，而是挂载。

---

## 9. GPU 检测逻辑

`run.sh` 当前不会盲目加 `--gpus all`，而是做一轮真实探测。

判断逻辑大致是：

1. 宿主机 `nvidia-smi` 是否可用
2. 直接运行 `docker run --rm --gpus all --entrypoint nvidia-smi researchos/system:latest`
3. 若失败，再在宿主机存在 `nvidia-container-runtime` 时尝试 `--runtime=nvidia --gpus all`
4. 若任一 probe 成功，则给正式容器添加 GPU 参数
5. 否则明确提示 GPU probe 错误并退回 CPU 模式

### 9.1 为什么这样设计

因为“宿主机能跑 `nvidia-smi`”并不等于“Docker 里就能用 GPU”。

常见失败场景：

- 宿主机有 NVIDIA 驱动
- 但 Docker 没装 `nvidia-container-toolkit`
- 或当前系统是 LXC，GPU 设备没暴露给 Docker

### 9.2 你应该如何验证

宿主机验证：

```bash
nvidia-smi
docker info --format '{{json .Runtimes}}'
```

Docker 验证：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

ResearchOS 镜像验证：

```bash
docker run --rm --gpus all --entrypoint nvidia-smi researchos/system:latest
```

### 9.3 注册 NVIDIA runtime / CDI

如果宿主机 `nvidia-smi` 正常，但 Docker GPU probe 失败，需要安装并配置 NVIDIA Container Toolkit。Ubuntu/Debian 常见步骤如下，实际以 [NVIDIA Container Toolkit 安装文档](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.17.4/install-guide.html) 为准：

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

如果 Docker 版本或宿主环境走 CDI 发现路径，还可以生成 CDI 配置：

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
sudo systemctl restart docker
```

修复后验证：

```bash
docker info --format '{{json .Runtimes}}'
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
docker run --rm --gpus all --entrypoint nvidia-smi researchos/system:latest
```

只有最后一个 ResearchOS 镜像 probe 成功，`infra/docker/run.sh` 才会在正式运行时自动启用 GPU。

---

## 10. T5/T7 与 Docker

实验阶段往往最适合放进 Docker。

原因：

- 环境更稳定
- 依赖更可复现
- CUDA / PyTorch 版本固定
- 更接近“实验容器”的真实部署形态

当前 `docker_exec` 工具和 unified image 的关系是：

- 容器已经带基础环境
- task 中若需要 `docker_exec`，它会在宿主运行时再调用容器执行具体命令

换言之，Docker 不只是“外层壳”，也是实验工具链的一部分。

---

## 11. T9 与 Docker

T9 尤其适合在 Docker 中运行，因为：

- LaTeX 依赖已经打包
- `latexmk` 已安装
- 论文打包和编译环境更一致

当前 T9 已收紧为：

- 必须编译成功
- 必须产出 `main.pdf`
- 若编译失败，要尝试修复并重试

这类行为在 Docker 中更稳定，因为宿主机不同发行版 / TeX 发行版之间差异较大。

同一套 `latex_compile` 也被 T3.6-COMPILE 复用。编译 `drafts/survey/survey.tex` 时，工具会自动写 `drafts/survey/survey_compile_report.json`；编译 `submission/bundle/main.tex` 时，工具会自动写 `submission/compile_report.json`。这两个 report 都记录 `tex_path`、engine、exit code、PDF/log hash、mtime 和 error，validator 不只看 PDF 是否存在。

### 11.1 LaTeX smoke test

容器内工具链最小检查：

```bash
docker run --rm --entrypoint bash researchos/system:latest -lc 'which latexmk && latexmk -version | head -5'
```

如果要确认完整编译链路，可以在一个临时 workspace 中放最小 `main.tex`，再执行：

```bash
docker run --rm \
  -v /mnt/data/DIG-ResearchOS/workspace/docker-tex-smoke:/workspace \
  --entrypoint bash \
  researchos/system:latest \
  -lc 'cd /workspace && latexmk -pdf -interaction=nonstopmode main.tex'
```

成功后应在挂载目录看到 `main.pdf`。

---

## 12. 环境变量与 `.env`

Docker 脚本会自动：

1. 读取项目根目录 `.env`
2. 优先保留 shell 中已显式设置的变量
3. 把已设置的变量逐个传入容器

推荐做法：

1. 复制 `.env.example`
2. 填上实际 key
3. 让 `run.sh` 自动加载

例如：

```bash
cp .env.example .env
```

然后填写：

- `SILICONFLOW_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `S2_API_KEY`
- `RESEARCHER_EMAIL`

### 12.1 这些变量分别干什么

| 变量 | 用途 |
| --- | --- |
| `SILICONFLOW_API_KEY` | 当前默认主路由 provider 之一，很多 agent 直接依赖它 |
| `SILICONFLOW_BASE_URL` | SiliconFlow OpenAI-compatible endpoint |
| `OPENROUTER_API_KEY` | OpenRouter fallback |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI 或兼容 provider |
| `ANTHROPIC_API_KEY` | Anthropic provider |
| `S2_API_KEY` | Semantic Scholar 检索增强 |
| `RESEARCHER_EMAIL` | OpenAlex / arXiv 等抓取时的联系标识 |
| `GITHUB_TOKEN` | 部分 MCP / GitHub 集成需要 |

### 12.2 Docker 里变量如何生效

典型链路是：

1. 你在宿主机根目录放 `.env`
2. `infra/docker/run.sh` 读取 `.env`
3. 脚本把变量透传给 `docker run`
4. 容器内 `researchos.cli` 和 runtime 再读取这些变量

所以如果 Docker 里报“缺少 API key”，排查顺序通常是：

1. `.env` 有没有填
2. 变量名有没有拼错
3. `run.sh` 是否从项目根目录执行
4. 你是否手动 `docker run` 却忘了 `-e ...`

---

## 13. 数据持久化、日志和追踪

### 13.1 哪些文件会持久化

只要写在挂载的 workspace 里，就会持久化到宿主机：

- `literature/`
- `ideation/`
- `pilot/`
- `experiments/`
- `drafts/`
- `submission/`
- `_runtime/logs/`
- `_runtime/traces/`

### 13.2 哪些文件不会保留

容器内部但不在挂载目录里的临时文件，容器退出后就会消失。

### 13.3 Docker 模式下怎么看日志

ResearchOS 自己的主日志仍然在 workspace 里：

```bash
tail -f ./workspace/local-test2/_runtime/logs/researchos.log
```

trace 文件也仍然在 workspace 里：

```bash
ls ./workspace/local-test2/_runtime/traces
```

如果你要看容器标准输出，可以直接观察当前终端，或者不用 `--rm` 时再配合 `docker logs`。

---

## 14. 常见问题与排查

### 14.1 `镜像不存在`

表现：

```text
错误: 镜像 researchos/system:latest 不存在
```

解决：

```bash
bash infra/docker/build.sh
```

### 14.2 `未检测到 LLM API 密钥`

表现：

```text
提示: 未检测到 LLM API 密钥
```

说明：

- 这是提示，不一定立刻失败
- 但真正运行到 LLM 调用时会失败

解决：

- 在 `.env` 中填写至少一个 provider 的 API key

### 14.3 GPU 不可用

表现：

- `run.sh` 提示退回 CPU
- 容器内 `nvidia-smi` 失败

排查顺序：

1. 宿主机 `nvidia-smi`
2. Docker runtimes
3. `nvidia-container-toolkit`
4. 是否处于 LXC / 嵌套虚拟化

### 14.4 T9 编译失败

不要先怀疑 Docker 本身。

当前更常见原因是：

- LaTeX 源文件问题
- 图路径问题
- BibTeX 问题
- venue 样式与原文冲突

推荐在容器里直接定位：

```bash
bash infra/docker/run.sh bash
cd /workspace/local-test2/submission/bundle
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
tail -n 80 main.log
```

### 14.5 `run.sh` 能跑，但手动 `docker run` 失败

最常见原因：

- 你忘了挂载 `-v ...:/workspace`
- 你忘了传 `-e API_KEY=...`
- 你把宿主机路径写成了容器内路径
- 你在容器里用了宿主机路径

### 14.6 明明宿主机文件存在，容器里看不到

通常是挂载目录不对。

例如你挂载的是：

```bash
-v ./workspace:/workspace
```

那容器里正确路径就是：

```bash
/workspace/local-test2
```

### 14.7 容器里路径和宿主机路径不一致

记住：

- 宿主机看的是 `.../workspace/...`
- 容器内看的是 `/workspace/...`

这是正常的路径映射，不是文件丢失。

---

## 15. 推荐使用模式

### 15.1 使用者

推荐：

- 平时本地阅读与检查：宿主机模式
- 正式实验与打包：Docker 模式

### 15.2 开发者

推荐：

- 改代码：宿主机模式
- 验证容器一致性：Docker 模式
- 验证 T9 编译：优先 Docker 模式

---

## 16. 一组推荐命令

### 构建

```bash
cd ResearchOS
bash infra/docker/build.sh
```

### 初始化项目

```bash
bash infra/docker/run.sh init-workspace --workspace /workspace/local-test2 --project-id local-test2 --topic "agent memory retrieval"
```

### 全链运行

```bash
bash infra/docker/run.sh run --workspace /workspace/local-test2
```

### 从中断点恢复

```bash
bash infra/docker/run.sh resume --workspace /workspace/local-test2
```

### 单任务恢复式调试

```bash
bash infra/docker/run.sh run-task T3 --workspace /workspace/local-test2
bash infra/docker/run.sh run-task T7 --workspace /workspace/local-test2
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

### 跑完整流水线

```bash
bash infra/docker/run.sh run --workspace /workspace/local-test2
```

### 续跑

```bash
bash infra/docker/run.sh resume --workspace /workspace/local-test2
```

### 单阶段调试

```bash
bash infra/docker/run.sh run-task T3 --workspace /workspace/local-test2
bash infra/docker/run.sh run-task T9 --workspace /workspace/local-test2
```

### 容器内排查 LaTeX

```bash
bash infra/docker/run.sh bash
cd /workspace/local-test2/submission/bundle
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

---

## 17. 相关文档

- [docs/runtime.md](./runtime.md)
- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/config.md](./config.md)
- [docs/dev.md](./dev.md)
- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)
- Docker daemon `data-root` 官方说明：<https://docs.docker.com/engine/daemon/>
- NVIDIA Container Toolkit 官方说明：<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.17.4/install-guide.html>
