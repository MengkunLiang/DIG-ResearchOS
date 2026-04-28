"""T9 Submission Agent — 投稿准备

将论文迁移到目标会议格式，匿名化检查，编译验证，打包。
输入: drafts/paper.tex, project.yaml
输出: submission/bundle/, submission/migration_report.md
"""

from __future__ import annotations

import re
from pathlib import Path

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec, get_agent_params
from ..runtime.prompts import render_prompt
from ._common import load_project, prepend_resume_prefix, read_text_file


def check_anonymization(ctx: ExecutionContext) -> tuple[bool, str | None]:
    """Pre-hook: 检查论文匿名化。"""
    paper_path = ctx.workspace_dir / "drafts" / "paper.tex"

    if not paper_path.exists():
        return True, None

    paper_text = paper_path.read_text(encoding="utf-8")

    # 匿名化检查模式
    PATTERNS = {
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "github": r"github\.com/[a-zA-Z0-9_-]+",
        "url": r"https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "acknowledgments": r"\\section\*?\{Acknowledgments?\}|\\section\*?\{致谢\}",
    }

    issues = []

    for name, pattern in PATTERNS.items():
        matches = re.findall(pattern, paper_text, re.IGNORECASE)
        if matches:
            issues.append(f"{name}: {matches[:3]}")  # 只显示前3个

    if issues:
        return False, f"匿名化检查失败:\n" + "\n".join(issues)

    return True, None


class SubmissionAgent(Agent):
    """投稿准备Agent，处理模板迁移、匿名化检查、编译验证。"""

    def __init__(self):
        params = get_agent_params("submission")
        self._params = params
        # 匿名化前置检查改为显式开关，便于本地调试/非匿名投稿场景按需关闭。
        enforce_anonymization_precheck = bool(
            params.get("enforce_anonymization_precheck", False)
        )
        super().__init__(
            build_agent_spec(
                "submission",
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "list_files",
                        "bash_run",
                        "docker_exec",
                        "finish_task",
                    ],
                    "max_steps": 40,
                    "max_tokens_total": 80_000,
                    "max_wall_seconds": 300,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    "allowed_read_prefixes": ["", "drafts/", "literature/", "experiments/"],
                    "allowed_write_prefixes": ["submission/"],
                    "prompt_template": "submission.j2",
                    "pre_hooks": [check_anonymization] if enforce_anonymization_precheck else [],
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt。"""
        project = load_project(ctx)
        target_venue = project.get("target_venue", "neurips2026")
        # 编译重试上限用于指导 T9 在“诊断-修复-重试”循环里及时收敛。
        max_compile_attempts = int(self._params.get("max_compile_attempts", 4))

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            target_venue=target_venue,
            max_compile_attempts=max_compile_attempts,
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """生成投稿任务消息。"""
        project = load_project(ctx)
        target_venue = project.get("target_venue", "neurips")

        return prepend_resume_prefix(
            ctx,
            (
            f"请执行 T9 Submission Agent。\n\n"
            f"将 drafts/paper.tex 迁移到 {target_venue} 会议格式，"
            "执行匿名化检查，验证LaTeX编译，生成投稿包。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验投稿包。"""
        ws = ctx.workspace_dir

        # 检查bundle目录存在
        bundle_dir = ws / "submission" / "bundle"
        if not bundle_dir.exists():
            return False, "submission/bundle/ 目录不存在"

        # 检查必需文件
        required_files = ["main.tex", "references.bib"]
        missing = [f for f in required_files if not (bundle_dir / f).exists()]
        if missing:
            return False, f"bundle缺少必需文件: {missing}"

        # 编译成功后必须留下 PDF，避免“只写报告不真正编译通过”的假成功。
        pdf_path = bundle_dir / "main.pdf"
        if not pdf_path.exists():
            return False, "bundle缺少 main.pdf，说明投稿包尚未编译成功"

        # 检查migration_report.md
        report_path = ws / "submission" / "migration_report.md"
        if not report_path.exists():
            return False, "migration_report.md 不存在"

        report_text = read_text_file(report_path)
        if len(report_text) < 100:
            return False, f"migration_report.md 过短({len(report_text)}字符)"

        # 检查报告包含关键内容
        required_content = ["迁移状态", "编译状态", "匿名化检查"]
        for content in required_content:
            if content not in report_text:
                return False, f"migration_report.md 缺少: {content}"

        # 报告必须明确声明编译成功，避免把失败尝试误判为通过。
        if not re.search(r"编译状态[:：]\s*成功", report_text):
            return False, "migration_report.md 未声明“编译状态: 成功”"

        # 如果存在日志文件，还要排除明显的 fatal 错误残留。
        log_path = bundle_dir / "main.log"
        if log_path.exists():
            log_text = read_text_file(log_path)
            fatal_markers = [
                "Fatal error occurred",
                "! Emergency stop.",
                "==> Fatal error occurred",
            ]
            for marker in fatal_markers:
                if marker in log_text:
                    return False, f"main.log 仍包含致命编译错误: {marker}"

        return True, None
