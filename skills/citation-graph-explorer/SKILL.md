---
name: citation-graph-explorer
description: Explore one-hop references and related works from a DOI or OpenAlex work, then produce a bounded, provenance-aware citation neighborhood for literature expansion or baseline discovery. Use when a researcher wants to snowball from seed papers without treating citation count or graph proximity as scholarly quality.
tools:
  - read_file
  - write_file
  - process_seed_paper
  - fetch_outgoing_citations
  - build_domain_map
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/citation-graph-explorer/
  - user_seeds/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_seeds/
  - literature/
outputs_expected:
  graph_report: literature/skill_citation_graph.md
  graph_record: literature/skill_citation_graph.json
  domain_map: literature/skill_citation_domain_map.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从 DOI 或 OpenAlex 论文标识出发抓取一跳参考文献和 related works，输出可追溯的扩展候选与图谱边界，不把图谱位置当成质量结论。
  request_required: true
  request_prompt: 请说明要探索的主题、希望发现的角色（基础方法、基线、数据集、桥接理论等）和每个种子允许的最大邻居数。
  example_request: 从这些 DOI 扩展当前研究主题的基础方法和竞争性 baseline；每个种子最多 30 条参考文献，单独标出只有元数据的候选。
  required_inputs:
    - id: seed_identifiers
      label: DOI 或 OpenAlex 种子清单
      description: 一行一个 DOI、doi.org URL、OpenAlex W ID 或 OpenAlex work URL；建议 1-10 篇。
      paths:
        - user_inputs/citation-graph-explorer/seeds.md
      extensions: [.md]
      min_bytes: 8
      example: user_inputs/citation-graph-explorer/seeds.md
  optional_inputs:
    - id: graph_policy
      label: 图谱探索范围
      description: 可选；说明最大邻居数、目标年份或语言、筛选角色、只要引用还是也要 related works。
      paths:
        - user_inputs/citation-graph-explorer/policy.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/citation-graph-explorer/policy.md
  outputs:
    - id: graph_report
      label: 引文邻域报告
      path: literature/skill_citation_graph.md
      description: 按种子列出实际抓取来源、引用/related 数量、候选角色、警告和建议的下一步阅读动作。
    - id: graph_record
      label: 引文邻域结构记录
      path: literature/skill_citation_graph.json
      description: 机器可读的种子、原始边、候选记录、provider fallback、抓取警告和筛选边界。
    - id: domain_map
      label: 引文图谱领域映射
      path: literature/skill_citation_domain_map.json
      description: 基于本次实际候选和引文边形成的 core/bridge/adjacent/boundary 机械映射，供人工复核。
---

# Citation Graph Explorer

Read and normalize each listed seed without discarding the original string. For a DOI,
register the seed with `process_seed_paper` when possible, then call
`fetch_outgoing_citations`. For an OpenAlex work identifier, call
`fetch_outgoing_citations` directly. Respect the bounded policy; do not recursively
snowball, scrape publisher pages, or silently replace an unavailable seed with a
title-similar paper.

Write the raw but bounded response for every seed to `skill_citation_graph.json` and
make every fallback or provider warning visible. Pass only the actual returned paper
records and edges to `build_domain_map`, explicitly setting the declared
`skill_citation_domain_map.json` output path. Treat that map as a mechanical
organization aid, never as a final gap, novelty, importance, or relevance judgment.

The Markdown report must distinguish direct references, provider-reported related
works, metadata-only candidates, and sources requiring verification. It may propose
section-specific follow-up searches, but it must not create final citations or assert
that an unread neighbor supports a claim. Finish after all three declared output files
exist, including a report of failed or unresolved seeds.
