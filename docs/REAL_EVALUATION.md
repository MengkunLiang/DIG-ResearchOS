# ResearchOS 真实评测报告

> **评测日期**: 2026-04-21
> **评测范围**: ResearchOS Runtime 全组件（第二轮）
> **评测模式**: 单元测试 + 真实 API 测试 + 配置验证

---

## 一、系统概览

### 1.1 组件状态

| 模块 | 状态 | 说明 |
|------|------|------|
| T1-T9 Agent | ✅ 全部实现 | 11 个 agent 类，全部测试通过 |
| 单元测试 | ✅ 232 个通过 | 覆盖率良好 |
| Skills 系统 | ✅ 已实现 | loader/agent/runner 完整 |
| CLI 界面 | ✅ 3D 风格 | 新版 DIG Lab ASCII art |
| Docker 配置 | ✅ 已统一 | 镜像名称一致 |
| 配置管理 | ✅ 已统一 | runtime.yaml 作为配置源 |

### 1.2 本轮修复内容

| 修复项 | 文件 | 状态 |
|--------|------|------|
| CLI 界面 3D 风格 | `researchos/runtime/cli_ui.py` | ✅ 已完成 |
| Docker 镜像名统一 | `researchos/tools/docker_exec.py` | ✅ 已完成 |
| 配置管理统一 | `config/runtime.yaml` | ✅ 已完成 |

---

## 二、CLI 界面优化

### 2.1 优化前

简单的 Unicode 块字符，视觉冲击力不足：

```
  ██████╗
  ╚════██╗
   █████╔╝
  ██╔══██╗
  ███████╗
  ╚══════╝
```

### 2.2 优化后

3D 风格 DIG Lab 标识，带边框和阴影效果：

```
 ╔══════════════════════════════════════════════════╗
 ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄         ║
 ║ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀█         ║
 ║ █  ████████████ █ █  ██████████ █ █ █         ║
 ║ █ █▀▀▀▀▀▀▀▀▀▀▀▀▀█ █▀▀▀▀▀▀▀▀▀▀▀▀▀▀█ █ █         ║
 ║ █ █        ██████ █        ████ █ █         ║
 ║ █ █   ███  ██████ █   ███  ████ █ █         ║
 ║ █ █   █ █  ██████ █   █ █  ████ █ █         ║
 ║ █ █   ███  ██████ █   ███  ████ █ █         ║
 ║ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ █▄▄▄▄▄▄▄▄▄▄▄▄▄▄█ █▄█         ║
 ║  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀  ▀▀▀         ║
 ╚══════════════════════════════════════════════════╝

  DIG Lab - ResearchOS  |  command=test
```

### 2.3 关键代码

```python
# researchos/runtime/cli_ui.py

_DIG_FRAMES = [
    # Frame 1-3: 逐步构建动画
    # Frame 4: 最终 3D 风格版本
    "\n".join([
        " ╔═══════════════════════════════════════╗",
        " ║  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄▄▄▄▄▄▄▄▄▄▄  ▄▄▄▄  ║",
        # ... 更多帧
    ])
]
```

---

## 三、Docker 配置统一

### 3.1 问题描述

修复前存在镜像名称不一致问题：

| 位置 | 镜像名称 |
|------|----------|
| `docker_exec.py` | `researchos/python:3.11-ml` |
| `runtime.yaml` | `researchos/system:latest` |
| `Dockerfile` | `researchos/system:latest` |

### 3.2 修复方案

1. 在 `docker_exec.py` 中添加配置读取函数
2. 优先从 `runtime.yaml` 读取配置
3. 统一使用 `researchos/system:latest` 作为主镜像

### 3.3 关键代码

```python
# researchos/tools/docker_exec.py

_DEFAULT_ALLOWED_IMAGES = [
    "researchos/system:latest",  # 与 config/runtime.yaml 保持一致
    "researchos/python:3.11-ml",  # 保留旧镜像以支持迁移
    "researchos/latex:texlive-2024",
]

def get_default_image() -> str:
    """从 runtime.yaml 读取默认镜像配置。"""
    # ... 从配置文件读取的逻辑
    return "researchos/system:latest"

def get_default_allowed_images() -> list[str]:
    """从 runtime.yaml 读取允许的镜像列表。"""
    # ... 从配置文件读取的逻辑
```

### 3.4 配置文件更新

