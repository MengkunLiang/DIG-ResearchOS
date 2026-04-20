# ResearchOS Docker 使用指南

本文档介绍如何使用 Docker 运行 ResearchOS，实现零配置部署和完全可复现的研究环境。

## 目录

- [快速开始](#快速开始)
- [构建镜像](#构建镜像)
- [运行容器](#运行容器)
- [常用命令](#常用命令)
- [环境变量](#环境变量)
- [GPU 支持](#gpu-支持)
- [数据持久化](#数据持久化)
- [故障排查](#故障排查)

---

## 快速开始

### 前置要求

- Docker 20.10+ 已安装
- （可选）nvidia-docker2（如果需要 GPU 支持）
- 至少 20GB 磁盘空间用于镜像

### 三步运行

```bash
# 1. 构建镜像
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh

# 2. 设置环境变量
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.example.com"

# 3. 运行
bash infra/docker/run.sh --help
```

---

## 构建镜像

### 基本构建

```bash
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh
```

这将构建标签为 `researchos/system:latest` 的镜像。

### 指定标签

```bash
bash infra/docker/build.sh v0.1.0
```

构建标签为 `researchos/system:v0.1.0` 的镜像。

### 构建内容

镜像包含：
- Ubuntu 22.04 + CUDA 12.4 运行时
- Python 3.11
- ResearchOS runtime 及其依赖
- 常用 ML 库（PyTorch 2.6.0、Transformers 4.45.0 等）
- LaTeX 完整工具链（texlive-full）
- MCP 服务器（@modelcontextprotocol/server-arxiv）

### 镜像大小

预期大小：10-15GB（取决于依赖版本）

---

## 运行容器

### 使用便捷脚本

```bash
# 显示帮助
bash infra/docker/run.sh --help

# 初始化 workspace
bash infra/docker/run.sh init-workspace --workspace /workspace

# 运行完整 pipeline
bash infra/docker/run.sh run --workspace /workspace

# 单 task 调试
bash infra/docker/run.sh run-task --workspace /workspace --task hello --mock
```

### 手动运行

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  --gpus all \
  researchos/system:latest \
  run --workspace /workspace
```

### 参数说明

- `--rm`: 容器退出后自动删除
- `-it`: 交互式终端
- `-v $(pwd)/workspace:/workspace`: 挂载 workspace 目录
- `-e`: 传递环境变量
- `--gpus all`: 挂载所有 GPU（需要 nvidia-docker2）

---

## 常用命令

### 初始化 workspace

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  init-workspace --workspace /workspace
```

### 运行完整 pipeline

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  run --workspace /workspace
```

### 单 task 调试（Mock 模式）

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run-task --workspace /workspace --task hello --mock
```

### 单 task 调试（真实 LLM）

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  run-task --workspace /workspace --task scout
```

### 查看 workspace 状态

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  status --workspace /workspace
```

### 查看 trace

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  trace --workspace /workspace --run-id <run-id>
```

### 进入容器 shell

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest
```

---

## 环境变量

### 必需环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `OPENAI_API_KEY` | LLM API 密钥 | `sk-xxx` |
| `OPENAI_BASE_URL` | LLM API 基础 URL | `https://api.example.com` |

### 可选环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `RESEARCHOS_IMAGE` | 镜像名称 | `researchos/system:latest` |
| `RESEARCHOS_WORKSPACE` | Workspace 路径 | `$(pwd)/workspace` |

### 设置环境变量

**方法 1：命令行传递**

```bash
docker run --rm -it \
  -e OPENAI_API_KEY="your-key" \
  -e OPENAI_BASE_URL="https://api.example.com" \
  researchos/system:latest \
  --help
```

**方法 2：使用 .env 文件**

```bash
# 创建 .env 文件
cat > .env <<EOF
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.example.com
EOF

# 使用 --env-file 传递
docker run --rm -it \
  --env-file .env \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace
```

**方法 3：从宿主机环境继承**

```bash
# 在宿主机设置
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://api.example.com"

# 使用便捷脚本（自动传递）
bash infra/docker/run.sh run --workspace /workspace
```

---

## GPU 支持

### 前置要求

1. 宿主机安装 NVIDIA 驱动
2. 安装 nvidia-docker2

```bash
# Ubuntu/Debian
sudo apt-get install nvidia-docker2
sudo systemctl restart docker

# 验证安装
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

### 使用 GPU

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  --gpus all \
  researchos/system:latest \
  run --workspace /workspace
```

### 验证 GPU 可用性

```bash
docker run --rm --gpus all \
  researchos/system:latest \
  bash -c "python -c 'import torch; print(torch.cuda.is_available())'"
```

应该输出 `True`。

### 指定特定 GPU

```bash
# 使用 GPU 0 和 1
docker run --rm -it \
  --gpus '"device=0,1"' \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace
```

---

## 数据持久化

### Workspace 挂载

**重要**：Workspace 必须挂载到宿主机，否则数据会在容器退出后丢失。

```bash
# 正确：挂载 workspace
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 错误：不挂载 workspace（数据会丢失）
docker run --rm -it \
  researchos/system:latest \
  run --workspace /workspace
```

### 配置文件挂载（可选）

如果需要自定义配置：

```bash
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -v $(pwd)/config:/app/config:ro \
  researchos/system:latest \
  run --workspace /workspace
```

### 数据目录结构

```
workspace/
├── project.yaml              # 项目配置
├── _runtime/                 # 运行时数据
│   ├── traces/              # 执行跟踪
│   └── logs/                # 日志
├── user_seeds/              # 用户输入
├── literature/              # 文献数据
├── ideation/                # 假设和实验计划
├── experiments/             # 实验结果
└── drafts/                  # 论文草稿
```

---

## 日志查看

### 查看容器日志

Docker 模式下，ResearchOS 的日志会同时输出到：
1. **日志文件**：`workspace/_runtime/logs/researchos.log`（持久化）
2. **容器标准输出**：可通过 `docker logs` 查看

### 方法 1：通过挂载目录查看日志文件

由于 workspace 挂载到宿主机，可以直接在宿主机查看日志文件：

```bash
# 实时查看日志
tail -f workspace/_runtime/logs/researchos.log

# 查看最近 100 行
tail -n 100 workspace/_runtime/logs/researchos.log

# 搜索错误
grep "ERROR" workspace/_runtime/logs/researchos.log

# 高亮显示错误和警告
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto -E 'ERROR|WARNING|$'
```

### 方法 2：查看容器标准输出

```bash
# 查看运行中容器的日志
docker logs <container-id>

# 实时跟踪
docker logs -f <container-id>

# 查看最近 100 行
docker logs --tail 100 <container-id>

# 查看带时间戳的日志
docker logs -t <container-id>
```

### 方法 3：进入容器查看

```bash
# 启动容器并进入 shell
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest

# 容器内查看日志
tail -f /workspace/_runtime/logs/researchos.log
```

### 方法 4：使用 docker exec

```bash
# 找到运行中的容器 ID
docker ps

# 在运行中的容器内执行命令
docker exec -it <container-id> tail -f /workspace/_runtime/logs/researchos.log
```

### 日志级别控制

```bash
# 使用 DEBUG 级别（详细日志）
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  run --workspace /workspace --log-level DEBUG

# 使用 WARNING 级别（只记录警告和错误）
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace --log-level WARNING
```

---

## 调试技巧

### 1. 验证容器环境

```bash
# 检查容器是否能正常启动
docker run --rm researchos/system:latest --help

# 检查 Python 环境
docker run --rm researchos/system:latest bash -c "python --version"

# 检查依赖是否安装
docker run --rm researchos/system:latest bash -c "pip list | grep litellm"

# 检查 LaTeX 是否可用
docker run --rm researchos/system:latest bash -c "latexmk --version"
```

### 2. 测试 workspace 挂载

```bash
# 创建测试文件
echo "test" > workspace/test.txt

# 在容器内验证文件可见
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  bash -c "cat /workspace/test.txt"

# 清理测试文件
rm workspace/test.txt
```

### 3. 调试网络问题

```bash
# 测试容器网络（允许网络）
docker run --rm -it \
  researchos/system:latest \
  bash -c "curl -I https://www.google.com"

# 测试 API 连接
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  bash -c "curl -I $OPENAI_BASE_URL"
```

### 4. 调试 GPU 支持

```bash
# 检查 GPU 是否可用
docker run --rm --gpus all \
  researchos/system:latest \
  bash -c "nvidia-smi"

# 检查 PyTorch GPU 支持
docker run --rm --gpus all \
  researchos/system:latest \
  bash -c "python -c 'import torch; print(torch.cuda.is_available())'"

# 查看 GPU 信息
docker run --rm --gpus all \
  researchos/system:latest \
  bash -c "python -c 'import torch; print(torch.cuda.get_device_name(0))'"
```

### 5. 交互式调试

```bash
# 进入容器 shell 进行交互式调试
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  --entrypoint bash \
  researchos/system:latest

# 容器内手动运行命令
python -m researchos.cli status --workspace /workspace
python -m researchos.cli run-task --workspace /workspace --task hello --mock
```

### 6. 检查容器资源使用

```bash
# 查看容器资源使用情况
docker stats <container-id>

# 查看容器详细信息
docker inspect <container-id>

# 查看容器进程
docker top <container-id>
```

### 7. 调试 docker_exec 工具

容器内模式下，`docker_exec` 工具会直接执行命令而不是启动新容器：

```bash
# 进入容器
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest

# 容器内测试 docker_exec 行为
# docker_exec 会检测到容器环境并直接执行命令
python -c "
from pathlib import Path
print('In container:', Path('/.dockerenv').exists())
"
```

### 8. 调试 LaTeX 编译

```bash
# 测试 LaTeX 工具链
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  bash -c "cd /workspace && echo '\\documentclass{article}\\begin{document}Hello\\end{document}' > test.tex && latexmk -pdf test.tex"

# 检查 PDF 是否生成
ls -la workspace/test.pdf
```

### 9. 使用 Mock 模式快速测试

```bash
# Mock 模式不需要 API key，适合快速验证环境
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run-task --workspace /workspace --task hello --mock
```

### 10. 保留容器用于事后分析

```bash
# 不使用 --rm，容器退出后仍保留
docker run -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 容器退出后查看日志
docker logs <container-id>

# 进入已停止的容器
docker start <container-id>
docker exec -it <container-id> bash

# 清理容器
docker rm <container-id>
```

---

## 故障排查

### 问题 1：镜像构建失败

**症状**：`docker build` 报错

**可能原因**：
- 网络问题（无法下载依赖）
- 磁盘空间不足
- Docker 版本过旧

**解决方法**：
```bash
# 检查磁盘空间
df -h

# 清理 Docker 缓存
docker system prune -a

# 使用国内镜像源（如果在中国）
# 编辑 /etc/docker/daemon.json
{
  "registry-mirrors": ["https://docker.mirrors.ustc.edu.cn"]
}
sudo systemctl restart docker
```

### 问题 2：容器无法访问 GPU

**症状**：`torch.cuda.is_available()` 返回 `False`

**可能原因**：
- nvidia-docker2 未安装
- 未使用 `--gpus all` 标志
- NVIDIA 驱动版本不兼容

**解决方法**：
```bash
# 检查宿主机 GPU
nvidia-smi

# 检查 nvidia-docker2
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# 重新安装 nvidia-docker2
sudo apt-get purge nvidia-docker2
sudo apt-get install nvidia-docker2
sudo systemctl restart docker
```

### 问题 3：权限错误

**症状**：容器内无法写入 workspace

**可能原因**：
- Workspace 目录权限不正确
- SELinux 阻止挂载

**解决方法**：
```bash
# 检查权限
ls -la workspace/

# 修改权限
chmod -R 755 workspace/

# 如果使用 SELinux，添加 :z 标志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace:z \
  researchos/system:latest \
  run --workspace /workspace
```

### 问题 4：环境变量未生效

**症状**：API 调用失败，提示缺少 API key

**可能原因**：
- 环境变量未正确传递
- 环境变量名称错误

**解决方法**：
```bash
# 检查环境变量
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  researchos/system:latest \
  bash -c "echo \$OPENAI_API_KEY"

# 使用 --env-file
docker run --rm -it \
  --env-file .env \
  researchos/system:latest \
  run --workspace /workspace
```

### 问题 5：容器内网络不通

**症状**：无法访问外部 API

**可能原因**：
- Docker 网络配置问题
- 防火墙阻止

**解决方法**：
```bash
# 测试网络
docker run --rm -it \
  researchos/system:latest \
  bash -c "curl -I https://www.google.com"

# 检查 Docker 网络
docker network ls
docker network inspect bridge

# 重启 Docker
sudo systemctl restart docker
```

### 问题 6：镜像体积过大

**症状**：镜像超过 15GB

**可能原因**：
- 未清理 apt 缓存
- 未清理 pip 缓存

**解决方法**：
```bash
# 查看镜像大小
docker images researchos/system

# 查看镜像层
docker history researchos/system:latest

# 优化 Dockerfile（已在当前版本中实现）
# - 使用 --no-cache-dir
# - 清理 apt lists
# - 合并 RUN 命令
```

### 问题 7：容器内看不到日志

**症状**：容器内 `/workspace/_runtime/logs/` 目录为空或日志文件不存在

**可能原因**：
- Workspace 未正确挂载
- 日志目录未初始化
- 首次运行尚未创建日志文件

**解决方法**：
```bash
# 检查 workspace 挂载
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest \
  -c "ls -la /workspace/_runtime/"

# 初始化 workspace
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  init-workspace --workspace /workspace

# 运行任意命令创建日志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  status --workspace /workspace
```

### 问题 8：容器退出后日志丢失

**症状**：容器停止后无法查看日志文件

**可能原因**：
- Workspace 未挂载到宿主机
- 使用了 `--rm` 标志但未挂载 workspace

**解决方法**：
```bash
# 确保挂载 workspace（推荐）
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 或者不使用 --rm，容器退出后仍可查看
docker run -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 事后查看容器标准输出
docker logs <container-id>

# 从已停止的容器复制日志
docker cp <container-id>:/workspace/_runtime/logs/researchos.log ./
```

### 问题 9：日志级别过高，缺少调试信息

**症状**：日志中缺少详细的执行信息

**可能原因**：
- 默认日志级别为 INFO
- 需要更详细的调试信息

**解决方法**：
```bash
# 使用 DEBUG 级别重新运行
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  run --workspace /workspace --log-level DEBUG

# 查看详细日志
tail -f workspace/_runtime/logs/researchos.log
```

### 问题 10：docker_exec 工具行为异常

**症状**：容器内运行时 docker_exec 工具报错或行为不符合预期

**可能原因**：
- 容器内模式下，docker_exec 直接执行命令而不是启动新容器
- 镜像白名单检查在容器内被跳过

**解决方法**：
```bash
# 检查是否在容器内运行
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest \
  -c "test -f /.dockerenv && echo 'In container' || echo 'Not in container'"

# 查看 docker_exec 日志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run-task --workspace /workspace --task hello --mock --log-level DEBUG | grep docker_exec

# 容器内模式是预期行为，docker_exec 会自动适配
```

---

## 最佳实践

### 1. 使用版本标签

```bash
# 不推荐：使用 latest
docker build -t researchos/system:latest .

# 推荐：使用语义化版本
docker build -t researchos/system:v0.1.0 .
```

### 2. 定期清理

```bash
# 清理未使用的镜像
docker image prune -a

# 清理未使用的容器
docker container prune

# 清理所有未使用资源
docker system prune -a
```

### 3. 使用 .dockerignore

确保 `.dockerignore` 文件正确配置，避免将不必要的文件打包到镜像中。

### 4. 分离数据和代码

- 代码：打包到镜像中
- 数据：通过 `-v` 挂载
- 配置：通过 `-e` 或 `--env-file` 传递

### 5. 使用便捷脚本

优先使用 `infra/docker/build.sh` 和 `infra/docker/run.sh`，而不是手动输入 `docker` 命令。

---

## 与宿主机模式对比

| 特性 | Docker 模式 | 宿主机模式 |
|------|------------|-----------|
| 环境配置 | 零配置（只需 Docker） | 需要配置 conda 环境 |
| 复现性 | 完全一致 | 依赖宿主机环境 |
| 隔离性 | 强隔离 | 弱隔离 |
| 性能 | 轻微开销（< 5%） | 原生性能 |
| GPU 支持 | 需要 nvidia-docker2 | 直接使用 |
| 调试便利性 | 需要进入容器 | 直接调试 |
| 适用场景 | 生产部署、论文复现 | 开发调试 |

---

## 下一步

- 阅读 [日志系统文档](logging.md) - 了解如何查看和分析日志
- 阅读 [ResearchOS Runtime 开发文档](../reference_materials/ResearchOS_Runtime_Dev_Spec.md)
- 查看 [Docker 迁移设计文档](../reference_materials/ResearchOS_Docker_Migration_Design.md)
- 运行测试：`docker run --rm researchos/system:latest bash -c "cd /app && pytest tests/"`

---

## 反馈与支持

如有问题，请提交 Issue 到 GitHub 仓库：
https://github.com/MengkunLiang/DIG-ResearchOS/issues
