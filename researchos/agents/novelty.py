"""T6 Novelty Agent — 新颖性验证与基线补充

业务需求：
- 在 T5 Pilot 实验完成后进行新颖性最终验证
- 检查实验结果是否支撑假设的创新性
- 识别潜在撞车案例
- 补充必须的基线方法

输入：
- ideation/hypotheses.md: T4 产出的研究假设
- ideation/exp_plan.yaml: T4 产出的实验计划
- pilot/pilot_results.json: T5 Pilot 实验结果
- pilot/motivation_validation.md: T5 Pilot 动机验证
- literature/comparison_table.csv: 已有方法对比表
- literature/synthesis.md: T3.5 文献综述

输出：
- novelty/novelty_report.md: 新颖性报告
- novelty/collision_cases.md: 潜在撞车案例（如有）
- novelty/must_add_baselines.md: 必须补充的基线方法
"""

from __future__ import annotations

import re
from pathlib import Path

from ..time_utils import recent_year_from
from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.logger import get_logger
from ..runtime.prompts import render_prompt
from ._common import (
    prepend_resume_prefix,
    load_project,
    read_text_file,
    validate_files_exist,
)

logger = get_logger(__name__)


class NoveltyAgent(Agent):
    """T6 Novelty Agent。新颖性验证与基线补充。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "novelty",
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "list_files",
                        "search_papers",
                        "ask_human",
                        "finish_task",
                    ],
                    "max_steps": 60,
                    "max_tokens_total": 150_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    # T6 在恢复运行时需要读取 novelty/ 下已有草稿，否则只能“会写不会读”。
                    "allowed_read_prefixes": ["", "ideation/", "literature/", "pilot/", "novelty/"],
                    "allowed_write_prefixes": ["novelty/"],
                    "prompt_template": "novelty.j2",
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染 system prompt。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir

        # 读取假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")

        # 读取实验计划
        exp_plan = read_text_file(ws / "ideation" / "exp_plan.yaml", default="")

        # 读取 Pilot 结果（如果有）
        pilot_results = read_text_file(ws / "pilot" / "pilot_results.json", default="")

        # 读取 Motivation Validation
        motivation = read_text_file(ws / "pilot" / "motivation_validation.md", default="")

        # 读取 T4.5 审计结果。T6 的职责不是从零重跑一遍 novelty audit，
        # 而是在已有审计基础上，结合 Pilot 证据做增量复核和补充 baseline。
        novelty_audit = read_text_file(ws / "ideation" / "novelty_audit.md", default="")

        # 读取对比表
        comparison_table = read_text_file(ws / "literature" / "comparison_table.csv", default="")

        # 读取文献综述
        synthesis = read_text_file(ws / "literature" / "synthesis.md", default="")

        # 提取假设 anchor
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses, re.MULTILINE)
        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            hypotheses_preview=hypotheses[:5000],
            exp_plan_preview=exp_plan[:2000],
            pilot_results_preview=pilot_results[:2000],
            motivation_preview=motivation[:1500],
            novelty_audit_preview=novelty_audit[:2500],
            comparison_table_preview=comparison_table[:1000],
            synthesis_preview=synthesis[:2000],
            hypothesis_count=len(anchors),
            hypothesis_anchors=anchors,
            recent_year_from=recent_year_from(1),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return prepend_resume_prefix(
            ctx,
            (
            "请执行 T6 新颖性验证任务。\n"
            "先以 T4.5 的 novelty_audit.md 为主参考，再结合 T5 Pilot 实验结果，"
            "更新高风险假设的新颖性判断，只做必要的增量搜索，识别潜在撞车风险并补充必须的基线方法。\n"
            "产出 novelty/novelty_report.md、novelty/collision_cases.md（如有）和 "
            "novelty/must_add_baselines.md。"
            ),
        )

    def _extract_mechanism_keywords(self, hypothesis: dict) -> list[str]:
        """从假设中提取技术机制关键词。

        使用结构化模式匹配 + 最小通用术语集提取技术术语。
        通用术语集覆盖跨领域共用的 ML 基础概念（架构、训练、优化），
        不绑定特定研究方向。

        Args:
            hypothesis: 假设字典，包含 title 和 content 字段

        Returns:
            机制关键词列表
        """
        import re

        # 提取文本
        text = ""
        if isinstance(hypothesis, dict):
            text = f"{hypothesis.get('title', '')} {hypothesis.get('content', '')}"
        else:
            text = str(hypothesis)

        text_lower = text.lower()
        keywords: set[str] = set()

        # 1. 提取带连字符的技术术语 (e.g., "self-supervised", "cross-attention")
        hyphenated = re.findall(r"\b[a-z]+(?:-[a-z]+)+\b", text_lower)
        keywords.update(h for h in hyphenated if len(h) > 5)

        # 2. 提取缩写词 (e.g., "GNN", "RAG", "LoRA")
        abbrevs = re.findall(r"\b[A-Z]{2,6}\b", text)
        keywords.update(a.lower() for a in abbrevs)

        # 3. 提取大写开头的技术名词 (e.g., "Transformer", "BERT", "ViT")
        capitalized = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text)
        common_words = {
            "the", "this", "that", "these", "those", "which", "what",
            "where", "when", "how", "our", "their", "your", "with",
            "from", "into", "through", "during", "before", "after",
            "above", "below", "between", "about", "proposed", "proposes",
            "method", "methods", "approach", "result", "results",
            "paper", "work", "study", "problem", "solution", "figure",
            "table", "section", "chapter", "however", "therefore",
            "moreover", "furthermore", "although", "because", "while",
            "whereas", "also", "still", "already", "even", "just",
            "only", "both", "either", "neither", "each", "every",
            "such", "than", "other", "another", "some", "many",
            "much", "few", "several", "most", "all", "any",
            "test", "user", "data", "system", "task", "tasks",
            "process", "service", "support", "provide", "based",
            "using", "used", "new", "first", "second", "third",
            "high", "low", "large", "small", "good", "best",
            "different", "same", "specific", "general", "important",
            "available", "current", "recent", "existing", "traditional",
            "effective", "efficient", "novel", "improved", "improving",
            "behavior", "behaviour", "performance", "evaluation",
            "analysis", "experiment", "experiments", "experimental",
            "comparison", "comparative", "investigation", "survey",
            "review", "overview", "introduction", "conclusion",
            "discussion", "summary", "description", "explanation",
        }
        for term in capitalized:
            lower = term.lower()
            if lower not in common_words:
                keywords.add(lower)

        # 4. 通用 ML 术语匹配（跨领域共用的基础概念）
        #    这些不是特定研究方向的关键词，而是 ML 领域共用的技术词汇。
        #    用于为 LLM agent 提供搜索种子，agent 自身会根据上下文调整查询。
        universal_terms = [
            # 架构
            "transformer", "attention", "encoder", "decoder",
            "cnn", "rnn", "lstm", "gru", "gan", "vae",
            "bert", "gpt", "llama", "t5", "bart", "roberta",
            "resnet", "vgg", "inception", "vit", "diffusion",
            # 训练技术
            "fine-tuning", "transfer learning", "contrastive learning",
            "self-supervised learning", "few-shot learning", "zero-shot learning",
            "meta-learning", "knowledge distillation", "pruning", "quantization",
            "lora", "adapter", "prompt tuning",
            # 强化学习
            "reinforcement learning", "policy gradient", "actor-critic",
            "q-learning", "ppo", "dqn",
            # 优化
            "adam", "sgd", "adamw", "gradient descent", "momentum",
            # 检索与生成
            "retrieval", "retrieval-augmented generation",
            # 归一化与正则化
            "batch normalization", "layer normalization", "dropout",
            # 注意力变体
            "self-attention", "cross-attention", "flash attention",
            # 图神经网络
            "graph neural network", "graph convolution",
            # 其他
            "embedding", "representation", "backpropagation",
        ]
        for term in universal_terms:
            if term in text_lower:
                keywords.add(term)

        # 5. 提取复合技术词 (e.g., "autoencoder", "feedforward", "backpropagation")
        compound_patterns = [
            r"\b([a-z]+(?:encoder|decoder|network|attention|embedding))\b",
            r"\b((?:auto|feed|backprop|dropout|softmax|sigmoid)[a-z]*)\b",
        ]
        for pattern in compound_patterns:
            compounds = re.findall(pattern, text_lower)
            keywords.update(c for c in compounds if len(c) > 5)

        # 6. 提取引号中的术语
        quoted = re.findall(r"['\"]([^'\"]{3,30})['\"]", text)
        keywords.update(q.lower().strip() for q in quoted)

        # Filter out very short terms and normalize plurals
        filtered = set()
        for kw in keywords:
            if len(kw) < 3:
                continue
            # Simple plural normalization: "adapters" → "adapter"
            if kw.endswith("s") and not kw.endswith("ss") and len(kw) > 4:
                singular = kw[:-1]
                if singular in keywords or len(singular) >= 3:
                    filtered.add(singular)
                    continue
            filtered.add(kw)

        return list(filtered)[:20]

    def _search_similar_mechanisms(
        self, mechanism_keywords: list[str], tool_registry
    ) -> list[dict]:
        """搜索使用相似机制的论文。

        Args:
            mechanism_keywords: 机制关键词列表
            tool_registry: 工具注册表（用于调用 search_papers）

        Returns:
            搜索到的论文列表
        """
        if not mechanism_keywords:
            logger.info("未提取到机制关键词，跳过机制相似度搜索")
            return []

        logger.info(f"提取到 {len(mechanism_keywords)} 个机制关键词: {mechanism_keywords[:5]}")

        # 构建机制聚焦的查询
        # 选择最重要的几个关键词（避免查询过长）
        top_keywords = mechanism_keywords[:3]
        query = " ".join(top_keywords)

        logger.info(f"机制相似度搜索查询: {query}")

        # 注意：这里返回空列表，因为实际搜索需要在 agent 运行时通过 tool_call 完成
        # 这个方法主要用于生成搜索策略和关键词
        return []

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """验证 T6 输出。"""
        ws = ctx.workspace_dir

        # 1. 必需文件检查
        required_files = [
            "novelty/novelty_report.md",
            "novelty/must_add_baselines.md",
        ]
        ok, err = validate_files_exist(ctx, required_files)
        if not ok:
            return False, err

        # 2. novelty_report.md 内容检查
        report_path = ws / "novelty" / "novelty_report.md"
        report_text = read_text_file(report_path)

        if len(report_text) < 500:
            return False, f"novelty/novelty_report.md 过短({len(report_text)} 字符)"

        # 检查是否包含新颖性等级标记
        level_markers = ["Level 0", "Level 1", "Level 2", "Level 3"]
        has_level = any(marker in report_text for marker in level_markers)
        if not has_level:
            return False, "novelty/novelty_report.md 必须包含新颖性等级（Level 0-3）"

        # 3. must_add_baselines.md 内容检查
        baselines_path = ws / "novelty" / "must_add_baselines.md"
        baselines_text = read_text_file(baselines_path)

        if len(baselines_text) < 100:
            return False, f"novelty/must_add_baselines.md 过短({len(baselines_text)} 字符)"

        # 4. 检查是否审计了所有假设
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")
        anchors = re.findall(r"^#+\s*(H\d+)", hypotheses, re.MULTILINE)

        for anchor in anchors:
            if anchor not in report_text:
                return False, f"novelty/novelty_report.md 缺少对假设 {anchor} 的审计"

        # 5. collision_cases.md 检查（如果有 High Overlap 则必须存在）
        collision_path = ws / "novelty" / "collision_cases.md"
        if collision_path.exists():
            collision_text = read_text_file(collision_path)
            # 检查是否标记了高风险撞车
            has_high_risk = "高风险" in collision_text or "High" in collision_text
            if has_high_risk and "Level 0" in report_text:
                logger.warning(
                    "发现 Level 0 假设但 novelty_report 未明确标记撞车风险"
                )

        return True, None