```yaml
# config/runtime.yaml

docker:
  default_image: "researchos/system:latest"
  allowed_images:
    - "researchos/system:latest"  # 主镜像
    - "researchos/python:3.11-ml"  # 保留以支持迁移
    - "researchos/latex:texlive-2024"  # LaTeX 编译专用
  default_memory_limit: "16g"
  build_context: "infra/docker"
```

---

## 四、测试验证

### 4.1 单元测试结果

```
232 passed in 3.44s
```

所有单元测试通过，包括：
- T1-T9 Agent 测试
- State Machine 测试
- Skills 系统测试
- Docker 执行测试
- Writer/Reviewer/Submission Agent 测试

### 4.2 配置验证

```python
# CLI Banner 验证
from researchos.runtime.cli_ui import render_final_banner
banner = render_final_banner('test')
# ✅ 输出正确的 3D 风格 banner

# Docker 配置验证
from researchos.tools.docker_exec import get_default_image, get_default_allowed_images
get_default_image()  # ✅ 'researchos/system:latest'
get_default_allowed_images()  # ✅ ['researchos/system:latest', ...]
```

### 4.3 CLI 功能验证

```bash
$ python -m researchos.cli --help
# ✅ 正确显示帮助信息，启动 banner 正常
```

---

## 五、真实 API 测试结果

| Agent | Task | 状态 | 耗时 | API调用 | 成本 |
|-------|------|------|------|---------|------|
| hello | HELLO | ✅ | 3149ms | 1 | $0.0008 |
| pi | T1 | ✅ | 3346ms | 2 | $0.0116 |
| scout | T2 | ✅ | 2290ms | 1 | $0.0080 |
| reader | T3 | ✅ | 1501ms | 1 | $0.0042 |
| ideation | T4 | ✅ | 2601ms | 1 | $0.0132 |
| novelty_auditor | T4.5 | ✅ | 2464ms | 1 | $0.0100 |
| novelty | T6 | ✅ | 2630ms | 1 | $0.0109 |
| writer | T8-WRITE | ✅ | 5872ms | 1 | $0.0111 |
| reviewer | T8-REVIEW-1 | ✅ | 5224ms | 1 | $0.0099 |
| submission | T9 | ✅ | 2554ms | 1 | $0.0038 |

**总计**: 10/10 通过，总耗时 31.6s，总成本 $0.0835

---

## 六、其他测试结果

### 6.1 断点恢复机制测试

```
5/5 测试通过
```

| 测试项 | 状态 |
|--------|------|
| PAUSED 状态检测 | ✅ |
| WAITING_HUMAN 状态检测 | ✅ |
| Resume 场景 (interrupted) | ✅ |
| Resume 场景 (retry_after_failure) | ✅ |
| 状态持久化 | ✅ |

### 6.2 内容质量测试

```
4/4 测试通过
```

| 测试项 | 状态 |
|--------|------|
| 引用幻觉检测 | ✅ |
| 数字幻觉检测 | ✅ |
| 逻辑一致性检测 | ✅ |
| LaTeX 编译测试 | ✅ |

### 6.3 多 Agent 协作链测试

```
3/3 测试通过
```

| 阶段 | 状态 | 耗时 | 数据流 |
|------|------|------|--------|
| T3-Reader | ✅ | 24307ms | synthesis.md |
| T4-Ideation | ✅ | 26290ms | hypotheses.md + exp_plan.yaml |
| T5-Experimenter-Pilot | ✅ | 2231ms | pilot_results |

---

## 七、已知限制

1. **T1 需要用户交互**: PIAgent 在 init 模式需要用户输入研究方向
2. **Docker 执行需要 docker CLI**: 无 docker 环境时自动 fallback 到 host 模式
3. **Skills 依赖外部工具**: paper-compile 需要 latexmk，deepxiv 需要 deepxiv-sdk

---

## 八、验收标准达成情况

- ✅ 所有配置可解析
- ✅ CLI 启动无报错
- ✅ 232 个单元测试全部通过
- ✅ Skills 系统可正常运行
- ✅ 3D 风格 CLI 界面完成
- ✅ Docker 镜像名称统一
- ✅ 配置管理统一
- ✅ 断点恢复机制验证 (5/5 测试通过)
- ✅ 内容质量测试 (4/4 测试通过)

---

## 九、后续建议

1. **真实项目测试**: 在实际研究项目上运行完整 pipeline
2. **MCP 工具完善**: 扩展 MCP 连接器支持更多外部服务
3. **性能优化**: 考虑添加缓存层减少 API 调用
4. **文档完善**: 补充用户快速入门文档
