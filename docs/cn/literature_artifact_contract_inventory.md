# Literature Artifact Contract Inventory（2026-07-16）

本报告记录 `/mnt/data/reference/tmp/优化文献和t4交互.md` 中要求的文献路径审计、真实 Producer 判定、迁移策略和 T4 Gate 运行时发现。结论来自当前仓库代码扫描与本次回归测试，不以旧设计文档作为“最新路径”的来源。

## 1. Canonical Artifact Contract

当前运行时统一使用以下路径作为文献事实源。持久化路径均以 workspace root 为基准保存为 relative path。

| Artifact | Canonical path | 语义 | Producer | 主要 Consumer |
| --- | --- | --- | --- | --- |
| Literature Manifest | `literature/literature_manifest.json` | 统一文献清单；列出 canonical roots、note cards、aliases、sha256、evidence level、catalog summary 和 migration report | `researchos.runtime.literature_contract.build_literature_manifest`；在 import/resume、T4 pre-run、T4 Evidence Index 中确定性刷新 | T3.6 visuals resolver、T4 pre-run/evidence、T5/T8 task contract、read_file note alias resolver |
| Deep paper notes | `literature/deep_read_notes/` | 主线全文/部分全文论文阅读笔记；可作为机制、方法、强比较证据 | `save_paper_note` 对 core queue 写入；旧 `paper_notes` 由 workspace migration 合并 | T3.5 synthesis、T3.6 survey、T4/T4.5 evidence、T5 handoff、T8 manuscript/resource index |
| Shallow paper notes | `literature/shallow_read_notes/` | 摘要级/轻读笔记；只可支持范围、趋势、候选发现和弱证据提示 | T3 abstract sweep / Reader；旧 `paper_notes_abstract`、`abstract_notes` 迁移 | T3.6 taxonomy coverage、T4 evidence with ABSTRACT_ONLY permission、T8 related work context |
| Bridge paper notes | `literature/bridge_notes/` | 跨领域/理论桥接论文的真实全文/部分全文阅读笔记 | `save_paper_note` 对 `target_bucket=bridge_deep/bridge_probe` 且未进入 core 的记录写入 `bridge_notes/<bridge_id>/<note_id>.md` | 与 deep notes 同级强证据，但必须保留 bridge 来源边界；T3.5/T3.6/T4/T5/T8 消费 |
| Cross-domain catalogs | `literature/cross_domain_catalogs/` | B1/B2/... 跨领域检索、metadata、abstract leads、context；不是论文阅读笔记 root | `refresh_bridge_catalogs`、T2 recovery/finalization；旧 colocated JSON 由 `migrate_legacy_bridge_catalogs` 复制 | T3.5/T3.6/T4/T4.5/T8 用作结构类比、边界、候选发现、阅读升级线索；不能直接证明机制或 citation anchor |
| PDF acquisition receipt | `literature/pdf_acquisition_manifest.json` + `literature/pdf_acquisition_receipts.jsonl` | 对每篇保留且已核验候选的一次开放 PDF 获取、来源 URL、状态、checksum、字节数、可解析性和本地相对路径；这是可得性，不是阅读证据 | T2 deterministic finalization；旧 workspace 在任一文献消费者阶段 resume 时由 runtime preflight 补齐；T3.6 targeted supplement 同样写入 | Reader 用 `local_pdf_path`/receipt 直接开始实际阅读；T3.5/T3.6/T4/T5/T8 可在 manifest 中追踪可得性，但不得将 receipt 当作 citation 或 FULL_TEXT |
| Synthesis | `literature/synthesis.md` | T3.5 综合报告 | Reader/T3.5 synthesis agent and tools | T3.6 survey、T4 idea fuel、T8 writing |
| Synthesis workbench | `literature/synthesis_workbench.json` | `notes`、`shallow_read_notes`、`all_note_cards`、method families、tensions、adjacent transfers、bridge transfer drafts | `build_synthesis_workbench` | T3.6 section writing、T4 context pack、T8 resource index/related work |
| Domain map | `literature/domain_map.json` | citation/domain structure and theory bridge hints；不是 novelty gate | T2 `build_domain_map` / recovery finalization | T3.5 synthesis、T3.6 taxonomy/survey、T4 opportunity map、T8 related work |
| Related work BibTeX | `literature/related_work.bib` | 真实 citation key 库 | T3 Reader/citation refresh | T3.6 assemble/audit、T8 writing/review/submission |

