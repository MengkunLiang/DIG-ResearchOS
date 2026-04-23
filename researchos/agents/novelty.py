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

import structlog

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.agent_params import get_agent_params
from ..runtime.prompts import render_prompt
from ._common import (
    load_project,
    read_text_file,
    validate_files_exist,
)

logger = structlog.get_logger(__name__)


class NoveltyAgent(Agent):
    """T6 Novelty Agent。新颖性验证与基线补充。"""

    def __init__(self):
        params = get_agent_params("novelty")
        super().__init__(
            AgentSpec(
                name="novelty",
                model_tier=params.get("model_tier", "medium"),
                tool_names=[
                    "read_file",
                    "write_file",
                    "list_files",
                    "search_papers",
                    "ask_human",
                    "finish_task",
                ],
                max_steps=params.get("max_steps", 60),
                max_tokens_total=params.get("max_tokens_total", 150_000),
                max_wall_seconds=params.get("max_wall_seconds", 600),
                max_validation_retries=params.get("max_validation_retries", 3),
                temperature=0.3,
                allowed_read_prefixes=["", "ideation/", "literature/", "pilot/"],
                allowed_write_prefixes=["novelty/"],
                prompt_template="novelty.j2",
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
            comparison_table_preview=comparison_table[:1000],
            synthesis_preview=synthesis[:2000],
            hypothesis_count=len(anchors),
            hypothesis_anchors=anchors,
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """初始用户消息。"""
        return (
            "请执行 T6 新颖性验证任务。\n"
            "基于 T5 Pilot 实验结果和 T4 假设，检查每个假设的创新性，"
            "搜索近期相关工作，识别潜在撞车风险，补充必须的基线方法。\n"
            "产出 novelty/novelty_report.md、novelty/collision_cases.md（如有）和 "
            "novelty/must_add_baselines.md。"
        )

    def _extract_mechanism_keywords(self, hypothesis: dict) -> list[str]:
        """从假设中提取技术机制关键词。

        识别算法名、架构名、技术术语等。

        Args:
            hypothesis: 假设字典，包含 title 和 content 字段

        Returns:
            机制关键词列表
        """
        # 常见技术术语模式
        common_mechanisms = {
            # 深度学习架构
            "transformer", "bert", "gpt", "llama", "t5", "bart",
            "cnn", "convolutional neural network", "resnet", "vgg", "inception",
            "rnn", "lstm", "gru", "recurrent neural network",
            "gan", "generative adversarial network", "vae", "variational autoencoder",
            "diffusion", "stable diffusion", "ddpm",
            "vision transformer", "vit", "swin transformer",

            # 注意力机制
            "attention", "self-attention", "cross-attention", "multi-head attention",
            "flash attention", "linear attention",

            # 优化算法
            "adam", "sgd", "adamw", "rmsprop", "adagrad",
            "gradient descent", "momentum",

            # 强化学习
            "reinforcement learning", "rl", "ppo", "dqn", "a3c", "sac",
            "q-learning", "policy gradient", "actor-critic",

            # 训练技术
            "fine-tuning", "prompt tuning", "lora", "qlora", "adapter",
            "knowledge distillation", "transfer learning",
            "contrastive learning", "self-supervised learning",
            "few-shot learning", "zero-shot learning", "meta-learning",

            # 架构组件
            "encoder", "decoder", "encoder-decoder",
            "feedforward", "mlp", "residual connection", "skip connection",
            "batch normalization", "layer normalization", "dropout",

            # 其他技术
            "graph neural network", "gnn", "gcn",
            "neural architecture search", "nas",
            "pruning", "quantization", "compression",
            "retrieval", "rag", "retrieval-augmented generation",
        }

        # 提取文本
        text = ""
        if isinstance(hypothesis, dict):
            text = f"{hypothesis.get('title', '')} {hypothesis.get('content', '')}"
        else:
            text = str(hypothesis)

        text_lower = text.lower()

        # 查找匹配的机制关键词
        found_keywords = []
        for mechanism in common_mechanisms:
            if mechanism in text_lower:
                found_keywords.append(mechanism)

        # 去重并返回
        return list(set(found_keywords))

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
