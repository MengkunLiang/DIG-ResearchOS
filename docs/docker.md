# ResearchOS Docker Guide

本文档详细说明 ResearchOS 当前 Docker 方案的目标、使用方法、注意事项和常见问题。

当前仓库的 Docker 方案不是“可选附属品”，而是正式支持的运行模式之一，尤其适合：

- T5 / T7 实验阶段
- T9 LaTeX 编译和投稿打包
- 环境一致性要求高的场景
- 需要避免宿主机依赖漂移的场景

---

## 1. 当前 Docker 方案的定位

ResearchOS 当前采用 **统一镜像** 模式，而不是“实验镜像一套、LaTeX 镜像一套”的多镜像模式。

当前统一镜像名称：

- `researchos/system:latest`

目标：

- 同一个镜像支持 T5/T7 实验执行
- 同一个镜像支持 T9 论文编译与投稿打包
- 同一个镜像支持 CLI 命令和 runtime 工具

相关文件：

- [infra/docker/Dockerfile](../infra/docker/Dockerfile)
- [infra/docker/build.sh](../infra/docker/build.sh)
- [infra/docker/run.sh](../infra/docker/run.sh)
- [docker-compose.yml](../docker-compose.yml)

---

## 2. Docker 方案包含什么

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

## 3. 核心设计原则

### 3.1 统一入口

容器入口点设置为：

```text
python3 -m researchos.cli
```

所以在容器里最终跑的不是某个 ad-hoc shell，而是同一套 CLI。

### 3.2 Workspace 挂载优先

容器运行时，宿主机的 workspace 挂载到容器内：

- 宿主机：`$WORKSPACE_DIR`
- 容器内：`/workspace`

这意味着：

- 所有产物都保留在宿主机
- 容器退出后文件不丢
- 便于本地继续调试同一 workspace

### 3.3 自动环境变量透传

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

## 4. 主要脚本与职责

### 4.1 `infra/docker/build.sh`

用途：

- 构建统一镜像
- 做基础检查
- 展示镜像大小和镜像信息

使用方式：

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh
```

也可以自定义 tag：

```bash
bash infra/docker/build.sh dev
```

### 4.2 `infra/docker/run.sh`

用途：

- 启动容器
- 挂载 workspace
- 透传环境变量
- 自动判断 GPU 是否可用
- 执行任意 ResearchOS CLI 命令

使用方式：

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/run.sh <command> [args...]
```

典型例子：

```bash
bash infra/docker/run.sh selftest
bash infra/docker/run.sh init-workspace --workspace /workspace
bash infra/docker/run.sh run-task T3 --workspace /workspace
bash infra/docker/run.sh run --workspace /workspace
bash infra/docker/run.sh bash
```

### 4.3 `docker-compose.yml`

用途：

- 提供 compose 风格的统一服务配置
- 固定挂载、环境变量、工作目录和 GPU 能力声明

更适合：

- 团队内共享运行方式
- 固定化服务定义
- 长期环境维护

---

## 5. 构建镜像

### 5.1 标准构建

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh
```

成功后可检查：

```bash
docker images | grep researchos
```

### 5.2 Dockerfile 做了什么

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

### 5.3 常见构建问题

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

## 6. 运行容器

### 6.1 最小运行

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/run.sh --help
```

### 6.2 初始化 workspace

```bash
bash infra/docker/run.sh init-workspace --workspace /workspace --project-id demo --topic "demo topic"
```

注意：

- 这里的 `/workspace` 是容器内路径
- 实际落盘会回写到宿主机映射目录

### 6.3 跑完整流水线

```bash
bash infra/docker/run.sh run --workspace /workspace
```

### 6.4 跑单阶段

```bash
bash infra/docker/run.sh run-task T2 --workspace /workspace
bash infra/docker/run.sh run-task T9 --workspace /workspace
```

### 6.5 交互式进入容器

```bash
bash infra/docker/run.sh bash
```

进入后你可以：

- 检查 `python -m researchos.cli --help`
- 检查 `pdflatex --version`
- 检查 `nvidia-smi`

---

## 7. Workspace 映射与文件保存机制

这是 Docker 使用里最关键的一点。

### 7.1 映射关系

宿主机：

```text
/home/liangmengkun/ResearchOS/workspace
```

容器内：

```text
/workspace
```

### 7.2 这意味着什么