## 2. Legacy aliases and migration

以下历史路径不再作为 live source 使用。迁移是非破坏性的，旧 workspace 可兼容恢复。

| Legacy path/name | Canonical target | 当前行为 |
| --- | --- | --- |
| `literature/paper_notes` | `literature/deep_read_notes` | `migrate_workspace_note_directories` 非破坏复制/映射；legacy 原文件保留；同名同内容去重映射；冲突复制到 `literature/note_migration_conflicts/` |
| `literature/paper_notes_abstract` | `literature/shallow_read_notes` | 同上 |
| `literature/abstract_notes` | `literature/shallow_read_notes` | 同上 |
| `literature/reading_notes` | `literature/deep_read_notes` | 同上 |
| `literature/paper_notes_bridge` | `literature/bridge_notes` | 同上 |
| `paper_notes_dir` | `deep_read_notes_dir` | workspace artifact text migration 会更新 JSON/YAML/MD/TXT/CSV/TEX 中的 active references |
| `paper_notes_abstract_dir` | `shallow_read_notes_dir` | 同上 |
| `paper_notes_bridge_dir` | `bridge_notes_dir` | 同上 |
| old catalog JSON under `literature/bridge_notes/<B#>/paper_catalog.json` | `literature/cross_domain_catalogs/<B#>/paper_catalog.json` | 复制，不删除；canonical catalog 优先，legacy 只作为 fallback |
| typo `cross_domain_catelog(s)` | 不支持为 canonical | 当前全仓库运行路径未发现 live typo；若旧 workspace 中出现，应按 migration report 作为无法识别项处理 |

本次新增 `researchos.runtime.literature_contract` 作为集中入口：

- `build_literature_manifest(workspace, write=True)`
- `migrate_legacy_literature_paths(workspace)`
- `iter_literature_note_cards(workspace)`
- `resolve_literature_note_card_path(workspace, requested_path)`
- `validate_literature_corpus(workspace)`

## 3. Actual Inventory

扫描命令覆盖 Python、Jinja prompt、YAML、JSON schema、Markdown skills/docs、shell/scripts 和 tests，排除了 `workspace/**`、`tmp/**`、`latex_templete/**`：

```bash
rg -n --glob '!workspace/**' --glob '!tmp/**' --glob '!latex_templete/**' --glob '!*.pyc' \
  'paper_note|paper_notes|paper_notes_abstract|abstract_notes|bridge_note|bridge_notes|paper_notes_bridge|cross_domain_catalog|cross_domain_catalogs|cross_domain_catelog|cross_domain_catelogs|bridge_transfer|bridge_transfer_drafts|literature/|synthesis_workbench|domain_map|all_note_cards|read_file|list_files|glob|Path\(' \
  researchos config docs skills tests README.md
```

高频位置摘要：

