from __future__ import annotations

"""workspace 初始化与说明辅助。

Runtime Spec 明确要求 workspace 是 artifact-first 的唯一事实来源。
因此除了 `_runtime/` 本身，这里还把后续 T1-T9 常用目录的“标准树”固定下来，
便于：
- CLI 从 0 初始化一个可调试 workspace；
- README 给出稳定目录结构；
- 后续 agent 开发在同一套路径约定上协作，而不是每个人各建一套目录。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


STANDARD_WORKSPACE_DIRS = [
    "user_seeds",
    "user_seeds/pdfs",
    "literature",
    "literature/pdfs",
    "literature/paper_notes",
    "ideation",
    "pilot",
    "pilot/pilot_code",
    "novelty",
    "experiments",
    "experiments/runs",
    "experiments/configs",
    "evaluation",
    "drafts",
    "reviews",
    "reviews/review_rounds",
    "submission",
    "submission/bundle",
    "skills",
]


def build_standard_workspace_dirs(runtime_dir_name: str = "_runtime") -> list[str]:
    """返回标准 workspace 目录列表。

    `runtime_dir_name` 默认仍是 `_runtime`，这样对已有 workspace 和测试保持兼容；
    但如果团队想统一改成 `.runtime` 一类名字，也只需要改 `config/runtime.yaml`。
    """

    return [
        f"{runtime_dir_name}/traces",
        f"{runtime_dir_name}/logs",
        *STANDARD_WORKSPACE_DIRS,
    ]


@dataclass
class WorkspaceInitResult:
    """初始化 workspace 后返回的摘要。"""

    workspace_dir: Path
    created_dirs: list[str]
    project_file: Path | None


def initialize_workspace(
    workspace_dir: Path,
    *,
    create_project_file: bool = True,
    project_id: str | None = None,
    topic: str | None = None,
    force_project_file: bool = False,
    runtime_dir_name: str = "_runtime",
) -> WorkspaceInitResult:
    """创建标准 workspace 树。

    约定：
    - 永远不会删除已有文件；
    - `project.yaml` 仅在不存在或显式 `force_project_file=True` 时写入；
    - 目录初始化是幂等操作，适合在 CLI / 测试 / 脚本里反复调用。
    """

    workspace_dir = workspace_dir.resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    created_dirs: list[str] = []

    for rel_dir in build_standard_workspace_dirs(runtime_dir_name):
        candidate = workspace_dir / rel_dir
        if not candidate.exists():
            created_dirs.append(rel_dir)
        candidate.mkdir(parents=True, exist_ok=True)

    project_file: Path | None = None
    if create_project_file:
        project_file = write_project_stub(
            workspace_dir,
            project_id=project_id or "demo-project",
            topic=topic or "",
            force=force_project_file,
        )

    # 创建 user_seeds 示例文件
    create_user_seeds_examples(workspace_dir)

    return WorkspaceInitResult(
        workspace_dir=workspace_dir,
        created_dirs=created_dirs,
        project_file=project_file,
    )


def write_project_stub(
    workspace_dir: Path,
    *,
    project_id: str,
    topic: str,
    force: bool = False,
) -> Path:
    """写入最小 `project.yaml` 模板。"""

    project_path = workspace_dir / "project.yaml"
    if project_path.exists() and not force:
        return project_path

    payload: dict[str, Any] = {
        "project_id": project_id,
        "topic": topic,
        "created_at": _now_iso(),
        "status": "draft",
        "notes": (
            "该文件是由 runtime 初始化生成的最小模板。"
            "后续 T1/T7.5 等 agent 落地后，可在此基础上补业务字段。"
        ),
    }
    project_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return project_path


def create_user_seeds_examples(workspace_dir: Path) -> None:
    """在 user_seeds 目录下创建示例文件和空模板，指导用户如何放置种子数据。

    策略：
    1. 创建 .example 示例文件（仅作参考）
    2. 如果实际 seed 文件不存在，创建空模板（避免 Agent 读取时报错）
    """

    user_seeds_dir = workspace_dir / "user_seeds"

    # 1. README.md - 使用说明
    readme_path = user_seeds_dir / "README.md"
    if not readme_path.exists():
        readme_content = """# User Seeds 目录说明

这个目录用于存放项目的种子数据，T1 Agent 会在初始化时收集这些信息。

## 目录结构

