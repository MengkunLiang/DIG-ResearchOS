# `evidence-packaging` 文件清单

## 目录

```text
evidence-packaging/
├── SKILL.md
├── MANIFEST.md
├── references/
│   ├── snapshot-and-staleness-policy.md
│   ├── evidence-level-policy.md
│   ├── realized-method-contract.md
│   ├── framework-figure-contract.md
│   ├── figure-table-inventory-contract.md
│   ├── visual-traceability-policy.md
│   ├── evidence-mapping-contract.md
│   ├── packaging-review-checklist.md
│   └── output-contract.md
├── scripts/
│   ├── _common.py
│   ├── preflight_evidence_packaging.py
│   ├── build_evidence_snapshot.py
│   ├── validate_evidence_snapshot.py
│   ├── build_realized_method_package.py
│   ├── build_framework_figure_spec.py
│   ├── render_framework_figure.py
│   ├── build_figure_table_inventory.py
│   ├── build_evidence_mapping.py
│   ├── build_package_manifest.py
│   ├── compute_packaging_gate.py
│   ├── assemble_evidence_packaging_report.py
│   ├── validate_evidence_packaging_report.py
│   └── apply_evidence_packaging_report.py
└── tests/
    └── test_evidence_packaging_scripts.py
```

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `SKILL.md` | Phase F1–F3 的主工作流、边界、命令、Gate 与返回契约。 |
| `snapshot-and-staleness-policy.md` | 定义最终证据快照、active/stale 选择、源变化和重打包规则。 |
| `evidence-level-policy.md` | 分离方法定义、实现事实、经验支持、受控机制支持、诊断提示和 pre-T7 claim candidate。 |
| `realized-method-contract.md` | 规定最终方法、模块、代码/config 映射、归因和 intent delta。 |
| `framework-figure-contract.md` | 规定框架图 panels/nodes/edges/caption、显示约束与状态。 |
| `figure-table-inventory-contract.md` | 规定 result figure/table inventory、evidence layer、missing/stale 行为。 |
| `visual-traceability-policy.md` | 规定 source result/data → metric → plot script → render 的数字血缘。 |
| `evidence-mapping-contract.md` | 规定 method/code/config/evidence/visual/claim candidate 双向映射。 |
| `packaging-review-checklist.md` | 生成后独立审查清单。 |
| `output-contract.md` | 定义 report、result-pack ownership 和 child return。 |
| `_common.py` | 标准库 JSON、路径、hash、Artifact reference 与遍历工具。 |
| `preflight_evidence_packaging.py` | 检查 Phase F dispatch、控制文件、Schema 和写路径。 |
| `build_evidence_snapshot.py` | 固定同一最终证据快照，记录 active 与 stale/failed 历史。 |
| `validate_evidence_snapshot.py` | 检查 snapshot 后源 section 是否变化、checksum 是否有效、active protocol 是否一致。 |
| `build_realized_method_package.py` | 从实际实现、配置、归因与边界生成 realized method。 |
| `build_framework_figure_spec.py` | 由 realized method 生成 evidence-bound 框架图规格。 |
| `render_framework_figure.py` | 无外部依赖生成 Mermaid editable source 与 SVG；不完整规格拒绝渲染。 |
| `build_figure_table_inventory.py` | 识别真实视觉 Artifact，并记录数值和生成路径。 |
| `build_evidence_mapping.py` | 生成模块、视觉和 claim candidate 的双向映射。 |
| `build_package_manifest.py` | 生成轻量 Research Object 式 entities/relations/checksums manifest。 |
| `compute_packaging_gate.py` | 确定性计算 `ready/partial/blocked`。 |
| `assemble_evidence_packaging_report.py` | 将 F1–F3 组件组装为 durable child report。 |
| `validate_evidence_packaging_report.py` | 检查单快照、方法/图表 provenance、claim 审批越界和 Gate 一致性。 |
| `apply_evidence_packaging_report.py` | 原子更新五个本 Skill 拥有的 `result_pack` sections。 |
| `test_evidence_packaging_scripts.py` | 端到端、snapshot mutation、stale exclusion、cross-snapshot 阻塞测试。 |

## 外部依赖

运行脚本仅依赖 Python 标准库。框架图默认输出 Mermaid 文本和 SVG，不要求 Graphviz、Mermaid CLI、Matplotlib 或 LaTeX。项目可以替换渲染器，但不能破坏同一 snapshot、node/edge evidence mapping 和 `must_not_show` 约束。