| 位置/类别 | 当前引用路径 | Producer/Consumer | 是否仍有效 | 修复方式 |
| --- | --- | --- | --- | --- |
| `researchos/runtime/literature_contract.py` | canonical roots + legacy aliases | Resolver / Manifest / Migration | 有效 | 新增统一入口；供 Survey/File/T4/import/resume 使用 |
| `researchos/runtime/workspace.py` | `paper_notes*`、`abstract_notes` -> canonical | Migration | 有效（仅兼容） | 复制/映射到 canonical，legacy 原目录不删除；生成 migration report |
| `researchos/runtime/bridge_catalog.py` | `bridge_notes` legacy catalog fallback、`cross_domain_catalogs` canonical catalog | Migration / Catalog loader | 有效 | 保留语义分离：paper notes 不移动，catalog JSON 复制 |
| `researchos/tools/save_paper_note.py` | `deep_read_notes`、`bridge_notes/<bridge_id>` | Producer | 有效 | Bridge note 仍是真实阅读笔记，不改名为 catalog |
| `researchos/tools/literature_synthesis.py` | `synthesis_workbench.json`、`all_note_cards`、`bridge_transfer_drafts` | Producer / synthesis workbench | 有效 | 保留为综合索引/idea fuel，不当作单篇 paper note |
| `researchos/runtime/t3_notes_manifest.py` | `notes_manifest.json`、cross-domain catalog refresh | Producer / indexer | 有效 | 与新 `literature_manifest.json` 并存：前者服务 T3 队列进度，后者服务跨任务文献事实源 |
| `researchos/tools/survey_tools.py` | `deep_read_notes`、`shallow_read_notes`、`bridge_notes` | T3.6 consumer | 有效 | `_audit_taxonomy_paper_links` 改为使用统一 note lookup；零证据不得 generated |
| `researchos/tools/filesystem.py` | guessed note-card paths | Tool consumer | 有效 | `read_file` 使用统一 resolver canonicalize 误猜 DOI/citation-key note path；目录仍返回 `is_directory` |
| `researchos/ideation/prerun.py` | note roots + catalogs | T4 pre-run consumer | 有效 | pre-run 刷新 `literature_manifest.json` 并统计真实 notes/catalogs |
| `researchos/ideation/evidence.py` | note roots + catalogs | T4 Evidence Index consumer | 有效 | Evidence Index 改用统一 manifest note enumeration；catalog-only 仍只产生 abstract/metadata permission atoms |
| `researchos/orchestration/task_io_contract.py` | T3.5/T3.6/T4/T4.5/T5/T8 inputs | Contract | 有效 | downstream import closure includes full `literature/`;关键任务显式携带 `literature_manifest` |
| `researchos/cli.py`、`single_task.py` | import/resume copy | Runtime import consumer | 有效 | copy 后运行 migration + build manifest；初始化空目录不会压住来源真实笔记 |
| `researchos/prompts/reader.j2`、`survey_writer.j2`、`novelty_auditor.j2` | read/list prompt policy | Prompt consumer | 有效 | Prompt 明确 `read_file` 不接收目录；catalog 与 bridge notes 分离 |
| `researchos/tools/manuscript.py` | note roots、catalog、related_work.bib | T8 consumer | 有效 | 仍保留 T8 resource/claim/citation使用；task contract 加 manifest input |
| `skills/*/SKILL.md` | `literature/`、`read_file`、`list_files` | Skill consumer | 有效 | Skills 保持 workspace navigation；不把旧路径作为新 producer |
| `docs/en/cn/*` | historical notes/cross-domain explanations | Documentation | 有效（说明性） | 文档中旧路径只作 migration/history；不得作为 producer 判定来源 |
| tests | legacy paths、bridge/catalog fixtures | Test fixtures | 有效 | 新增 Literature Contract tests 覆盖旧 workspace、重复、非文本、路径规范化、T5/T8 import |

未发现 live canonical producer 写入 typo `cross_domain_catelog` / `cross_domain_catelogs`。

## 4. Producer confirmation

按“当前仍执行的 Producer > 当前 workspace 有效 artifact > task contract > 文档”的顺序判定：

1. Paper notes:
   - Producer: `SavePaperNoteTool._note_rel_path`。
   - Core notes: `literature/deep_read_notes/{note_id}.md`。
   - Bridge notes: `literature/bridge_notes/{bridge_id}/{note_id}.md` when target bucket is bridge-specific and not core-passed.
   - Shallow notes: Reader/abstract sweep writes `literature/shallow_read_notes/`.

2. Cross-domain catalogs:
   - Producer: T2 bridge catalog refresh/finalization (`refresh_bridge_catalogs`, T2 recovery).
   - Canonical root: `literature/cross_domain_catalogs/<B#>/`.
   - Index: `literature/cross_domain_catalogs/index.json`.
   - Migration: `migrate_legacy_bridge_catalogs` copies old colocated catalog JSON from `bridge_notes/<B#>/`.

3. T3.5 synthesis/workbench:
   - Producer: `build_synthesis_workbench` and Reader/T3.5 synthesis flow.
   - Output: `literature/synthesis_workbench.json` and `literature/synthesis.md`.
   - `all_note_cards` is a routing/index field, not a live note directory.

4. T4 Evidence:
   - Producer: `build_idea_evidence_index`.
   - Output: `ideation/evidence/evidence_index.jsonl` and summary.
   - Source: unified literature manifest note cards plus cross-domain catalog atoms with restricted permissions.

5. T5/T8:
   - Producer: T5 handoff / T8 manuscript tools.
   - Source: task contract imports full `literature/` and now includes `literature/literature_manifest.json`.