```
user_seeds/
├── README.md                        # 本说明文件
├── seed_papers.jsonl.example        # 种子论文示例
├── seed_ideas.md.example            # 初步想法示例
├── seed_constraints.md.example      # 硬约束清单示例
├── seed_external_resources.jsonl.example  # 外部资源示例
└── pdfs/                            # 存放 PDF 文件
```

## 使用方式

### 1. 提供种子论文（推荐方式）

**🎯 推荐方式：直接放入 PDF 文件（自动识别）**
- **将 PDF 文件放入 `pdfs/` 目录**
- **T1 Agent 会自动扫描并识别所有 PDF 文件**
- **无需手动提供路径或编辑配置文件**
- 支持批量：一次性放入多个 PDF，T1 会逐个处理

**其他方式（在 T1 对话中提供）：**

**方式 2：提供 arXiv ID**
- 在 T1 对话中直接提供 arXiv ID：`2601.03192`
- 或 arXiv DOI：`10.48550/arXiv.2601.03192`

**方式 3：提供 DOI**
- 在 T1 对话中提供 DOI：`10.1145/3534678.3539147`

**方式 4：手动编辑 seed_papers.jsonl**
- 复制 `seed_papers.jsonl.example` 为 `seed_papers.jsonl`
- 按照示例格式填写论文信息

### 2. 提供初步想法（可选）

- 复制 `seed_ideas.md.example` 为 `seed_ideas.md`
- 填写你的研究想法和假设
- **用途**：T4 Ideation Agent 会将其作为候选研究方向之一

### 3. 提供硬约束（可选）

- 复制 `seed_constraints.md.example` 为 `seed_constraints.md`
- 填写必须遵守的技术或方法约束
- **用途**：T2 Scout Agent 会在文献检索时考虑这些约束

### 4. 提供外部资源（可选）

- 复制 `seed_external_resources.jsonl.example` 为 `seed_external_resources.jsonl`
- 填写已有的数据集、代码仓库、预训练模型等资源
- **用途**：T5 Experimenter Agent 等后续阶段会使用这些资源

## 注意事项

1. `.example` 文件仅作为示例，不会被 T1 Agent 读取
2. 实际使用时，去掉 `.example` 后缀
3. **推荐做法**：将 PDF 放入 `pdfs/` 目录，其他信息在 T1 对话中提供
4. 也可以手动创建这些文件，T1 Agent 会读取并使用

## 各文件的使用阶段

| 文件 | 使用阶段 | 用途 |
|------|---------|------|
| `seed_papers.jsonl` | T1 生成，T2 使用 | 种子论文列表 |
| `seed_ideas.md` | T4 Ideation Agent | 作为候选研究方向 |
| `seed_constraints.md` | T2 Scout Agent | 文献检索约束 |
| `seed_external_resources.jsonl` | T5+ | 外部资源清单 |
| `pdfs/` | T1 自动扫描 | 存放 PDF 文件 |
"""
        readme_path.write_text(readme_content, encoding="utf-8")

    # 2. seed_papers.jsonl.example
    papers_example_path = user_seeds_dir / "seed_papers.jsonl.example"
    if not papers_example_path.exists():
        papers_example = """{"title": "Attention Is All You Need", "authors": ["Vaswani, Ashish", "Shazeer, Noam"], "year": 2017, "role": "anchor", "why_relevant": "Transformer 架构的开创性论文，是我们研究的核心参考"}
{"title": "BERT: Pre-training of Deep Bidirectional Transformers", "authors": ["Devlin, Jacob", "Chang, Ming-Wei"], "year": 2019, "role": "reference", "why_relevant": "预训练语言模型的重要参考"}
"""
        papers_example_path.write_text(papers_example, encoding="utf-8")

    # 3. seed_ideas.md.example
    ideas_example_path = user_seeds_dir / "seed_ideas.md.example"
    if not ideas_example_path.exists():
        ideas_example = """# 初步研究想法

## 核心假设

我们假设通过改进注意力机制的计算方式，可以在保持模型性能的同时显著降低计算复杂度。

## 初步方案

1. **稀疏注意力**：只计算最相关的 token 之间的注意力
2. **局部注意力**：限制注意力窗口大小
3. **分层注意力**：在不同层使用不同的注意力模式

## 预期效果

- 计算复杂度从 O(n²) 降低到 O(n log n)
- 在长文本任务上性能提升 20%
- 训练速度提升 2-3 倍

## 需要验证的问题

