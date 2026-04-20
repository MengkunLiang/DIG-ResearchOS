# ResearchOS 快速入门指南

> **版本**: v1.0
> **更新日期**: 2026-04-20

---

## 一、环境准备

### 1.1 系统要求

- **Python**: 3.10+
- **Conda**: 推荐使用 conda 管理环境
- **API Key**: OpenAI API key（用于 LLM 调用）

### 1.2 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/MengkunLiang/DIG-ResearchOS.git
cd DIG-ResearchOS

# 2. 创建并激活 conda 环境
conda create -n researchos python=3.10 -y
conda activate researchos

# 3. 安装依赖
pip install -e .
# 或使用 conda 安装基础包
conda install pyyaml pydantic

# 4. 设置 API Key
export OPENAI_API_KEY=sk-your-key-here
```

---

## 二、快速开始

### 2.1 初始化 Workspace

```bash
# 创建新的研究项目 workspace
python -m researchos.cli init-workspace /path/to/project

# 查看帮助
python -m researchos.cli --help
```

### 2.2 运行 Workflow

#### 方式 A: 运行完整 pipeline

```bash
python -m researchos.cli --workspace /path/to/project run
```

#### 方式 B: 运行单个任务

```bash
# 运行 HELLO 测试
python -m researchos.cli --workspace /path/to/project run-task HELLO

# 运行 T1 项目初始化
python -m researchos.cli --workspace /path/to/project run-task T1

# 查看可用任务
python -m researchos.cli --workspace /path/to/project status
```

### 2.3 使用 Skills

```bash
# 编译 LaTeX 论文
python -m researchos.cli run-skill paper-compile /path/to/paper

# 论文写作
python -m researchos.cli run-skill paper-write "my research topic"
```

---

## 三、项目结构

```
project/
├── project.yaml              # 项目配置
├── user_seeds/               # 用户提供的种子数据
│   ├── seed_papers.jsonl     # 种子论文列表
│   ├── seed_ideas.md         # 初始想法
│   └── seed_constraints.md   # 研究约束
├── literature/               # 文献资料（T2-T3）
│   ├── papers_raw.jsonl
│   ├── papers_dedup.jsonl
│   ├── paper_notes/
│   └── synthesis.md
├── ideation/                 # 假设生成（T4-T4.5）
│   ├── hypotheses.md
│   ├── exp_plan.yaml
│   └── novelty_audit.md
├── pilot/                    # 试点实验（T5）
├── experiments/              # 完整实验（T7）
│   └── results_summary.json
├── drafts/                   # 论文写作（T8）
│   ├── outline.md
│   └── paper.tex
└── submission/              # 投稿准备（T9）
```

---

## 四、配置说明

### 4.1 主要配置文件

| 文件 | 说明 |
|------|------|
| `config/state_machine.yaml` | 定义工作流状态机 |
| `config/model_routing.yaml` | LLM 模型路由配置 |
| `config/gates.yaml` | 质量门控配置 |
| `config/runtime.yaml` | 运行时配置 |

### 4.2 模型配置

默认使用 `gpt-4o`，可在 `config/model_routing.yaml` 中修改：

```yaml
heavy:
  primary:
    model: "gpt-4o"
    api_key: ${OPENAI_API_KEY}
```

---

## 五、调试与排查

### 5.1 常见问题

**Q: CLI 命令找不到**
```bash
# 使用 python -m 方式运行
python -m researchos.cli --help

# 或添加到 PATH
export PATH=$PATH:/path/to/researchos
```

**Q: LLM 连接失败**
```bash
# 检查 API key
echo $OPENAI_API_KEY

# 运行自检
python -m researchos.cli selftest
```

**Q: Skills 加载失败**
```bash
# 检查 skills 目录
ls -la skills/

# 查看 skills 配置
python -c "from researchos.skills.loader import discover_skills; from pathlib import Path; print(discover_skills(Path('skills')))"
```

### 5.2 日志查看

```bash
# 查看运行时日志
cat logs/researchos.log

# 查看 trace
python -m researchos.cli --workspace /path/to/project trace <run_id>
```

### 5.3 验证配置

```bash
# 验证所有配置文件
python -m researchos.cli validate-config

# 验证 workspace
python -m researchos.cli --workspace /path/to/project validate
```

---

## 六、进阶用法

### 6.1 自定义 Agent

参考 `docs/AGENT_DEVELOPMENT_GUIDE.md` 创建新的 Agent。

### 6.2 添加 Skills

在 `skills/` 目录下创建新的 skill 目录和 `SKILL.md` 文件。

### 6.3 扩展 MCP 工具

参考 `docs/MCP_TOOLS.md` 连接外部工具和服务。

---

## 七、更多资源

- **GitHub**: https://github.com/MengkunLiang/DIG-ResearchOS
- **设计文档**: `/home/liangmengkun/reference_materials/ResearchOS_Runtime_Dev_Spec.md`
- **评测报告**: `docs/EVALUATION_REPORT.md`

---

## 八、联系与反馈

如有问题或建议，请通过 GitHub Issues 反馈。