6. PDF acquisition and evidence boundary:
   - Producer: `researchos.runtime.pdf_acquisition.acquire_retained_pdfs`.
   - Scope: every paper in `papers_verified.jsonl`（即 T2 保留、已核验、会进入后续研究链路的候选），而非仅 deep-read queue；T3.6 补检记录也会尝试。
   - Local path: `literature/pdfs/{canonical-note-id}.pdf`；所有 receipt 路径均相对 workspace 保存。
   - Status examples: `acquired_parseable`、`existing_parseable`、`unavailable`、`access_denied`、`acquired_unparseable`、`unresolved_identifier`。
   - Crucial boundary: 下载、已有本地 PDF、`FULL_TEXT_LOCAL` access hint 或 `pdf_verified` metadata verification 都不允许自动把 `evidence_level` 设为 `FULL_TEXT`。只有 Reader 使用 `extract_pdf_text` 覆盖全部页面且无未解决截断、并保存带 Reading Coverage 的 paper note 后，才能成为 `FULL_TEXT`；只读部分则为 `PARTIAL_TEXT`。旧 workspace 的 access→evidence 误标会在 resume preflight 中保守修复，并保留 PDF 文件。

## 5. T4 Gate and HumanInterface findings

FSM:

- `T4.next_on_success = T4.5` for normal T4 completion.
- `T4-GATE1` is an immediate gate over completed T4 artifacts. It has `next_on_success = T4` because many Gate operations intentionally re-enter T4 for another evolution/repair pass.
- The native selection path must override this and move to `T4.5` after selected candidate artifacts are compiled.

Root causes fixed:

| Issue | Root cause | Fix |
| --- | --- | --- |
| 输入 `D1` 被解析成 `focus_candidate` | free text + LLM proposal was accepted before checking if the whole input was only a display handle | `_t4_public_handle_tokens` now detects bare `D#`/multi-`D#`; gate reopens with clarification and persists no directive |
| T4 选择后没有进入 T4.5 | `_select_native_t4_candidate` wrote `next_task: T4` and set `state.current_task = "T4"` | selection payload now writes `next_task: T4.5`; state advances to `T4.5` |
| 已有 `_gate1_user_selection.json` resume 仍回 T4 | complete pipeline fast-forward hard-coded `state.current_task = "T4"` | fast-forward reads selection file `next_task`, defaulting to `T4.5` |
| 输入一次后退出 | complete pipeline returned after a read-only/clarification/confirmation gate remained `WAITING_HUMAN` | T4 Gate now loops in the same process while still `WAITING_HUMAN` |
| Gate 首页暴露 12 个内部操作 | config presented high/low frequency operations together | default menu now shows 推进/优化/再探索/查看/暂不决定/更多操作；advanced actions are hidden unless requested |

## 7. Local validation

按“测试文件一律不上传”的约束，本次交付不包含 `tests/` 目录改动。以下场景曾在本地临时回归中覆盖，用于验证实现风险；后续若允许提交测试，可由维护者把这些场景正式落入测试仓库：

- Literature Contract: canonical roots、legacy `paper_notes` 迁移、bridge notes 与 cross-domain catalogs 分离、重复论文去重、stale manifest rebuild、空目录/非文本文件拒绝、Windows/WSL 路径规范化、非 ASCII/空格路径、T5/T8 manifest import closure。
- T4 Gate: 裸 `D1` 澄清且不写 directive、显式 `proceed_candidate + D1` 确认后进入 `T4.5`、`更多操作` 只读并保持 Gate 打开。
- T4 Normal UI: 默认 Gate 首页隐藏内部/高级操作。

Local validation command:

```bash
conda run -n researchos python -m pytest \
  tests/unit/test_workspace_cli_regressions.py \
  tests/unit/test_filesystem_tools.py \
  tests/unit/test_survey_visual_policy.py \
  tests/unit/test_workspace_cli_regressions.py \
  tests/unit/test_t4_gate1_directive_runtime.py \
  tests/unit/test_t4_runtime_evolution.py \
  tests/unit/test_t4_rich_ui.py \
  tests/unit/test_t4_progress_observability.py \
  -q
```

Result:

```text
96 passed
```

## 8. Remaining guardrails

- Do not globally replace `bridge_notes` with `cross_domain_catalogs`; they are different artifacts.
- Do not let prompts ask the model to guess files under note roots. Controller/tool code must resolve concrete file lists.
- Do not mark T3.6 visuals as generated when no paper-note evidence exists.
- Do not treat `not_found`, `is_directory`, or `not_text` loops as harmless if a downstream task then reports success.
- Do not remove legacy directories before migration report is written.