- 在容器内写入 `/workspace/local-test2/...`
- 等价于在宿主机写入 `.../ResearchOS/workspace/local-test2/...`

所以：

- 容器退出后产物保留
- 本地 IDE 里能直接打开同一套文件
- 单任务续跑和 pipeline resume 仍然可用

---

## 8. GPU 检测逻辑

`run.sh` 当前不会盲目加 `--gpus all`，而是做一轮真实探测。

判断逻辑大致是：

1. 宿主机 `nvidia-smi` 是否可用
2. Docker 是否注册了 `nvidia` runtime
3. `/proc/driver/nvidia/gpus` 是否可见
4. 若可用则添加：
   - `--gpus all`
   - 或必要时回退为 `--runtime=nvidia --gpus all`
5. 否则退回 CPU 模式

### 8.1 为什么这样设计

因为“宿主机能跑 `nvidia-smi`”并不等于“Docker 里就能用 GPU”。

常见失败场景：

- 宿主机有 NVIDIA 驱动
- 但 Docker 没装 `nvidia-container-toolkit`
- 或当前系统是 LXC，GPU 设备没暴露给 Docker

### 8.2 你应该如何验证

宿主机验证：

```bash
nvidia-smi
docker info --format '{{json .Runtimes}}'
```

Docker 验证：

```bash
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

---

## 9. T5/T7 与 Docker

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

## 10. T9 与 Docker

T9 尤其适合在 Docker 中运行，因为：

- LaTeX 依赖已经打包
- `latexmk` 已安装
- 论文打包和编译环境更一致

当前 T9 已收紧为：

- 必须编译成功
- 必须产出 `main.pdf`
- 若编译失败，要尝试修复并重试

这类行为在 Docker 中更稳定，因为宿主机不同发行版 / TeX 发行版之间差异较大。

---

## 11. 环境变量与 `.env`

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

---

## 12. 常见问题与排查

### 12.1 `镜像不存在`

表现：

```text
错误: 镜像 researchos/system:latest 不存在
```

解决：

```bash
bash infra/docker/build.sh
```

### 12.2 `未检测到 LLM API 密钥`

表现：

```text
提示: 未检测到 LLM API 密钥
```

说明：

- 这是提示，不一定立刻失败
- 但真正运行到 LLM 调用时会失败

解决：

- 在 `.env` 中填写至少一个 provider 的 API key

### 12.3 GPU 不可用

表现：

- `run.sh` 提示退回 CPU
- 容器内 `nvidia-smi` 失败

排查顺序：

1. 宿主机 `nvidia-smi`
2. Docker runtimes
3. `nvidia-container-toolkit`
4. 是否处于 LXC / 嵌套虚拟化

### 12.4 T9 编译失败

不要先怀疑 Docker 本身。

当前更常见原因是：

- LaTeX 源文件问题
- 图路径问题
- BibTeX 问题
- venue 样式与原文冲突

### 12.5 容器里路径和宿主机路径不一致

记住：

- 宿主机看的是 `.../workspace/...`
- 容器内看的是 `/workspace/...`

这是正常的路径映射，不是文件丢失。

---

## 13. 推荐使用模式

### 13.1 使用者

推荐：

- 平时本地阅读与检查：宿主机模式
- 正式实验与打包：Docker 模式

### 13.2 开发者

推荐：

- 改代码：宿主机模式
- 验证容器一致性：Docker 模式
- 验证 T9 编译：优先 Docker 模式

---

## 14. 一组推荐命令

### 构建

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh
```

### 初始化项目

```bash
bash infra/docker/run.sh init-workspace --workspace /workspace --project-id demo --topic "agent memory retrieval"
```

### 跑完整流水线

```bash
bash infra/docker/run.sh run --workspace /workspace
```

### 续跑

```bash
bash infra/docker/run.sh resume --workspace /workspace
```

### 单阶段调试

```bash
bash infra/docker/run.sh run-task T3 --workspace /workspace
bash infra/docker/run.sh run-task T9 --workspace /workspace
```

### 容器内排查 LaTeX

```bash
bash infra/docker/run.sh bash
cd /workspace/local-test2/submission/bundle
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

---

## 15. 相关文档

- [docs/runtime.md](./runtime.md)
- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/config.md](./config.md)
- [docs/dev.md](./dev.md)
- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)
