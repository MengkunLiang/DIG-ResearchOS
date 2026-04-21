# ResearchOS Docker 使用指南

> **文档版本**: v1.1
> **更新日期**: 2026-04-21

---

## 一、镜像概述

ResearchOS 使用统一的 Docker 镜像 `researchos/system:latest`，包含：

| 组件 | 版本/说明 |
|------|-----------|
| Python | 3.11 |
| CUDA | 12.4 (Ubuntu 22.04) |
| PyTorch | 2.6.0 |
| Transformers | 4.45.0 |
| LaTeX | texlive-full |
| MCP 服务器 | arxiv, filesystem, github |

**优势**：一个镜像跑完整系统，用户无需配置多个镜像。

---

## 二、快速开始

### 2.1 构建镜像

```bash
cd /home/liangmengkun/ResearchOS

# 使用构建脚本
bash infra/docker/build.sh

# 如需通过代理构建
HTTP_PROXY=http://proxy.example.com:8080 HTTPS_PROXY=http://proxy.example.com:8080 bash infra/docker/build.sh
```

### 2.2 运行系统

```bash
# 基本运行
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  --help

# 带 GPU 支持
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  --gpus all \
  researchos/system:latest \
  run --workspace /workspace
```

### 2.3 使用 Docker Compose

```bash
# 复制环境变量模板
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY

# 启动
docker-compose up

# 后台运行
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

---

## 三、配置说明

### 3.1 镜像配置（config/runtime.yaml）

```yaml
docker:
  default_image: "researchos/system:latest"
  allowed_images:
    - "researchos/system:latest"  # 统一镜像
  default_memory_limit: "16g"
```

### 3.2 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `OPENAI_API_KEY` | OpenAI API 密钥 | 是 |
| `OPENAI_BASE_URL` | API 端点（可选） | 否 |
| `RESEARCHOS_NO_BANNER` | 禁用启动动画 | 否 |

---

## 四、容器内执行模式

### 4.1 架构说明

ResearchOS 支持两种执行模式：

1. **宿主机模式**：在宿主机上通过 `docker run` 启动容器执行命令
2. **容器内模式**：当在容器内运行时，直接使用 subprocess 执行（避免 Docker-in-Docker）

### 4.2 环境检测

容器内模式通过以下方式检测：
- `/.dockerenv` 文件存在
- `/run/.containerenv` 文件存在
- `CONTAINER_ID` 环境变量

### 4.3 统一执行接口

无论哪种模式，用户调用 `docker_exec` 工具的接口保持一致：

```python
result = await tool.execute(
    image="researchos/system:latest",  # 镜像（容器内模式会忽略）
    command="python train.py",
    cwd="/workspace",
    timeout_seconds=600,
    gpu=True,
)
```

---

## 五、MCP 服务器

ResearchOS 预装了以下 MCP 服务器：

| 服务器 | 功能 | API Key |
|--------|------|---------|
| arxiv | arXiv 论文搜索和下载 | 不需要 |
| filesystem | 本地文件系统访问 | 不需要 |
| github | GitHub 仓库和代码搜索 | GITHUB_TOKEN |

配置位于 `config/mcp.yaml`。如需添加 Semantic Scholar（学术论文搜索更强），请参考配置文件中的说明。

---

## 六、常见问题

### 5.1 GPU 不可用

**问题**：`--gpus all` 报错

**解决方案**：
1. 安装 [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
2. 配置 Docker runtime：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### 5.2 镜像拉取失败

```bash
# 手动拉取
docker pull researchos/system:latest

# 重新构建
docker build -t researchos/system:latest .
```

### 5.3 工作空间挂载

```bash
# 正确挂载
docker run -v $(pwd)/workspace:/workspace ...

# 权限问题：确保 workspace 目录存在且可写
mkdir -p workspace
chmod 777 workspace
```

---

## 七、自定义镜像

如需在基础镜像上添加额外依赖：

```dockerfile
FROM researchos/system:latest

# 添加自定义依赖
RUN pip install --no-cache-dir your-package

# 或安装系统包
RUN apt-get update && apt-get install -y your-package && rm -rf /var/lib/apt/lists/*
```

---

## 八、性能优化建议

1. **使用 BuildKit**：
   ```bash
   export DOCKER_BUILDKIT=1
   docker build ...
   ```

2. **预拉取镜像**：
   ```bash
   docker pull researchos/system:latest
   ```

3. **卷挂载**：
   - 工作空间使用卷挂载提高 I/O 性能
   - 避免在容器内进行大量小文件操作

---

## 九、联系与支持

- **项目主页**: https://github.com/MengkunLiang/DIG-ResearchOS
- **问题反馈**: GitHub Issues