1. 稀疏注意力是否会损失重要的长距离依赖？
2. 如何自动学习最优的注意力模式？
3. 在不同任务上的泛化能力如何？
"""
        ideas_example_path.write_text(ideas_example, encoding="utf-8")

    # 4. seed_constraints.md.example
    constraints_example_path = user_seeds_dir / "seed_constraints.md.example"
    if not constraints_example_path.exists():
        constraints_example = """# 硬约束清单

## 技术约束

1. **必须使用 PyTorch**：团队熟悉 PyTorch，不考虑其他框架
2. **必须兼容 Hugging Face Transformers**：便于复用预训练模型
3. **不使用外部 API**：所有计算必须在本地完成

## 方法约束

1. **不使用知识蒸馏**：我们关注架构改进，不依赖教师模型
2. **必须保持端到端训练**：不使用多阶段训练

## 资源约束

1. **GPU 限制**：最多使用 4 张 A100 GPU
2. **时间限制**：单次实验不超过 24 小时
3. **存储限制**：模型大小不超过 10GB

## 评估约束

1. **必须在 GLUE 基准上评估**：便于与现有工作比较
2. **必须报告推理速度**：不仅关注准确率，也关注效率
"""
        constraints_example_path.write_text(constraints_example, encoding="utf-8")

    # 5. seed_external_resources.jsonl.example
    resources_example_path = user_seeds_dir / "seed_external_resources.jsonl.example"
    if not resources_example_path.exists():
        resources_example = """{"type": "dataset", "name": "GLUE", "source": "huggingface:glue", "access": "auto", "purpose": "主要评估基准"}
{"type": "baseline_repo", "name": "Transformers", "source": "github:huggingface/transformers", "commit": "v4.30.0", "purpose": "baseline 实现和预训练模型"}
{"type": "pretrained_model", "name": "BERT-base", "source": "huggingface:bert-base-uncased", "purpose": "预训练编码器"}
{"type": "docker_image", "name": "pytorch-env", "source": "docker:pytorch/pytorch:2.0.0-cuda11.7-cudnn8-runtime", "purpose": "实验环境"}
{"type": "tool", "name": "wandb", "source": "pip:wandb", "purpose": "实验跟踪"}
"""
        resources_example_path.write_text(resources_example, encoding="utf-8")

    # 6. 创建空模板文件（如果实际文件不存在）
    # 这样 Agent 读取时不会因为文件不存在而报错
    _create_empty_seed_files_if_missing(user_seeds_dir)


def _create_empty_seed_files_if_missing(user_seeds_dir: Path) -> None:
    """如果 seed 文件不存在，创建空模板。

    这样 Agent 读取时不会因为文件不存在而报错，
    同时也不会覆盖用户已经创建的文件。
    """

    # seed_papers.jsonl - 空文件（JSONL 格式，每行一个 JSON 对象）
    papers_path = user_seeds_dir / "seed_papers.jsonl"
    if not papers_path.exists():
        papers_path.write_text("", encoding="utf-8")

    # seed_ideas.md - 空文件
    ideas_path = user_seeds_dir / "seed_ideas.md"
    if not ideas_path.exists():
        ideas_path.write_text("# 初步研究想法\n\n（暂无）\n", encoding="utf-8")

    # seed_constraints.md - 空文件
    constraints_path = user_seeds_dir / "seed_constraints.md"
    if not constraints_path.exists():
        constraints_path.write_text("# 硬约束清单\n\n（暂无）\n", encoding="utf-8")

    # seed_external_resources.jsonl - 空文件（可选，不强制创建）
    # 这个文件是可选的，所以不创建空模板


def render_workspace_tree(runtime_dir_name: str = "_runtime") -> str:
    """返回 README / CLI 可复用的标准 workspace 树说明。"""

    return "\n".join(
        [
            "workspace/",
            "|-- project.yaml",
            "|-- state.yaml",
            "|-- user_seeds/",
            "|-- literature/",
            "|   |-- pdfs/",
            "|   `-- paper_notes/",
            "|-- ideation/",
            "|-- pilot/",
            "|   `-- pilot_code/",
            "|-- novelty/",
            "|-- experiments/",
            "|   |-- runs/",
            "|   `-- configs/",
            "|-- evaluation/",
            "|-- drafts/",
            "|-- reviews/",
            "|   `-- review_rounds/",
            "|-- submission/",
            "|   `-- bundle/",
            "|-- skills/",
            f"`-- {runtime_dir_name}/",
            "    |-- traces/",
            "    `-- logs/",
        ]
    )
