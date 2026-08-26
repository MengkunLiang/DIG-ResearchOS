"""AgentRunner execution loop for tool calls, validation, and recovery state.

The runner owns turn sequencing and durable diagnostics; task-specific Agents
retain scientific responsibility and cannot bypass policy or output contracts.
"""

from __future__ import annotations


import asyncio
from contextlib import suppress
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from io import StringIO
import inspect
import json
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

from ..pydantic_compat import model_dump
from ..orchestration.task_io_contract import get_task_io
from .agent import Agent, AgentResult, EffectiveConfig, ExecutionContext, resolve_effective_config
from .budget import BudgetTracker
from .config import RuntimeSettings
from .errors import BudgetExceeded, LLMProviderError, RecoverableRuntimePause, ToolAccessDenied, ToolError
from .llm_client import LLMClient, ModelBinding
from .logger import get_logger
from .manuscript_recovery import (
    can_refresh_t8_manuscript_outputs,
    can_repair_t8_section_plan,
    refresh_t8_manuscript_outputs,
    repair_t8_section_plan_outputs,
)
from .message import Message, Role, ToolCall, is_empty_assistant
from .progress import (
    CliProgressEmitter,
    build_tool_narrative,
    describe_task_artifacts,
    format_cli_message,
    next_step_for_task,
    safe_relative,
    summarize_reader_note_progress,
    summarize_progress_markdown,
    summarize_tool_result,
)
from .t2_recovery import finalize_t2_outputs, validate_t2_finalize_manifest
from .abstract_sweep import (
    build_sweep_candidates,
    has_shallow_read_coverage_contract,
    run_abstract_sweep,
    run_abstract_sweep_with_reader,
    validate_abstract_sweep_coverage,
)
from .t2_config import (
    get_effective_reader_read_params,
    load_deep_read_queue_config,
    load_t2_finalize_config,
    require_deep_read_target,
)
from .pdf_acquisition import acquire_retained_pdfs, attach_pdf_acquisition, repair_access_only_evidence_levels
from .literature_contract import build_literature_manifest, iter_literature_note_cards
from .t3_recovery import prepare_t3_resume_artifacts
from .t3_notes_manifest import validate_t3_input_fingerprints
from .bridge_catalog import iter_bridge_catalog_paths
from .artifact_fingerprints import validate_t45_fingerprint_report
from .workflow_mode import (
    auto_execution_setup_summary,
    configure_workflow_mode,
    is_execution_setup_confirmation_answer,
    load_workflow_mode,
    parse_auto_execution_setup_answer,
    parse_execution_setup_proposal,
    parse_workflow_mode_answer,
    parse_workflow_mode_proposal,
    workflow_startup_setup_needs_confirmation,
    workflow_startup_template_needs_confirmation,
    workflow_mode_needs_confirmation,
)
from ..latex_templates import available_ccf_template_ids, ccf_template_entries, parse_available_ccf_template_answer
from .task_recovery import prepare_generic_resume_artifacts
from .run_logger import RunLogger
from ..agents.ideation import (
    T4_GATE1_ARTIFACTS,
    ensure_t4_evidence_pool,
    prepare_t4_context_pack,
    refresh_t4_gate1_progress,
    validate_t4_gate1_ready,
)
from ..ideation.config import load_t4_evolution_settings
from ..ideation.directives import current_population_context
from ..ideation.evolution_controller import IdeaEvolutionController
from ..ideation.final_card_diagnostics import (
    FinalCardCompilationFailure,
    classify_final_card_exception,
    classify_final_card_readiness_error,
)
from ..ideation.final_card_readiness import (
    archive_final_card_profile_mismatch,
    validate_t4_portfolio_final_cards,
)
from ..ideation.legacy_projection import project_gate1_population
from ..ideation.llm_roles import (
    LLMCandidateEnricher,
    LLMFinalIdeaCardCompiler,
    LLMJsonRoleInvoker,
    LLMIdeaEvolver,
    LLMIdeaGenerator,
    LLMIdeaScorer,
    T4RoleCallConfig,
)
from ..ideation.models import EvolutionPhase, HumanCompositionCompatibility
from ..ideation.prerun import has_current_t4_prerun_confirmation
from ..ideation.selected_compilation import ensure_t45_pre_novelty_brief, validate_legacy_t45_brief_source
from ..ideation.state import T4ArtifactStore
from ..ideation.formalization import collect_t45_semantic_errors
from ..ideation.t45_semantic_adjudication import (
    accepted_t45_semantic_errors,
    persist_t45_semantic_adjudication,
    semantic_adjudication_scope,
)
from ..survey_semantic_adjudication import (
    accepted_t36_semantic_errors,
    collect_t36_semantic_errors,
    persist_t36_semantic_adjudication,
    semantic_adjudication_scope as t36_semantic_adjudication_scope,
)
from ..ui.idea_evolution_renderer import render_t4_evolution_phase
from .trace import NullTraceWriter, TraceWriter
from ..tools.base import Tool, ToolResult
from ..tools.filesystem import STRUCTURED_ONLY_WRITE_PATHS
from ..tools.workspace_policy import WorkspaceAccessPolicy
from ..tools.external_experiment import (
    AuditPaperClaimsTool,
    CompileResearchReboostHandoffTool,
)
from ..orchestration.t5_t8_bridge import (
    accept_and_ingest_t5_handoff,
    validate_t8_ingest_artifacts,
)
from ..tools.human_gate import HumanInputUnavailable, HumanInterface
from ..tools.paper_save_tools import SavePapersRawTool
from ..tools.registry import ToolBuildContext, ToolRegistry
from .agent_params import get_agent_mode_params, get_budget_escalation_policy, get_global_timeout, get_retry_policy
from ..tools.scout_progress import ScoutProgressLogger
from rich.console import Console

if TYPE_CHECKING:
    from ..tools.workspace_policy import WorkspaceAccessPolicy


# Source-aware prose repair persists across resume.  A repeated diagnosis with
# unchanged relevant source artifacts pauses before another model call; a
# meaningful source change remains eligible for a fresh targeted repair.
T45_QUALITY_REPAIR_LEDGER_REL_PATH = "_runtime/t45_quality_repair_ledger.json"
T36_QUALITY_REPAIR_LEDGER_REL_PATH = "_runtime/t36_quality_repair_ledger.json"
# The repair ledger is tied to the selected research decision and its stable
# upstream evidence.  Proposal edits are recorded as source progress rather
# than silently resetting a retry counter, while a real T4 reframe or changed
# literature basis receives an independent diagnostic history.
T45_QUALITY_REPAIR_BASELINE_ARTIFACTS = (
    "project.yaml",
    "ideation/selected/selected_candidate.json",
    "ideation/hypothesis_brief.yaml",
    "ideation/novelty_audit.md",
    "literature/synthesis.md",
    "ideation/orientation_config.yaml",
)
# T3.6 records a narrow fingerprint for the artifacts relevant to each failed
# check.  It blocks only a resumed repetition that leaves that source scope
# unchanged; useful section, evidence, template, or scope changes can proceed.
T36_QUALITY_REPAIR_BASELINE_ARTIFACTS = (
    "project.yaml",
    "literature/synthesis.md",
    "literature/synthesis_workbench.json",
    "literature/literature_manifest.json",
    "drafts/survey/decision.json",
    "drafts/survey/writing_template.json",
    "drafts/survey/outline_decision.json",
    "drafts/survey/corpus_decision.json",
)


T2_AUTO_PERSIST_SEARCH_TOOLS = frozenset(
    {
        "multi_source_search",
        "search_papers",
        "semantic_scholar_search",
        "arxiv_search",
        "openalex_search",
        "crossref_search",
        "elsevier_scopus_search",
        "informs_search",
        "fetch_outgoing_citations",
    }
)
TOOL_FAILURE_CACHE_NAMES = frozenset({"fetch_paper_pdf", "expand_corpus_for_survey"})
T45_QUALITY_SOURCE_ARTIFACTS = (
    "ideation/orientation_config.yaml",
    "ideation/research_blueprint.yaml",
    "ideation/claim_registry.yaml",
    "ideation/exp_plan.yaml",
    "ideation/hypotheses.md",
    "ideation/proposal/research_proposal.md",
    "ideation/orientation_review.json",
)
T36_QUALITY_SOURCE_ARTIFACTS = (
    "drafts/survey/survey_plan.json",
    "drafts/survey/survey_state.json",
    "drafts/survey/sections",
    "literature/related_work.bib",
)
# A T4.5 Proposal can be several thousand words.  Retaining every historic
# full-document write until a provider's global context limit is reached made
# one local prose repair inflate to millions of input tokens.  This is a
# history-retention cap only: the complete current artifacts stay on disk and
# the Formalizer is explicitly told to re-read them after compaction.
T45_HISTORY_MAX_INPUT_TOKENS = 96_000
T45_HISTORY_TRIGGER_RATIO = 0.72
T45_HISTORY_TARGET_RATIO = 0.55
# Reader stages repeatedly inspect long evidence artifacts.  Their durable
# note cards and workbenches are available through targeted tools, so retaining
# an almost-full provider context of historic PDF previews and prior tool
# outputs has no research benefit and has caused otherwise healthy T3.5 calls
# to exceed the request deadline.  These are history caps, not evidence caps.
READER_HISTORY_MAX_INPUT_TOKENS = {
    "T3": 96_000,
    "T3.5": 112_000,
    "T3.6": 112_000,
}
READER_HISTORY_TRIGGER_RATIO = 0.78
READER_HISTORY_TARGET_RATIO = 0.60
T45_RESEARCH_CONTENT_SOURCE_ARTIFACTS = (
    "ideation/research_blueprint.yaml",
    "ideation/claim_registry.yaml",
    "ideation/exp_plan.yaml",
    "ideation/hypotheses.md",
    "ideation/proposal/research_proposal.md",
)
TOOL_CONTEXT_CONTENT_LIMITS = {
    # PDF 文本工具是 T3 上下文膨胀的主要来源。工具自身也有上限，这里再加
    # runtime 兜底，防止未来工具改动或异常 PDF 解析再次把长文本塞进模型。
    "extract_paper_sections": 12000,
    "extract_pdf_text": 50000,
}
# A read can be perfectly valid as an artifact operation while still being far
# too large to append wholesale to an agent's next conversational turn.  These
# are per-result visibility caps; the complete file stays in the workspace and
# the cap receipt tells the model how to continue with a smaller page.  They
# protect task quality by preserving room for the question, tool schemas, and
# synthesis rather than treating a giant first page as evidence utilization.
TASK_READ_FILE_CONTEXT_CHAR_CAPS = {
    "T3": 48_000,
    "T3.5": 64_000,
    "T3.6": 56_000,
    "T4": 64_000,
    "T4.5-FORMALIZE": 48_000,
    "T4.5-REVIEW": 48_000,
    "T8": 56_000,
}
DEFAULT_READ_FILE_CONTEXT_CHAR_CAP = 64_000
T2_CROSS_DOMAIN_QUERY_BUCKET_ALIASES = {
    "adjacent": "adjacent_field",
    "adjacent-field": "adjacent_field",
    "adjacent_field": "adjacent_field",
    "cross-domain": "adjacent_field",
    "cross_domain": "adjacent_field",
    "nearby-field": "adjacent_field",
    "nearby_field": "adjacent_field",
    "theory": "theory_bridge",
    "theory-bridge": "theory_bridge",
    "theory_bridge": "theory_bridge",
    "theoretical": "theory_bridge",
}


class _T4OperationEnvelope:
    """Small typed-read adapter for a durable T4 operation envelope."""

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    @classmethod
    def model_validate(cls, value: object) -> "_T4OperationEnvelope":
        if not isinstance(value, dict):
            raise ValueError("T4 operation artifact must be a JSON object")
        return cls(value)


def _t4_recap_title(candidate: dict[str, object], *, limit: int = 72) -> str:
    """Use the declared display label for CLI telemetry, never a long pitch."""

    text = str(
        candidate.get("display_title")
        or candidate.get("title_short_zh")
        or candidate.get("short_title")
        or candidate.get("title")
        or "未命名方向"
    )
    compact = " ".join(text.split())
    effective_limit = min(limit, 32) if re.search(r"[\u4e00-\u9fff]", compact) else limit
    return compact if len(compact) <= effective_limit else compact[: max(0, effective_limit - 3)] + "..."


def _normalize_t2_query_bucket(raw: object) -> str:
    value = str(raw or "").strip().casefold()
    if not value:
        return ""
    return T2_CROSS_DOMAIN_QUERY_BUCKET_ALIASES.get(value, value.replace(" ", "_"))


class HookExecutionError(RuntimeError):
    """hook 执行失败时使用的统一异常。"""


class AgentRunner:
    """驱动单个 agent 完成一次完整 run。

    这里集中处理：
    - budget 与步数限制；
    - LLM 调用与消息拼装；
    - tool 调用、异常兜底与结果回填；
    - finish_task 触发后的输出校验；
    - trace 写入。
    """

    def __init__(
        self,
        agent: Agent,
        tool_registry: ToolRegistry,
        llm_client: LLMClient,
        human_interface: HumanInterface,
        runtime_settings: RuntimeSettings | None = None,
        workspace_policy_factory: Callable[[ExecutionContext, EffectiveConfig], "WorkspaceAccessPolicy"]
        | None = None,
    ):
        self.agent = agent
        self.tool_registry = tool_registry
        self.llm = llm_client
        self.human = human_interface
        # runner 默认使用共享 runtime 配置；测试里若不传，则安全回退到默认值。
        self.runtime_settings = runtime_settings or RuntimeSettings()
        self.workspace_policy_factory = workspace_policy_factory or self._default_policy_factory
        self.log = get_logger(f"runner.{agent.spec.name}")
        self.global_timeout = get_global_timeout()
        self.retry_policy = get_retry_policy()
        self.budget_escalation_policy = get_budget_escalation_policy()
        self.progress = CliProgressEmitter(
            quiet=self.runtime_settings.ui.quiet,
            verbose=self.runtime_settings.ui.verbose,
            verbosity=self.runtime_settings.ui.verbosity,
            no_color=self.runtime_settings.ui.no_color,
            json_events=self.runtime_settings.ui.json_events,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
        )
        self._t4_durable_recap_keys: set[str] = set()

    def _resolve_run_tool_names(self, eff: EffectiveConfig) -> list[str]:
        """Resolve runtime-added tools without overriding an Agent's write contract.

        ``grep_search`` is a read-only workspace navigation primitive.  It is
        intentionally available to every Agent that can read workspace files,
        including legacy mode overrides whose static tool list predates the
        capability.  The workspace policy remains authoritative, so this does
        not broaden a task's readable paths or grant write/execute access.
        """

        tool_names = list(eff.tool_names)
        if (
            "grep_search" not in tool_names
            and any(name in tool_names for name in ("read_file", "list_files", "glob_files"))
            and self.tool_registry.has("grep_search")
        ):
            tool_names.append("grep_search")
        dynamic_tool_names = self.tool_registry.dynamic_tool_names_for(self.agent.spec.name)
        if dynamic_tool_names:
            # MCP tools are configured by the workspace owner at startup. They
            # augment, rather than replace, the capability contract declared by
            # the Agent or Skill.
            tool_names.extend(dynamic_tool_names)

        if not eff.allow_edit_file_compatibility:
            # Some tasks own schema-bound sources and deliberately expose only
            # write_structured_file for them. Do this after dynamic-tool
            # augmentation as well, so an MCP registration cannot quietly
            # reintroduce an incompatible compatibility surface.
            return list(dict.fromkeys(name for name in tool_names if name != "edit_file"))

        if "write_file" in tool_names and self.tool_registry.has("edit_file"):
            # OpenAI-compatible providers often choose the familiar
            # ``edit_file`` name after reading an existing text document.
            # Agents can opt out when that alias would conflict with their
            # structured-write contract.
            tool_names.append("edit_file")
        return list(dict.fromkeys(tool_names))

    def _is_t4_ideation_agent(self, ctx: ExecutionContext) -> bool:
        """Return whether native T4 controls apply to this runner.

        ``task_id`` is intentionally available to test and extension agents so
        they can exercise generic runtime features under a T4-shaped context.
        Those agents must not inherit T4's controller, artifact ordering, or
        legacy-isolation behavior merely because they use the same task label.
        The production T4 state-machine node always uses the registered
        ``ideation`` agent; scope controller-only behavior to that agent.
        """

        return ctx.task_id == "T4" and self.agent.spec.name == "ideation"

    @staticmethod
    def _default_policy_factory(
        ctx: ExecutionContext, eff: EffectiveConfig
    ) -> "WorkspaceAccessPolicy":
        from ..tools.workspace_policy import WorkspaceAccessPolicy

        allowed_write_prefixes = list(eff.allowed_write_prefixes)
        allowed_survey_section_ids: frozenset[str] | None = None

        # A T3.6 section worker has historically inherited drafts/survey/ and
        # could therefore rewrite Abstract, Conclusion, outlines, or trigger
        # assembly while writing Introduction.  The task I/O contract already
        # declares exactly one section output; make it an enforced capability
        # boundary instead of a prompt-only convention.
        if ctx.task_id.startswith("T3.6-SEC-"):
            section_id = str(ctx.extra.get("section_id") or "").strip()
            if not section_id:
                section_id = ctx.task_id.removeprefix("T3.6-SEC-").lower().replace("-", "_")
            section_path = ctx.outputs_expected.get("section")
            if section_path is None:
                section_path = ctx.workspace_dir / "drafts" / "survey" / "sections" / f"{section_id}.tex"
            try:
                section_rel = section_path.relative_to(ctx.workspace_dir).as_posix()
            except ValueError:
                section_rel = f"drafts/survey/sections/{section_id}.tex"
            scoped_writes = [
                section_rel,
                "drafts/survey/survey_state.json",
                # A section writer may autonomously close one precise
                # evidence gap.  These are the deterministic supplement
                # tool's only global outputs; other survey sections remain
                # outside the task capability boundary.
                "literature/targeted_supplements/",
                "literature/shallow_read_notes/",
                "literature/related_work.bib",
                "literature/literature_manifest.json",
                # Keep a durable, compact receipt of what this section
                # actually retrieved.  Without this scoped exception the
                # query succeeds in memory but silently loses its audit and
                # downstream-reuse record under the section capability wall.
                "literature/evidence_queries/",
            ]
            allowed_write_prefixes = [
                path
                for path in scoped_writes
                if WorkspaceAccessPolicy.path_allowed(path, allowed_write_prefixes)
            ]
            allowed_survey_section_ids = frozenset({section_id})

        return WorkspaceAccessPolicy(
            workspace_dir=ctx.workspace_dir,
            allowed_read_prefixes=eff.allowed_read_prefixes,
            allowed_write_prefixes=allowed_write_prefixes,
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            allowed_survey_section_ids=allowed_survey_section_ids,
        )

    @staticmethod
    def _is_timeout_provider_error(exc: LLMProviderError) -> bool:
        text = str(exc).lower()
        if not text:
            return False
        timeout_markers = (
            "timeouterror",
            "timeout error",
            "timed out",
            "timeout",
            "readtimeout",
            "connecttimeout",
            "超时",
        )
        fatal_markers = (
            "authentication",
            "permissiondenied",
            "permission denied",
            "invalid_api_key",
            "invalid api key",
            "unauthorized",
            "rate limit",
            "ratelimit",
            "context_length",
            "context window",
            "badrequest",
            "bad request",
        )
        return any(marker in text for marker in timeout_markers) and not any(
            marker in text for marker in fatal_markers
        )

    @classmethod
    def _is_recoverable_provider_error(cls, exc: LLMProviderError) -> bool:
        text = str(exc).lower()
        if not text:
            return False
        timeout_or_connection_markers = (
            "timeouterror",
            "timeout",
            "timed out",
            "readtimeout",
            "connecttimeout",
            "connectionerror",
            "connection error",
            "server disconnected",
            "超时",
        )
        if any(marker in text for marker in timeout_or_connection_markers):
            return True
        fatal_markers = (
            "authentication",
            "permissiondenied",
            "permission denied",
            "invalid_api_key",
            "invalid api key",
            "unauthorized",
            "context_length",
            "context window",
            "badrequest",
            "bad request",
        )
        if any(marker in text for marker in fatal_markers):
            return False
        transient_markers = (
            "temporarily unavailable",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
            "overloaded",
            "超时",
        )
        return cls._is_timeout_provider_error(exc) or any(marker in text for marker in transient_markers)

    def _llm_provider_recovery_policy(self) -> tuple[int, float, float]:
        """Return bounded, user-facing recovery settings for provider outages.

        ``LLMClient.chat`` already tries the primary/fallback chain once.  A
        recovery *batch* below deliberately starts that chain over after a
        short cooldown, which is what lets a briefly overloaded provider
        recover without forcing the researcher to resume the whole project.
        The two legacy keys remain accepted so existing user settings do not
        silently change behaviour.
        """

        raw_batches = self.retry_policy.get("llm_provider_retry_batches")
        if raw_batches is None:
            raw_batches = self.retry_policy.get("llm_timeout_pause_after_cooldowns")
        try:
            batches = int(raw_batches)
        except (TypeError, ValueError):
            batches = 1
        # Historically ``0`` meant "do not auto-pause". A provider recovery
        # batch may itself contain the configured connection retries, so more
        # than one unattended batch compounds a long request deadline into an
        # opaque multi-minute wait without adding research information. Keep
        # the default to one; the visible Gate still offers retry and delay.
        if batches <= 0:
            batches = 1
        batches = max(1, min(batches, 50))

        raw_cooldown = self.retry_policy.get("llm_provider_initial_cooldown_seconds")
        if raw_cooldown is None:
            raw_cooldown = self.retry_policy.get("llm_timeout_cooldown_seconds")
        try:
            cooldown = float(raw_cooldown)
        except (TypeError, ValueError):
            cooldown = 10.0
        # A legacy zero is useful in tests and explicit local development, but
        # the checked-in user-facing default is ten seconds.
        cooldown = max(0.0, min(cooldown, 300.0))

        try:
            long_cooldown = float(self.retry_policy.get("llm_provider_long_cooldown_seconds", 20))
        except (TypeError, ValueError):
            long_cooldown = 20.0
        return batches, cooldown, max(0.0, min(long_cooldown, 900.0))

    def _llm_retry_overrides(self) -> tuple[int | None, float | None]:
        """Use model-settings fallback unless a legacy override exists.

        New one-model runs must not receive the historical runner defaults of
        two attempts and two seconds: that would silently override the
        researcher-maintained ``model_settings.yaml`` fallback block. Legacy
        endpoint/profile configurations retain their prior defaults.
        """

        raw_attempts = self.retry_policy.get("llm_retries")
        raw_delay = self.retry_policy.get("llm_retry_delay")
        if bool(getattr(self.llm, "single_model_mode", False)):
            attempts: int | None = None
            delay: float | None = None
        else:
            attempts = 2
            delay = 2.0
        if raw_attempts is not None:
            try:
                attempts = max(1, min(int(raw_attempts), 10))
            except (TypeError, ValueError):
                pass
        if raw_delay is not None:
            try:
                delay = max(0.0, min(float(raw_delay), 300.0))
            except (TypeError, ValueError):
                pass
        return attempts, delay

    def _llm_request_timeout_seconds(self) -> int:
        """Resolve the public deadline for a normal research-model request."""

        if bool(getattr(self.llm, "single_model_mode", False)):
            getter = getattr(self.llm, "get_request_timeout_seconds", None)
            if callable(getter):
                try:
                    return max(1, int(getter()))
                except (TypeError, ValueError):
                    pass
        try:
            configured = self.global_timeout.get("llm_call")
            if configured is not None:
                return max(1, int(configured))
        except (TypeError, ValueError):
            pass
        # Legacy endpoint/profile configurations do not carry the compact
        # model-settings fallback block. Keep them aligned with the public
        # research-request default instead of silently reverting to an old
        # shorter deadline.
        return 300

    @staticmethod
    def _provider_error_category(exc: LLMProviderError) -> str:
        """Classify a provider failure without treating every HTTP 400 as context.

        LiteLLM uses ``BadRequestError`` for several materially different
        conditions: a context limit, an unsupported request parameter, a
        model capability mismatch, and occasionally a provider-side policy
        rejection.  The old UI collapsed all of them into a request to edit
        context settings, which both misdirected the researcher and hid the
        only useful distinction for T4 recovery.
        """

        text = str(exc).casefold()
        if any(marker in text for marker in ("authentication", "invalid_api_key", "invalid api key", "unauthorized", "permissiondenied", "permission denied")):
            return "authentication"
        if any(
            marker in text
            for marker in (
                "insufficient balance",
                "insufficient credit",
                "insufficient funds",
                "credit balance",
                "billing balance",
                "quota exhausted",
                "余额不足",
                "额度不足",
            )
        ):
            return "account_balance"
        if any(
            marker in text
            for marker in (
                "context_length",
                "context length",
                "context window",
                "maximum context",
                "maximum context length",
                "prompt is too long",
                "input is too long",
                "too many tokens",
            )
        ):
            return "context_limit"
        if any(marker in text for marker in ("rate limit", "ratelimit", "too many requests", "status code: 429", "http 429")):
            return "rate_limit"
        if any(marker in text for marker in ("content policy", "content_filter", "safety policy", "responsible ai", "被内容安全")):
            return "content_policy"
        if any(
            marker in text
            for marker in (
                "unsupported parameter",
                "unsupported value",
                "invalid_request_error",
                "invalid request",
                "response_format",
                "json schema",
                "does not support",
                "not supported for this model",
            )
        ):
            return "request_schema"
        if any(marker in text for marker in ("badrequest", "bad request", "status code: 400", "http 400")):
            return "bad_request"
        if any(marker in text for marker in ("timeouterror", "timeout", "timed out", "readtimeout", "connecttimeout", "超时")):
            return "timeout"
        if any(marker in text for marker in ("connectionerror", "connection error", "server disconnected", "network")):
            return "connection"
        return "unknown"

    @staticmethod
    def _safe_provider_error_detail(exc: LLMProviderError) -> str:
        """Keep an actionable provider detail without persisting credentials or URLs."""

        detail = " ".join(str(exc).split())
        detail = re.sub(
            r"(?i)(api[_-]?key|authorization|bearer)\s*([=:])\s*[^,\s\]\)]+",
            r"\1\2<redacted>",
            detail,
        )
        detail = re.sub(r"(?i)api_base\s*=\s*[^,\s\]\)]+", "api_base=<redacted>", detail)
        detail = re.sub(r"https?://[^\s,\]\)]+", "<endpoint>", detail)
        return detail[:900]

    @staticmethod
    def _provider_http_status(exc: LLMProviderError) -> int | None:
        match = re.search(r"(?:status(?:[_ ]code)?|http)\s*[:=]?\s*(\d{3})", str(exc), flags=re.IGNORECASE)
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _t4_role_request_metrics(
        self,
        *,
        eff: EffectiveConfig,
        messages: list[dict[str, object]],
    ) -> dict[str, object]:
        """Describe a typed T4 request without sending another provider call."""

        try:
            resolved = self.llm.resolve(
                profile=eff.llm_profile,
                tier=eff.llm_tier,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
            )
            if not resolved:
                return {}
            binding, endpoint = resolved[0]
            context_info = self.llm.get_context_window_info(
                binding,
                endpoint,
                explicit_override=eff.llm_max_context_override is not None,
            )
            return {
                "estimated_input_tokens": self.llm.count_tokens(messages, binding),
                "effective_context_window": self.llm.get_context_window(binding),
                "context_window_source": context_info.source,
                "response_reserve": "provider_default_not_explicitly_configured",
            }
        except Exception:
            # Diagnostics must never become another T4 failure path.
            return {}

    def _record_t4_provider_failure_diagnostic(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        messages: list[dict[str, object]],
        exc: LLMProviderError,
        failed_batches: int,
    ) -> str:
        """Persist a safe, request-scoped T4 rejection receipt.

        The raw exception is intentionally not shown in the terminal because
        LiteLLM can include endpoint hints.  This receipt provides the next
        resume with enough evidence to distinguish a local schema mismatch
        from an actual context or provider availability problem.
        """

        phase_key = str(ctx.extra.get("t4_heartbeat_phase_key") or "t4_role")
        safe_phase = re.sub(r"[^a-zA-Z0-9_.-]+", "_", phase_key).strip("_") or "t4_role"
        sequence = int(ctx.extra.get("t4_provider_failure_sequence") or 0) + 1
        ctx.extra["t4_provider_failure_sequence"] = sequence
        relative_path = f"ideation/evolution/diagnostics/provider_request_{sequence:03d}_{safe_phase}.json"
        payload: dict[str, object] = {
            "schema_version": "1.0.0",
            "semantics": "t4_provider_request_diagnostic",
            "phase": phase_key,
            "failure_batch": max(1, failed_batches),
            "error_category": self._provider_error_category(exc),
            "http_status": self._provider_http_status(exc),
            "safe_provider_detail": self._safe_provider_error_detail(exc),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        payload.update(self._t4_role_request_metrics(eff=eff, messages=messages))
        try:
            T4ArtifactStore(ctx.workspace_dir).write_json(relative_path, payload)
        except (OSError, ValueError):
            return ""
        return relative_path

    def _t4_provider_pause(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        messages: list[dict[str, object]],
        exc: LLMProviderError,
        failed_batches: int,
    ) -> RecoverableRuntimePause:
        diagnostic_path = self._record_t4_provider_failure_diagnostic(
            ctx=ctx,
            eff=eff,
            messages=messages,
            exc=exc,
            failed_batches=failed_batches,
        )
        message = self._public_provider_error_message(exc)
        if diagnostic_path:
            message += f" 已写入脱敏诊断：{diagnostic_path}。"
        return RecoverableRuntimePause(message)

    @classmethod
    def _public_provider_error_message(cls, exc: LLMProviderError) -> str:
        """Return a safe, correctly scoped CLI message for a provider error."""

        category = cls._provider_error_category(exc)
        if category == "authentication":
            return "模型服务配置未通过验证；请检查已选择服务的凭据和模型名称后 resume。"
        if category == "account_balance":
            return "模型服务账户余额或信用额度不足；请充值、恢复配额或更换已获授权的模型连接后 resume。"
        if category == "context_limit":
            return "模型明确拒绝了本次上下文长度；请核对该模型的真实上下文容量或缩小本次输入后 resume。"
        if category in {"request_schema", "bad_request", "content_policy"}:
            return "模型拒绝了本次请求（请求格式、模型能力或内容策略）；这不是已确认的上下文错误。"
        if category == "rate_limit":
            return "模型服务当前触发频率或配额限制；已保留进度，可稍后 resume。"
        return "模型服务暂时不可用；已保留当前进度，可稍后 resume。"

    async def _choose_llm_provider_recovery(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        failed_batches: int,
        retry_batches: int,
        cooldown_seconds: float,
        long_cooldown_seconds: float,
        failure_category: str | None = None,
    ) -> tuple[str, float]:
        """Choose a safe next action after a recoverable provider failure.

        Returns ``("retry", seconds)`` or ``("pause", 0)``.  The human gate
        is only opened after the bounded automatic retries are exhausted.
        """

        if failed_batches < retry_batches:
            return "retry", cooldown_seconds

        if failure_category == "timeout":
            failure_explanation = (
                f"本次模型请求在配置的 {self._llm_request_timeout_seconds()} 秒内没有返回。"
                "这不表示已检测到上下文或校验错误；必要的大请求可以在服务恢复后继续等待或 resume。"
            )
        elif failure_category == "rate_limit":
            failure_explanation = "模型服务当前触发频率或配额限制。"
        else:
            failure_explanation = "本次模型请求未能从服务端获得可用回复。"

        self.progress.stage_human_action_required(
            task_id=ctx.task_id,
            gate_id="runtime_llm_provider_recovery",
            reason="模型服务连续不可用，需要确认是否继续等待。",
        )
        human_started = time.time()
        try:
            selection = await self.human.present_gate(
                gate_id="runtime_llm_provider_recovery",
                presentation={
                    "_title": "模型服务暂时不可用",
                    "_description": (
                        f"{failure_explanation} 当前请求批次已尝试 {failed_batches} 次，尚未改变任何产物。"
                        "项目进度已经安全保留，请选择下一步。"
                    ),
                    "task_id": ctx.task_id,
                    "retry_count": failed_batches,
                },
                options=[
                    {
                        "id": "retry_now",
                        "label": "立即重新提交",
                        "description": "用相同输入重新发起一次请求；适合确认服务已恢复时使用。",
                    },
                    {
                        "id": "wait_20_seconds",
                        "label": "等待 20 秒后重试",
                        "description": "服务可能拥堵时使用；不会重做已完成的工具或产物。",
                    },
                    {"id": "pause", "label": "暂停项目", "description": "保留进度，稍后使用 resume 继续。"},
                ],
            )
        except HumanInputUnavailable:
            return "pause", 0.0
        finally:
            budget.exclude_wall_time(time.time() - human_started)

        option_id = str((selection or {}).get("option_id") or "pause")
        self.progress.stage_gate_resolved(
            task_id=ctx.task_id,
            gate_id="runtime_llm_provider_recovery",
            decision=option_id,
        )
        if option_id == "retry_now":
            return "retry", 0.0
        if option_id == "wait_20_seconds":
            return "retry", long_cooldown_seconds
        self._mark_explicit_runtime_pause(
            ctx,
            kind="provider",
            decision=option_id,
        )
        return "pause", 0.0

    async def _wait_before_llm_provider_retry(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        seconds: float,
        attempt: int,
        retry_batches: int,
    ) -> None:
        """Wait without consuming the active agent wall-clock budget."""

        wait_seconds = max(0.0, seconds)
        if wait_seconds:
            automatic_retry_limit = max(1, retry_batches - 1)
            self.progress.emit(
                f"[Runtime] 当前模型请求暂时失败，将在 {wait_seconds:g} 秒后自动重试"
                f"（当前请求第 {attempt} 次，最多 {automatic_retry_limit} 次；不是 T4 全局轮次）。",
                important=True,
            )
            self._record_skill_progress(
                ctx,
                step=budget.steps,
                step_limit="unlimited" if budget.unlimited_budget else budget.max_steps,
                phase="waiting_runtime",
                detail=f"模型服务暂时不可用，等待 {wait_seconds:g} 秒后重新请求。",
            )
            started = time.time()
            await asyncio.sleep(wait_seconds)
            budget.exclude_wall_time(time.time() - started)
        else:
            self.progress.emit(
                "[Runtime] 已按你的选择重新提交模型请求；不会重做已完成的工具或产物。",
                important=True,
            )

    async def run(self, ctx: ExecutionContext) -> AgentResult:
        """执行一次完整 agent run。"""
        self.progress.configure_observability(workspace=ctx.workspace_dir)
        started = time.time()
        # A recovery signal describes *this* invocation's stop condition.  A
        # durable recovery directive from a previous human decision remains in
        # ``ctx.extra["runtime_recovery"]`` and must not be mistaken for a new
        # failure while the task is retrying.
        ctx.extra.pop("_runtime_recovery_signal", None)
        ctx.extra.pop("_runtime_explicit_pause", None)
        eff = resolve_effective_config(self.agent.spec, ctx)
        eff = self._apply_runtime_recovery_window(eff, ctx)
        eff.tool_names = self._resolve_run_tool_names(eff)
        max_agent_runtime = int(self.global_timeout.get("max_agent_runtime") or 0)
        effective_wall_seconds = eff.max_wall_seconds
        if max_agent_runtime > 0:
            effective_wall_seconds = min(effective_wall_seconds, max_agent_runtime)
        budget = BudgetTracker(
            max_steps=eff.max_steps,
            max_tokens=eff.max_tokens,
            max_wall_seconds=effective_wall_seconds,
            unlimited_budget=eff.unlimited_budget,
        )
        skill_tool_budget = self._new_skill_tool_budget_state(ctx)
        trace_file: Path | None = None
        if self.runtime_settings.debug.enable_trace:
            trace_file = self.runtime_settings.traces_dir(ctx.workspace_dir) / f"{ctx.run_id}.jsonl"
            trace = TraceWriter(trace_file)
            trace.write_run_start(
                run_id=ctx.run_id,
                agent_name=self.agent.spec.name,
                project_id=ctx.project_id,
                task_id=ctx.task_id,
                workspace_dir=ctx.workspace_dir,
            )
        else:
            trace = NullTraceWriter()

        run_logger = RunLogger(
            ctx.workspace_dir,
            runtime_dir_name=self.runtime_settings.workspace.runtime_dir,
            quiet=self.runtime_settings.ui.quiet,
            verbose=self.runtime_settings.ui.verbose,
        )
        run_logger.event(
            "RUN_START",
            run_id=ctx.run_id,
            task=ctx.task_id,
            agent=self.agent.spec.name,
            project_id=ctx.project_id,
        )
        run_logger.event(
            "TASK_START",
            task=ctx.task_id,
            agent=self.agent.spec.name,
            mode=ctx.mode or ctx.extra.get("phase"),
        )

        self._print_task_start_summary(ctx, eff)
        try:
            task_io = get_task_io(ctx.task_id)
        except KeyError:
            # Programmatic callers and unit tests may use a lightweight Agent
            # without a state-machine contract. Keep their established CLI
            # behaviour instead of inventing a formal research-stage panel.
            task_io = None
        # Standalone Skills are not state-machine nodes, but they still have
        # declared inputs, outputs and durable sessions.  Route them through
        # the same observable stage protocol so their Markdown and Tool events
        # receive the Rich rendering instead of falling back to raw `[Tool]`
        # lines.  Ad-hoc programmatic agents keep the historical lightweight
        # behaviour below.
        if task_io is not None or ctx.task_id.startswith("SKILL_"):
            required_input_keys = {
                str(key)
                for key in (task_io.get("required_inputs") or [])
                if isinstance(key, str)
            } if task_io is not None else set(ctx.inputs)
            self.progress.stage_started(
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                inputs=ctx.inputs,
                outputs=ctx.outputs_expected,
                required_input_keys=required_input_keys,
                agent=self.agent.spec.name,
                mode=str(ctx.mode or ctx.extra.get("phase") or "-"),
                is_resume=self._is_resume_run(ctx),
            )
        self.progress.agent_start(
            task_id=ctx.task_id,
            agent=self.agent.spec.name,
            phase=ctx.mode or ctx.extra.get("phase") or "-",
            objective=str(ctx.extra.get("task_description") or self._infer_task_description(ctx)),
            inputs=[
                safe_relative(path, ctx.workspace_dir) or str(path)
                for path in list(ctx.inputs.values())
            ],
            expected_outputs=[
                safe_relative(path, ctx.workspace_dir) or str(path)
                for path in list(ctx.outputs_expected.values())
            ],
            expected_artifacts=describe_task_artifacts(ctx.task_id),
            llm_tier=eff.llm_tier,
            step_limit="unlimited" if eff.unlimited_budget else str(eff.max_steps),
        )
        if (
            self._is_t4_ideation_agent(ctx)
            and not self._t4_gate1_user_selection_exists(ctx)
            and not has_current_t4_prerun_confirmation(ctx.workspace_dir)
        ):
            # Write and render the first durable checkpoint before provider work
            # begins. This lets the CLI distinguish "preparing evidence" from a
            # silent provider wait without exposing private reasoning.
            self._refresh_t4_gate1_progress(ctx, active_path=None)
        self._record_skill_progress(
            ctx,
            step=0,
            step_limit="unlimited" if eff.unlimited_budget else eff.max_steps,
            phase="starting",
            detail="已建立运行上下文，正在准备第一组可执行动作。",
        )
        last_model_used: str | None = None
        last_endpoint_used: str | None = None
        stop_reason = AgentResult.STOP_ERROR
        error_msg: str | None = None

        try:
            primary_binding, primary_endpoint = self.llm.resolve(
                profile=eff.llm_profile,
                tier=eff.llm_tier,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
            )[0]
            # ``max_context`` in routing is an auditable fallback rather than
            # a claim about a provider's live deployment. Discover once before
            # building context-sensitive tools, then resolve again so file
            # reads, history truncation, and the first model call agree.
            discover_context = getattr(self.llm, "discover_context_window", None)
            if eff.llm_max_context_override is None and callable(discover_context):
                discovery = discover_context(primary_binding, primary_endpoint)
                if inspect.isawaitable(discovery):
                    await discovery
                primary_binding, primary_endpoint = self.llm.resolve(
                    profile=eff.llm_profile,
                    tier=eff.llm_tier,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=None,
                )[0]

            context_source: str | None = None
            context_info_getter = getattr(self.llm, "get_context_window_info", None)
            if callable(context_info_getter):
                context_info = context_info_getter(
                    primary_binding,
                    primary_endpoint,
                    explicit_override=eff.llm_max_context_override is not None,
                )
                source_labels = {
                    "provider_metadata": "服务端元数据",
                    "configured_fallback": "配置回退",
                    "explicit_override": "显式上限",
                }
                context_source = str(context_info.source)
                self.progress.emit(
                    f"[Runtime] 模型上下文：{context_info.max_context:,} tokens "
                    f"（{source_labels.get(context_info.source, context_info.source)}）",
                    verbose_only=True,
                )

            policy = self.workspace_policy_factory(ctx, eff)
            if self._is_t4_ideation_agent(ctx):
                self._maybe_prepare_t4_context_pack_before_prompt(ctx)
            build_ctx = ToolBuildContext(
                policy=policy,
                human=self.human,
                skill_dir=Path(ctx.extra["skill_dir"]) if "skill_dir" in ctx.extra else None,
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                llm_model=primary_binding.model,
                llm_tier=eff.llm_tier,
                llm_max_context=primary_binding.max_context,
                llm_context_source=context_source,
                skill_session_id=str(ctx.extra.get("skill_session_id") or "") or None,
            )
            tool_map = self.tool_registry.build(eff.tool_names, build_ctx)
            tool_schemas = self.tool_registry.to_openai_schemas(tool_map)
        except asyncio.CancelledError:
            return self._finish_agent_startup_interruption(
                ctx=ctx,
                budget=budget,
                eff=eff,
                started=started,
                trace=trace,
                trace_file=trace_file,
                run_logger=run_logger,
                error="Cancelled during agent startup.",
                exception_type="CancelledError",
                recovery=False,
            )
        except Exception as exc:
            return self._finish_agent_startup_interruption(
                ctx=ctx,
                budget=budget,
                eff=eff,
                started=started,
                trace=trace,
                trace_file=trace_file,
                run_logger=run_logger,
                error=f"Agent startup failed before the task loop: {type(exc).__name__}: {str(exc) or repr(exc)}",
                exception_type=type(exc).__name__,
                recovery=True,
            )

        deterministic_pre_finalized = False
        try:
            from ..skills.project_specialization.task_adapter import (
                can_reuse_existing_project_skill_specialization,
            )

            deterministic_pre_finalized = can_reuse_existing_project_skill_specialization(ctx)
        except Exception:
            deterministic_pre_finalized = False
        if deterministic_pre_finalized:
            stop_reason = AgentResult.STOP_FINISHED
            error_msg = None

        # Agents that prepare large, source-grounded prompts can use the same
        # discovered context capacity as the provider-bound tool layer.  This
        # avoids a stale per-agent character cap while keeping a smaller model
        # on a safe, explicit file-reading path.
        try:
            ctx.extra["runtime_context_window"] = primary_binding.max_context
            self._prepare_t4_execution_mode_before_prompt(ctx)
            messages: list[Message] = []
            if not deterministic_pre_finalized:
                sys_msg = Message.system(self.agent.system_prompt(ctx), step=0)
                user_msg = Message.user(self.agent.initial_user_message(ctx), step=0)
                messages = [sys_msg, user_msg]
                trace.write_message(sys_msg)
                trace.write_message(user_msg)
                recovery_note = self._runtime_recovery_prompt(ctx)
                if recovery_note:
                    note = Message.user(recovery_note, step=0)
                    messages.append(note)
                    trace.write_message(note)
                resumed_human = ctx.extra.get("resumed_human_interaction")
                if isinstance(resumed_human, dict) and str(resumed_human.get("answer") or "").strip():
                    note = Message.user(
                        "【已恢复的人工回答】\n"
                        "上次运行在等待下列问题时被中断。问题与回答均已从持久化状态恢复；"
                        "请直接据此继续当前任务，不要再次询问同一个问题。\n\n"
                        f"问题：{resumed_human.get('question') or ''}\n\n"
                        f"回答：{resumed_human.get('answer') or ''}",
                        step=0,
                    )
                    messages.append(note)
                    trace.write_message(note)
        except asyncio.CancelledError:
            return self._finish_agent_startup_interruption(
                ctx=ctx,
                budget=budget,
                eff=eff,
                started=started,
                trace=trace,
                trace_file=trace_file,
                run_logger=run_logger,
                error="Cancelled while preparing the agent prompt.",
                exception_type="CancelledError",
                recovery=False,
            )
        except Exception as exc:
            return self._finish_agent_startup_interruption(
                ctx=ctx,
                budget=budget,
                eff=eff,
                started=started,
                trace=trace,
                trace_file=trace_file,
                run_logger=run_logger,
                error=f"Agent startup failed before the task loop: {type(exc).__name__}: {str(exc) or repr(exc)}",
                exception_type=type(exc).__name__,
                recovery=True,
            )

        empty_count = 0
        nudge_count = 0
        validation_fails = 0
        validation_retry_limit = int(self.agent.spec.max_validation_retries)
        budget_extensions_used = 0
        validation_extensions_used = 0
        llm_timeout_cooldowns_used = 0
        tool_failure_cache: dict[tuple[str, str], Message] = {}
        # Only T4.5 uses this narrowly scoped circuit breaker.  It detects a
        # model regenerating the same invalid structured shape, while still
        # allowing ordinary schema repair when the diagnostic changes.
        t45_schema_failure_state: dict[tuple[str, str, str], tuple[str, int]] = {}

        try:
            await self._maybe_run_t1_workflow_mode_gate(ctx, tool_map, messages, trace)
            await self._maybe_run_t1_workflow_template_gate(ctx, tool_map, messages, trace)
            await self._maybe_run_t1_startup_gate(ctx, tool_map, messages, trace)

            t9_pre_finalized = await self._maybe_finalize_t9_submission_before_hooks(ctx)
            if t9_pre_finalized or deterministic_pre_finalized:
                deterministic_pre_finalized = True
                stop_reason = AgentResult.STOP_FINISHED
                error_msg = None
            else:
                deterministic_pre_finalized = False

            # pre-hook 允许是同步或异步 callable；若返回 (ok, err) 且 ok=False，
            # 这里会统一转换成可读错误，而不是让 CLI 因 await 非协程直接崩溃。
            if not deterministic_pre_finalized:
                for hook in self.agent.spec.pre_hooks:
                    await self._run_pre_hook(hook, ctx)

            t5_reboost_pre_finalized = False
            if not deterministic_pre_finalized:
                t5_reboost_pre_finalized = await self._maybe_finalize_t5_reboost_before_llm(ctx, policy)
                if t5_reboost_pre_finalized:
                    deterministic_pre_finalized = True

            t5_specialization_pre_finalized = False
            if not deterministic_pre_finalized:
                t5_specialization_pre_finalized = await self._maybe_finalize_project_skill_specialization_before_llm(ctx)
                if t5_specialization_pre_finalized:
                    deterministic_pre_finalized = True

            t2_pre_finalized = False
            if not deterministic_pre_finalized:
                t2_pre_finalized = await self._maybe_finalize_t2_before_llm(ctx)
            if not (deterministic_pre_finalized or t2_pre_finalized):
                await self._ensure_shared_pdf_acquisition(ctx)
            t3_pre_finalized = False
            if not (deterministic_pre_finalized or t2_pre_finalized):
                t3_pre_finalized = await self._maybe_finalize_t3_before_llm(ctx)
            t4_pre_finalized = False
            t35_prepared = False
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized):
                t35_prepared = await self._maybe_prepare_t35_before_llm(ctx, policy)
            t36_section_pre_finalized = False
            if not (deterministic_pre_finalized or t2_pre_finalized or t3_pre_finalized):
                self._pause_t36_quality_repair_before_llm(ctx)
                t36_section_pre_finalized = await self._maybe_finalize_t36_section_before_llm(ctx)
            t36_visuals_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
            ):
                t36_visuals_pre_finalized = await self._maybe_finalize_t36_visuals_before_llm(
                    ctx,
                    tool_map=tool_map,
                )
            t36_compile_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
            ):
                t36_compile_pre_finalized = await self._maybe_finalize_t36_compile_before_llm(
                    ctx,
                    tool_map=tool_map,
                )
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
            ):
                t4_pre_finalized = await self._maybe_finalize_t4_before_llm(ctx)
            t4_pre_novelty_selected = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
            ):
                t4_pre_novelty_selected = await self._maybe_advance_t4_pre_novelty_selection(ctx)
            t4_gate1_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_pre_novelty_selected
            ):
                t4_gate1_pre_finalized = await self._maybe_finalize_t4_gate1_before_llm(ctx)
            t4_evolution_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_pre_novelty_selected
                or t4_gate1_pre_finalized
            ):
                t4_evolution_pre_finalized = await self._maybe_run_t4_evolution_before_llm(
                    ctx=ctx,
                    eff=eff,
                    budget=budget,
                )
            if not (
                t4_pre_finalized
                or t4_pre_novelty_selected
                or t4_gate1_pre_finalized
                or t4_evolution_pre_finalized
            ):
                self._enforce_t4_execution_mode_before_legacy_loop(ctx)
            if t4_evolution_pre_finalized:
                deterministic_pre_finalized = True
            if t4_pre_novelty_selected:
                deterministic_pre_finalized = True
            t45_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
            ):
                if ctx.task_id == "T4.5":
                    t45_brief = ensure_t45_pre_novelty_brief(ctx.workspace_dir)
                    legacy_migration = t45_brief.get("mode") == "legacy_migrated"
                    ctx.extra["t45_legacy_migrated_brief"] = legacy_migration
                t45_pre_finalized = await self._maybe_finalize_t45_before_llm(ctx)
            resource_prepare_wait_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
            ):
                resource_prepare_wait_pre_finalized = await self._maybe_finalize_resource_prepare_wait_before_llm(ctx)
            if resource_prepare_wait_pre_finalized:
                deterministic_pre_finalized = True
            external_wait_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
            ):
                external_wait_pre_finalized = await self._maybe_finalize_external_wait_before_llm(ctx)
            paper_claim_audit_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
            ):
                paper_claim_audit_pre_finalized = await self._maybe_finalize_paper_claim_audit_before_llm(ctx, policy)
            t8_resource_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
            ):
                t8_resource_pre_finalized = await self._maybe_finalize_t8_resource_before_llm(ctx)
            t8_section_plan_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or
                t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
                or t8_resource_pre_finalized
            ):
                t8_section_plan_pre_finalized = await self._maybe_finalize_t8_section_plan_before_llm(
                    ctx,
                    policy,
                )
            t8_section_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
                or t8_resource_pre_finalized
                or t8_section_plan_pre_finalized
            ):
                t8_section_pre_finalized = await self._maybe_finalize_t8_section_before_llm(ctx)
            t8_manuscript_pre_finalized = False
            if not (
                deterministic_pre_finalized
                or
                t2_pre_finalized
                or t3_pre_finalized
                or t36_section_pre_finalized
                or t36_visuals_pre_finalized
                or t36_compile_pre_finalized
                or t4_pre_finalized
                or t4_gate1_pre_finalized
                or t45_pre_finalized
                or external_wait_pre_finalized
                or paper_claim_audit_pre_finalized
                or t8_resource_pre_finalized
                or t8_section_plan_pre_finalized
                or t8_section_pre_finalized
            ):
                t8_manuscript_pre_finalized = await self._maybe_finalize_t8_manuscript_before_llm(ctx)
            deterministic_pre_finalized = deterministic_pre_finalized or (
                    t2_pre_finalized
                    or t3_pre_finalized
                    or t36_section_pre_finalized
                    or t36_visuals_pre_finalized
                    or t36_compile_pre_finalized
                    or t4_pre_finalized
                    or t4_gate1_pre_finalized
                    or t45_pre_finalized
                    or external_wait_pre_finalized
                    or paper_claim_audit_pre_finalized
                    or t8_resource_pre_finalized
                    or t8_section_plan_pre_finalized
                    or t8_section_pre_finalized
                    or t8_manuscript_pre_finalized
                )
            if deterministic_pre_finalized:
                stop_reason = AgentResult.STOP_FINISHED
                error_msg = None

            while not deterministic_pre_finalized:
                # 每进入一轮 while，就代表一次“agent step”。
                budget.tick_step()
                step_limit = "unlimited" if budget.unlimited_budget else str(budget.max_steps)
                self._record_skill_progress(
                    ctx,
                    step=budget.steps,
                    step_limit=step_limit,
                    phase="preparing_step",
                    detail="正在整理当前 workspace 产物并请求下一组可执行动作。",
                )
                run_logger.event(
                    "AGENT_STEP",
                    task=ctx.task_id,
                    step=budget.steps,
                    tokens=budget.tokens_in + budget.tokens_out,
                    cost_usd=f"{budget.cost_usd:.4f}",
                )

                # 每5步输出一次进度
                if budget.steps % 5 == 1 or budget.steps == 1:
                    self.progress.agent_step(
                        agent=self.agent.spec.name,
                        step=budget.steps,
                        step_limit=step_limit,
                        tokens=budget.tokens_in + budget.tokens_out,
                        cost_usd=budget.cost_usd,
                    )
                try:
                    budget.check()
                except BudgetExceeded as exc:
                    extended, budget_extensions_used = await self._maybe_offer_budget_extension(
                        ctx=ctx,
                        budget=budget,
                        exc=exc,
                        used_extensions=budget_extensions_used,
                    )
                    if extended:
                        continue
                    stop_reason = AgentResult.STOP_BUDGET
                    error_msg = str(exc)
                    self._mark_runtime_recovery(
                        ctx,
                        kind="budget",
                        error=error_msg,
                        details={
                            "dimension": exc.dimension,
                            "used": exc.used,
                            "limit": exc.limit,
                            "extensions_used": budget_extensions_used,
                        },
                    )
                    break

                # 如果上下文太长，这里会按“完整 tool call group”为单位裁掉旧消息，
                # 同时插入一条 runtime note，提醒模型去读 artifact 而不是假装记得历史。
                messages_before_truncation = messages
                messages = self._maybe_truncate(messages, primary_binding, task_id=ctx.task_id)
                # A long synthesis run may legitimately need to reread a file
                # after history was evicted.  Clear the same-run large-read
                # guard only when that eviction actually happened; otherwise
                # repeated reads of the same 100KB+ index would be injected
                # into every subsequent request and can turn a healthy
                # provider call into a deadline timeout.
                if len(messages) < len(messages_before_truncation):
                    ctx.extra.pop("_t35_large_read_seen", None)
                messages = self._repair_openai_tool_message_sequence(messages)

                provider_retry_batches, provider_cooldown, provider_long_cooldown = self._llm_provider_recovery_policy()
                llm_retry_attempts, llm_retry_delay = self._llm_retry_overrides()
                llm_request_timeout = self._llm_request_timeout_seconds()
                provider_failures_this_request = 0
                provider_pause_requested = False
                while True:
                    try:
                        run_logger.event(
                            "LLM_CALL",
                            task=ctx.task_id,
                            step=budget.steps,
                            tier=eff.llm_tier,
                            profile=eff.llm_profile,
                            tool_count=len(tool_schemas or []),
                            provider_recovery_attempt=provider_failures_this_request,
                        )
                        llm_resp = await self._await_llm_with_progress(
                            ctx=ctx,
                            step=budget.steps,
                            progress_step_limit=step_limit,
                            messages=[item.to_openai_dict() for item in messages],
                            tools=tool_schemas or None,
                            temperature=eff.llm_temperature,
                            tier=eff.llm_tier,
                            profile=eff.llm_profile,
                            model_override=eff.llm_model_override,
                            endpoint_override=eff.llm_endpoint_override,
                            max_context_override=eff.llm_max_context_override,
                            timeout=llm_request_timeout,
                            max_retries_per_model=llm_retry_attempts,
                            retry_base_delay=llm_retry_delay,
                            reasoning_effort=(
                                "low"
                                if (
                                    # A blank T4.5 source must be delivered as
                                    # one valid native tool JSON.  On several
                                    # OpenAI-compatible reasoning endpoints,
                                    # leaving effort at the provider default
                                    # exhausts the response allowance in hidden
                                    # reasoning and truncates the final tool
                                    # arguments.  The Formalizer still receives
                                    # the full evidence and owns every research
                                    # decision; this reserves output room for
                                    # the model-authored contract.
                                    ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}
                                    or (ctx.task_id == "T8-WRITE" and ctx.mode in {None, "outline"})
                                    or ctx.task_id.startswith("T8-SEC-")
                                )
                                else None
                            ),
                            **(
                                {"max_completion_tokens": 16_384}
                                if (
                                    ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}
                                    and self._llm_chat_accepts_keyword("max_completion_tokens")
                                )
                                else {}
                            ),
                        )
                    except LLMProviderError as exc:
                        # Keep complete provider diagnostics in the durable run
                        # log.  The terminal must not expose endpoint URLs,
                        # model chains, SDK internals, or credential hints.
                        run_logger.event(
                            "ERROR",
                            task=ctx.task_id,
                            step=budget.steps,
                            kind="llm_provider",
                            message=str(exc)[:300],
                        )
                        if not self._is_recoverable_provider_error(exc):
                            stop_reason = AgentResult.STOP_ERROR
                            error_msg = self._public_provider_error_message(exc)
                            provider_pause_requested = True
                            break

                        llm_timeout_cooldowns_used += 1
                        provider_failures_this_request += 1
                        action, wait_seconds = await self._choose_llm_provider_recovery(
                            ctx=ctx,
                            budget=budget,
                            failed_batches=provider_failures_this_request,
                            retry_batches=provider_retry_batches,
                            cooldown_seconds=provider_cooldown,
                            long_cooldown_seconds=provider_long_cooldown,
                            failure_category=self._provider_error_category(exc),
                        )
                        if action == "retry":
                            await self._wait_before_llm_provider_retry(
                                ctx=ctx,
                                budget=budget,
                                seconds=wait_seconds,
                                attempt=provider_failures_this_request,
                                retry_batches=provider_retry_batches,
                            )
                            # A human-confirmed retry starts a fresh bounded
                            # batch.  Automatic retries retain their count.
                            if provider_failures_this_request >= provider_retry_batches:
                                provider_failures_this_request = 0
                            continue

                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = "模型服务持续不可用；当前进度已保留，可在服务恢复后 resume。"
                        self._mark_runtime_recovery(
                            ctx,
                            kind="provider",
                            error=error_msg,
                            details={
                                "failed_retry_batches": provider_failures_this_request,
                                "automatic_retry_batches": provider_retry_batches,
                            },
                        )
                        self.progress.emit(
                            "[Runtime] 模型服务持续不可用，项目已暂停并保留当前进度。",
                            important=True,
                        )
                        self._record_skill_progress(
                            ctx,
                            step=budget.steps,
                            step_limit=step_limit,
                            phase="waiting_runtime",
                            detail=error_msg,
                        )
                        self._refresh_t4_gate1_progress(ctx, active_path=None, paused_reason=error_msg)
                        provider_pause_requested = True
                        break
                    else:
                        break

                if provider_pause_requested:
                    break

                last_model_used = llm_resp.model_used
                last_endpoint_used = llm_resp.endpoint_used
                self._record_skill_progress(
                    ctx,
                    step=budget.steps,
                    step_limit=step_limit,
                    phase="llm_response_received",
                    detail="模型已返回；正在校验并执行声明的工具调用。",
                )
                budget.add_tokens(llm_resp.tokens_in, llm_resp.tokens_out, llm_resp.cost_usd)
                run_logger.event(
                    "LLM_RESULT",
                    task=ctx.task_id,
                    step=budget.steps,
                    model=llm_resp.model_used,
                    endpoint=llm_resp.endpoint_used,
                    tokens_in=llm_resp.tokens_in,
                    tokens_out=llm_resp.tokens_out,
                    duration_ms=llm_resp.duration_ms,
                )
                assistant_msg = self._parse_llm_response(llm_resp, step=budget.steps)
                trace.write_llm_response(llm_resp, assistant_msg)

                # 空回复不是立刻判死刑，而是先给模型一次 nudged retry 的机会。
                if is_empty_assistant(assistant_msg):
                    empty_count += 1
                    if empty_count > self.runtime_settings.agent_behavior.max_empty_reply:
                        stop_reason = AgentResult.STOP_ERROR
                        error_msg = f"{self.runtime_settings.agent_behavior.max_empty_reply} consecutive empty replies"
                        break
                    nudge = Message.user(
                        "你刚才没有输出任何内容也没有调用工具。请继续推进任务，或在确认完成后调用 finish_task。",
                        step=budget.steps,
                    )
                    messages.append(nudge)
                    trace.write_message(nudge)
                    continue

                empty_count = 0
                messages.append(assistant_msg)

                # 输出 Agent 的文本回复（如果有）。普通状态说明默认只在 verbose 显示；
                # 但同一轮如果要 ask_human，正文通常包含用户必须看到的草案、
                # 候选清单或决策上下文，不能被简洁模式吞掉。
                if assistant_msg.content and assistant_msg.content.strip():
                    self.progress.agent_markdown(
                        task_id=ctx.task_id,
                        agent=self.agent.spec.name,
                        content=assistant_msg.content,
                        human_action_context=any(tc.name == "ask_human" for tc in assistant_msg.tool_calls),
                        verbose_only=not any(tc.name == "ask_human" for tc in assistant_msg.tool_calls),
                    )

                post_tool_runtime_notes: list[Message] = []
                # 如果模型在文本里向用户提问/要求选择，但没有显式调用 ask_human，
                # runtime 必须先等待人类输入。即便同一轮还混有 read/write 等工具，
                # 也不能继续执行那些工具，否则会复现“模型问了但没有输入框仍继续跑”的问题。
                if self._looks_like_human_interaction_request(assistant_msg) and not any(
                    tc.name == "ask_human" for tc in assistant_msg.tool_calls
                ):
                    if self._maybe_complete_t8_resource_after_spurious_human_prompt(ctx):
                        stop_reason = AgentResult.STOP_FINISHED
                        error_msg = None
                        break
                    if "ask_human" not in tool_map:
                        trace.write_message(assistant_msg)
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = (
                            "Agent asked for human input but ask_human is not available in this task. "
                            "Paused so the user can answer or the task tool policy can be fixed."
                        )
                        break
                    tool_call = ToolCall.create(
                        "ask_human",
                        {
                            "question": self._build_autobridged_human_question(
                                assistant_msg.content or "请补充必要的人类输入。"
                            ),
                            "suggestions": [],
                        },
                    )
                    assistant_msg.tool_calls = [tool_call]
                    post_tool_runtime_notes.append(Message.user(
                        "[Runtime] 检测到 Agent 向用户提问/要求选择但未调用 ask_human，"
                        "已自动转成 ask_human，并阻止本轮其它工具继续执行；如果输入不可用将暂停等待 resume。",
                        step=budget.steps,
                    ))

                # 如果模型只说话不调用工具，runtime 会反复提醒它：
                # 要么继续推进，要么明确 finish_task。
                if not assistant_msg.tool_calls:
                    if not self._looks_like_human_interaction_request(assistant_msg):
                        nudge_count += 1
                        if nudge_count > self.runtime_settings.agent_behavior.max_nudge_finish:
                            trace.write_message(assistant_msg)
                            stop_reason = AgentResult.STOP_ERROR
                            error_msg = "agent 多次只输出文本但未调用工具"
                            break
                        nudge = Message.user(
                            "你没有调用任何工具。如果任务已完成，请调用 finish_task；否则请继续调用适当工具。",
                            step=budget.steps,
                        )
                        trace.write_message(assistant_msg)
                        messages.append(nudge)
                        trace.write_message(nudge)
                        continue

                nudge_count = 0
                self._ensure_ask_human_questions_are_self_contained(assistant_msg)
                if any(tc.name == "ask_human" for tc in assistant_msg.tool_calls):
                    ask_call = next(tc for tc in assistant_msg.tool_calls if tc.name == "ask_human")
                    blocked_tools = [tc.name for tc in assistant_msg.tool_calls if tc.name != "ask_human"]
                    if blocked_tools:
                        assistant_msg.tool_calls = [ask_call]
                        post_tool_runtime_notes.append(Message.user(
                            "[Runtime] 本轮包含 ask_human，已先等待用户输入；"
                            f"延后执行同轮其它工具: {', '.join(blocked_tools)}。",
                            step=budget.steps,
                        ))
                trace.write_message(assistant_msg)
                # 输出工具调用信息
                if len(assistant_msg.tool_calls) > 0:
                    tool_names = [tc.name for tc in assistant_msg.tool_calls]
                    if len(tool_names) > 1:
                        self._emit(
                            f"[{self.agent.spec.name} Agent] 本轮将按顺序处理 {len(tool_names)} 个工具调用："
                            f"{', '.join(tool_names)}",
                            verbose_only=True,
                        )
                    for tc in assistant_msg.tool_calls:
                        run_logger.tool_call(tc.name, tc.arguments, step=budget.steps)
                        self.progress.stage_tool_call(
                            task_id=ctx.task_id,
                            run_id=ctx.run_id,
                            tool_name=tc.name,
                            arguments=tc.arguments,
                        )
                        narrative = build_tool_narrative(
                            task_id=ctx.task_id,
                            agent=self.agent.spec.name,
                            tool_name=tc.name,
                            arguments=tc.arguments,
                            workspace_dir=ctx.workspace_dir,
                            verbose=self.runtime_settings.ui.verbose,
                        )
                        self.progress.tool_call(
                            agent=self.agent.spec.name,
                            tool_name=tc.name,
                            narrative=narrative,
                        )
                    if len(tool_names) == 1:
                        self._emit(
                            f"[{self.agent.spec.name} Agent] 正在调用工具：{tool_names[0]}",
                            verbose_only=True,
                        )

                # T4 Gate1 has ordered durable artifacts.  Executing its calls
                # one by one makes both artifact dependencies and the CLI
                # progress truthful.  Other tasks retain parallel tool calls.
                if self._requires_sequential_tool_execution(ctx, assistant_msg.tool_calls):
                    tool_msgs = []
                    for tc in assistant_msg.tool_calls:
                        self._record_skill_progress(
                            ctx,
                            step=budget.steps,
                            step_limit=step_limit,
                            phase="tool_running",
                            tool_name=tc.name,
                            detail=f"正在执行工具 {tc.name}。",
                        )
                        tool_msgs.append(
                            await self._execute_one_tool_call(
                                tc,
                                tool_map,
                                ctx=ctx,
                                policy=policy,
                                budget=budget,
                                step=budget.steps,
                                tool_failure_cache=tool_failure_cache,
                                run_logger=run_logger,
                                skill_tool_budget=skill_tool_budget,
                            )
                        )
                else:
                    for tc in assistant_msg.tool_calls:
                        self._record_skill_progress(
                            ctx,
                            step=budget.steps,
                            step_limit=step_limit,
                            phase="tool_running",
                            tool_name=tc.name,
                            detail=f"正在调度工具 {tc.name}。",
                        )
                    tool_msgs = await asyncio.gather(
                        *[
                            self._execute_one_tool_call(
                                tc,
                                tool_map,
                                ctx=ctx,
                                policy=policy,
                                budget=budget,
                                step=budget.steps,
                                tool_failure_cache=tool_failure_cache,
                                run_logger=run_logger,
                                skill_tool_budget=skill_tool_budget,
                            )
                            for tc in assistant_msg.tool_calls
                        ]
                    )

                finish_requested = False
                pause_requested = False
                pause_reason: str | None = None
                pause_tool_name: str | None = None
                pause_tool_data: dict[str, object] = {}
                t45_checkpoint_feedback: list[Message] = []
                for tool_call, tool_msg in zip(assistant_msg.tool_calls, tool_msgs):
                    messages.append(tool_msg)
                    trace.write_message(tool_msg)
                    tool_ok = not bool(tool_msg.metadata.get("is_error"))
                    tool_data = (
                        tool_msg.metadata.get("data")
                        if isinstance(tool_msg.metadata, dict)
                        and isinstance(tool_msg.metadata.get("data"), dict)
                        else {}
                    )
                    # Read-only validation can execute correctly while
                    # reporting a non-passing research contract.  Keep that
                    # diagnostic non-error for the model, but render it as a
                    # failed checkpoint so the terminal no longer claims a
                    # green success for an invalid T4.5 package.
                    display_ok = tool_ok and not (
                        str(tool_data.get("display_disposition") or "").casefold() == "validation_failed"
                        or (
                            tool_call.name in {"validate_t45_formalization_sources", "validate_t45_research_package"}
                            and tool_data.get("valid") is False
                        )
                    )
                    tool_summary, output_path = summarize_tool_result(
                        tool_name=tool_call.name,
                        ok=tool_ok,
                        content=tool_msg.content,
                        data=tool_data,
                        error=tool_msg.metadata.get("error") if isinstance(tool_msg.metadata, dict) else None,
                        metadata=tool_msg.metadata if isinstance(tool_msg.metadata, dict) else {},
                        verbose=self.runtime_settings.ui.verbose,
                    )
                    tool_error = (
                        tool_msg.metadata.get("error")
                        if isinstance(tool_msg.metadata, dict)
                        else None
                    )
                    self.progress.stage_tool_result(
                        task_id=ctx.task_id,
                        run_id=ctx.run_id,
                        tool_name=tool_call.name,
                        ok=display_ok,
                        data=tool_data,
                        error=str(tool_error) if tool_error else None,
                    )
                    self.progress.tool_result(
                        agent=self.agent.spec.name,
                        tool_name=tool_call.name,
                        ok=display_ok,
                        result_summary=tool_summary,
                        output_path=safe_relative(output_path, ctx.workspace_dir) or output_path,
                        next_step=next_step_for_task(ctx.task_id, ok=display_ok) if not display_ok else None,
                        duration_ms=tool_msg.duration_ms,
                        data=tool_data,
                        error=str(tool_error) if tool_error else None,
                    )
                    self._record_skill_progress(
                        ctx,
                        step=budget.steps,
                        step_limit=step_limit,
                        phase="tool_completed" if display_ok else "tool_failed",
                        tool_name=tool_call.name,
                        detail=("工具完成：" if display_ok else "校验未通过：") + tool_summary,
                    )
                    if (
                        ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}
                        and tool_call.name in {"validate_t45_formalization_sources", "validate_t45_research_package"}
                        and tool_data.get("valid") is False
                    ):
                        t45_checkpoint_feedback.append(
                            Message.user(
                                self._t45_checkpoint_repair_feedback(
                                    tool_name=tool_call.name,
                                    data=tool_data,
                                    fallback_error=str(tool_msg.content or "T4.5 checkpoint did not pass"),
                                ),
                                step=budget.steps,
                            )
                        )
                    if self._is_t4_ideation_agent(ctx) and tool_call.name in {"write_file", "write_structured_file", "append_file"}:
                        # The tool result itself already announces the durable
                        # write. Refresh the on-disk checkpoint silently so a
                        # second, misleading "0/6" line is never printed.
                        self._refresh_t4_gate1_progress(
                            ctx,
                            active_path=output_path if tool_ok else None,
                            announce=False,
                        )
                        if tool_ok:
                            self._emit_t4_durable_candidate_recap(ctx, output_path)
                    if self._is_t4_ideation_agent(ctx) and tool_call.name == "log_t4_ideation_progress" and tool_ok:
                        self._update_t4_public_activity_from_event(ctx, tool_data)
                        # Candidate milestones and Gate1 artifact checkpoints
                        # measure different things. The candidate card above is
                        # sufficient here; retain only the durable state for
                        # the next heartbeat.
                        self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
                    if (
                        ctx.task_id == "T2"
                        and tool_ok
                        and self._is_t2_raw_pool_read(tool_call, tool_data)
                    ):
                        checkpoint = self._t2_raw_pool_checkpoint_message(
                            ctx=ctx,
                            tool_data=tool_data,
                            step=budget.steps,
                        )
                        if checkpoint is not None:
                            post_tool_runtime_notes.append(checkpoint)
                    if tool_call.name == "finish_task" and not tool_msg.metadata.get("is_error"):
                        finish_requested = True
                    if (
                        ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}
                        and tool_call.name == "write_structured_file"
                    ):
                        failure_kind = str(tool_error or "")
                        structured_error = failure_kind in {"schema_validation_failed", "parameter_validation"}
                        path = str(tool_data.get("path") or tool_call.arguments.get("path") or "").strip()
                        schema = str(tool_data.get("schema_name") or tool_call.arguments.get("schema_name") or "").strip()
                        key = (path, schema, failure_kind)
                        if structured_error and path and schema:
                            diagnostics = tool_data.get("schema_errors")
                            # A schema failure can be a stable model-content
                            # error.  A malformed native tool JSON is not: it
                            # may be an endpoint completion cut-off, and its
                            # exact raw shape is meaningful.  Do not classify
                            # every parse error as the same failure and pause
                            # after two attempts before the model has seen a
                            # useful diagnosis.
                            raw_arguments = str(tool_call.arguments.get("__raw__") or "")
                            signature = json.dumps(
                                diagnostics
                                if failure_kind == "schema_validation_failed"
                                else {
                                    # Do not include raw length here. Providers
                                    # often truncate the same call at slightly
                                    # different byte offsets; using the length
                                    # as identity defeated the circuit breaker
                                    # and allowed an effectively infinite loop.
                                    "json_parse_failed": bool(tool_call.arguments.get("__parse_error__")),
                                    "open_curly": raw_arguments.count("{"),
                                    "close_curly": raw_arguments.count("}"),
                                    "open_square": raw_arguments.count("["),
                                    "close_square": raw_arguments.count("]"),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            )
                            previous_signature, previous_count = t45_schema_failure_state.get(key, ("", 0))
                            repeated_count = previous_count + 1 if signature == previous_signature else 1
                            t45_schema_failure_state[key] = (signature, repeated_count)
                            # Give the model a real, explicit repair chance.
                            # We only interrupt after repeated *identical*
                            # malformed calls have made no structural progress.
                            # This is a circuit breaker for a broken endpoint,
                            # not a substitute for model-authored repair.
                            pause_threshold = 4 if failure_kind == "parameter_validation" else 2
                            if repeated_count >= pause_threshold:
                                failure_label = (
                                    "Schema 错误"
                                    if failure_kind == "schema_validation_failed"
                                    else "工具参数 JSON 解析错误"
                                )
                                pause_requested = True
                                pause_tool_name = tool_call.name
                                pause_tool_data = {
                                    "path": path,
                                    "schema_name": schema,
                                    "schema_errors": diagnostics,
                                    "repeated_identical_failure_count": repeated_count,
                                    "json_structure": {
                                        "open_curly": raw_arguments.count("{"),
                                        "close_curly": raw_arguments.count("}"),
                                        "open_square": raw_arguments.count("["),
                                        "close_square": raw_arguments.count("]"),
                                    },
                                }
                                pause_reason = (
                                    f"{path} 连续两次产生相同的 {failure_label}（相同结构，累计 {repeated_count} 次），runtime 已暂停以避免继续重复生成和消耗额度。"
                                    "已保存此前工具结果；resume 后应依据列出的精确字段结构修复同一文件。"
                                )
                        elif path and schema:
                            for failure_key in list(t45_schema_failure_state):
                                if failure_key[:2] == (path, schema):
                                    t45_schema_failure_state.pop(failure_key, None)
                    if self._is_recoverable_tool_pause(tool_call.name, tool_msg):
                        pause_requested = True
                        pause_reason = tool_msg.content or "需要用户输入，但当前输入不可用。"
                        pause_tool_name = tool_call.name
                        raw_pause_data = tool_msg.metadata.get("data")
                        pause_tool_data = dict(raw_pause_data) if isinstance(raw_pause_data, dict) else {}
                for feedback in t45_checkpoint_feedback:
                    messages.append(feedback)
                    trace.write_message(feedback)
                for note in post_tool_runtime_notes:
                    messages.append(note)
                    trace.write_message(note)

                if pause_requested:
                    stop_reason = AgentResult.STOP_INTERRUPTED
                    error_msg = pause_reason
                    recovery_kind = "human_input" if pause_tool_name == "ask_human" else "environment"
                    if pause_tool_name == "write_structured_file":
                        recovery_kind = "structured_schema"
                    recovery_details: dict[str, object] = {"tool_name": pause_tool_name or "unknown"}
                    if pause_tool_name == "expand_corpus_for_survey":
                        recovery_kind = "survey_retrieval"
                        # This is a checkpointed multi-query operation. Keep
                        # progress on the recovery boundary so resume can
                        # continue it without making the model retry blindly.
                        for key in (
                            "checkpoint_path",
                            "checkpoint_available",
                            "status",
                            "phase",
                            "completed_query_count",
                            "query_count",
                            "retrieved_record_count",
                        ):
                            if key in pause_tool_data:
                                recovery_details[key] = pause_tool_data[key]
                    self._mark_runtime_recovery(
                        ctx,
                        kind=recovery_kind,
                        error=error_msg,
                        details=recovery_details,
                    )
                    self.progress.emit(f"[Runtime] 当前任务暂停：{pause_reason}", important=True)
                    break

                if finish_requested:
                    # finish_task 只是“请求结束”而不是直接结束。
                    # 真正能否成功结束，仍以 validate_outputs 为准。
                    run_logger.event("FINISH_REQUESTED", task=ctx.task_id, step=budget.steps)
                    if ctx.task_id == "T2":
                        run_logger.event("FINALIZE_STARTED", task=ctx.task_id, mode="t2_finish_finalize")
                        await self._finalize_t2_from_raw(
                            ctx,
                            mode="t2_finish_finalize",
                            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
                            start_message="[Scout Agent] T2 收到 finish_task，先基于 papers_raw 执行确定性收尾...",
                            success_message="[Scout Agent] T2 确定性收尾成功，继续校验输出",
                        )
                        run_logger.event("FINALIZE_DONE", task=ctx.task_id, mode="t2_finish_finalize")
                    t3_continuation = self._t3_finish_preflight(ctx)
                    if t3_continuation is not None:
                        preflight = ctx.extra.get("t3_finish_preflight")
                        run_logger.event(
                            "T3_FINISH_DEFERRED",
                            task=ctx.task_id,
                            step=budget.steps,
                            completed=(preflight or {}).get("completed") if isinstance(preflight, dict) else None,
                            required=(preflight or {}).get("required") if isinstance(preflight, dict) else None,
                            pending=(preflight or {}).get("pending") if isinstance(preflight, dict) else None,
                        )
                        messages.append(t3_continuation)
                        trace.write_message(t3_continuation)
                        continue
                    self.progress.validation_start(task_id=ctx.task_id)
                    # T3's abstract sweep is a deterministic post-read
                    # operation.  Let the deep-read validator complete this
                    # turn, then validate the exact shallow-reading manifest
                    # after the sweep has either fulfilled or blocked the
                    # requested coverage target.
                    if ctx.task_id == "T3":
                        ctx.extra["_t3_pending_abstract_sweep"] = True
                    if ctx.task_id == "T3.6-SUPPLEMENT-READ":
                        # The receipt is an auditable projection of this small
                        # queue. Reconcile only at an explicit finish request:
                        # a missing skipped row then cannot cause a
                        # validation-only LLM loop. This helper never invents
                        # an upgrade: that outcome still requires a validated
                        # FULL/PARTIAL reading note.
                        try:
                            from ..agents._common import load_jsonl
                            from ..agents.reader import ReaderAgent

                            queue_path = (
                                ctx.workspace_dir
                                / "literature"
                                / "survey_supplement"
                                / "reading_upgrade_queue.jsonl"
                            )
                            queue = load_jsonl(queue_path) if queue_path.exists() else []
                            reconciled = ReaderAgent._reconcile_supplement_receipt(
                                ctx.workspace_dir,
                                queue,
                            )
                            if reconciled:
                                self.progress.emit(
                                    "[Reader Agent] 补读回执已由 runtime 对账补齐 "
                                    f"{reconciled} 条遗漏处置；未形成 FULL/PARTIAL note 的论文已明确记录为 skipped。",
                                    important=True,
                                )
                        except (OSError, ValueError, TypeError) as exc:
                            # Keep the normal validator authoritative: a real
                            # parse or filesystem fault must remain visible and
                            # cannot become a false successful completion.
                            self.log.warning("t36_supplement_receipt_reconcile_failed", error=str(exc))
                    try:
                        ok, err = self.agent.validate_outputs(ctx)
                    finally:
                        ctx.extra.pop("_t3_pending_abstract_sweep", None)
                    semantic_adjudication_feedback = ""
                    if not ok:
                        adjudication = await self._maybe_adjudicate_t45_semantic_failure(
                            ctx=ctx,
                            eff=eff,
                            budget=budget,
                            error=str(err or "unknown validation error"),
                            run_logger=run_logger,
                        )
                        semantic_adjudication_feedback = str(adjudication.get("feedback") or "")
                        if adjudication.get("accepted"):
                            # The receipt is hash-bound and the Agent's normal
                            # validator still runs every non-overridden hard
                            # rule. A newly exposed error remains a repair
                            # target; one finish request gets at most one LLM
                            # semantic adjudication.
                            ok, err = self.agent.validate_outputs(ctx)
                    if not ok:
                        adjudication = await self._maybe_adjudicate_t36_semantic_failure(
                            ctx=ctx,
                            eff=eff,
                            budget=budget,
                            error=str(err or "unknown validation error"),
                            run_logger=run_logger,
                        )
                        semantic_adjudication_feedback += str(adjudication.get("feedback") or "")
                        if adjudication.get("accepted"):
                            # T3.6 receives the same independent, hash-bound
                            # revalidation discipline as T4.5.  A receipt can
                            # only affect its one prose check; every hard
                            # source, citation, TeX, and compile check runs
                            # again immediately.
                            ok, err = self.agent.validate_outputs(ctx)
                    if ok:
                        self.progress.validation_result(task_id=ctx.task_id, ok=True)
                        run_logger.event("VALIDATION_PASS", task=ctx.task_id, step=budget.steps)
                        stop_reason = AgentResult.STOP_FINISHED
                        break
                    validation_fails += 1
                    repeated_validation_failures = self._record_validation_failure(
                        ctx,
                        str(err or "unknown validation error"),
                    )
                    t45_quality_repair = self._uses_t45_quality_repair_loop(ctx)
                    t36_quality_repair = self._uses_t36_quality_repair_loop(ctx)
                    t45_repairable_warning = self._is_t45_repairable_warning(err)
                    if not t45_quality_repair and not t36_quality_repair:
                        self.progress.validation_result(
                            task_id=ctx.task_id,
                            ok=False,
                            error=str(err or "unknown validation error"),
                            failure_count=validation_fails,
                            retry_limit=validation_retry_limit,
                        )
                    run_logger.event(
                        "VALIDATION_FAILED",
                        task=ctx.task_id,
                        step=budget.steps,
                        failure=validation_fails,
                        limit=validation_retry_limit,
                        reason=err,
                    )
                    if t36_quality_repair:
                        # Survey writing can require several evidence-bounded
                        # source repairs. A numeric retry cap used to stop the
                        # worker just as an audit had finally identified the
                        # affected section. Continue only while the relevant
                        # source inputs change; an unchanged repeat pauses
                        # instead of silently spinning.
                        no_source_progress = self._record_t36_quality_repair_attempt(
                            ctx=ctx,
                            error=str(err or "unknown validation error"),
                        )
                        if no_source_progress:
                            stop_reason = AgentResult.STOP_INTERRUPTED
                            error_msg = (
                                "T3.6 质量校验在收到定向修复说明后再次出现同一诊断，"
                                "但相关 survey source artifacts 没有变化。"
                                f"最后原因：{err}。系统已暂停以避免无修改的 finish_task 循环；"
                                "恢复后会把该诊断和最小修复范围再次交给 Survey Writer。"
                            )
                            self.progress.emit(
                                "[T3.6 Quality Gate] 同一诊断未伴随相关 source 修改，已暂停以避免无声循环；"
                                "保留 sections、audit 和定向修复原因。",
                                important=True,
                            )
                            self._mark_runtime_recovery(
                                ctx,
                                kind="t36_quality_no_source_progress",
                                error=error_msg,
                                details={
                                    "failure_count": validation_fails,
                                    "repair_attempt_count": int(ctx.extra.get("t36_quality_repair_attempt_count") or validation_fails),
                                    "validator_error": str(err or "unknown validation error"),
                                    "repair_policy": "targeted_with_source_progress",
                                    "source_artifacts": list(T36_QUALITY_SOURCE_ARTIFACTS),
                                },
                            )
                            break
                        self.progress.validation_result(
                            task_id=ctx.task_id,
                            ok=False,
                            error=str(err or "unknown validation error"),
                        )
                        self.progress.emit(
                            "[T3.6 Quality Gate] 第 "
                            f"{validation_fails} 次校验未通过；已把具体原因和最小修复范围注入 Survey Writer，"
                            "修复后会重新运行全部质量校验；若同一诊断且相关来源未变化，才会暂停防止空转。",
                            important=True,
                        )
                        feedback = Message.user(
                            self._validation_repair_feedback(
                                ctx=ctx,
                                error=str(err or "unknown validation error"),
                            )
                            + semantic_adjudication_feedback,
                            step=budget.steps,
                        )
                        messages.append(feedback)
                        trace.write_message(feedback)
                        continue
                    if t45_quality_repair:
                        # T4.5 is a source-first formalization workflow.  A
                        # generic retry counter makes it stop exactly when the
                        # validator has supplied the most useful diagnosis for
                        # a targeted source-artifact repair.  Continue while
                        # the model is making real source changes; only pause
                        # when it asks for another validation without changing
                        # anything for the same diagnosis.
                        no_source_progress = self._record_t45_quality_repair_attempt(
                            ctx=ctx,
                            error=str(err or "unknown validation error"),
                        )
                        if not t45_repairable_warning:
                            self.progress.validation_result(
                                task_id=ctx.task_id,
                                ok=False,
                                error=str(err or "unknown validation error"),
                            )
                        if no_source_progress:
                            stop_reason = AgentResult.STOP_INTERRUPTED
                            if t45_repairable_warning:
                                error_msg = (
                                    "T4.5 内部质量修订在收到定向指导后未产生新的来源修改，已暂停以避免无声循环。"
                                    "恢复后会继续将内部质量目标交给 Formalizer，不会跳过 T4.5 或进入 T5。"
                                )
                            else:
                                error_msg = (
                                    "T4.5 质量 Gate 的同一错误在收到定向修复说明后再次出现，"
                                    "但 blueprint、claim registry、实验计划、假设、proposal、review 等源产物没有变化。"
                                    f"最后原因：{err}。系统未按固定次数放弃，而是暂停以避免无修改的 finish_task 循环；"
                                    "恢复后将再次把该诊断交给 Formalizer 定向修复。"
                                )
                            self.progress.emit(
                                "[T4.5 Quality Gate] 同一诊断未伴随任何源产物修改，已暂停以避免无声循环；"
                                "保留全部产物和定向修复原因。",
                                important=True,
                            )
                            self._mark_runtime_recovery(
                                ctx,
                                kind="t45_quality_no_source_progress",
                                error=error_msg,
                                details={
                                    "failure_count": validation_fails,
                                    "validator_error": str(err or "unknown validation error"),
                                    "repairable_warning": t45_repairable_warning,
                                    "repair_policy": "targeted_with_source_progress",
                                    "source_artifacts": list(T45_QUALITY_SOURCE_ARTIFACTS),
                                },
                            )
                            break
                        repair_attempts = int(ctx.extra.get("t45_quality_repair_attempt_count") or validation_fails)
                        if not t45_repairable_warning:
                            self.progress.emit(
                                "[T4.5 Quality Gate] 第 "
                                f"{repair_attempts} 次定向校验未通过；已把具体原因和最小修复范围注入 Formalizer，"
                                "修复后会重新运行全部质量校验；若同一诊断且相关来源未变化，才会暂停防止空转。",
                                important=True,
                            )
                        feedback = Message.user(
                            self._validation_repair_feedback(
                                ctx=ctx,
                                error=str(err or "unknown validation error"),
                            )
                            + semantic_adjudication_feedback,
                            step=budget.steps,
                        )
                        messages.append(feedback)
                        trace.write_message(feedback)
                        continue
                    if validation_fails >= validation_retry_limit:
                        (
                            extended,
                            validation_retry_limit,
                            validation_extensions_used,
                        ) = await self._maybe_offer_validation_retry_extension(
                            ctx=ctx,
                            budget=budget,
                            last_error=str(err or "unknown validation error"),
                            failures=validation_fails,
                            retry_limit=validation_retry_limit,
                            used_extensions=validation_extensions_used,
                        )
                        if extended:
                            # A user explicitly approved another repair window.
                            # It is a new decision, so previous identical-error
                            # counts must not suppress the newly granted attempt.
                            ctx.extra.pop("last_validation_error", None)
                            ctx.extra.pop("same_validation_error_count", None)
                            run_logger.event(
                                "VALIDATION_RETRY",
                                task=ctx.task_id,
                                step=budget.steps,
                                failure=validation_fails,
                                new_limit=validation_retry_limit,
                            )
                            feedback = Message.user(
                                self._validation_repair_feedback(
                                    ctx=ctx,
                                    error=str(err or "unknown validation error"),
                                    resumed_after_extension=True,
                                ),
                                step=budget.steps,
                            )
                            messages.append(feedback)
                            trace.write_message(feedback)
                            continue
                        # All ordinary validation repairs receive the same
                        # configured window.  T3 queue completion is handled
                        # above by a deterministic no-counter preflight; this
                        # circuit only applies to errors that still require
                        # model judgment or a real artifact change.
                        validation_circuit_limit = validation_retry_limit
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        if repeated_validation_failures >= validation_circuit_limit:
                            error_msg = (
                                f"同一输出校验问题连续出现 {validation_circuit_limit} 次，已停止重复修复并保留当前产物。"
                                f"最后原因：{err}。请按该原因修复对应文件后再恢复运行。"
                            )
                            self.progress.emit(
                                "[Validation] 同一问题再次出现，已暂停并保留当前结果；"
                                "不会继续重复执行相同修复。",
                                important=True,
                            )
                        else:
                            error_msg = (
                                f"Validation failed {validation_fails} times. "
                                f"Paused for artifact repair/resume. Last reason: {err}"
                            )
                        self._mark_runtime_recovery(
                            ctx,
                            kind="validation",
                            error=error_msg,
                            details={
                                "failure_count": validation_fails,
                                "retry_limit": validation_retry_limit,
                                "same_error_count": repeated_validation_failures,
                                "validator_error": str(err or "unknown validation error"),
                                "extensions_used": validation_extensions_used,
                            },
                        )
                        break
                    validation_circuit_limit = validation_retry_limit
                    if repeated_validation_failures >= validation_circuit_limit:
                        stop_reason = AgentResult.STOP_INTERRUPTED
                        error_msg = (
                            f"同一输出校验问题连续出现 {validation_circuit_limit} 次，已停止重复修复并保留当前产物。"
                            f"最后原因：{err}。请按该原因修复对应文件后再恢复运行。"
                        )
                        self.progress.emit(
                            "[Validation] 同一问题再次出现，已暂停并保留当前结果；"
                            "不会继续重复执行相同修复。",
                            important=True,
                        )
                        self._mark_runtime_recovery(
                            ctx,
                            kind="validation",
                            error=error_msg,
                            details={
                                "failure_count": validation_fails,
                                "retry_limit": validation_retry_limit,
                                "same_error_count": repeated_validation_failures,
                                "validator_error": str(err or "unknown validation error"),
                                "extensions_used": validation_extensions_used,
                            },
                        )
                        break
                    feedback = Message.user(
                        self._validation_repair_feedback(
                            ctx=ctx,
                            error=str(err or "unknown validation error"),
                        ),
                        step=budget.steps,
                    )
                    messages.append(feedback)
                    trace.write_message(feedback)

                if not budget.unlimited_budget and budget.steps >= budget.max_steps:
                    extended, budget_extensions_used = await self._maybe_offer_budget_extension(
                        ctx=ctx,
                        budget=budget,
                        exc=BudgetExceeded("steps", budget.max_steps, budget.steps),
                        used_extensions=budget_extensions_used,
                    )
                    if extended:
                        continue
                    stop_reason = AgentResult.STOP_MAX_STEPS
                    error_msg = "Reached maximum allowed steps; paused so you can resume or raise the step budget."
                    self._mark_runtime_recovery(
                        ctx,
                        kind="max_steps",
                        error=error_msg,
                        details={
                            "dimension": "steps",
                            "used": budget.steps,
                            "limit": budget.max_steps,
                            "extensions_used": budget_extensions_used,
                        },
                    )
                    break

        except asyncio.CancelledError:
            stop_reason = AgentResult.STOP_INTERRUPTED
            error_msg = "Cancelled"
            run_logger.event("PAUSED", task=ctx.task_id, reason=error_msg)
        except RecoverableRuntimePause as exc:
            stop_reason = AgentResult.STOP_INTERRUPTED
            error_msg = str(exc)
            self._mark_runtime_recovery(
                ctx,
                kind="runtime",
                error=error_msg,
                details={"source": "recoverable_runtime_pause"},
            )
            run_logger.event("PAUSED", task=ctx.task_id, reason=error_msg)
        except HookExecutionError as exc:
            stop_reason = AgentResult.STOP_ERROR
            error_msg = str(exc)
            run_logger.event("ERROR", task=ctx.task_id, kind="hook", message=error_msg)
        except Exception as exc:  # pragma: no cover - safety net
            stop_reason = AgentResult.STOP_ERROR
            error_msg = f"Unexpected: {exc!r}"
            self.log.exception("agent_runner_crashed")
            run_logger.event("ERROR", task=ctx.task_id, kind="runner_crash", message=error_msg)
        finally:
            stop_reason, error_msg = await self._maybe_finalize_t2_outputs(
                ctx=ctx,
                stop_reason=stop_reason,
                error_msg=error_msg,
            )
            stop_reason, error_msg = await self._maybe_finalize_t5_reboost_outputs(
                ctx=ctx,
                policy=policy,
                stop_reason=stop_reason,
                error_msg=error_msg,
                steps=budget.steps,
                llm_response_seen=last_model_used is not None,
            )
            stop_reason, error_msg = self._maybe_finalize_t4_gate1_outputs(
                ctx=ctx,
                stop_reason=stop_reason,
                error_msg=error_msg,
            )
            self._refresh_resume_artifacts(ctx)
            self._maybe_refresh_t3_resume_artifacts(ctx, stop_reason)
            stop_reason, error_msg = await self._maybe_run_t3_abstract_sweep(
                ctx,
                stop_reason,
                error_msg,
                eff,
            )
            result = self._build_result(
                ctx=ctx,
                budget=budget,
                stop_reason=stop_reason,
                error_msg=error_msg,
                started=started,
                trace_file=trace_file,
                eff=eff,
                last_model_used=last_model_used,
                last_endpoint_used=last_endpoint_used,
            )
            for hook in self.agent.spec.post_hooks:
                try:
                    await self._run_post_hook(hook, ctx, result)
                except Exception:  # pragma: no cover - logging path
                    self.log.exception("post_hook_failed")
            trace.close(result)
            self.progress.agent_done(
                task_id=ctx.task_id,
                agent=self.agent.spec.name,
                ok=result.ok,
                stop_reason=result.stop_reason,
                summary=result.message,
                artifacts=[
                    safe_relative(path, ctx.workspace_dir) or str(path)
                    for path in list(result.outputs_produced.values())
                ],
                next_step=next_step_for_task(ctx.task_id, ok=result.ok) if not result.ok else None,
                trace_file=str(result.trace_file.relative_to(ctx.workspace_dir))
                if result.trace_file is not None
                else None,
                error=result.error,
                outputs_expected=ctx.outputs_expected,
                run_id=ctx.run_id,
            )
            run_logger.event(
                "TASK_END",
                task=ctx.task_id,
                ok=result.ok,
                stop_reason=result.stop_reason,
                error=result.error,
                steps=result.steps_used,
                tokens=result.tokens_in + result.tokens_out,
            )
            run_logger.event(
                "RUN_END",
                run_id=ctx.run_id,
                task=ctx.task_id,
                ok=result.ok,
                stop_reason=result.stop_reason,
            )
        return result

    def _finish_agent_startup_interruption(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        eff: EffectiveConfig,
        started: float,
        trace: TraceWriter | NullTraceWriter,
        trace_file: Path | None,
        run_logger: RunLogger,
        error: str,
        exception_type: str,
        recovery: bool,
    ) -> AgentResult:
        """Return a durable interruption for failures before the Agent loop.

        Prompt rendering, context discovery, and tool construction used to run
        before ``AgentRunner.run`` entered its ordinary exception boundary.
        A Jinja undefined variable could therefore escape as a CLI traceback.
        Startup is part of one Agent run, so it must produce the same explicit
        recovery signal, trace closure, and stage-end event as a later failure.
        """

        stop_reason = AgentResult.STOP_INTERRUPTED
        if recovery:
            self._mark_runtime_recovery(
                ctx,
                kind="runtime",
                error=error,
                details={
                    "source": "agent_runner_startup",
                    "exception_type": exception_type,
                },
            )
            run_logger.event(
                "PAUSED",
                task=ctx.task_id,
                reason=error,
                source="agent_runner_startup",
                exception_type=exception_type,
            )
        else:
            run_logger.event(
                "PAUSED",
                task=ctx.task_id,
                reason=error,
                source="agent_runner_startup",
                exception_type=exception_type,
            )
        result = self._build_result(
            ctx=ctx,
            budget=budget,
            stop_reason=stop_reason,
            error_msg=error,
            started=started,
            trace_file=trace_file,
            eff=eff,
            last_model_used=None,
            last_endpoint_used=None,
        )
        try:
            trace.close(result)
        except Exception:  # pragma: no cover - trace output must not mask recovery
            self.log.exception("startup_failure_trace_close_failed")
        try:
            self.progress.agent_done(
                task_id=ctx.task_id,
                agent=self.agent.spec.name,
                ok=False,
                stop_reason=result.stop_reason,
                summary=result.message,
                artifacts=[
                    safe_relative(path, ctx.workspace_dir) or str(path)
                    for path in list(result.outputs_produced.values())
                ],
                next_step=next_step_for_task(ctx.task_id, ok=False),
                trace_file=str(result.trace_file.relative_to(ctx.workspace_dir))
                if result.trace_file is not None
                else None,
                error=result.error,
                outputs_expected=ctx.outputs_expected,
                run_id=ctx.run_id,
            )
        except Exception:  # pragma: no cover - observability cannot re-raise a startup failure
            self.log.exception("startup_failure_progress_emit_failed")
        run_logger.event(
            "TASK_END",
            task=ctx.task_id,
            ok=False,
            stop_reason=result.stop_reason,
            error=result.error,
            steps=result.steps_used,
            tokens=result.tokens_in + result.tokens_out,
        )
        run_logger.event(
            "RUN_END",
            run_id=ctx.run_id,
            task=ctx.task_id,
            ok=False,
            stop_reason=result.stop_reason,
        )
        return result

    async def _run_pre_hook(self, hook, ctx: ExecutionContext) -> None:
        """兼容同步/异步 pre-hook，并解释常见返回值。"""
        result = hook(ctx)
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, tuple) and len(result) == 2:
            ok, message = result
            if not ok:
                text = str(message or f"Pre-hook failed: {hook.__name__}")
                if "WAITING_ENVIRONMENT" in text or "环境不可用" in text:
                    raise RecoverableRuntimePause(text)
                raise HookExecutionError(text)
            return

        if result is False:
            raise HookExecutionError(f"Pre-hook failed: {hook.__name__}")

    def _print_task_start_summary(self, ctx: ExecutionContext, eff: EffectiveConfig) -> None:
        """Print a human-readable one-line task summary before LLM work."""

        phase = ctx.mode or ctx.extra.get("phase") or "-"
        description = str(ctx.extra.get("task_description") or self._infer_task_description(ctx))
        expected = [
            str(path.relative_to(ctx.workspace_dir))
            for path in list(ctx.outputs_expected.values())[:5]
        ]
        if len(ctx.outputs_expected) > 5:
            expected.append(f"...(+{len(ctx.outputs_expected) - 5})")
        separator = self._centered_separator(f"{ctx.task_id} | {self.agent.spec.name}", width=80)
        self._emit(
            f"\n{separator}\n"
            f"[{self.agent.spec.name} Agent] 初始化完成 | "
            f"任务: {ctx.task_id} | 阶段: {phase} | "
            f"目标: {description} | 输出: {', '.join(expected) if expected else '未声明'} | "
            "LLM: 当前全局配置\n"
            f"{'=' * len(separator)}",
            verbose_only=True,
        )

    @staticmethod
    def _requires_sequential_tool_execution(ctx: ExecutionContext, tool_calls: list[ToolCall]) -> bool:
        """Return whether this response has order-sensitive durable writes."""

        # Multiple compiler invocations compete for LaTeX auxiliary files and
        # make a failed child process difficult to diagnose in the CLI.  Keep
        # them serial even outside T4; other independent tool calls can remain
        # concurrent.
        if sum(call.name == "latex_compile" for call in tool_calls) > 1:
            return True
        # A Skill-owned tool budget is stateful across one run. Execute its
        # calls serially so two calls in the same model response cannot race
        # past a shared discovery/lookup cap.
        if isinstance(ctx.extra.get("skill_tool_call_budget"), dict) and tool_calls:
            return True
        return ctx.task_id == "T4" and bool(tool_calls)

    @staticmethod
    def _new_skill_tool_budget_state(ctx: ExecutionContext) -> dict[str, object] | None:
        """Build mutable counters for an already-validated Skill budget.

        Skill metadata is validated at discovery. This defensive parser keeps
        programmatic callers harmless if they construct an ExecutionContext by
        hand: malformed optional limits simply do not alter normal tool access.
        """

        raw = ctx.extra.get("skill_tool_call_budget")
        if not isinstance(raw, dict):
            return None
        raw_per_tool = raw.get("per_tool")
        per_tool = {
            str(name): int(limit)
            for name, limit in raw_per_tool.items()
            if isinstance(name, str) and isinstance(limit, int) and not isinstance(limit, bool) and limit > 0
        } if isinstance(raw_per_tool, dict) else {}
        groups: list[dict[str, object]] = []
        raw_groups = raw.get("groups")
        if isinstance(raw_groups, list):
            for raw_group in raw_groups:
                if not isinstance(raw_group, dict):
                    continue
                label = str(raw_group.get("label") or "").strip()
                raw_tools = raw_group.get("tools")
                raw_limit = raw_group.get("max_calls")
                tools = tuple(
                    str(name).strip()
                    for name in raw_tools
                    if isinstance(name, str) and str(name).strip()
                ) if isinstance(raw_tools, list) else ()
                if label and tools and isinstance(raw_limit, int) and not isinstance(raw_limit, bool) and raw_limit > 0:
                    groups.append({"label": label, "tools": tools, "max_calls": raw_limit})
        if not per_tool and not groups:
            # A rate-limit boundary can be useful without a numeric call cap.
            # Keep building state when that independent policy is declared.
            if not bool(raw.get("stop_remote_on_rate_limit")):
                return None
        raw_remote_tools = raw.get("remote_tools")
        remote_tools = tuple(
            str(name).strip()
            for name in raw_remote_tools
            if isinstance(name, str) and str(name).strip()
        ) if isinstance(raw_remote_tools, list) else ()
        return {
            "per_tool": per_tool,
            "groups": groups,
            "tool_counts": {},
            "group_counts": {},
            "stop_remote_on_rate_limit": bool(raw.get("stop_remote_on_rate_limit")),
            "remote_tools": remote_tools,
            "remote_stop": {},
        }

    @staticmethod
    def _consume_skill_tool_budget(
        *,
        tool_name: str,
        state: dict[str, object] | None,
    ) -> tuple[bool, str, dict[str, object]]:
        """Reserve one declared Skill operation or explain why it is blocked.

        A rejected call is an explicit evidence boundary: the model must turn
        material already obtained into its declared output rather than retrying
        a rate-limited or duplicate source request.
        """

        if state is None:
            return True, "", {}
        remote_tools = state.get("remote_tools")
        remote_stop = state.get("remote_stop")
        if (
            bool(state.get("stop_remote_on_rate_limit"))
            and isinstance(remote_tools, tuple)
            and tool_name in remote_tools
            and isinstance(remote_stop, dict)
            and remote_stop
        ):
            trigger_tool = str(remote_stop.get("trigger_tool") or "another remote tool")
            trigger_signal = str(remote_stop.get("signal") or "rate limit")
            return False, (
                "SKILL_REMOTE_RETRIEVAL_STOPPED: "
                f"`{trigger_tool}` reported {trigger_signal}; this Skill stops all further remote retrieval in this run. "
                "Do not try another source. Use already returned source data, record the failure boundary, write the partial declared outputs, then finish."
            ), {
                "skill_remote_retrieval_stopped": True,
                "trigger_tool": trigger_tool,
                "trigger_signal": trigger_signal,
                "blocked_tool": tool_name,
            }
        raw_tool_counts = state.get("tool_counts")
        tool_counts = raw_tool_counts if isinstance(raw_tool_counts, dict) else {}
        raw_group_counts = state.get("group_counts")
        group_counts = raw_group_counts if isinstance(raw_group_counts, dict) else {}
        per_tool = state.get("per_tool")
        if isinstance(per_tool, dict):
            limit = per_tool.get(tool_name)
            used = int(tool_counts.get(tool_name, 0) or 0)
            if isinstance(limit, int) and used >= limit:
                return False, (
                    "SKILL_TOOL_BUDGET_REACHED: "
                    f"`{tool_name}` already used {used}/{limit} allowed call(s). Do not retry it. "
                    "Use prior tool data, record the retrieval boundary in the declared outputs, then finish."
                ), {
                    "skill_tool_budget_reached": True,
                    "budget_kind": "tool",
                    "tool_name": tool_name,
                    "used": used,
                    "limit": limit,
                }
        groups = state.get("groups")
        matching_groups = [
            group
            for group in (groups if isinstance(groups, list) else [])
            if isinstance(group, dict) and tool_name in group.get("tools", ())
        ]
        for group in matching_groups:
            label = str(group.get("label") or "tool_group")
            limit = group.get("max_calls")
            used = int(group_counts.get(label, 0) or 0)
            if isinstance(limit, int) and used >= limit:
                return False, (
                    "SKILL_TOOL_BUDGET_REACHED: "
                    f"shared budget `{label}` already used {used}/{limit} call(s). Do not try another tool in this group. "
                    "Use prior tool data, record the retrieval boundary in the declared outputs, then finish."
                ), {
                    "skill_tool_budget_reached": True,
                    "budget_kind": "group",
                    "budget_group": label,
                    "tool_name": tool_name,
                    "used": used,
                    "limit": limit,
                }
        if isinstance(per_tool, dict) and isinstance(per_tool.get(tool_name), int):
            tool_counts[tool_name] = int(tool_counts.get(tool_name, 0) or 0) + 1
        for group in matching_groups:
            label = str(group.get("label") or "tool_group")
            group_counts[label] = int(group_counts.get(label, 0) or 0) + 1
        state["tool_counts"] = tool_counts
        state["group_counts"] = group_counts
        return True, "", {}

    @staticmethod
    def _observe_skill_remote_rate_limit(
        *,
        tool_name: str,
        result: ToolResult,
        state: dict[str, object] | None,
    ) -> None:
        """Close a Skill's remote boundary after explicit or embedded 429 data.

        Multi-source retrieval can return useful papers while one provider is
        already rate-limited, so checking only ``ToolResult.ok`` would miss the
        condition that its Skill contract promised to respect.  Inspect the
        narrow result/error surface recursively for a rate-limit marker; the
        next remote call is then rejected before it reaches the network.
        """

        if (
            state is None
            or not bool(state.get("stop_remote_on_rate_limit"))
            or tool_name not in state.get("remote_tools", ())
            or state.get("remote_stop")
        ):
            return

        def find_rate_limit(value: object) -> str | None:
            if isinstance(value, str):
                normalized = value.casefold()
                if "rate_limit" in normalized or "rate limit" in normalized or "http_429" in normalized or "429" == normalized:
                    return value
                return None
            if isinstance(value, dict):
                for key, nested in value.items():
                    key_text = str(key).casefold()
                    if key_text in {"status", "error", "failure_class", "http_status"}:
                        marker = find_rate_limit(nested)
                        if marker:
                            return marker
                    elif key_text in {
                        "source_stats",
                        "source_failures",
                        "failed_sources",
                        "failures",
                        "errors",
                        "providers",
                        "details",
                    }:
                        marker = find_rate_limit(nested)
                        if marker:
                            return marker
                    elif isinstance(nested, (dict, list)):
                        # Provider diagnostics are often keyed by provider
                        # name (for example ``source_stats.openalex``). Walk
                        # those containers, but never scan arbitrary paper
                        # title/abstract strings for a coincidental phrase.
                        marker = find_rate_limit(nested)
                        if marker:
                            return marker
                return None
            if isinstance(value, list):
                for nested in value:
                    marker = find_rate_limit(nested)
                    if marker:
                        return marker
            if isinstance(value, int) and value == 429:
                return "http_429"
            return None

        marker = find_rate_limit(result.error) or find_rate_limit(result.data)
        if marker:
            state["remote_stop"] = {"trigger_tool": tool_name, "signal": marker}

    @staticmethod
    def _t4_artifact_write_order_error(ctx: ExecutionContext, tc: ToolCall) -> str | None:
        """Reject a Gate1 artifact write that skips a durable predecessor."""

        if ctx.task_id != "T4" or tc.name not in {"write_file", "write_structured_file", "append_file"}:
            return None
        path = str(tc.arguments.get("path") or "").replace("\\", "/").lstrip("./")
        ordered_paths = [item[0] for item in T4_GATE1_ARTIFACTS]
        if path not in ordered_paths:
            return None
        index = ordered_paths.index(path)
        missing = [candidate for candidate in ordered_paths[:index] if not (ctx.workspace_dir / candidate).exists()]
        if not missing:
            return None
        return (
            f"T4 Gate1 artifact order violation: cannot write {path} before "
            f"{', '.join(missing)}. Write the missing predecessor(s) first."
        )

    def _record_skill_progress(
        self,
        ctx: ExecutionContext,
        *,
        step: int | None,
        step_limit: int | str | None,
        phase: str,
        detail: str,
        tool_name: str | None = None,
    ) -> None:
        """Persist observable runtime events for standalone Skill sessions."""

        session_id = str(ctx.extra.get("skill_session_id") or "").strip()
        if not session_id or not ctx.task_id.startswith("SKILL_"):
            return
        try:
            from ..skills.session import record_run_progress

            record_run_progress(
                workspace=ctx.workspace_dir,
                session_id=session_id,
                step=step,
                step_limit=step_limit,
                phase=phase,
                detail=detail,
                tool_name=tool_name,
            )
        except Exception as exc:  # pragma: no cover - progress must not break a run
            self.log.warning("skill_session_progress_write_failed", error=str(exc))

    def _refresh_t4_gate1_progress(
        self,
        ctx: ExecutionContext,
        *,
        active_path: str | Path | None,
        paused_reason: str | None = None,
        announce: bool = True,
    ) -> None:
        """Refresh the durable T4 checkpoint without conflating it with candidates.

        Gate1's ``n/6`` measures only required *persisted artifacts*. It is
        intentionally independent from a model-authored D1/D2/... candidate
        count. Store the exact checkpoint in ``ctx.extra`` for heartbeats and
        print it only when a user needs a new artifact-level milestone.
        """

        if not self._is_t4_ideation_agent(ctx):
            return
        try:
            refreshed = refresh_t4_gate1_progress(
                ctx.workspace_dir,
                active_path=str(active_path) if active_path else None,
                paused_reason=paused_reason,
            )
            current = str(refreshed.get("current_label") or "正在更新 Gate1 进度")
            completed = int(refreshed.get("completed_count") or 0)
            total = int(refreshed.get("total_count") or 0)
            next_artifact = str(refreshed.get("next_artifact_label") or current)
            ctx.extra["t4_artifact_progress"] = {"completed": completed, "total": total}
            ctx.extra["t4_public_activity"] = current
            ctx.extra["t4_next_artifact"] = next_artifact
            if not announce:
                return
            signature = (completed, total, current, paused_reason or "")
            if ctx.extra.get("t4_last_announced_artifact_progress") == signature:
                return
            ctx.extra["t4_last_announced_artifact_progress"] = signature
            self.progress.emit(
                f"[T4 Gate1 artifacts] {completed}/{total} · {current}",
                important=True,
            )
        except Exception as exc:  # pragma: no cover - progress is observational
            self.log.warning("t4_progress_refresh_failed", error=str(exc))

    @staticmethod
    def _update_t4_public_activity_from_event(ctx: ExecutionContext, data: dict[str, object]) -> None:
        """Keep the last public candidate milestone available to LLM heartbeats.

        This accepts only the bounded event emitted by
        ``log_t4_ideation_progress``. It never records model rationale or
        unpersisted research content.
        """

        event = data.get("event") if isinstance(data.get("event"), dict) else {}
        if not isinstance(event, dict):
            return
        phase = str(event.get("phase") or "T4").replace("_", " ")
        status = str(event.get("status") or "updated").replace("_", " ")
        subject_parts = [
            str(event.get("candidate_id") or "").strip(),
            str(event.get("candidate_title") or event.get("channel") or "").strip(),
        ]
        subject = " · ".join(part for part in subject_parts if part)
        label = " · ".join(part for part in (phase, subject, status) if part)
        if label:
            ctx.extra["t4_candidate_activity"] = label

    @staticmethod
    def _t4_heartbeat_context(ctx: ExecutionContext) -> dict[str, object]:
        """Return only public, durable T4 status for a provider heartbeat."""

        if ctx.task_id != "T4":
            return {}
        if ctx.extra.get("t4_evolution_active"):
            deliverable = str(ctx.extra.get("t4_evolution_current_deliverable") or "T4 阶段产物")
            return {
                "activity": str(ctx.extra.get("t4_evolution_activity") or "T4 Evolution"),
                # ``next_artifact`` is retained for older event consumers. The
                # heartbeat renderer receives the clearer present-tense fields
                # below and no longer calls the current output a "next step".
                "next_artifact": deliverable,
                "current_deliverable": deliverable,
                "following_phase": str(ctx.extra.get("t4_evolution_following_phase") or ""),
                "artifact_completed": None,
                "artifact_total": None,
            }
        progress = ctx.extra.get("t4_artifact_progress")
        completed = progress.get("completed") if isinstance(progress, dict) else None
        total = progress.get("total") if isinstance(progress, dict) else None
        activity = str(
            ctx.extra.get("t4_candidate_activity")
            or ctx.extra.get("t4_public_activity")
            or "正在准备下一项可执行动作"
        )
        next_artifact = str(ctx.extra.get("t4_next_artifact") or "等待下一项持久化产物")
        return {
            "activity": activity,
            "next_artifact": next_artifact,
            "current_deliverable": next_artifact,
            "following_phase": "",
            "artifact_completed": int(completed) if isinstance(completed, int) else None,
            "artifact_total": int(total) if isinstance(total, int) else None,
        }

    @staticmethod
    def _t4_heartbeat_phase_identity(ctx: ExecutionContext, heartbeat: dict[str, object]) -> str:
        """Return the stable logical phase represented by a T4 heartbeat.

        A provider request is not a T4 phase.  Formation and independent
        scoring may each use several provider calls, including retries, while
        remaining one researcher-visible unit of work.  Prefer the explicit
        controller phase marker.  The fallback only uses stable public
        deliverable fields, not a candidate-level activity string that can
        change many times within one phase.
        """

        explicit = str(ctx.extra.get("t4_heartbeat_phase_key") or "").strip()
        if explicit:
            return explicit
        deliverable = " ".join(
            str(heartbeat.get("current_deliverable") or heartbeat.get("next_artifact") or "").split()
        )
        following = " ".join(str(heartbeat.get("following_phase") or "").split())
        activity = " ".join(str(heartbeat.get("activity") or "T4").split())
        return "compat:" + "|".join((deliverable or activity, following))

    @classmethod
    def _mark_t4_heartbeat_phase(
        cls,
        ctx: ExecutionContext,
        phase_key: str,
        *,
        now: float | None = None,
    ) -> None:
        """Start a new monotonic clock only when the logical phase changes."""

        if ctx.task_id != "T4":
            return
        normalized = " ".join(str(phase_key or "T4").split()) or "T4"
        current = ctx.extra.get("t4_heartbeat_phase_timer")
        if isinstance(current, dict) and str(current.get("phase_key") or "") == normalized:
            return
        ctx.extra["t4_heartbeat_phase_timer"] = {
            "phase_key": normalized,
            "started_monotonic": time.monotonic() if now is None else float(now),
            "last_elapsed_seconds": 0,
        }

    @classmethod
    def _t4_heartbeat_phase_elapsed_seconds(
        cls,
        ctx: ExecutionContext,
        heartbeat: dict[str, object],
        *,
        now: float | None = None,
    ) -> int | None:
        """Return a monotonic phase elapsed time that survives request retries.

        The value intentionally lives in the execution context rather than a
        provider-call local.  A retry or a next model call within the same
        controller phase therefore cannot restart the visible timer.  A new
        process starts a fresh monotonic clock on resume; completed artifacts,
        not an elapsed display counter, remain the authoritative resume
        boundary.
        """

        if ctx.task_id != "T4":
            return None
        phase_key = cls._t4_heartbeat_phase_identity(ctx, heartbeat)
        cls._mark_t4_heartbeat_phase(ctx, phase_key, now=now)
        timer = ctx.extra.get("t4_heartbeat_phase_timer")
        if not isinstance(timer, dict):  # defensive: the marker above writes it
            return 0
        current = time.monotonic() if now is None else float(now)
        try:
            started = float(timer.get("started_monotonic"))
        except (TypeError, ValueError):
            started = current
            timer["started_monotonic"] = started
        try:
            prior = max(0, int(timer.get("last_elapsed_seconds") or 0))
        except (TypeError, ValueError):
            prior = 0
        elapsed = max(prior, int(max(0.0, current - started)))
        timer["last_elapsed_seconds"] = elapsed
        return elapsed

    @classmethod
    def _heartbeat_phase_identity(
        cls,
        ctx: ExecutionContext,
        heartbeat: dict[str, object],
    ) -> str:
        """Return the researcher-visible phase that owns one wait clock.

        Provider requests are implementation attempts, not progress phases.  A
        retry in T4.5, T5, or T8 must therefore continue the elapsed time shown
        for that task instead of starting again at twelve seconds.  Native T4
        has more granular, controller-owned phases and retains its established
        identity rules for compatibility with its evolution telemetry.

        Other stages may set ``heartbeat_phase_key`` when one task intentionally
        contains several independently visible long phases.  In the usual case
        the state-machine task ID is the stable phase boundary.
        """

        if ctx.task_id == "T4":
            return cls._t4_heartbeat_phase_identity(ctx, heartbeat)
        explicit = " ".join(str(ctx.extra.get("heartbeat_phase_key") or "").split())
        if explicit:
            return explicit
        mode = " ".join(str(ctx.mode or ctx.extra.get("phase") or "execution").split())
        return f"{ctx.task_id}:{mode or 'execution'}"

    @classmethod
    def _mark_heartbeat_phase(
        cls,
        ctx: ExecutionContext,
        phase_key: str,
        *,
        now: float | None = None,
    ) -> None:
        """Start a clock only when a logical visible phase actually changes."""

        if ctx.task_id == "T4":
            cls._mark_t4_heartbeat_phase(ctx, phase_key, now=now)
            return
        normalized = " ".join(str(phase_key or ctx.task_id).split()) or ctx.task_id
        current = ctx.extra.get("heartbeat_phase_timer")
        if isinstance(current, dict) and str(current.get("phase_key") or "") == normalized:
            return
        ctx.extra["heartbeat_phase_timer"] = {
            "phase_key": normalized,
            "started_monotonic": time.monotonic() if now is None else float(now),
            "last_elapsed_seconds": 0,
        }

    @classmethod
    def _heartbeat_phase_elapsed_seconds(
        cls,
        ctx: ExecutionContext,
        heartbeat: dict[str, object],
        *,
        now: float | None = None,
    ) -> int:
        """Return a monotonic elapsed time that survives provider retries.

        The timer is deliberately in ``ExecutionContext.extra`` rather than
        local to ``_await_llm_with_progress``.  This makes every task that uses
        the common provider wait path, including T4.5, T5 and T8, obey the
        same retry-safe behavior.  Resume starts a new process-local clock;
        durable artifacts, not a display counter, are the resume authority.
        """

        phase_key = cls._heartbeat_phase_identity(ctx, heartbeat)
        cls._mark_heartbeat_phase(ctx, phase_key, now=now)
        timer_key = "t4_heartbeat_phase_timer" if ctx.task_id == "T4" else "heartbeat_phase_timer"
        timer = ctx.extra.get(timer_key)
        if not isinstance(timer, dict):  # defensive: the marker above writes it
            return 0
        current = time.monotonic() if now is None else float(now)
        try:
            started = float(timer.get("started_monotonic"))
        except (TypeError, ValueError):
            started = current
            timer["started_monotonic"] = started
        try:
            prior = max(0, int(timer.get("last_elapsed_seconds") or 0))
        except (TypeError, ValueError):
            prior = 0
        elapsed = max(prior, int(max(0.0, current - started)))
        timer["last_elapsed_seconds"] = elapsed
        return elapsed

    def _emit_t4_durable_candidate_recap(self, ctx: ExecutionContext, output_path: str | Path | None) -> None:
        """Backstop candidate-level CLI facts when the model omits progress events.

        The output is parsed only after a durable artifact was reported as
        written. It therefore describes persisted candidates/reviews/scores,
        never intermediate reasoning.
        """

        if not self._is_t4_ideation_agent(ctx) or not output_path:
            return
        relative = safe_relative(output_path, ctx.workspace_dir) or str(output_path)
        if relative not in {
            "ideation/_pass1_forward_candidates.json",
            "ideation/_pass2_grounding_review.json",
            "ideation/_candidate_directions.json",
            "ideation/_gate1_candidate_cards.md",
        }:
            return
        path = ctx.workspace_dir / relative
        try:
            stat = path.stat()
        except OSError:
            return
        recap_key = f"{relative}:{stat.st_mtime_ns}:{stat.st_size}"
        if recap_key in self._t4_durable_recap_keys:
            return
        self._t4_durable_recap_keys.add(recap_key)
        try:
            if relative.endswith(".json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
            else:
                payload = None
        except (OSError, json.JSONDecodeError) as exc:
            self.log.warning("t4_durable_recap_parse_failed", path=relative, error=str(exc))
            return

        if relative == "ideation/_pass1_forward_candidates.json":
            candidates = payload.get("candidates") if isinstance(payload, dict) else []
            candidates = [item for item in candidates if isinstance(item, dict)]
            mainline = sum(1 for item in candidates if str(item.get("constraint_status") or "") == "mainline")
            supplements = sum(1 for item in candidates if str(item.get("constraint_status") or "") == "supplement")
            self.progress.emit(
                f"[T4 Pass1] 已保存候选池：{len(candidates)} 个方向（主线 {mainline}，补充 {supplements}）。",
                important=True,
            )
            for index, candidate in enumerate(candidates, start=1):
                candidate_id = str(candidate.get("id") or f"#{index}")
                title = _t4_recap_title(candidate)
                origin = str(candidate.get("idea_origin") or candidate.get("origin") or "未标注")
                lane = str(candidate.get("constraint_status") or "未标注")
                self.progress.emit(
                    f"[T4 Pass1] {index}/{len(candidates)} · {candidate_id} · {title} | {lane}/{origin} | 已写入候选记录。",
                    important=True,
                )
            return

        if relative == "ideation/_pass2_grounding_review.json":
            reviews = payload.get("reviews") if isinstance(payload, dict) else []
            reviews = [item for item in reviews if isinstance(item, dict)]
            self.progress.emit(f"[T4 Pass2] 已保存接地复核：{len(reviews)} 个候选。", important=True)
            for index, review in enumerate(reviews, start=1):
                candidate_id = str(review.get("idea_id") or review.get("id") or f"#{index}")
                recommendation = str(review.get("screening_recommendation") or "需复核")
                novelty = str(review.get("novelty_signal") or "未计算")
                self.progress.emit(
                    f"[T4 Pass2] {index}/{len(reviews)} · {candidate_id} | 建议={recommendation} | 新颖性信号={novelty}。",
                    important=True,
                )
            return

        if relative == "ideation/_candidate_directions.json":
            candidates = payload.get("candidates") if isinstance(payload, dict) else []
            candidates = [item for item in candidates if isinstance(item, dict)]
            self.progress.emit(f"[T4 评分] 已保存结构化候选：{len(candidates)} 个方向。", important=True)
            for index, candidate in enumerate(candidates, start=1):
                scores = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
                score_text = ", ".join(f"{key}={value}/5" for key, value in scores.items()) or "未评分"
                self.progress.emit(
                    f"[T4 评分] {index}/{len(candidates)} · {candidate.get('id') or '#'} · {_t4_recap_title(candidate)} | {score_text}",
                    important=True,
                )
            return

        if relative == "ideation/_gate1_candidate_cards.md":
            self.progress.emit(
                "[T4 Gate1] 完整候选卡片已写入：短标题、创新、候选 H1/H2/H3、可组合关系、评分依据和证据边界均在卡片中展示。",
                important=True,
            )


    def _llm_chat_accepts_keyword(self, keyword: str) -> bool:
        """Keep optional transport controls compatible with lightweight test clients."""

        try:
            parameters = inspect.signature(self.llm.chat).parameters.values()
        except (TypeError, ValueError, AttributeError):
            # The production client accepts the optional control.  If an
            # adapter cannot be introspected, preserve that normal behavior.
            return True
        return any(
            parameter.name == keyword or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )

    async def _await_llm_with_progress(
        self,
        *,
        ctx: ExecutionContext,
        step: int,
        progress_step_limit: int | str | None,
        **kwargs,
    ):
        """Await one LLM call while emitting a bounded observable heartbeat."""

        self._record_skill_progress(
            ctx,
            step=step,
            step_limit=progress_step_limit,
            phase="awaiting_llm",
            detail="已提交模型请求，正在等待下一组可执行动作。",
        )
        if (
            self._is_t4_ideation_agent(ctx)
            and not self._t4_gate1_user_selection_exists(ctx)
            and not ctx.extra.get("t4_evolution_active")
        ):
            self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
        heartbeat = self._t4_heartbeat_context(ctx)
        # Keep a phase-level timer for trace/debug observability, but never use
        # it as the normal UI wait counter. One T4 phase can contain several
        # distinct model calls, such as generating Children and then scoring
        # their union. Showing the accumulated phase total beside a fresh
        # scoring call falsely makes that call look stalled.
        self._heartbeat_phase_elapsed_seconds(ctx, heartbeat)
        self.progress.llm_request_started(task_id=ctx.task_id, step=step, **heartbeat)
        task = asyncio.create_task(self.llm.chat(**kwargs))
        request_started = time.monotonic()
        timeout = 12.0
        try:
            while True:
                done, _pending = await asyncio.wait({task}, timeout=timeout)
                if done:
                    return task.result()
                request_elapsed = int(time.monotonic() - request_started)
                if (
                    self._is_t4_ideation_agent(ctx)
                    and not self._t4_gate1_user_selection_exists(ctx)
                    and not ctx.extra.get("t4_evolution_active")
                ):
                    self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
                heartbeat = self._t4_heartbeat_context(ctx)
                phase_elapsed = self._heartbeat_phase_elapsed_seconds(ctx, heartbeat)
                self.progress.llm_waiting(
                    task_id=ctx.task_id,
                    agent=self.agent.spec.name,
                    step=step,
                    # The researcher-facing line means this exact provider
                    # request. It resets at every model call, including a
                    # new call after a tool result or a completed Child.
                    elapsed_seconds=request_elapsed,
                    phase_elapsed_seconds=phase_elapsed,
                    **heartbeat,
                )
                self._record_skill_progress(
                    ctx,
                    step=step,
                    step_limit=progress_step_limit,
                    phase="awaiting_llm",
                    detail=(
                        f"本次模型调用仍在等待，已持续 {request_elapsed}s。"
                        + (
                            f"当前可见阶段累计 {phase_elapsed}s。"
                            if phase_elapsed is not None
                            else ""
                        )
                    ),
                )
                # The first heartbeat remains at 12s. T4 uses a 30-second
                # cadence because concurrent route calls can be active; all
                # other long stages use 20 seconds. Both rates keep normal UI
                # visible without turning a provider wait into a tool log.
                timeout = 30.0 if self._is_t4_ideation_agent(ctx) else 20.0
        except BaseException:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            raise

    @staticmethod
    def _centered_separator(title: str, *, width: int = 80, fill: str = "=") -> str:
        label = f" {title.strip()} "
        if len(label) >= width:
            return label
        left = (width - len(label)) // 2
        right = width - len(label) - left
        return f"{fill * left}{label}{fill * right}"

    def _emit(self, message: str, *, important: bool = False, verbose_only: bool = False) -> None:
        """Print according to CLI verbosity while RunLogger keeps full timeline."""

        if verbose_only and not self.runtime_settings.ui.verbose:
            return
        if self.runtime_settings.ui.quiet and not important:
            return
        print(format_cli_message(message), flush=True)

    @staticmethod
    def _infer_task_description(ctx: ExecutionContext) -> str:
        task_map = {
            "T1": "初始化项目配置和 workspace 状态",
            "T2": "检索、去重并验证候选论文",
            "T3": "精读论文并生成结构化 paper notes",
            "T3.5": "基于 notes 分阶段合成 literature synthesis",
            "T4": "生成候选研究假设、实验计划和风险分析",
            "T4.5": "做新颖性预审和 mechanism tuple 审计",
            "T5-REBOOST-GATE": "调用 LLM API 对 Pre-T5 材料做 context re-boost",
            "T5-HANDOFF": "编译外部实验协议和执行器控制说明",
            "T5-SPECIALIZE-EXECUTOR-SKILLS": "生成并校验项目专属 executor Skill Suite",
            "T5-EXPR-MATERIAL-GATE": "等待用户放置外部实验材料并确认继续",
            "T5-EXECUTOR-GATE": "由用户选择 mock、Claude Code、Codex CLI 或人工外部执行器",
            "T5-EXTERNAL-WAIT": "等待外部执行器写回 executor_research_report 并在 resume 时校验",
            "T5-DRY-RUN": "跑通 mock 外部执行器文件协议，不执行真实实验",
            "T5": "legacy pilot 实验兼容节点",
            "T6": "legacy pilot 后新颖性复核兼容节点",
            "T8-RESOURCE": "构建写作资源索引、证据计划和图表计划",
            "T8-WRITE": "生成资源驱动的论文总大纲",
            "T8-SECTION-PLAN": "初始化 paper_state 和每章局部大纲",
            "T8-DRAFT": "拼装章节、审计 claim 并生成 paper.tex",
            "T8-SELF-CHECK": "作者自查整篇论文",
            "T8-REVIEW-1": "第一轮逐章节审稿",
            "T8-REVIEW-2": "第二轮逐章节审稿",
            "T8-REVISE-1": "按第一轮 patch list 修订论文",
            "T8-REVISE-2": "按第二轮 patch list 修订论文",
            "T8-PAPER-CLAIM-AUDIT": "进入 T9 前最终审计 paper claim 与 evidence pack 一致性",
            "T9": "构建投稿包、编译 PDF 并修复 TeX 问题",
        }
        if ctx.task_id.startswith("T8-SEC-"):
            section_id = ctx.extra.get("section_id") or ctx.extra.get("section") or "section"
            return f"只写单个论文 section: {section_id}"
        return task_map.get(ctx.task_id, "执行当前状态机节点声明的任务")

    async def _run_post_hook(self, hook, ctx: ExecutionContext, result: AgentResult) -> None:
        """兼容同步/异步 post-hook。"""
        outcome = hook(ctx, result)
        if inspect.isawaitable(outcome):
            await outcome

    async def _maybe_run_t1_workflow_mode_gate(
        self,
        ctx: ExecutionContext,
        tool_map: dict[str, Tool],
        messages: list[Message],
        trace: TraceWriter,
    ) -> None:
        """Get an explicit Auto/Copilot choice before T1 gathers materials."""

        if ctx.task_id != "T1" or self.agent.spec.name != "pi" or (ctx.mode or "init") != "init":
            return
        tool = tool_map.get("ask_human")
        if tool is None and workflow_mode_needs_confirmation(ctx.workspace_dir):
            raise RecoverableRuntimePause("T1 工作模式选择需要 ask_human 工具，但当前策略没有开放它。")
        if workflow_mode_needs_confirmation(ctx.workspace_dir):
            question = (
                "<!-- researchos_workflow_mode_selector -->\n"
                "请选择本项目的运行方式。这个选择只决定已授权常规 Gate 的自动化程度；"
                "不会替你决定研究问题、文献范围、关键假设、失败恢复或外部执行。"
            )
            result = await tool.execute(
                question=question,
                suggestions=[
                    "1 · Copilot",
                    "2 · Auto research_ccf",
                    "3 · Auto research_utd",
                    "4 · Auto survey_ccf",
                    "5 · Auto survey_utd",
                    "6 · Auto survey_exhaustive_utd",
                ],
            )
            if not result.ok:
                raise RecoverableRuntimePause(str(result.content or result.error or "未获得工作模式选择"))
            data = result.data if isinstance(result.data, dict) else {}
            answer = str(data.get("answer") or "").strip()
            parsed = parse_workflow_mode_answer(answer)
            if parsed is None:
                interpreter = getattr(getattr(tool, "human", None), "interpret_workflow_mode", None)
                proposal = await interpreter(answer) if callable(interpreter) else {}
                parsed = parse_workflow_mode_proposal(proposal)
            if parsed is None:
                raise RecoverableRuntimePause(
                    "未识别工作模式。请在恢复后输入 Copilot、Auto research_ccf / Auto research_utd，或用一句话说明希望自动化还是逐步确认。"
                )
            mode, preset, t4_mode = parsed
            profile = configure_workflow_mode(
                ctx.workspace_dir,
                mode=mode,
                preset=preset,
                t4_mode=t4_mode,
                # Selecting a mode is not agreement to its cost/coverage
                # defaults. Both Auto and Copilot receive the next compact
                # default-settings confirmation before T1 starts model work.
                startup_setup_confirmed=False,
                selection_source="t1_gate",
            )
            summary = (
                "已确认 Copilot 模式；T4 前将单独确认投稿取向。"
                if profile["mode"] == "copilot"
                else f"已确认 Auto 模式，预设={profile['preset']}。"
            )
            note = Message.user(f"【T1 工作模式已确认】\n{summary}", step=0)
            messages.append(note)
            trace.write_message(note)

        if not workflow_startup_setup_needs_confirmation(ctx.workspace_dir):
            return
        if tool is None:
            raise RecoverableRuntimePause("项目默认设置确认需要 ask_human 工具，但当前策略没有开放它。")
        existing_profile = load_workflow_mode(ctx.workspace_dir)
        # The first screen may already have parsed an explicit effort from a
        # compact command such as ``Auto survey_utd deep``. Do not rebuild the
        # profile from its preset here: that would silently replace the user's
        # stated value before the Rich confirmation has a chance to show it.
        # Free-form changes below remain in memory until an explicit second
        # confirmation, so a phrase like "两个 Proposal" is a proposal rather
        # than immediate permission to alter durable workflow defaults.
        profile = existing_profile
        settings = profile["settings"]
        setup_suggestions = [
            "确认",
            "综述均衡覆盖、深入探索、两个 Proposal",
            "标准研究覆盖、标准探索、一条 Proposal",
        ]
        current_preset = str(settings.get("literature_preset") or "standard_research")
        current_t4_mode = str(settings.get("t4_mode") or "auto")
        current_proposal_tracks = str(settings.get("proposal_tracks") or "one")
        parsed_setup: tuple[str, str, str] | None = None
        feedback = ""
        awaiting_change_confirmation = False
        for _attempt in range(4):
            setup_question = (
                "<!-- researchos_workflow_settings:"
                + json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + " -->\n"
                + ("<!-- researchos_workflow_settings_change_confirmation -->\n" if awaiting_change_confirmation else "")
                + (
                    "<!-- researchos_workflow_settings_feedback:"
                    + feedback
                    + " -->\n"
                    if feedback
                    else ""
                )
                + (
                    "请确认上方未保存的修改预览。输入“1”或“确认”才会保存；也可继续描述新的修改。"
                    if awaiting_change_confirmation
                    else "请查看参数含义与输入示例。接受当前设置可输入“1”或“确认”；也可描述希望调整的文献覆盖、T4 探索或 Proposal 数量。"
                )
            )
            result = await tool.execute(question=setup_question, suggestions=setup_suggestions)
            if not result.ok:
                raise RecoverableRuntimePause(str(result.content or result.error or "未获得 Auto 启动配置"))
            data = result.data if isinstance(result.data, dict) else {}
            raw_setup_answer = str(data.get("answer") or "").strip()
            # The renderer presents suggestions as examples, not numbered
            # actions. Only ``1`` has a stable semantic: accept the displayed
            # settings. Mapping ``2`` to an example used to turn a user's
            # ordinary numeric answer into a hidden mutation.
            if raw_setup_answer in {"1", "[1]"}:
                raw_setup_answer = "确认"

            if is_execution_setup_confirmation_answer(raw_setup_answer):
                parsed_setup = (current_preset, current_t4_mode, current_proposal_tracks)
                break

            normalized_answer = " ".join(raw_setup_answer.casefold().split())
            status_question = any(
                marker in normalized_answer
                for marker in ("我选", "当前", "是不是", "为什么", "咋", "怎么")
            )
            if not status_question:
                parsed_setup = parse_auto_execution_setup_answer(
                    raw_setup_answer,
                    current_preset=current_preset,
                    current_t4_mode=current_t4_mode,
                    current_proposal_tracks=current_proposal_tracks,
                )
            interpreter = getattr(getattr(tool, "human", None), "interpret_workflow_setup", None)
            proposal = await interpreter(raw_setup_answer) if parsed_setup is None and callable(interpreter) else {}
            if parsed_setup is None and not status_question:
                parsed_setup = parse_execution_setup_proposal(
                    proposal,
                    current_preset=current_preset,
                    current_t4_mode=current_t4_mode,
                    current_proposal_tracks=current_proposal_tracks,
                )
            if parsed_setup is not None:
                proposed_preset, proposed_t4_mode, proposed_proposal_tracks = parsed_setup
                previous_values = (current_preset, current_t4_mode, current_proposal_tracks)
                current_preset = proposed_preset
                current_t4_mode = proposed_t4_mode
                current_proposal_tracks = proposed_proposal_tracks
                preview_settings = dict(settings)
                preview_settings.update(
                    {
                        "literature_preset": current_preset,
                        "t4_mode": current_t4_mode,
                        "proposal_tracks": current_proposal_tracks,
                    }
                )
                profile = {**profile, "settings": preview_settings}
                settings = preview_settings
                changed_labels = [
                    label
                    for label, before, after in (
                        ("文献覆盖", previous_values[0], current_preset),
                        ("T4 探索", previous_values[1], current_t4_mode),
                        ("Proposal 数量", previous_values[2], current_proposal_tracks),
                    )
                    if before != after
                ]
                awaiting_change_confirmation = bool(changed_labels) or awaiting_change_confirmation
                parsed_setup = None
                if changed_labels:
                    feedback = (
                        "已按你的描述生成未保存的修改预览："
                        + "、".join(changed_labels)
                        + " 已更新。请检查上方设置后输入“1”或“确认”保存；继续描述可再次调整。"
                    )
                else:
                    feedback = "你的描述与当前预览相同；当前预览仍未保存。请输入“1”或“确认”，或继续描述修改。"
                continue

            is_auto = str(profile.get("mode") or "").casefold() == "auto"
            survey_policy = str(settings.get("survey_policy") or "")
            survey_label = (
                "自动综述"
                if survey_policy == "write_with_supplement"
                else "研究论文"
                if survey_policy == "skip"
                else "综述支线将在后续单独确认"
            )
            orientation = str(settings.get("publication_orientation") or "")
            orientation_label = "CCF/CS" if orientation == "ccf_cs" else "UTD/IS" if orientation == "utd_is" else "T4 前由你确认"
            llm_feedback = str(proposal.get("clarification") or "").strip() if isinstance(proposal, dict) else ""
            feedback = (
                f"当前已选择：{'Auto' if is_auto else 'Copilot'} · {survey_label} · {orientation_label}。"
                "本页只确认文献覆盖、T4 探索和 Proposal 数量；不会改变刚才选择的综述/研究模式。"
                + (f" {llm_feedback}" if llm_feedback else "")
                + (
                    "当前预览尚未保存；如接受它，请输入“1”或“确认”。"
                    if awaiting_change_confirmation
                    else "如保持当前设置，请输入“1”或“确认”。"
                )
            )
        if parsed_setup is None:
            raise RecoverableRuntimePause(
                f"{feedback or '未识别默认执行设置。'} 请在恢复后输入“1”/“确认”，或如“综述均衡覆盖、深入探索、两个 Proposal”说明希望怎样调整。"
            )
        literature_preset, configured_t4_mode, configured_proposal_tracks = parsed_setup
        profile = configure_workflow_mode(
            ctx.workspace_dir,
            mode=str(profile.get("mode") or "copilot"),
            preset=str(profile.get("preset") or "research_ccf"),
            literature_preset=literature_preset,
            t4_mode=configured_t4_mode,
            proposal_tracks=configured_proposal_tracks,
            startup_setup_confirmed=True,
            selection_source="t1_gate",
        )
        note = Message.user(f"【项目默认执行设置已确认】\n{auto_execution_setup_summary(profile)}", step=0)
        messages.append(note)
        trace.write_message(note)

    async def _maybe_run_t1_workflow_template_gate(
        self,
        ctx: ExecutionContext,
        tool_map: dict[str, Tool],
        messages: list[Message],
        trace: TraceWriter,
    ) -> None:
        """Choose a concrete CCF venue before T1 starts project work.

        The numbered menu is resolved locally against the installed template
        catalogue.  This is a finite, user-owned configuration choice, so an
        LLM is neither needed nor allowed to guess a conference that is not
        present in the workspace-independent template bundle.
        """

        if ctx.task_id != "T1" or self.agent.spec.name != "pi" or (ctx.mode or "init") != "init":
            return
        if not workflow_startup_template_needs_confirmation(ctx.workspace_dir):
            return
        tool = tool_map.get("ask_human")
        if tool is None:
            raise RecoverableRuntimePause("T1 CCF/CS 会议模板选择需要 ask_human 工具，但当前策略没有开放它。")

        repo_root = Path(__file__).resolve().parents[2]
        entries = ccf_template_entries(repo_root=repo_root, available_only=True)
        available_template_ids = available_ccf_template_ids(repo_root)
        if not entries:
            raise RecoverableRuntimePause(
                "当前安装未检测到可用 CCF/CS LaTeX 模板；请检查 latex_templete/ccf-latex-templates 后 resume。"
            )

        selected_template = ""
        feedback = ""
        for _attempt in range(3):
            question = (
                "<!-- researchos_workflow_ccf_template_selector -->\n"
                + (f"上次输入未识别：{feedback}\n" if feedback else "")
                + "请选择将由本项目未来 Survey 与 T8 复用的具体 CCF/CS 会议 LaTeX 模板。"
                "选择只保存工作流默认设置，不会改写已有 TeX、研究材料或实验产物。"
            )
            suggestions = [f"{index} · {entry.label} ({entry.template_id})" for index, entry in enumerate(entries, start=1)]
            result = await tool.execute(question=question, suggestions=suggestions)
            if not result.ok:
                raise RecoverableRuntimePause(str(result.content or result.error or "未获得 CCF/CS 会议模板选择"))
            data = result.data if isinstance(result.data, dict) else {}
            answer = str(data.get("answer") or "").strip()
            selected_template = parse_available_ccf_template_answer(answer, entries)
            if selected_template in available_template_ids:
                break
            feedback = "请直接输入上表编号、会议名或 template id。"

        if selected_template not in available_template_ids:
            raise RecoverableRuntimePause(
                "未识别 CCF/CS 会议模板。恢复后请输入上表编号、会议名或 template id；不会用 basic_en 替代。"
            )
        current = load_workflow_mode(ctx.workspace_dir)
        profile = configure_workflow_mode(
            ctx.workspace_dir,
            mode="auto",
            preset=str(current.get("preset") or "research_ccf"),
            template_id=selected_template,
            startup_setup_confirmed=True,
            selection_source="t1_gate",
        )
        note = Message.user(
            "【T1 CCF/CS 会议模板已确认】\n"
            f"已选择 {selected_template}；未来 Survey 与 T8 将复用该模板。已有 TeX 和研究产物不会被改写。",
            step=0,
        )
        messages.append(note)
        trace.write_message(note)

    async def _maybe_run_t1_startup_gate(
        self,
        ctx: ExecutionContext,
        tool_map: dict[str, Tool],
        messages: list[Message],
        trace: TraceWriter,
    ) -> None:
        """T1 必须先给用户一次补充材料/确认窗口，再让 PI 扫描 seeds。

        这是一个 runtime 级前置 gate，不依赖 LLM 是否记得调用 ask_human。
        首次运行会写 `_runtime/t1_startup_gate.json`；resume 或重跑时复用该
        artifact，把用户回答注入上下文，但不重复弹输入框。
        """

        if ctx.task_id != "T1" or self.agent.spec.name != "pi":
            return
        if (ctx.mode or "init") != "init":
            return

        gate_path = ctx.workspace_dir / "_runtime" / "t1_startup_gate.json"
        existing = self._load_t1_startup_gate(gate_path)
        if existing:
            answer = str(existing.get("answer") or "").strip()
            if answer:
                ctx.extra["t1_startup_gate_answer"] = answer
                ctx.extra["t1_startup_gate_path"] = str(gate_path)
                note = Message.user(
                    "【T1 启动补充 gate 已完成】\n"
                    "下面是用户在扫描 user_seeds/ 之前补充或确认的信息。"
                    "请先结合这段信息，再调用 list_files/read_file 扫描 user_seeds/。\n\n"
                    f"{answer}",
                    step=0,
                )
                messages.append(note)
                trace.write_message(note)
                return

        if "ask_human" not in tool_map:
            raise RecoverableRuntimePause(
                "T1 启动补充 gate 需要 ask_human 工具，但当前 Agent 工具策略没有开放 ask_human。"
            )

        question = (
            "【T1 启动补充 gate】\n"
            "在 ResearchOS 扫描 user_seeds/ 之前，请先补充或确认初始化信息。\n\n"
            "为什么需要回答：T1 会把你的研究边界、已有论文/想法/约束和外部资源写成 "
            "project.yaml、user_seeds/* 与 literature/retrieval_scope_plan.json；"
            "这些 artifact 会直接影响后续 T2 检索、T3 阅读、T4 idea 生成和实验计划。"
            "先确认一次可以避免系统用过期或缺失材料启动。\n\n"
            "你可以回答：\n"
            "1. 已经放入 user_seeds/ 的材料有哪些，是否可以直接扫描；\n"
            "2. 还想补充的种子论文、arXiv/DOI、初步想法、硬约束、目标 venue、预算/GPU；\n"
            "3. 外部资源，如数据集、benchmark、代码仓库、预训练模型；\n"
            "4. 如果没有补充，直接回答“继续，扫描现有 user_seeds”。"
        )
        suggestions = [
            "继续，扫描现有 user_seeds",
            "我已补充 seed PDFs/seed_ideas/seed_constraints，请先读取这些文件",
            "我要补充研究问题、目标 venue、预算/GPU 或外部资源",
        ]

        self.progress.emit(
            "[PI Agent] T1 启动补充 gate：先确认种子材料和研究边界，再扫描 user_seeds/",
            important=True,
        )
        result = await tool_map["ask_human"].execute(question=question, suggestions=suggestions)
        if not result.ok:
            reason = result.content or result.error or "T1 启动补充 gate 未获得用户输入"
            raise RecoverableRuntimePause(str(reason))

        data = result.data if isinstance(result.data, dict) else {}
        answer = str(data.get("answer") or "").strip()
        if not answer:
            raise RecoverableRuntimePause("T1 启动补充 gate 收到空回答，已暂停等待明确输入。")

        gate_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "version": "1.0",
            "semantics": "t1_startup_material_supplement_gate",
            "interaction_id": data.get("interaction_id") or f"t1_startup_{uuid4().hex[:12]}",
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "suggestions": suggestions,
            "answer": answer,
            "next_action": "scan_user_seeds_after_gate",
        }
        gate_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ctx.extra["t1_startup_gate_answer"] = answer
        ctx.extra["t1_startup_gate_path"] = str(gate_path)

        note = Message.user(
            "【T1 启动补充 gate 用户回答】\n"
            "必须先结合这段回答，再扫描 user_seeds/ 并继续后续分轮访谈：\n\n"
            f"{answer}",
            step=0,
        )
        messages.append(note)
        trace.write_message(note)

    @staticmethod
    def _load_t1_startup_gate(path: Path) -> dict[str, object] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("semantics") != "t1_startup_material_supplement_gate":
            return None
        return data

    async def _maybe_finalize_t2_outputs(
        self,
        *,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
    ) -> tuple[str, str | None]:
        """T2 退出后的窄口恢复。

        不能把普通冷启动中的 LLM/step 失败当成“raw 已足够，可以完成 T2”。
        Scout 是否已经完成覆盖判断，必须由 finish_task 或真实 resume/retry 语义
        触发；否则第一轮多源搜索返回大量 raw 时会伪装成 T2 已成功。
        """

        if ctx.task_id != "T2":
            return stop_reason, error_msg
        if stop_reason in {AgentResult.STOP_INTERRUPTED, AgentResult.STOP_HUMAN_REJECT}:
            return stop_reason, error_msg
        if stop_reason == AgentResult.STOP_FINISHED:
            return stop_reason, error_msg
        if not self._allow_t2_exit_recovery(ctx):
            return stop_reason, error_msg

        needs_recovery = any(
            not path.exists()
            for name, path in ctx.outputs_expected.items()
            if name != "papers_raw"
        )
        if not needs_recovery:
            return stop_reason, error_msg

        finalized = await self._finalize_t2_from_raw(
            ctx,
            mode="t2_recovery",
            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
            start_message="[Scout Agent] T2 resume/recovery 检测到未完成输出，尝试基于 papers_raw 补齐...",
            success_message="[Scout Agent] T2 resume/recovery 补齐成功，已恢复完整 T2 产物",
        )
        if finalized:
            return AgentResult.STOP_FINISHED, None

        return stop_reason, error_msg

    def _t5_reboost_artifacts_valid(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        if ctx.task_id != "T5-REBOOST-GATE":
            return False, "not a T5-REBOOST task"
        try:
            from ..schemas.validator import validate_task_artifacts

            return validate_task_artifacts(ctx.workspace_dir, ctx.task_id)
        except Exception as exc:
            return False, str(exc)

    async def _maybe_finalize_t5_reboost_before_llm(
        self,
        ctx: ExecutionContext,
        policy: WorkspaceAccessPolicy,
    ) -> bool:
        """Compile the T4.5 to T5 handoff before an LLM can touch its contract.

        T5 is a source-preserving protocol compilation step.  Its authoritative
        inputs are the formal artifacts produced after the T4.5 verdict, and
        the compiler already has the complete schema, provenance, and claim
        boundary rules needed to create the handoff.  Asking an LLM to first
        reproduce the same large JSON object made this transition depend on
        model availability and frequently led to repeated partial repairs.

        A valid existing pack is reused.  Otherwise the deterministic compiler
        is the primary path.  A real upstream-source deficiency becomes one
        actionable recovery pause instead of an LLM retry loop; a model cannot
        safely invent the missing T4.5 evidence or execution constraints.
        """

        if ctx.task_id != "T5-REBOOST-GATE":
            return False
        ok, err = self._t5_reboost_artifacts_valid(ctx)
        if ok:
            self.progress.emit(
                "[Research Reboost] 检测到已有有效 handoff 与 executor 控制文件，跳过重复编译。",
                important=True,
            )
            self._record_runtime_completion(
                ctx,
                "t5_reboost_resume_prefinalize",
                {"outputs": [str(path.relative_to(ctx.workspace_dir)) for path in ctx.outputs_expected.values() if path.exists()]},
                action_type="t5_reboost_prefinalize",
            )
            return True

        self.log.info("t5_reboost_existing_handoff_invalid", reason=err)
        self.progress.emit(
            "[Research Reboost] 正在从已通过的 T4.5 产物编译并校验执行交接，不调用模型重写 handoff。",
            important=True,
        )
        result = await CompileResearchReboostHandoffTool(policy).execute()
        if not result.ok:
            detail = " ".join(str(result.content or result.error or "unknown compiler failure").split())[:1200]
            ctx.extra["t5_reboost_compile_failure"] = {
                "error": str(result.error or "research_reboost_compiler_failed"),
                "detail": detail,
                "validation_report": "external_executor/report/reboost_validation_report.json",
                "handoff_report": "external_executor/report/reboost_report.json",
            }
            self.log.warning("t5_reboost_primary_compile_failed", error=result.error, detail=detail)
            raise RecoverableRuntimePause(
                "T5 无法从当前 T4.5 产物编译可执行交接，未调用模型进行无依据修补。"
                f"具体原因：{detail}。"
                "请查看 external_executor/report/reboost_validation_report.json 和 "
                "external_executor/report/reboost_report.json 中列出的缺失源文件或契约字段；"
                "补齐或修复对应上游材料后 resume，系统只会重新编译交接。"
            )

        ok, err = self._t5_reboost_artifacts_valid(ctx)
        if not ok:
            self.log.warning("t5_reboost_primary_compile_postcheck_failed", error=err)
            raise RecoverableRuntimePause(
                "T5 已完成确定性编译，但独立文件校验仍未通过。"
                f"具体原因：{str(err or 'unknown validation error')[:1200]}。"
                "请查看 external_executor/report/reboost_validation_report.json；"
                "现有 T4.5 产物未被修改，resume 只会重新校验和编译。"
            )

        self._record_runtime_completion(
            ctx,
            "t5_reboost_deterministic_compile",
            {"outputs": [str(path.relative_to(ctx.workspace_dir)) for path in ctx.outputs_expected.values() if path.exists()]},
            action_type="t5_reboost_deterministic_compile",
        )
        self.progress.emit(
            "[Research Reboost] 已从 T4.5 正式材料生成并校验 handoff，进入项目专属执行 Skill 发布。",
            important=True,
        )
        return True

    async def _maybe_finalize_project_skill_specialization_before_llm(
        self,
        ctx: ExecutionContext,
    ) -> bool:
        """Publish the T5 executor Skill Suite without an LLM shell-control loop.

        Project Skill specialization is a deterministic projection of the
        already-approved handoff and repository templates.  Earlier versions
        asked an LLM to invoke the wrapper scripts.  A schema mismatch then
        made the model probe files and rerun shell commands, even though the
        compiler had already produced the precise error report.  This path
        calls that same compiler directly, preserves the report, and turns a
        real compiler failure into one targeted recovery pause.
        """

        if ctx.task_id != "T5-SPECIALIZE-EXECUTOR-SKILLS":
            return False

        from ..skills.project_specialization.compiler import specialize_project_skills
        from ..skills.project_specialization.task_adapter import (
            _format_error_summary,
            _repo_root_from_ctx,
            build_project_skill_specialization_fingerprint,
            validate_project_skill_specialization_outputs,
            write_deterministic_project_skill_specialization_execution,
        )

        repo_root = _repo_root_from_ctx(ctx)
        fingerprint = build_project_skill_specialization_fingerprint(
            workspace=ctx.workspace_dir,
            repo_root=repo_root,
        )
        ctx.extra["project_skill_specialization_input_fingerprint"] = fingerprint

        existing = validate_project_skill_specialization_outputs(
            workspace=ctx.workspace_dir,
            repo_root=repo_root,
        )
        if existing.ok:
            ctx.extra["project_skill_specialization_reused"] = True
            ctx.extra["project_skill_specialization_validation"] = existing.to_record()
            ctx.extra["project_skill_specialization_report_status"] = existing.report_status
            self.progress.emit(
                "[Project Skill Specialization] 检测到已校验的项目专属 Skill Suite，跳过重复发布。",
                important=True,
            )
            self._record_runtime_completion(
                ctx,
                "t5_skill_specialization_resume_prefinalize",
                {
                    "outputs": [
                        str(path.relative_to(ctx.workspace_dir))
                        for path in ctx.outputs_expected.values()
                        if path.exists()
                    ]
                },
                action_type="t5_skill_specialization_prefinalize",
            )
            return True

        self.progress.emit(
            "[Project Skill Specialization] 正在从已校验 handoff 确定性发布项目专属 Skill Suite，不调用模型或 shell 诊断。",
            important=True,
        )
        build = specialize_project_skills(
            workspace=ctx.workspace_dir,
            repo_root=repo_root,
            dry_run=False,
            validate_only=False,
        )
        validation = validate_project_skill_specialization_outputs(
            workspace=ctx.workspace_dir,
            repo_root=repo_root,
        )
        try:
            execution = write_deterministic_project_skill_specialization_execution(
                workspace=ctx.workspace_dir,
                repo_root=repo_root,
            )
        except Exception as exc:  # noqa: BLE001 - preserve the compiler result below
            execution = {"execution_record_error": str(exc)}

        ctx.extra["project_skill_specialization_validation"] = validation.to_record()
        ctx.extra["project_skill_specialization_report_status"] = validation.report_status
        if build.status == "failed" or not validation.ok:
            detail = _format_error_summary(list(build.errors) + list(validation.errors))
            ctx.extra["project_skill_specialization_compile_failure"] = {
                "detail": detail,
                "report": "external_executor/report/skill_specialization_report.json",
                "execution": "external_executor/report/skill_specialization_execution.json",
                "execution_record": execution,
            }
            raise RecoverableRuntimePause(
                "T5 项目专属 Skill Suite 未能发布，系统没有让模型重复 shell 诊断。"
                f"具体原因：{detail[:1200]}。"
                "请查看 external_executor/report/skill_specialization_report.json 和 "
                "external_executor/report/skill_specialization_execution.json；"
                "修复报告中指出的 schema、模板或上游输入后再 resume。"
            )

        unresolved_count = len(validation.required_uncertain_fields)
        status_note = (
            "全部执行设置已明确。"
            if validation.report_status == "ready"
            else f"已保留 {unresolved_count} 项待确认字段；它们会在 T5 协议确认中显示，且不会授权正式实验。"
        )
        self._record_runtime_completion(
            ctx,
            "t5_skill_specialization_deterministic_publish",
            {
                "outputs": [
                    str(path.relative_to(ctx.workspace_dir))
                    for path in ctx.outputs_expected.values()
                    if path.exists()
                ],
                "report_status": validation.report_status,
                "required_uncertain_count": unresolved_count,
            },
            action_type="t5_skill_specialization_deterministic_publish",
        )
        self.progress.emit(
            "[Project Skill Specialization] 已发布并校验 13/13 个项目专属 Skill。" + status_note,
            important=True,
        )
        return True

    @staticmethod
    def _t5_reboost_recovery_allowed(stop_reason: str, error_msg: str | None) -> bool:
        if stop_reason not in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_ERROR,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            return False
        lowered = str(error_msg or "").casefold()
        fatal_markers = (
            "模型服务配置未通过验证",
            "invalid_api_key",
            "invalid api key",
            "authentication",
            "unauthorized",
            "permissiondenied",
            "permission denied",
            "human input",
            "ask_human",
            "需要用户输入",
        )
        return not any(marker in lowered for marker in fatal_markers)

    def _annotate_t5_reboost_recovery_report(
        self,
        ctx: ExecutionContext,
        *,
        stop_reason: str,
        error_msg: str | None,
        steps: int,
    ) -> None:
        report_path = ctx.workspace_dir / "external_executor" / "report" / "reboost_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                return
        except Exception:
            return
        previous_source = report.get("generation_source")
        report["generation_source"] = "llm_api_skill_execution_with_deterministic_timeout_recovery"
        report["llm_runtime_recovery"] = {
            "used": True,
            "reason": "llm_skill_execution_did_not_reach_handoff_publication_before_runtime_stop",
            "previous_generation_source": previous_source,
            "stop_reason_before_recovery": stop_reason,
            "error_before_recovery": error_msg,
            "steps_before_recovery": steps,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async def _maybe_finalize_t5_reboost_outputs(
        self,
        *,
        ctx: ExecutionContext,
        policy: WorkspaceAccessPolicy,
        stop_reason: str,
        error_msg: str | None,
        steps: int,
        llm_response_seen: bool,
    ) -> tuple[str, str | None]:
        """Close T5-REBOOST when the LLM path times out before calling the compiler.

        The normal path is still: LLM executes the research-reboost Skill and
        calls ``compile_research_reboost_handoff``.  This fallback only runs
        after at least one model step has happened and the task would otherwise
        pause/error without a validated handoff.
        """

        if ctx.task_id != "T5-REBOOST-GATE":
            return stop_reason, error_msg
        if stop_reason == AgentResult.STOP_FINISHED:
            return stop_reason, error_msg
        existing_ok, _existing_err = self._t5_reboost_artifacts_valid(ctx)
        if existing_ok:
            self._record_runtime_completion(
                ctx,
                "t5_reboost_resume_prefinalize",
                {"outputs": [str(path.relative_to(ctx.workspace_dir)) for path in ctx.outputs_expected.values() if path.exists()]},
                action_type="t5_reboost_prefinalize",
            )
            return AgentResult.STOP_FINISHED, None
        if not llm_response_seen or steps <= 0 or not self._t5_reboost_recovery_allowed(stop_reason, error_msg):
            return stop_reason, error_msg

        self.progress.emit(
            "[Research Reboost] LLM 已执行但未完成 handoff 发布；正在使用同一编译工具做确定性恢复收尾。",
            important=True,
        )
        result = await CompileResearchReboostHandoffTool(policy).execute()
        if not result.ok:
            self.log.warning(
                "t5_reboost_recovery_compile_failed",
                error=result.error,
                content=result.content,
            )
            return stop_reason, error_msg
        self._annotate_t5_reboost_recovery_report(
            ctx,
            stop_reason=stop_reason,
            error_msg=error_msg,
            steps=steps,
        )
        ok, err = self._t5_reboost_artifacts_valid(ctx)
        if not ok:
            self.log.warning("t5_reboost_recovery_validation_failed", error=err)
            return stop_reason, error_msg
        outputs = [str(path.relative_to(ctx.workspace_dir)) for path in ctx.outputs_expected.values() if path.exists()]
        self._record_runtime_completion(
            ctx,
            "t5_reboost_timeout_recovery",
            {"outputs": outputs},
            action_type="t5_reboost_timeout_recovery",
        )
        self.progress.emit(
            "[Research Reboost] 确定性恢复收尾已生成有效 handoff，T5-REBOOST 可以完成。",
            important=True,
        )
        return AgentResult.STOP_FINISHED, None

    def _refresh_resume_artifacts(self, ctx: ExecutionContext) -> None:
        """在任意退出路径刷新通用恢复快照，避免失败/暂停后仍看到旧进度。"""

        try:
            recovery = prepare_generic_resume_artifacts(
                ctx.workspace_dir,
                task_id=ctx.task_id,
                outputs_expected=ctx.outputs_expected,
            )
            ctx.extra.update(
                {
                    "resume_state_path": recovery.get("resume_state_path"),
                    "resume_existing_outputs": recovery.get("resume_existing_outputs"),
                    "resume_missing_outputs": recovery.get("resume_missing_outputs"),
                    "resume_output_summaries": recovery.get("resume_output_summaries"),
                    "resume_existing_artifacts": recovery.get("resume_existing_artifacts"),
                }
            )
        except Exception:  # pragma: no cover - refresh failure should not hide the real result
            self.log.exception("resume_artifact_refresh_failed")

    def _maybe_refresh_t3_resume_artifacts(self, ctx: ExecutionContext, stop_reason: str) -> None:
        """T3 退出时刷新 pending queue 快照，避免暂停/失败后仍显示旧进度。"""

        if ctx.task_id != "T3":
            return
        try:
            recovery = prepare_t3_resume_artifacts(
                ctx.workspace_dir,
                refresh_reason=f"runner_exit:{stop_reason}",
            )
            ctx.extra.update(
                {
                    "resume_queue_path": recovery.get("resume_queue_path"),
                    "resume_queue_count": recovery.get("resume_queue_count"),
                    "existing_note_count": recovery.get("existing_note_count"),
                }
            )
        except Exception:  # pragma: no cover - refresh failure should not fail a completed T3
            self.log.exception("t3_resume_artifact_refresh_failed")

    def _t3_finish_preflight(self, ctx: ExecutionContext) -> Message | None:
        """Defer a premature production-Reader finish without failing validation.

        A partially read T3 queue is not a malformed output.  Before the
        Reader reaches its configured completion line, refresh the
        deterministic manifest and pending queue, then send it back to the
        remaining concrete work.  This deliberately has no validation-retry
        counter: the filesystem state determines whether it can continue.

        Once the count reaches the same threshold used by ``ReaderAgent``'s
        validator, normal full validation still checks note structure,
        protected papers, comparison rows, BibTeX, and later coverage work.
        """

        # Task labels are also used by generic runtime tests and extension
        # agents.  The T3 literature lifecycle belongs only to the real
        # Reader, just as the abstract-sweep lifecycle below does.
        if ctx.task_id != "T3" or self.agent.spec.name != "reader":
            return None

        try:
            recovery = prepare_t3_resume_artifacts(
                ctx.workspace_dir,
                refresh_reason="finish_requested_incomplete_queue",
            )
            queue_config = load_deep_read_queue_config(ctx.workspace_dir)
            mode_params = get_effective_reader_read_params(ctx.workspace_dir)
            target_queue_count = int(recovery.get("target_queue_count") or 0)
            completed = int(recovery.get("completed_queue_entry_count") or 0)
            pending = int(recovery.get("resume_queue_count") or 0)
        except Exception:  # pragma: no cover - full validation provides the actionable failure
            self.log.exception("t3_finish_preflight_failed")
            return None

        # An absent/empty queue must be diagnosed by the full validator rather
        # than being hidden behind a continuation request with no actual work.
        if target_queue_count <= 0:
            return None

        configured_requirement = (
            queue_config.deep_read_target
            if require_deep_read_target(mode_params)
            else queue_config.deep_read_min
        )
        required = min(target_queue_count, configured_requirement)
        if completed >= required:
            return None
        if pending <= 0:
            return None

        remaining = required - completed
        examples = recovery.get("pending_examples")
        example_lines: list[str] = []
        if isinstance(examples, list):
            for item in examples[:3]:
                if not isinstance(item, dict):
                    continue
                rank = item.get("queue_rank")
                original_rank = item.get("original_queue_rank")
                paper = str(item.get("paper") or "unknown")
                suffix = f"（原队列 rank {original_rank}）" if original_rank not in (None, "") else ""
                example_lines.append(f"- pending rank {rank}: {paper}{suffix}")

        ctx.extra["t3_finish_preflight"] = {
            "completed": completed,
            "required": required,
            "target_queue_count": target_queue_count,
            "pending": pending,
            "remaining": remaining,
            "queue_path": str(recovery.get("resume_queue_path") or "literature/deep_read_queue_pending.jsonl"),
        }
        self.progress.emit(
            "[Reader Agent] T3 阅读队列未完成："
            f"已完成 {completed}/{required}，仍需 {remaining}；"
            f"已刷新剩余 {pending} 篇工作清单，继续从 pending rank 1 阅读。",
            important=True,
        )

        example_block = "\n" + "\n".join(example_lines) if example_lines else ""
        return Message.user(
            "[Runtime] T3 深读队列尚未达到本轮完成门槛，尚未进入输出校验，也未消耗校验修复次数。\n"
            f"当前已完成：{completed}/{required}；仍需：{remaining}。\n"
            f"已刷新 `{recovery.get('resume_queue_path') or 'literature/deep_read_queue_pending.jsonl'}`，"
            f"其中有 {pending} 篇尚未完成的论文。\n"
            "请立即从该 pending queue 的 `queue_rank=1` 开始继续：逐篇调用 `lookup_paper_record`，"
            "按证据可得性读取 PDF/章节并写入或修补结构合格的 note。"
            "在上述机械进度达到门槛前，不要再次调用 `finish_task`。"
            f"{example_block}"
        )

    async def _maybe_run_t3_abstract_sweep(
        self,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
        eff: EffectiveConfig,
    ) -> tuple[str, str | None]:
        """T3 退出后自动运行/恢复 abstract sweep 补读。

        finished 路径使用 Reader LLM 生成轻量笔记；max_steps/budget/interrupt
        路径只用确定性 fallback，避免中断后又发起长 LLM 补读，但仍保证
        shallow/backlog 论文不会因为任务被取消而永远没有 abstract note。
        """

        # Generic test or extension agents may use a T3-shaped task label to
        # exercise the runtime. Only the production Reader owns the T3
        # reading-coverage lifecycle. Applying a literature policy to those
        # agents made unrelated executions fail after their own validation.
        if ctx.task_id != "T3" or self.agent.spec.name != "reader":
            return stop_reason, error_msg
        if not has_shallow_read_coverage_contract(ctx.workspace_dir):
            self.log.info("t3_abstract_sweep_skipped_legacy_workspace_without_coverage_contract")
            return stop_reason, error_msg
        if ctx.extra.get("skip_t3_abstract_sweep"):
            return stop_reason, error_msg
        allowed_stop_reasons = {
            AgentResult.STOP_FINISHED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
            AgentResult.STOP_INTERRUPTED,
        }
        if stop_reason not in allowed_stop_reasons:
            return stop_reason, error_msg

        try:
            mode_params = get_effective_reader_read_params(ctx.workspace_dir)
            sweep_config = mode_params.get("abstract_sweep", {})
            if not sweep_config.get("enabled", False):
                return stop_reason, error_msg

            if stop_reason == AgentResult.STOP_FINISHED:
                self.progress.emit(
                    "[Reader Agent] T3 精读阶段已完成，开始 abstract sweep 补齐摘要级覆盖",
                    important=True,
                )
            else:
                self.progress.emit(
                    f"[Reader Agent] T3 以 {stop_reason} 退出，使用 deterministic abstract sweep 刷新浅层笔记覆盖...",
                    important=True,
                )

            def _report_abstract_sweep_progress(message: str) -> None:
                self.progress.emit(message, important=True)

            if stop_reason != AgentResult.STOP_FINISHED:
                result = run_abstract_sweep(
                    ctx.workspace_dir,
                    sweep_config,
                    progress_reporter=_report_abstract_sweep_progress,
                )
                ctx.extra["abstract_sweep"] = result
                if result.get("notes_generated", 0) > 0:
                    self.progress.emit(
                        "[Reader Agent] 摘要轻读已完成确定性回退："
                        f"筛选 {result['candidates_found']} 篇候选，写入 {result['notes_generated']} 份 ABSTRACT-ONLY 笔记；"
                        "正在核验阅读覆盖。",
                        important=True,
                    )
                return self._apply_t3_abstract_sweep_outcome(
                    ctx=ctx,
                    stop_reason=stop_reason,
                    error_msg=error_msg,
                    sweep_config=sweep_config,
                    result=result,
                )

            abstract_reader_binding = self.llm.resolve(
                profile=eff.llm_profile,
                tier=eff.llm_tier,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
            )[0][0]
            llm_retry_attempts, llm_retry_delay = self._llm_retry_overrides()
            llm_request_timeout = self._llm_request_timeout_seconds()

            await self._acquire_t3_shallow_candidate_pdfs(ctx, sweep_config)

            def _abstract_reader_messages(prompt: str) -> list[dict[str, str]]:
                return [
                    {
                        "role": "system",
                        "content": (
                            "You are ResearchOS Reader. Produce cautious abstract-only "
                            "paper notes in the exact requested Markdown or JSON structure."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]

            async def _reader_llm(_paper: dict[str, object], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=_abstract_reader_messages(prompt),
                    tools=None,
                    temperature=0.2,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=llm_request_timeout,
                    max_retries_per_model=llm_retry_attempts,
                    retry_base_delay=llm_retry_delay,
                )
                choice = llm_resp.raw.choices[0].message
                return str(getattr(choice, "content", "") or "")

            async def _abstract_batch_llm(_papers: list[dict[str, object]], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=_abstract_reader_messages(prompt),
                    tools=None,
                    temperature=0.15,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=llm_request_timeout,
                    max_retries_per_model=llm_retry_attempts,
                    retry_base_delay=llm_retry_delay,
                )
                choice = llm_resp.raw.choices[0].message
                return str(getattr(choice, "content", "") or "")

            def _count_abstract_batch_prompt(prompt: str) -> int:
                return self.llm.count_tokens(_abstract_reader_messages(prompt), abstract_reader_binding)

            async def _metadata_triage_llm(_papers: list[dict[str, object]], prompt: str) -> str:
                llm_resp = await self.llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are ResearchOS Reader. Triage metadata-only literature candidates as a batch. "
                                "Never claim to have read abstracts or full text, and never produce evidence claims."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    tools=None,
                    temperature=0.1,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=llm_request_timeout,
                    max_retries_per_model=llm_retry_attempts,
                    retry_base_delay=llm_retry_delay,
                )
                choice = llm_resp.raw.choices[0].message
                return str(getattr(choice, "content", "") or "")

            result = await run_abstract_sweep_with_reader(
                ctx.workspace_dir,
                sweep_config,
                abstract_reader=_reader_llm,
                abstract_batch_reader=_abstract_batch_llm,
                metadata_triage_reader=_metadata_triage_llm,
                provider_context_window=abstract_reader_binding.max_context,
                prompt_token_counter=_count_abstract_batch_prompt,
                progress_reporter=_report_abstract_sweep_progress,
            )
            ctx.extra["abstract_sweep"] = result

            if result.get("notes_generated", 0) > 0 or result.get("metadata_triage_count", 0) > 0:
                self.progress.emit(
                    "[Reader Agent] 摘要轻读已写入："
                    f"{result['notes_generated']} 份 ABSTRACT-ONLY 笔记；"
                    f"{result.get('metadata_triage_count', 0)} 篇 metadata-only 已转为补资源清单。"
                    "正在核验阅读覆盖。",
                    important=True,
                )
            else:
                self.progress.emit("[Reader Agent] 摘要轻读没有新的候选；正在核验已有阅读覆盖。", important=True)
            return self._apply_t3_abstract_sweep_outcome(
                ctx=ctx,
                stop_reason=stop_reason,
                error_msg=error_msg,
                sweep_config=sweep_config,
                result=result,
            )
        except Exception as exc:  # a missing reading manifest must not masquerade as T3 success
            self.log.exception("t3_abstract_sweep_failed")
            if stop_reason != AgentResult.STOP_FINISHED:
                return stop_reason, error_msg
            message = (
                "T3 深读已保存，但摘要轻读覆盖未能生成，因而不能进入 T3.5。"
                f"原因：{type(exc).__name__}: {str(exc)[:500]}。"
                "现有论文笔记未被删除；resume 将只重试摘要轻读与覆盖检查。"
            )
            self._mark_runtime_recovery(
                ctx,
                kind="literature_coverage",
                error=message,
                details={"stage": "T3", "artifact": "literature/shallow_read_manifest.json"},
            )
            self.progress.emit(f"[Reader Agent] {message}", important=True)
            return AgentResult.STOP_INTERRUPTED, message

    async def _acquire_t3_shallow_candidate_pdfs(
        self,
        ctx: ExecutionContext,
        sweep_config: dict[str, Any],
    ) -> None:
        """Try access acquisition for the exact shallow-reading candidates only.

        T2 already attempts every retained candidate.  A numeric T3 sweep may
        responsibly draw a bounded readable refill from ``papers_backlog``;
        those selected papers deserve the same PDF availability attempt before
        they are offered for a voluntary full/partial-text upgrade.  Download
        receipts remain availability facts and never promote evidence level.
        """

        candidates = build_sweep_candidates(ctx.workspace_dir, sweep_config)
        if not candidates:
            return
        config = load_t2_finalize_config(ctx.workspace_dir)
        if not config.pdf_acquisition_enabled:
            return
        try:
            manifest = await acquire_retained_pdfs(
                ctx.workspace_dir,
                candidates,
                max_concurrency=config.pdf_acquisition_max_concurrency,
                retry_terminal_failures=config.pdf_acquisition_retry_terminal_failures,
                skip_known_books=config.pdf_acquisition_skip_known_books,
                max_auto_read_pages=config.pdf_acquisition_max_auto_read_pages,
                source_pool="t3_shallow_read_candidates",
            )
            counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
            self.progress.emit(
                "[Literature] 摘要轻读候选的 PDF 可得性（仅访问状态）："
                f"本地可解析 {int(counts.get('available_local') or 0)}/"
                f"{int(counts.get('total') or len(candidates))}。"
                "已登记可升级阅读队列；未改变任何证据等级。",
                important=True,
            )
        except Exception as exc:  # access enrichment must not erase abstract coverage
            self.log.warning(
                "t3_shallow_pdf_acquisition_failed",
                error=f"{type(exc).__name__}: {exc}",
            )

    def _apply_t3_abstract_sweep_outcome(
        self,
        *,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
        sweep_config: dict[str, Any],
        result: dict[str, Any],
    ) -> tuple[str, str | None]:
        """Refresh the shared literature manifest and block T3 on real shallow shortfall."""

        build_literature_manifest(ctx.workspace_dir, write=True)
        coverage_ok, coverage_error = validate_abstract_sweep_coverage(
            ctx.workspace_dir,
            sweep_config,
            require_manifest=True,
        )
        target = result.get("shallow_read_target")
        actual = result.get("shallow_read_note_count")
        if coverage_ok:
            if target not in (None, "all_readable"):
                self.progress.emit(
                    f"[Reader Agent] 摘要轻读覆盖已核验：{actual}/{target} 篇有效 ABSTRACT-ONLY 笔记。"
                    "metadata-only 分诊未计入覆盖；完整阅读验收将在本阶段结果面板中汇总。",
                    important=True,
                )
            return stop_reason, error_msg

        message = (
            "T3 已保留深读与已完成的浅读笔记，但不能进入 T3.5。"
            f"原因：{str(coverage_error or result.get('blocking_reason') or '摘要轻读覆盖未完成')[:1000]} "
            "请 resume 继续补齐可读 backlog，或在资料不足时进入定向补检。"
        )
        self._mark_runtime_recovery(
            ctx,
            kind="literature_coverage",
            error=message,
            details={
                "stage": "T3",
                "manifest": str(result.get("manifest_path") or "literature/shallow_read_manifest.json"),
                "target": target,
                "actual": actual,
                "unfulfilled_target": result.get("unfulfilled_target"),
                "metadata_triage_count": result.get("metadata_triage_count"),
            },
        )
        self.progress.emit(f"[Reader Agent] {message}", important=True)
        if stop_reason == AgentResult.STOP_FINISHED:
            return AgentResult.STOP_INTERRUPTED, message
        return stop_reason, error_msg

    async def _maybe_finalize_t2_before_llm(self, ctx: ExecutionContext) -> bool:
        """T2 续跑时，只有已足够完整的产物或显式恢复场景才跳过 LLM。

        冷启动后第一轮检索可能已经因为多源工具返回大量 raw，但这不等于
        Scout 的检索覆盖规划已经完成。因此这里不能只看 raw_count 自动结束。
        """

        if ctx.task_id != "T2":
            return False

        if bool(ctx.extra.get("t2_user_requested_expansion")):
            # The user explicitly chose "expand / adjust query" at the T2
            # coverage gate. Existing outputs are valuable evidence to retain,
            # but they are not a substitute for the newly requested Scout
            # round.  If that round already changed the persisted corpus before
            # an interruption, resume must finalize those results rather than
            # launch another full search loop.
            if self._t2_expansion_has_persisted_progress(ctx):
                return await self._finalize_t2_from_raw(
                    ctx,
                    mode="t2_expansion_resume_prefinalize",
                    min_raw_count=self._t2_finish_finalize_min_raw(ctx),
                    start_message="[T2] 检测到已保存的补检结果，正在整理本轮新增文献...",
                    success_message="[T2] 补检结果已整理完成，跳过重复检索。",
                )
            self.progress.emit(
                "[T2] 已按你的选择进入补检：保留现有论文池，并补充尚未覆盖的检索角度。",
                important=True,
            )
            return False

        if ctx.outputs_expected and all(path.exists() for path in ctx.outputs_expected.values()):
            ok, _err = self.agent.validate_outputs(ctx)
            manifest_ok, manifest_err = validate_t2_finalize_manifest(ctx.workspace_dir)
            if ok and manifest_ok:
                self._record_runtime_completion(
                    ctx,
                    "t2_existing_outputs_prefinalize",
                    {"raw_count": self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl")},
                )
                self.progress.emit(
                    "[Scout Agent] T2 检测到已有完整产物且校验通过，跳过重复 LLM 续跑",
                    important=True,
                )
                return True
            if ok and not manifest_ok:
                self.log.info("t2_existing_outputs_prefinalize_skipped", reason=manifest_err)

        if not self._is_resume_run(ctx):
            return False

        manifest_ok, manifest_err = validate_t2_finalize_manifest(ctx.workspace_dir)
        if not manifest_ok and (ctx.workspace_dir / "literature" / "papers_raw.jsonl").exists():
            if not self._raw_t2_cache_newer_than_inputs(ctx):
                self.log.info("t2_resume_prefinalize_skipped", reason=manifest_err)
                return False

        return await self._finalize_t2_from_raw(
            ctx,
            mode="t2_resume_prefinalize",
            min_raw_count=self._t2_finish_finalize_min_raw(ctx),
            start_message="[Scout Agent] T2 resume 检测到已有 papers_raw，尝试确定性补齐缺失产物...",
            success_message="[Scout Agent] T2 resume 确定性补齐成功，跳过 LLM 续跑",
        )

    def _t2_expansion_has_persisted_progress(self, ctx: ExecutionContext) -> bool:
        """Detect an interrupted T2 supplement without trusting volatile state.

        ``coverage_decision.json`` records the corpus fingerprints presented to
        the user before selecting "expand".  Any changed tracked artifact means
        the requested round has already produced durable work and can safely be
        finalized from ``papers_raw``.  Older decisions did not fingerprint the
        raw file, so search-log and downstream-artifact changes remain valid
        compatibility signals.
        """

        decision_path = ctx.workspace_dir / "literature" / "coverage_decision.json"
        if not decision_path.is_file():
            return False
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(decision, dict) or decision.get("selected_option") != "rerun_t2_expand":
            return False
        fingerprints = decision.get("input_fingerprints")
        if not isinstance(fingerprints, dict):
            return False

        tracked_labels = (
            "papers_raw",
            "search_log",
            "missing_areas",
            "papers_dedup",
            "papers_verified",
            "deep_read_queue",
        )
        for label in tracked_labels:
            expected = fingerprints.get(label)
            if not isinstance(expected, dict):
                continue
            rel_path = str(expected.get("path") or "").strip()
            expected_hash = str(expected.get("sha256") or "").strip()
            if not rel_path or not expected_hash:
                continue
            path = ctx.workspace_dir / rel_path
            if not path.is_file():
                continue
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                continue
            if digest.hexdigest() != expected_hash:
                return True
        return False

    def _raw_t2_cache_newer_than_inputs(self, ctx: ExecutionContext) -> bool:
        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if not raw_path.exists() or raw_path.stat().st_size <= 0:
            return False
        return self._outputs_newer_than_inputs(
            ctx,
            outputs=[raw_path],
            inputs=[
                ctx.workspace_dir / "project.yaml",
                ctx.workspace_dir / "literature" / "bridge_domain_plan.json",
                ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl",
                ctx.workspace_dir / "user_seeds" / "seed_outline_profile.json",
                ctx.workspace_dir / "user_seeds" / "seed_external_resources.jsonl",
            ],
            event="t2_resume_prefinalize_skipped",
            reason="papers_raw_older_than_t2_inputs",
        )

    async def _ensure_shared_pdf_acquisition(self, ctx: ExecutionContext) -> None:
        """Backfill the shared PDF-access receipt for existing/resumed workspaces.

        T2 normally performs this work.  Older workspaces can resume directly
        at T3/T3.5/T3.6/T4/T5/T8, though, so waiting for a new T2 run would
        leave their retained candidates without the same acquisition attempt.
        This preflight is intentionally non-fatal: unavailable PDFs remain
        auditable abstract/metadata evidence rather than blocking a valid
        research workflow.
        """

        literature_consumers = {
            "T3", "T3.5", "T3.6-GATE-SURVEY", "T3.6-PLAN", "T3.6-GATE-CORPUS",
            "T3.6-EXPAND", "T3.6-SUPPLEMENT-READ", "T3.6-STATE", "T3.6-VISUALS", "T4", "T4.5", "T5-HANDOFF",
            "T8", "T8-RESOURCE",
        }
        if ctx.task_id not in literature_consumers:
            return
        verified_path = ctx.workspace_dir / "literature" / "papers_verified.jsonl"
        if not verified_path.is_file():
            return
        try:
            records = [
                value for line in verified_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
                for value in [json.loads(line)]
                if isinstance(value, dict)
            ]
        except (OSError, json.JSONDecodeError):
            return
        if not records:
            return
        config = load_t2_finalize_config(ctx.workspace_dir)
        if not config.pdf_acquisition_enabled:
            return
        try:
            records, legacy_evidence_repairs = repair_access_only_evidence_levels(ctx.workspace_dir, records)
            manifest = await acquire_retained_pdfs(
                ctx.workspace_dir,
                records,
                max_concurrency=config.pdf_acquisition_max_concurrency,
                retry_terminal_failures=config.pdf_acquisition_retry_terminal_failures,
                skip_known_books=config.pdf_acquisition_skip_known_books,
                max_auto_read_pages=config.pdf_acquisition_max_auto_read_pages,
                source_pool="papers_verified_resume_preflight",
            )
            annotated = attach_pdf_acquisition(records, manifest)
            verified_path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in annotated),
                encoding="utf-8",
            )
            build_literature_manifest(ctx.workspace_dir, write=True)
            counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
            self.progress.emit(
                "[Literature] 已检查所有保留候选的 PDF 可得性："
                f"本地可解析 {int(counts.get('available_local') or 0)}/"
                f"{int(counts.get('total') or len(records))}；"
                f"可获得性不等同于已全文阅读；已修正旧版 access→evidence 误标 {legacy_evidence_repairs} 条。",
                important=True,
            )
        except Exception as exc:  # availability enrichment must not strand an old resume
            self.log.warning("shared_pdf_acquisition_preflight_failed", error=f"{type(exc).__name__}: {exc}")

    async def _maybe_finalize_t3_before_llm(self, ctx: ExecutionContext) -> bool:
        """T3 续跑时，已有 deep-read 产物通过校验则直接完成。

        T3 的成功条件是“足够且结构合格的深读证据”，不是必须把
        `deep_read_queue_pending.jsonl` 中所有低优先级或 overflow 条目全部读完。
        若当前 artifact 已满足 Reader validator，继续让 LLM 补 alias/stub 会浪费预算。
        """

        if ctx.task_id != "T3":
            return False

        expected_paths = [
            ctx.workspace_dir / "literature" / "deep_read_notes",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
            ctx.workspace_dir / "literature" / "related_work.bib",
        ]
        if any(not path.exists() for path in expected_paths):
            return False

        manifest_path = ctx.workspace_dir / "literature" / "notes_manifest.json"
        if not manifest_path.exists() or manifest_path.stat().st_size <= 0:
            self.log.info("t3_resume_prefinalize_skipped", reason="notes_manifest_missing")
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log.info("t3_resume_prefinalize_skipped", reason=f"notes_manifest_invalid:{exc}")
            return False
        if not isinstance(manifest, dict):
            self.log.info("t3_resume_prefinalize_skipped", reason="notes_manifest_not_object")
            return False
        ok, err = validate_t3_input_fingerprints(ctx.workspace_dir, manifest)
        if not ok:
            self.log.info("t3_resume_prefinalize_skipped", reason=err)
            return False

        # The post-read abstract sweep is deliberately executed in this run's
        # finalizer.  Validate existing deep evidence here without treating a
        # missing/stale shallow manifest as a reason to replay every deep-read
        # LLM action; the finalizer will rebuild it and enforce the numeric
        # coverage target before T3 can report success.
        ctx.extra["_t3_pending_abstract_sweep"] = True
        try:
            ok, err = self.agent.validate_outputs(ctx)
        finally:
            ctx.extra.pop("_t3_pending_abstract_sweep", None)
        if not ok:
            self.log.info("t3_resume_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Reader Agent] T3 检测到已有 deep-read 产物且校验通过，跳过重复 deep-read LLM",
            important=True,
        )
        # Do not suppress abstract sweep here. Resume may have valid deep-read
        # notes while shallow/metadata notes are missing or stale; the post-run
        # sweep is the cheap deterministic/Reader path that repairs that gap.
        self._record_runtime_completion(
            ctx,
            "t3_resume_prefinalize",
            {
                "outputs": [
                    "literature/deep_read_notes",
                    "literature/comparison_table.csv",
                    "literature/related_work.bib",
                ],
            },
            action_type="t3_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t36_section_before_llm(self, ctx: ExecutionContext) -> bool:
        """Advance a validated survey section after a pause without rewriting it.

        Section writing is the only T3.6 phase where an interrupted provider
        run can leave a complete, valid single-file artifact while the global
        survey remains unfinished.  Replaying the model call is harmful: it
        needlessly changes a reviewed section and used to combine with broad
        write privileges to disturb later sections.  A resumed section task
        therefore validates its declared output/state pair first and advances
        directly when both remain current.
        """

        if not ctx.task_id.startswith("T3.6-SEC-") or not self._is_resume_run(ctx):
            return False
        section_path = ctx.outputs_expected.get("section")
        if section_path is None or not section_path.exists() or section_path.stat().st_size <= 0:
            return False
        state_path = ctx.workspace_dir / "drafts" / "survey" / "survey_state.json"
        if not state_path.exists() or state_path.stat().st_size <= 0:
            return False
        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t36_section_resume_prefinalize_skipped", task=ctx.task_id, reason=err)
            return False
        relative_section = safe_relative(section_path, ctx.workspace_dir) or str(section_path)
        self.progress.emit(
            f"[Survey Writer Agent] {ctx.task_id} 的章节、状态与证据校验已通过；恢复时不重写 {relative_section}。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t36_section_resume_prefinalize",
            {"outputs": [relative_section, "drafts/survey/survey_state.json"]},
            action_type="t36_section_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t36_visuals_before_llm(
        self,
        ctx: ExecutionContext,
        *,
        tool_map: dict[str, Tool],
    ) -> bool:
        """Build or reuse the deterministic T3.6 taxonomy visual without an LLM loop.

        T3.6-VISUALS has no remaining scholarly-writing decision.  The survey
        plan is already authored and the only allowed output is a factual
        taxonomy rendering.  Running it through an LLM lets a retry toggle
        validation parameters, creating incompatible manifests.  Rebuild a
        stale or invalid derived manifest directly instead; a legitimate
        ``skipped`` manifest is a completed result, not a repair prompt.
        """

        if ctx.task_id != "T3.6-VISUALS":
            return False
        manifest_path = ctx.workspace_dir / "drafts" / "survey" / "figures" / "survey_visual_manifest.json"
        pdf_path = ctx.workspace_dir / "drafts" / "survey" / "figures" / "fig_taxonomy_overview.pdf"
        reused = False
        status = ""
        if manifest_path.exists() and manifest_path.stat().st_size > 0:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                manifest = None
            status = str(manifest.get("status") or "").strip().lower() if isinstance(manifest, dict) else ""
            # Re-evaluate a historical skipped manifest.  Citation-key
            # resolution may have improved after a resume migration, while a
            # valid generated PDF remains safe and inexpensive to reuse.
            if status == "generated" and pdf_path.exists() and pdf_path.stat().st_size > 0:
                reused, _err = self.agent.validate_outputs(ctx)

        if not reused:
            builder = tool_map.get("build_survey_figures")
            if builder is None:
                return False
            self.progress.emit(
                "[Survey Writer Agent] T3.6-VISUALS 正在确定性重建 taxonomy visual manifest；不会调用模型或改变综述正文。",
                important=True,
            )
            result = await builder.execute()
            if not result.ok:
                detail = " ".join(str(result.content or result.error or "unknown visual build failure").split())[:1200]
                raise RecoverableRuntimePause(
                    "T3.6-VISUALS 的确定性图表编译未完成。"
                    f"原因：{detail}。已保留 survey plan、文献笔记与既有图表产物；"
                    "请修复实际输入后 resume，不会进入模型重试循环。"
                )
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise RecoverableRuntimePause(
                    "T3.6-VISUALS 已调用图表工具，但未写出可读取的 visual manifest。"
                    f"原因：{exc}。"
                ) from exc
            status = str(manifest.get("status") or "").strip().lower() if isinstance(manifest, dict) else ""
            ok, err = self.agent.validate_outputs(ctx)
            if not ok:
                raise RecoverableRuntimePause(
                    "T3.6-VISUALS 已确定性编译，但 visual manifest 未通过完整性校验。"
                    f"原因：{str(err or 'unknown validation error')[:1200]}。"
                )
        self.progress.emit(
            "[Survey Writer Agent] T3.6-VISUALS 已复用通过校验的 taxonomy visual manifest，跳过重复生成"
            if reused
            else "[Survey Writer Agent] T3.6-VISUALS 已确定性编译并验证 taxonomy visual manifest",
            important=True,
        )
        outputs = ["drafts/survey/figures/survey_visual_manifest.json"]
        if status == "generated":
            outputs.insert(0, "drafts/survey/figures/fig_taxonomy_overview.pdf")
        self._record_runtime_completion(
            ctx,
            "t36_visuals_resume_prefinalize" if reused else "t36_visuals_deterministic",
            {"outputs": outputs, "status": status},
            action_type="t36_visuals_resume_prefinalize" if reused else "t36_visuals_deterministic",
        )
        return True

    async def _maybe_finalize_t36_compile_before_llm(
        self,
        ctx: ExecutionContext,
        *,
        tool_map: dict[str, Tool],
    ) -> bool:
        """Compile T3.6 deterministically or reuse an already valid PDF.

        T3.6-COMPILE has no remaining scientific-writing decision.  Letting a
        general Survey Writer loop here creates a surprising failure mode: it
        can spend many model calls inspecting the document, or try to repair
        the derived ``survey.tex`` even though its source sections have already
        been reviewed.  The compilation command, report, and validation are
        deterministic, so they belong in the runtime rather than in a model
        tool-choice loop.

        A failed compiler invocation remains truthful: it writes the native
        compile report and raises a recoverable pause for a human decision.
        It never fabricates a PDF, edits source prose, or declares success.
        """

        if ctx.task_id != "T3.6-COMPILE":
            return False
        expected_paths = [
            ctx.workspace_dir / "drafts" / "survey" / "survey.pdf",
            ctx.workspace_dir / "drafts" / "survey" / "survey.log",
            ctx.workspace_dir / "drafts" / "survey" / "survey_compile_report.json",
        ]
        if all(path.exists() and path.stat().st_size > 0 for path in expected_paths):
            ok, err = self.agent.validate_outputs(ctx)
            if ok:
                self.progress.emit(
                    "[Survey Writer Agent] T3.6-COMPILE 检测到已有 PDF、log 和 compile report 且校验通过，跳过重复编译",
                    important=True,
                )
                self._record_runtime_completion(
                    ctx,
                    "t36_compile_resume_prefinalize",
                    {
                        "outputs": [
                            "drafts/survey/survey.pdf",
                            "drafts/survey/survey.log",
                            "drafts/survey/survey_compile_report.json",
                        ],
                    },
                    action_type="t36_compile_resume_prefinalize",
                )
                return True
            self.log.info("t36_compile_existing_artifacts_not_reusable", reason=err)
            audit_error = str(err or "")
            if any(
                marker in audit_error.casefold()
                for marker in (
                    "survey_audit.json 存在硬失败",
                    "survey audit",
                    "citation_diversity",
                )
            ):
                raise RecoverableRuntimePause(
                    "T3.6-COMPILE 在启动编译前发现当前 survey audit 需要来源级修复。"
                    f"原因：{audit_error[:1200]}。"
                    "不会重复编译同一份 TeX；将保留 PDF、log、sections 和 audit，并回到定向 source-repair Gate。"
                )

        compiler = tool_map.get("latex_compile")
        if compiler is None:
            # Small unit-test agents and third-party integrations may still
            # provide their own compile loop.  Production SurveyWriter always
            # exposes this tool through its compile-only tool policy.
            return False

        tex_path = ctx.workspace_dir / "drafts" / "survey" / "survey.tex"
        if not tex_path.exists() or tex_path.stat().st_size <= 0:
            raise RecoverableRuntimePause(
                "T3.6-COMPILE 无法开始：缺少 drafts/survey/survey.tex。"
                "请回到拼装或 Review 检查 source sections，现有产物没有被删除。"
            )

        engine = self._t36_compile_engine(ctx.workspace_dir)
        self.progress.emit(
            f"[Survey Compile] 正在以 {engine} 编译已审核的 survey.tex；不会调用模型或改写正文。",
            important=True,
        )
        result = await compiler.execute(
            tex_path="drafts/survey/survey.tex",
            engine=engine,
            bibtex=True,
            backend="auto",
            allow_docker_fallback=True,
            auto_fit_wide_tables=False,
        )
        if not result.ok:
            detail = " ".join(str(result.content or result.error or "unknown compile failure").split())[:1200]
            raise RecoverableRuntimePause(
                "T3.6-COMPILE 的确定性编译未完成。"
                f"原因：{detail}。已保留 survey.tex、section、audit 和 compile report；"
                "请在恢复 Gate 中选择重试编译、回到 Review 修复源文件，或暂停检查环境。"
            )

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            raise RecoverableRuntimePause(
                "T3.6-COMPILE 已执行，但 PDF/report 未通过完整性校验。"
                f"原因：{str(err or 'unknown validation error')[:1200]}。"
                "不会自动改写 survey.tex；请在恢复 Gate 中选择重试或回到 Review 修复来源文件。"
            )
        self._record_runtime_completion(
            ctx,
            "t36_compile_deterministic",
            {
                "outputs": [
                    "drafts/survey/survey.pdf",
                    "drafts/survey/survey.log",
                    "drafts/survey/survey_compile_report.json",
                ],
                "engine": engine,
            },
            action_type="t36_compile_deterministic",
        )
        return True

    @staticmethod
    def _t36_compile_engine(workspace_dir: Path) -> str:
        """Choose the compiler only from persisted T3.6 language state."""

        candidates = (
            workspace_dir / "drafts" / "survey" / "survey_state.json",
            workspace_dir / "drafts" / "survey" / "writing_template.json",
            workspace_dir / "drafts" / "survey" / "survey_plan.json",
        )
        language = ""
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            shared_facts = payload.get("shared_facts")
            values = (
                payload.get("writing_language"),
                payload.get("language"),
                shared_facts.get("writing_language") if isinstance(shared_facts, dict) else None,
            )
            for value in values:
                if isinstance(value, str) and value.strip():
                    language = value.strip().casefold()
                    break
            if language:
                break
        return "xelatex" if language in {"zh", "zh-cn", "chinese", "中文"} else "pdflatex"

    def _maybe_prepare_t4_context_pack_before_prompt(self, ctx: ExecutionContext) -> bool:
        """Prepare compact T4 inputs before rendering the ideation prompt."""

        if not self._is_t4_ideation_agent(ctx):
            return False
        if has_current_t4_prerun_confirmation(ctx.workspace_dir):
            # The evolutionary controller builds its own Evidence Index and
            # route-scoped bundles. Retaining the legacy compact pack here
            # would duplicate work and produce an unrelated six-artifact
            # progress counter before the new eight-phase run begins.
            return False
        if not self._t4_gate1_user_selection_exists(ctx):
            gate1_ready, _gate1_err = validate_t4_gate1_ready(ctx.workspace_dir)
            cards_ready, _cards_err = validate_t4_portfolio_final_cards(ctx.workspace_dir)
            if gate1_ready and cards_ready:
                backfill = ensure_t4_evidence_pool(ctx.workspace_dir)
                if backfill.get("changed"):
                    self.progress.emit(
                        "[T4 Evidence Pool] 已补齐历史 workspace 的可回查笔记索引："
                        f"首轮 {backfill.get('selected_count', 0)} 张，延后可回查 {backfill.get('deferred_count', 0)} 张。",
                        important=True,
                    )
                self._refresh_t4_gate1_progress(ctx, active_path=None, announce=False)
                return False
        try:
            pack = prepare_t4_context_pack(ctx.workspace_dir)
        except Exception as exc:
            # A compact-pack construction error is a code, path, or artifact
            # contract defect.  It is not equivalent to an intentionally
            # sparse evidence set, which ``prepare_t4_context_pack`` already
            # represents as a valid bounded pack.  Falling back to arbitrary
            # raw reads here hid that defect and made it look as though T4 had
            # merely ignored its prepared Cross-domain material.  Propagate it
            # to the startup boundary, which records a durable recovery gate
            # with the original exception instead of letting a CLI traceback
            # escape.
            raise RuntimeError(
                "T4 compact context-pack preparation failed; "
                "the workspace/material contract needs repair before prompt rendering: "
                f"{type(exc).__name__}: {str(exc) or repr(exc)}"
            ) from exc

        summary = pack.get("note_card_summary") if isinstance(pack.get("note_card_summary"), dict) else {}
        outputs = pack.get("outputs") if isinstance(pack.get("outputs"), list) else []
        selected = summary.get("selected_card_count", 0)
        usable = summary.get("usable_card_count", 0)
        raw = summary.get("raw_card_count", 0)
        self.progress.emit(
            "[Ideation Agent] T4 已准备 compact context pack\n"
            f"- 笔记卡: 已选 {selected} 张；可用 {usable} 张；原始 {raw} 张\n"
            f"- 写入: {'；'.join(str(item) for item in outputs[:3])}\n"
            "- 用途: 让 T4 先基于压缩证据生成 Gate1 候选，减少无目标分页读取",
            important=True,
        )
        self._refresh_t4_gate1_progress(ctx, active_path=None)
        self.progress.progress_file_update(
            label="Ideation/T4 进度",
            path="ideation/t4_progress.md",
            bullets=summarize_progress_markdown(ctx.workspace_dir / "ideation" / "t4_progress.md", max_items=4),
        )
        actions = ctx.extra.setdefault("runtime_actions", [])
        if isinstance(actions, list):
            actions.append(
                {
                    "type": "t4_context_pack_prepared",
                    "mode": "t4_context_pack_prepared",
                    "outputs": outputs,
                    "selected_note_cards": selected,
                    "usable_note_cards": usable,
                }
            )
        ctx.extra["t4_context_pack_prepared"] = True
        return True

    async def _maybe_run_t4_evolution_before_llm(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        budget: BudgetTracker,
    ) -> bool:
        """Run the confirmed evolutionary T4 path before the legacy tool loop.

        This is intentionally an internal T4 facade, not a new external state
        machine node. A successful run writes the retained Gate1 artifacts and
        returns ``t4_gate1_ready``. Gate1 then routes a selected ready
        Candidate to T4.5, while evolution requests return to T4 for a new
        preserved Population.
        """

        if not self._is_t4_ideation_agent(ctx) or self._t4_gate1_user_selection_exists(ctx):
            return False
        if not has_current_t4_prerun_confirmation(ctx.workspace_dir):
            return False
        self._record_t4_execution_mode(
            ctx,
            mode="evolutionary",
            reason="current_pre_run_confirmation",
        )
        store = T4ArtifactStore(ctx.workspace_dir)
        try:
            run_config = store.read_run_config()
        except ValueError:
            return False
        async def role_call(system_contract: str, user_prompt: str) -> str:
            return await self._call_t4_evolution_role(
                ctx=ctx,
                eff=eff,
                budget=budget,
                system_contract=system_contract,
                user_prompt=user_prompt,
            )

        async def progress_callback(phase: EvolutionPhase, status: str, payload: dict[str, object]) -> None:
            self._record_t4_evolution_activity(ctx, phase=phase, status=status, payload=payload)
            self._render_t4_evolution_phase(phase=phase, status=status, payload=payload)

        llm_retry_attempts, llm_retry_delay = self._llm_retry_overrides()
        llm_request_timeout = self._llm_request_timeout_seconds()
        invoker = LLMJsonRoleInvoker(
            config=T4RoleCallConfig(
                tier=eff.llm_tier,
                profile=eff.llm_profile,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
                timeout=llm_request_timeout,
                max_retries_per_model=llm_retry_attempts,
                retry_base_delay=llm_retry_delay,
                target_profile=run_config.target_profile,
            ),
            call=role_call,
        )
        t4_settings = load_t4_evolution_settings()
        generator = LLMIdeaGenerator(invoker)
        enricher = LLMCandidateEnricher(invoker)
        scorer = LLMIdeaScorer(
            invoker,
            crossover_structured_repair_attempts=t4_settings.crossover_structured_repair_attempts,
        )
        evolver = LLMIdeaEvolver(invoker)
        final_card_compiler = LLMFinalIdeaCardCompiler(invoker)
        controller = IdeaEvolutionController(
            workspace_dir=ctx.workspace_dir,
            settings=t4_settings,
            generator=generator,
            scorer=scorer,
            evolver=evolver,
            enricher=enricher,
            progress_callback=progress_callback,
        )
        ctx.extra["t4_evolution_active"] = True
        try:
            operation = ctx.extra.get("t4_operation_request")
            operation_action = str(operation.get("action") or "") if isinstance(operation, dict) else ""
            directive = operation.get("directive") if isinstance(operation, dict) and isinstance(operation.get("directive"), dict) else {}
            # Reconcile the narrow legacy gap between a completed survival
            # snapshot and the Final Card checkpoint before deciding whether
            # T4 must run an Evolution round.  This is deterministic: it
            # proves the active Population, Portfolio, candidate dossiers and
            # independent scores agree, then writes only the missing receipt.
            # It never creates a Candidate or changes a score.
            compatibility_migration = store.migrate_crossover_compatibility_records()
            ctx.extra["t4_compatibility_migration"] = compatibility_migration
            cards_before_recovery, _cards_before_recovery_error = validate_t4_portfolio_final_cards(ctx.workspace_dir)
            if not cards_before_recovery:
                reconciled_checkpoint, reconciliation_error = store.ensure_final_card_checkpoint_for_completed_population(
                    operation=operation if isinstance(operation, dict) else None,
                )
                if reconciled_checkpoint is not None:
                    ctx.extra["t4_final_card_checkpoint_reconciled"] = True
                    self.progress.emit(
                        "T4 · 已识别已完成的 Population；正在从保存的 Portfolio 补齐决策卡，不会重新演化候选。",
                        important=True,
                    )
                elif reconciliation_error:
                    ctx.extra["t4_final_card_checkpoint_reconciliation_error"] = reconciliation_error
            # A recoverable Card Compiler failure can happen after a human
            # operation has already produced a new Population. The durable
            # checkpoint is written before the first card call and binds that
            # consumed operation to its output Population. Gate1's structural
            # projection is intentionally written later, so checking only
            # structural readiness here would repeat the operation when card
            # compilation failed before projection.
            repair_checkpoint, _repair_checkpoint_error = store.current_final_card_repair_checkpoint(
                operation=operation if isinstance(operation, dict) else None,
            )
            structural_gate_ready, _structural_error = validate_t4_gate1_ready(ctx.workspace_dir)
            cards_ready, _card_error = validate_t4_portfolio_final_cards(ctx.workspace_dir)
            if not cards_ready:
                readiness_diagnostic = classify_final_card_readiness_error(_card_error)
                profile_refresh = archive_final_card_profile_mismatch(
                    ctx.workspace_dir,
                    current_profile_type=run_config.target_profile.profile_type,
                )
                store.write_json(
                    "ideation/evolution/diagnostics/final_card_readiness.json",
                    {
                        "schema_version": "1.0.0",
                        "semantics": "t4_final_idea_card_readiness_diagnostic",
                        "status": "repair_required" if readiness_diagnostic.repair_scheduled else "repair_prerequisite_required",
                        "failure": readiness_diagnostic.as_dict(),
                        "profile_refresh": profile_refresh,
                    },
                )
                if profile_refresh is not None:
                    self.progress.emit(
                        "T4 · 检测到论文取向已变化；已保留旧版 Candidate Card，正在只为当前取向重编译研究者可读说明。",
                        important=True,
                    )
            card_only_recovery = repair_checkpoint is not None
            if card_only_recovery:
                try:
                    result = controller.load_active_result_for_final_card_repair()
                except Exception as exc:
                    diagnostic = classify_final_card_exception(
                        exc,
                        stage="source_reload_for_final_card_repair",
                    )
                    store.write_json(
                        "ideation/evolution/diagnostics/final_card_source_reload.json",
                        {
                            "schema_version": "1.0.0",
                            "semantics": "t4_final_idea_card_compilation_diagnostic",
                            "status": "repair_prerequisite_required",
                            "failure": diagnostic.as_dict(),
                        },
                    )
                    raise RecoverableRuntimePause(
                        "T4 已保留 Candidate Population，但当前 Final Card 修复缺少一致的源数据。"
                        f"原因类别：{diagnostic.kind.value}；下一步：{diagnostic.recovery_action}。"
                    ) from exc
                self.progress.emit(
                    "T4 · 正在仅修复 Portfolio Idea Card 的 LLM 解释；当前 Candidate、评分、谱系和 Population 不会重新生成。",
                    important=True,
                )
            elif not operation_action and structural_gate_ready and not cards_ready:
                # Compatibility path for checkpoints created before the
                # durable receipt was introduced. The receipt is persisted
                # below before any new final-card call.
                try:
                    result = controller.load_active_result_for_final_card_repair()
                except Exception as exc:
                    diagnostic = classify_final_card_exception(
                        exc,
                        stage="source_reload_for_final_card_repair",
                    )
                    store.write_json(
                        "ideation/evolution/diagnostics/final_card_source_reload.json",
                        {
                            "schema_version": "1.0.0",
                            "semantics": "t4_final_idea_card_compilation_diagnostic",
                            "status": "repair_prerequisite_required",
                            "failure": diagnostic.as_dict(),
                        },
                    )
                    raise RecoverableRuntimePause(
                        "T4 已保留 Candidate Population，但当前 Final Card 修复缺少一致的源数据。"
                        f"原因类别：{diagnostic.kind.value}；下一步：{diagnostic.recovery_action}。"
                    ) from exc
                card_only_recovery = True
                self.progress.emit(
                    "T4 · 检测到已保存的 Population，正在仅补齐缺失的 LLM Portfolio Card 解释。",
                    important=True,
                )
            elif operation_action == "continue_evolution":
                result = await controller.continue_from_active_population(run_config)
            elif operation_action == "focus_candidate":
                targets = directive.get("target_candidate_ids") if isinstance(directive.get("target_candidate_ids"), list) else []
                if len(targets) != 1:
                    raise ValueError("Focus Evolution requires exactly one selected Candidate")
                result = await controller.focus_active_candidate(run_config, candidate_id=str(targets[0]))
            elif operation_action == "recover_selection_score":
                candidate_id = str(operation.get("candidate_id") or "").strip() if isinstance(operation, dict) else ""
                if not candidate_id:
                    raise ValueError("Selection score recovery requires one confirmed Candidate ID")
                self.progress.emit(
                    "T4 · 已确认推进候选；正在补做该候选缺失的独立评分。"
                    "不会重新演化 Candidate，评分完成后将自动进入 T4.5。",
                    important=True,
                )
                result = await controller.recover_active_candidate_score_for_selection(
                    run_config,
                    candidate_id=candidate_id,
                )
                self._write_t4_operation_outcome(
                    ctx,
                    operation=operation if isinstance(operation, dict) else None,
                    status="selection_score_recovered",
                    summary="The confirmed Candidate received its missing independent score. The original Gate1 confirmation remains valid and will now proceed to T4.5.",
                    details={
                        "candidate_id": candidate_id,
                        "score_artifact": f"ideation/scoring/selection_recovery/{re.sub(r'[^a-zA-Z0-9_.-]+', '_', candidate_id).strip('_') or 'candidate'}.json",
                    },
                )
            elif operation_action == "merge_candidates":
                targets = directive.get("target_candidate_ids") if isinstance(directive.get("target_candidate_ids"), list) else []
                if len(targets) != 2:
                    raise ValueError("Create a Crossover requires exactly two selected Candidates")
                try:
                    result = await controller.create_crossover_from_active_candidates(
                        run_config,
                        parent_ids=[str(targets[0]), str(targets[1])],
                    )
                except ValueError as exc:
                    if "Compatibility Check" not in str(exc):
                        raise
                    self._write_t4_operation_outcome(
                        ctx,
                        operation=operation,
                        status="compatibility_rejected",
                        summary="The requested Crossover was not generated because the independent Compatibility Check did not approve one coherent Gene Donor Map.",
                        details={"plan_artifact": f"ideation/evolution/plans/round_{T4ArtifactStore(ctx.workspace_dir).read_state().generation + 1}.json"},
                    )
                    ready, error = validate_t4_gate1_ready(ctx.workspace_dir)
                    cards_ready, cards_error = validate_t4_portfolio_final_cards(ctx.workspace_dir)
                    if not ready or not cards_ready:
                        raise RecoverableRuntimePause(
                            error
                            or cards_error
                            or "T4 Gate1 artifacts are unavailable after the Compatibility Check"
                        )
                    self._record_runtime_completion(
                        ctx,
                        "t4_gate1_ready",
                        {"outputs": ["ideation/evolution/latest_operation_result.json"]},
                        action_type="t4_crossover_compatibility_rejected",
                    )
                    return True
            elif operation_action == "compose_from_components":
                await self._run_t4_human_composition_check(
                    ctx=ctx,
                    scorer=scorer,
                    operation=operation,
                )
                self._record_runtime_completion(
                    ctx,
                    "t4_gate1_ready",
                    {"outputs": ["ideation/evolution/latest_operation_result.json"]},
                    action_type="t4_human_composition_checked",
                )
                return True
            elif operation_action == "execute_human_composition":
                result = await self._run_t4_human_composition_generation(
                    ctx=ctx,
                    run_config=run_config,
                    controller=controller,
                    evolver=evolver,
                    operation=operation,
                )
            elif operation_action == "regenerate_route":
                route = str(directive.get("requested_route") or "").strip()
                if not route:
                    self._write_t4_operation_outcome(
                        ctx,
                        operation=operation,
                        status="route_not_specified",
                        summary="No generation Route was specified. The active Population was not changed; choose Literature, Informed Brainstorm, a supplementary route, or Cross-domain / Bridge and try again.",
                    )
                    self._record_runtime_completion(
                        ctx,
                        "t4_gate1_ready",
                        {"outputs": ["ideation/evolution/latest_operation_result.json"]},
                        action_type="t4_route_regeneration_needs_choice",
                    )
                    return True
                try:
                    result = await controller.regenerate_route_from_active_population(run_config, route=route)
                except ValueError as exc:
                    if "did not produce a supported Candidate" not in str(exc) and "unknown T4 generation Route" not in str(exc):
                        raise
                    self._write_t4_operation_outcome(
                        ctx,
                        operation=operation,
                        status="route_regeneration_no_candidate",
                        summary=f"Route '{route}' completed without a supported new Candidate. Its route artifact was preserved; the active Population was not changed.",
                        details={"route": route},
                    )
                    ready, error = validate_t4_gate1_ready(ctx.workspace_dir)
                    cards_ready, cards_error = validate_t4_portfolio_final_cards(ctx.workspace_dir)
                    if not ready or not cards_ready:
                        raise RecoverableRuntimePause(
                            error
                            or cards_error
                            or "T4 Gate1 artifacts are unavailable after route regeneration"
                        )
                    self._record_runtime_completion(
                        ctx,
                        "t4_gate1_ready",
                        {"outputs": ["ideation/evolution/latest_operation_result.json"]},
                        action_type="t4_route_regeneration_no_candidate",
                    )
                    return True
            elif operation_action == "change_target_profile":
                result = await controller.reprofile_active_population(run_config)
            elif operation_action:
                raise RecoverableRuntimePause(
                    "保存的 T4 操作不受当前工作流支持，Candidate Population 未被修改。"
                    "请 resume 回到研究方向决策面板，再选择可用操作。"
                )
            else:
                result = await controller.run(run_config)
            portfolio_ids = [
                candidate_id
                for candidate_id in (
                    [result.portfolio.lead_id, *result.portfolio.alternative_ids, *result.portfolio.high_upside_ids]
                )
                if candidate_id
            ]
            dossier_by_id = {candidate.candidate_id: candidate for candidate in result.active_dossiers}
            portfolio_dossiers = [dossier_by_id[candidate_id] for candidate_id in portfolio_ids if candidate_id in dossier_by_id]
            if len(portfolio_dossiers) != len(portfolio_ids):
                raise ValueError("T4 Portfolio references a Candidate outside the active Population")
            # The controller has now persisted and activated the Population.
            # Record that fact before requesting LLM-authored display prose so
            # a later resume can prove the requested operation was already
            # consumed.  This is deliberately independent of the Gate1
            # projection, which is only compiled after cards are complete.
            if repair_checkpoint is None:
                store.write_final_card_repair_checkpoint(
                    population=result.population,
                    operation=operation if isinstance(operation, dict) else None,
                    status="pending_llm_card_compilation",
                    reason="population_persisted_before_final_card_compilation",
                )
                repair_checkpoint, _repair_checkpoint_error = store.current_final_card_repair_checkpoint(
                    operation=operation if isinstance(operation, dict) else None,
                )
            # A prior process can have written complete cards but stopped
            # before the deterministic Gate1 projection. Validate those cards
            # and reuse them; no deterministic fallback prose is created.
            cards_ready, _card_error = validate_t4_portfolio_final_cards(ctx.workspace_dir)
            # A Gate1 decision must be based on a complete LLM-authored Idea
            # Card, not a deterministic approximation assembled from title,
            # scores, or whichever presentation field happened to survive.
            # ``compile`` already performs one semantic repair for a
            # parseable response; give the independent Card Compiler one
            # fresh bounded retry before asking the researcher whether to
            # continue.  We never manufacture missing card prose locally.
            ctx.extra["t4_heartbeat_phase_key"] = "final_card_compilation"
            self._mark_t4_heartbeat_phase(ctx, "final_card_compilation")
            ctx.extra["t4_evolution_activity"] = "候选卡与决策说明整理（Portfolio Card Compilation）"
            ctx.extra["t4_evolution_current_deliverable"] = "可比较的完整 Candidate Card 与评分说明"
            ctx.extra["t4_evolution_following_phase"] = "Gate1 人工比较与选择"
            self.progress.emit(
                "T4 · 候选集已完成；正在整理完整 Candidate Card 与决策说明。"
                "完成后进入 Gate1 人工比较与选择，不会重新生成 Candidate。",
                important=True,
            )
            final_cards = []
            prior_card_errors = (
                [item for item in (repair_checkpoint or {}).get("attempts", []) if isinstance(item, dict)]
                if isinstance(repair_checkpoint, dict)
                else []
            )
            card_errors: list[dict[str, object]] = []
            if not cards_ready:
                try:
                    max_card_attempts = int(self.retry_policy.get("t4_final_card_compiler_attempts", 2))
                except (TypeError, ValueError):
                    max_card_attempts = 2
                # A live invocation is bounded separately from Candidate
                # Evolution.  Each compile call already contains one dedicated
                # semantic repair; a later resume retains the diagnostic but
                # may retry after the provider or relevant source changes.
                max_card_attempts = max(1, min(max_card_attempts, 4))
                for attempt in range(1, max_card_attempts + 1):
                    try:
                        repair_context: dict[str, object] = {}
                        if profile_refresh is not None:
                            repair_context["profile_refresh"] = profile_refresh
                        prior_attempts = [*prior_card_errors, *card_errors]
                        if prior_attempts:
                            prior_failure = prior_attempts[-1].get("failure")
                            if isinstance(prior_failure, dict):
                                repair_context["previous_failure"] = prior_failure
                        final_cards = await final_card_compiler.compile(
                            candidates=portfolio_dossiers,
                            target_profile=run_config.target_profile,
                            repair_context=repair_context or None,
                        )
                        break
                    except BudgetExceeded:
                        raise
                    except ValueError as card_error:
                        failure = (
                            card_error.diagnostic
                            if isinstance(card_error, FinalCardCompilationFailure)
                            else classify_final_card_exception(
                                card_error,
                                stage="outer_card_compilation",
                                candidate_ids=[candidate.candidate_id for candidate in portfolio_dossiers],
                            )
                        )
                        diagnostic = {
                            "schema_version": "1.0.0",
                            "semantics": "t4_final_idea_card_compilation_diagnostic",
                            "attempt": len(prior_card_errors) + attempt,
                            "attempt_in_current_run": attempt,
                            "max_attempts": max_card_attempts,
                            "population_id": result.population.population_id,
                            "candidate_ids": [candidate.candidate_id for candidate in portfolio_dossiers],
                            "status": "repair_required" if failure.repair_scheduled else "repair_prerequisite_required",
                            "failure": failure.as_dict(),
                        }
                        store.write_json(
                            f"ideation/evolution/diagnostics/final_card_compilation_attempt_{attempt}.json",
                            diagnostic,
                        )
                        card_errors.append(diagnostic)
                        if not failure.repair_scheduled:
                            break
                if not final_cards:
                    all_card_errors = [*prior_card_errors, *card_errors]
                    repair_scheduled = any(
                        bool((item.get("failure") or {}).get("repair_scheduled"))
                        for item in all_card_errors
                        if isinstance(item, dict)
                    )
                    store.write_json(
                        "ideation/final_cards/portfolio_cards.json",
                        {
                            "schema_version": "1.0.0",
                            "semantics": "t4_final_idea_card_translations",
                            "population_id": result.population.population_id,
                            "target_profile": model_dump(run_config.target_profile, mode="json"),
                            "cards": [],
                            "status": "llm_repair_required",
                            "attempts": all_card_errors,
                            "repair": {
                                "scheduled": repair_scheduled,
                                "scope": "portfolio_final_card_compiler",
                                "next_action": (
                                    "resume_t4_to_retry_final_card_llm"
                                    if repair_scheduled
                                    else "resolve_recorded_provider_or_source_prerequisite_then_resume_t4"
                                ),
                                "attempts_exhausted_in_current_run": len(card_errors) >= max_card_attempts,
                                "prior_diagnostic_count": len(all_card_errors),
                                "failure_kinds": [
                                    str((item.get("failure") or {}).get("kind") or "")
                                    for item in all_card_errors
                                    if isinstance(item, dict)
                                ],
                            },
                        },
                    )
                    store.update_final_card_repair_checkpoint(
                        status="llm_repair_required",
                        reason="bounded_llm_final_card_compilation_failed",
                        attempts=all_card_errors,
                    )
                    raise RecoverableRuntimePause(
                        "T4 的 Portfolio Idea Card 未能由 LLM 完整编译；候选、评分和谱系已保存，"
                        "但不会用固定模板或残缺字段替代科研解释。"
                        + "已记录每次失败的具体类别和 LLM 修复路径；请 resume 继续定向卡片修复，"
                        "或先处理诊断中标出的源数据或模型配置前置条件。"
                    )
                store.write_json(
                    "ideation/final_cards/portfolio_cards.json",
                    {
                        "schema_version": "1.0.0",
                        "semantics": "t4_final_idea_card_translations",
                        "population_id": result.population.population_id,
                        "target_profile": model_dump(run_config.target_profile, mode="json"),
                        "cards": [model_dump(card, mode="json") for card in final_cards],
                        "status": "completed",
                    },
                )
                # A previous resume may have persisted a readiness failure
                # before the current Population and cards existed.  Preserve
                # that it happened, but make the durable diagnostic describe
                # the current resolved state instead of leaving a misleading
                # ``repair_required`` artifact beside a completed Card file.
                store.write_json(
                    "ideation/evolution/diagnostics/final_card_readiness.json",
                    {
                        "schema_version": "1.0.0",
                        "semantics": "t4_final_idea_card_readiness_diagnostic",
                        "status": "resolved",
                        "population_id": result.population.population_id,
                        "resolution": {
                            "action": "llm_final_card_compilation_completed",
                            "card_count": len(final_cards),
                            "prior_compilation_failure_count": len([*prior_card_errors, *card_errors]),
                        },
                    },
                )
                store.update_final_card_repair_checkpoint(
                    status="cards_compiled_projection_pending",
                    reason="llm_final_card_compilation_completed_projection_pending",
                    attempts=[*prior_card_errors, *card_errors],
                    projection_completed=False,
                )
            ctx.extra["t4_heartbeat_phase_key"] = "gate1_projection"
            self._mark_t4_heartbeat_phase(ctx, "gate1_projection")
            ctx.extra["t4_evolution_activity"] = "决策页整理（Gate1 Projection）"
            ctx.extra["t4_evolution_current_deliverable"] = "候选比较卡、选择建议与可恢复的决策页"
            ctx.extra["t4_evolution_following_phase"] = "Gate1 人工比较与选择"
            self.progress.emit(
                "T4 · 正在生成 Gate1 决策页；完成后可查看、比较、推进或优化已保存的 Candidate。",
                important=True,
            )
            projection = project_gate1_population(
                ctx.workspace_dir,
                population=result.population,
                dossiers=result.active_dossiers,
                scores=result.active_scores,
                route_results=result.route_results,
            )
            store.update_final_card_repair_checkpoint(
                status="completed",
                reason="final_cards_and_gate1_projection_completed",
                projection_completed=True,
            )
            degradations = projection.get("degradations") if isinstance(projection, dict) else []
            if isinstance(degradations, list) and degradations:
                self.progress.emit(
                    "T4 · Cross-domain Bridge 复核暂未返回；已显式标为待审阅，"
                    "不会阻断现有 Candidate Population 进入 Gate1。",
                    important=True,
                )
        except RecoverableRuntimePause:
            raise
        except LLMProviderError:
            raise
        except Exception as exc:
            self.log.warning("t4_evolution_output_validation_paused", error=str(exc))
            diagnostic = " ".join(str(exc).split())[:600] or type(exc).__name__
            self.progress.emit(
                f"T4 · Gate1 投影遇到需要人工或代码修复的完整性问题：{diagnostic}。"
                "已保存的 Candidate、评分和演化结果不会被丢弃。",
                important=True,
            )
            raise RecoverableRuntimePause(
                "T4 未能安全完成 Gate1 兼容投影；已完成的候选、评分和演化结果均已保存。"
                f"具体原因：{diagnostic}。resume 会从上一个未完成的步骤继续，不会重复已通过的步骤。"
            ) from exc
        finally:
            ctx.extra["t4_evolution_active"] = False

        ready, error = validate_t4_gate1_ready(ctx.workspace_dir)
        if not ready:
            raise RecoverableRuntimePause(
                "T4 Evolution 已完成，但 Gate1 兼容投影尚未通过校验；"
                f"已保留 P0/P1 和评分结果，resume 可继续。原因：{error}"
            )
        cards_ready, cards_error = validate_t4_portfolio_final_cards(ctx.workspace_dir)
        if not cards_ready:
            raise RecoverableRuntimePause(
                "T4 Population 已完成，但 Portfolio Idea Card 仍缺少完整的 LLM 解释；"
                "已保留所有 Candidate、评分和谱系，resume 会只重试 Card Compiler。"
                f"原因：{cards_error}"
            )
        self.progress.emit(
            "T4 已完成一轮 Idea Evolution。P1、评分、谱系和完整 Archive 已保存；接下来请选择一个完整 Candidate，或保留多个并行推进。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_gate1_ready",
            {
                "outputs": [
                    "ideation/populations/P0.json",
                    f"ideation/populations/{result.population.population_id}.json",
                    "ideation/portfolio.json",
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                ],
                "candidate_count": projection["candidate_count"],
            },
            action_type="t4_evolution_controller",
        )
        return True

    def _prepare_t4_execution_mode_before_prompt(self, ctx: ExecutionContext) -> None:
        """Choose a safe prompt family before any T4 prompt is rendered.

        ``IdeationAgent.system_prompt`` runs before the controller preflight.
        Without this guard, a direct ``run-task T4`` could render the retired
        ``ideation.j2`` prompt even though the native controller later pauses
        for its pre-run gate.  Only an explicit migration setting can select
        the legacy prompt, and native artifacts always win that decision.
        """

        if not self._is_t4_ideation_agent(ctx):
            return
        if self._t4_has_native_artifacts(ctx.workspace_dir):
            ctx.extra["t4_execution_mode"] = "evolutionary"
            ctx.extra["t4_execution_mode_reason"] = "native_artifacts_present"
            return
        if has_current_t4_prerun_confirmation(ctx.workspace_dir):
            ctx.extra["t4_execution_mode"] = "evolutionary"
            ctx.extra["t4_execution_mode_reason"] = "current_pre_run_confirmation"
            return
        if self.runtime_settings.agent_behavior.allow_legacy_t4_fallback:
            ctx.extra["t4_execution_mode"] = "legacy_fallback"
            ctx.extra["t4_execution_mode_reason"] = (
                "explicit_runtime_setting_without_current_evolution_confirmation"
            )
            self._record_t4_execution_mode(
                ctx,
                mode="legacy_fallback",
                reason=str(ctx.extra["t4_execution_mode_reason"]),
            )
            return
        ctx.extra["t4_execution_mode"] = "evolutionary"
        ctx.extra["t4_execution_mode_reason"] = "pre_run_confirmation_required"

    def _enforce_t4_execution_mode_before_legacy_loop(self, ctx: ExecutionContext) -> None:
        """Fail closed rather than falling from native T4 into ``ideation.j2``.

        The state machine normally collects the pre-run choice before it starts
        T4.  This guard protects direct task invocation, stale resume contexts,
        and future callers that bypass that gate.  A native failure raises from
        the controller and never reaches this method, so it cannot silently
        degrade into a different scientific workflow.
        """

        if not self._is_t4_ideation_agent(ctx) or self._t4_gate1_user_selection_exists(ctx):
            return
        mode = str(ctx.extra.get("t4_execution_mode") or "evolutionary")
        if mode == "legacy_fallback":
            if self._t4_has_native_artifacts(ctx.workspace_dir):
                raise RecoverableRuntimePause(
                    "T4 detected native Evolution artifacts. Legacy fallback is blocked to protect the existing Population; resume through the native T4 decision flow."
                )
            self._record_t4_execution_mode(
                ctx,
                mode="legacy_fallback",
                reason=str(
                    ctx.extra.get("t4_execution_mode_reason")
                    or "explicit_runtime_setting_without_current_evolution_confirmation"
                ),
            )
            return

        if self._t4_has_native_artifacts(ctx.workspace_dir):
            raise RecoverableRuntimePause(
                "T4 has native Evolution artifacts but no resumable native pre-run confirmation. The Population was preserved; resume through the T4 decision flow instead of running a legacy prompt."
            )
        raise RecoverableRuntimePause(
            "T4 is waiting for its Publication Orientation and Evolution pre-run confirmation. No legacy ideation prompt was started; return through the T4 gate, choose a profile and run mode, then resume."
        )

    @staticmethod
    def _t4_has_native_artifacts(workspace_dir: Path) -> bool:
        """Return whether a workspace contains artifacts legacy must not touch."""

        workspace = Path(workspace_dir)
        protected = (
            "ideation/evolution/state.json",
            "ideation/populations/P0.json",
            "ideation/populations/P1.json",
            "ideation/portfolio.json",
            "ideation/final_cards/portfolio_cards.json",
        )
        return any((workspace / path).is_file() for path in protected)

    def _record_t4_execution_mode(
        self,
        ctx: ExecutionContext,
        *,
        mode: str,
        reason: str,
    ) -> None:
        """Persist a compact, non-secret receipt for native/legacy T4 routing."""

        if not self._is_t4_ideation_agent(ctx):
            return
        store = T4ArtifactStore(ctx.workspace_dir)
        protected = self._t4_has_native_artifacts(ctx.workspace_dir)
        if mode == "legacy_fallback" and protected:
            raise RecoverableRuntimePause(
                "Legacy fallback was refused because native Evolution artifacts already exist. The existing Population was not modified."
            )
        store.write_json(
            "ideation/evolution/execution_mode.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_execution_mode_receipt",
                "mode": mode,
                "reason": reason,
                "run_id": ctx.run_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "native_artifacts_present": protected,
                "artifact_protection": (
                    "legacy_fallback is refused whenever native Evolution artifacts exist"
                ),
            },
        )

    @staticmethod
    def _write_t4_operation_outcome(
        ctx: ExecutionContext,
        *,
        operation: object,
        status: str,
        summary: str,
        details: dict[str, object] | None = None,
    ) -> None:
        """Persist a compact, user-safe outcome for a requested Gate1 operation."""

        payload = operation if isinstance(operation, dict) else {}
        T4ArtifactStore(ctx.workspace_dir).write_json(
            "ideation/evolution/latest_operation_result.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_native_operation_result",
                "directive_path": str(payload.get("directive_path") or ""),
                "action": str(payload.get("action") or ""),
                "status": status,
                "summary": summary,
                "details": details or {},
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def _run_t4_human_composition_check(
        self,
        *,
        ctx: ExecutionContext,
        scorer: LLMIdeaScorer,
        operation: object,
    ) -> None:
        """Write a compatibility-gated composition plan without creating a Child."""

        request = operation if isinstance(operation, dict) else {}
        directive = request.get("directive") if isinstance(request.get("directive"), dict) else {}
        component_refs = [str(item) for item in directive.get("component_refs", []) if str(item).strip()] if isinstance(directive.get("component_refs"), list) else []
        source_ids = [str(item) for item in directive.get("target_candidate_ids", []) if str(item).strip()] if isinstance(directive.get("target_candidate_ids"), list) else []
        if len(set(source_ids)) < 2 or len(component_refs) < 2:
            raise ValueError("Human composition requires selected components from at least two Candidates")
        population, dossiers = current_population_context(ctx.workspace_dir)
        if not set(source_ids).issubset(dossiers):
            raise ValueError("Human composition references a Candidate outside the active Population")
        directive_id = str(directive.get("directive_id") or "")
        if not directive_id:
            raise ValueError("Human composition request is missing a stable Directive ID")
        composition_id = f"HC-{directive_id.removeprefix('DIR-')}"
        compatibility = await scorer.review_human_composition(
            composition_id=composition_id,
            candidates=[dossiers[candidate_id] for candidate_id in source_ids],
            component_refs=component_refs,
            preserve_genes=[str(item) for item in directive.get("preserve_genes", []) if str(item).strip()] if isinstance(directive.get("preserve_genes"), list) else [],
            donor_genes={str(key): str(value) for key, value in (directive.get("donor_genes") or {}).items()} if isinstance(directive.get("donor_genes"), dict) else {},
            constraints=[str(item) for item in directive.get("constraints", []) if str(item).strip()] if isinstance(directive.get("constraints"), list) else [],
        )
        if set(compatibility.source_candidate_ids) != set(source_ids):
            raise ValueError("Composition reviewer changed the source Candidate set")
        store = T4ArtifactStore(ctx.workspace_dir)
        root = f"ideation/human_compositions/{composition_id}"
        report_path = f"{root}/compatibility_report.json"
        store.write_json(report_path, model_dump(compatibility, mode="json"))
        composable = compatibility.recommended_action == "compose" and compatibility.gene_donor_map is not None
        plan_path = f"{root}/composition_plan.json"
        store.write_json(
            plan_path,
            {
                "schema_version": "1.0.0",
                "semantics": "t4_human_composition_plan",
                "composition_id": composition_id,
                "status": "awaiting_human_confirmation" if composable else "not_composable",
                "directive_path": str(request.get("directive_path") or ""),
                "population_id": population.population_id,
                "population_generation": population.generation,
                "input_fingerprint": population.input_fingerprint,
                "run_config_fingerprint": population.run_config_fingerprint,
                "compatibility_report": report_path,
                "compatibility": model_dump(compatibility, mode="json"),
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if composable:
            summary = (
                f"Compatibility Check found a potentially coherent Human-composed Candidate from {', '.join(source_ids)}. "
                f"Review the Gene Donor Map, then explicitly confirm composition {composition_id} to generate and independently score a new Candidate."
            )
            status = "awaiting_composition_confirmation"
        else:
            summary = (
                f"Compatibility Check recommends {compatibility.recommended_action}. No new Candidate was created; "
                "the source Candidates remain unchanged and can be kept in parallel or revised."
            )
            status = "not_composable"
        self._write_t4_operation_outcome(
            ctx,
            operation=request,
            status=status,
            summary=summary,
            details={"composition_id": composition_id, "compatibility_report": report_path, "composition_plan": plan_path},
        )

    async def _run_t4_human_composition_generation(
        self,
        *,
        ctx: ExecutionContext,
        run_config,
        controller: IdeaEvolutionController,
        evolver: LLMIdeaEvolver,
        operation: object,
    ):
        """Generate, validate, independently score, and integrate a confirmed Child."""

        request = operation if isinstance(operation, dict) else {}
        plan_path = str(request.get("composition_plan_path") or "")
        if not plan_path:
            raise ValueError("Human composition generation is missing its confirmed Composition Plan")
        store = T4ArtifactStore(ctx.workspace_dir)
        payload = store.read_model(plan_path, _T4OperationEnvelope).payload
        if payload.get("semantics") != "t4_human_composition_plan" or payload.get("status") != "awaiting_human_confirmation":
            raise ValueError("Human composition plan is not awaiting a valid final confirmation")
        population, dossiers = current_population_context(ctx.workspace_dir)
        if payload.get("population_id") != population.population_id:
            raise ValueError("Human composition plan is stale because the active Population changed")
        if payload.get("input_fingerprint") != population.input_fingerprint or payload.get("run_config_fingerprint") != population.run_config_fingerprint:
            raise ValueError("Human composition plan fingerprints are stale")
        compatibility = HumanCompositionCompatibility.model_validate(payload.get("compatibility"))
        source_ids = list(compatibility.source_candidate_ids)
        if not set(source_ids).issubset(dossiers):
            raise ValueError("Human composition source Candidate is no longer active")
        target_candidate_id = f"HC{population.generation + 1}-{compatibility.composition_id.removeprefix('HC-')}"
        child = await evolver.generate_human_composition(
            composition_id=compatibility.composition_id,
            target_candidate_id=target_candidate_id,
            compatibility=compatibility,
            parents=[dossiers[candidate_id] for candidate_id in source_ids],
        )
        result = await controller.integrate_human_composed_candidate(
            run_config,
            composition=compatibility,
            child=child,
        )
        payload.update(
            {
                "status": "generated_and_independently_scored",
                "generated_candidate_id": child.candidate_id,
                "output_population_id": result.population.population_id,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        store.write_json(plan_path, payload)
        self._write_t4_operation_outcome(
            ctx,
            operation=request,
            status="composition_scored",
            summary=(
                f"Human-composed Candidate {child.candidate_id} was created from the confirmed Gene Donor Map and independently rescored with its source Candidates. "
                "The source versions remain preserved; review the updated Portfolio before proceeding to T4.5."
            ),
            details={"composition_plan": plan_path, "candidate_id": child.candidate_id, "population_id": result.population.population_id},
        )
        return result

    async def _call_t4_evolution_role(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        budget: BudgetTracker,
        system_contract: str,
        user_prompt: str,
    ) -> str:
        """Use the normal provider recovery policy for one typed T4 role call."""

        request_messages: list[dict[str, object]] = [
            {"role": "system", "content": system_contract},
            {"role": "user", "content": user_prompt},
        ]
        retry_batches, cooldown, long_cooldown = self._llm_provider_recovery_policy()
        native_t4_recovery = bool(
            self._is_t4_ideation_agent(ctx)
            and ctx.extra.get("t4_evolution_active")
            and not self._t4_gate1_user_selection_exists(ctx)
        )
        if native_t4_recovery:
            # Native T4 must not open an in-memory provider menu while its
            # state machine still says RUNNING. That menu has no durable Gate
            # receipt and can leave a shell waiting indefinitely after a
            # provider disconnect. Keep a small, visible automatic window,
            # then return through the normal recoverable T4 boundary.
            try:
                t4_retry_batches = int(self.retry_policy.get("t4_provider_retry_batches", 1))
            except (TypeError, ValueError):
                t4_retry_batches = 1
            retry_batches = max(1, min(t4_retry_batches, 5))
        failed_batches = 0
        # A provider can return a syntactically valid completion whose first
        # message contains only hidden reasoning/tool metadata and no final
        # payload.  Treat that as a bounded transport-format recovery, not as
        # a scientific rejection of the Child.  The durable T4 checkpoint
        # remains the outer recovery boundary; this counter prevents an
        # invisible retry loop inside one role.
        empty_content_attempts = 0
        while True:
            budget.tick_step()
            budget.check()
            try:
                response = await self._await_llm_with_progress(
                    ctx=ctx,
                    step=budget.steps,
                    progress_step_limit="unlimited" if budget.unlimited_budget else str(budget.max_steps),
                    messages=request_messages,
                    tools=None,
                    temperature=0.2,
                    tier=eff.llm_tier,
                    profile=eff.llm_profile,
                    model_override=eff.llm_model_override,
                    endpoint_override=eff.llm_endpoint_override,
                    max_context_override=eff.llm_max_context_override,
                    timeout=self._llm_request_timeout_seconds(),
                    # Native T4 has a durable checkpoint and a controller
                    # recovery boundary. Retrying the same large structured
                    # request in both layers can turn one provider stall into
                    # many minutes of invisible waiting without improving the
                    # scientific result. A failure pauses safely for resume.
                    max_retries_per_model=1 if native_t4_recovery else self._llm_retry_overrides()[0],
                    retry_base_delay=self._llm_retry_overrides()[1],
                    # T4 roles return strict structured artifacts. Current
                    # reasoning providers default to high hidden thinking,
                    # which can consume a long completion and still leave the
                    # final content field empty. Low effort keeps substantive
                    # reasoning while prioritizing the required final object.
                    reasoning_effort="low",
                )
            except LLMProviderError as exc:
                if not self._is_recoverable_provider_error(exc):
                    raise self._t4_provider_pause(
                        ctx=ctx,
                        eff=eff,
                        messages=request_messages,
                        exc=exc,
                        failed_batches=failed_batches + 1,
                    ) from exc
                failed_batches += 1
                if native_t4_recovery and failed_batches >= retry_batches:
                    phase_key = str(ctx.extra.get("t4_heartbeat_phase_key") or "t4_role")
                    safe_phase = re.sub(r"[^a-zA-Z0-9_.-]+", "_", phase_key).strip("_") or "t4_role"
                    try:
                        T4ArtifactStore(ctx.workspace_dir).write_json(
                            f"ideation/evolution/diagnostics/provider_recovery_{safe_phase}.json",
                            {
                                "schema_version": "1.0.0",
                                "semantics": "t4_provider_recovery_diagnostic",
                                "phase": phase_key,
                                "automatic_retry_batches": retry_batches,
                                "failed_batches": failed_batches,
                                "error_category": self._provider_error_category(exc),
                                "error_summary": self._public_provider_error_message(exc),
                                "safe_provider_detail": self._safe_provider_error_detail(exc),
                                "http_status": self._provider_http_status(exc),
                                **self._t4_role_request_metrics(eff=eff, messages=request_messages),
                                "next_action": "resume_t4_from_durable_checkpoint",
                                "recorded_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                    except (OSError, ValueError):
                        pass
                    raise RecoverableRuntimePause(
                        "T4 模型服务连续不可用；已保存当前阶段的检查点和诊断。"
                        "本次不会在内部输入窗口无限等待；请在恢复页查看诊断后 resume。"
                    ) from exc
                action, delay = await self._choose_llm_provider_recovery(
                    ctx=ctx,
                    budget=budget,
                    failed_batches=failed_batches,
                    retry_batches=retry_batches,
                    cooldown_seconds=cooldown,
                    long_cooldown_seconds=long_cooldown,
                    failure_category=self._provider_error_category(exc),
                )
                if action != "retry":
                    raise self._t4_provider_pause(
                        ctx=ctx,
                        eff=eff,
                        messages=request_messages,
                        exc=exc,
                        failed_batches=failed_batches,
                    ) from exc
                await self._wait_before_llm_provider_retry(
                    ctx=ctx,
                    budget=budget,
                    seconds=delay,
                    attempt=failed_batches,
                    retry_batches=retry_batches,
                )
                continue
            budget.add_tokens(response.tokens_in, response.tokens_out, response.cost_usd)
            ctx.extra["t4_evolution_last_model"] = response.model_used
            ctx.extra["t4_evolution_last_endpoint"] = response.endpoint_used
            raw_response = getattr(response, "raw", None)
            choices = getattr(raw_response, "choices", None)
            if choices is None and isinstance(raw_response, dict):
                choices = raw_response.get("choices")
            first_choice = choices[0] if isinstance(choices, (list, tuple)) and choices else None
            if first_choice is None:
                message = None
            elif isinstance(first_choice, dict):
                message = first_choice.get("message")
            else:
                message = getattr(first_choice, "message", None)
            content = self._t4_response_content(message)
            if not content:
                empty_content_attempts += 1
                diagnostic = {
                    "schema_version": "1.0.0",
                    "semantics": "t4_empty_role_response_diagnostic",
                    "phase": str(ctx.extra.get("t4_heartbeat_phase_key") or "t4_role"),
                    "attempt": empty_content_attempts,
                    "automatic_retry_limit": 1,
                    "has_choices": bool(choices),
                    "has_tool_calls": bool(getattr(message, "tool_calls", None) or (message.get("tool_calls") if isinstance(message, dict) else None)),
                    "has_refusal": bool(getattr(message, "refusal", None) or (message.get("refusal") if isinstance(message, dict) else None)),
                    "has_reasoning_content": bool(getattr(message, "reasoning_content", None) or (message.get("reasoning_content") if isinstance(message, dict) else None)),
                    "model": response.model_used,
                    "endpoint": response.endpoint_used,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    phase_key = str(ctx.extra.get("t4_heartbeat_phase_key") or "t4_role")
                    safe_phase = re.sub(r"[^a-zA-Z0-9_.-]+", "_", phase_key).strip("_") or "t4_role"
                    T4ArtifactStore(ctx.workspace_dir).write_json(
                        f"ideation/evolution/diagnostics/empty_response_{safe_phase}_{empty_content_attempts:02d}.json",
                        diagnostic,
                    )
                except (OSError, ValueError):
                    pass
                if empty_content_attempts <= 1:
                    request_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "The previous provider response contained no final content. "
                                "Return one complete replacement payload now; do not explain the transport issue, "
                                "do not return an empty message, and preserve the requested structured format."
                            ),
                        }
                    )
                    self.progress.emit(
                        "[T4] 模型返回了空的最终 payload；这不是对研究方向的否定，正在进行一次有界格式恢复。",
                        important=True,
                    )
                    continue
                raise RecoverableRuntimePause(
                    "T4 role returned an empty final payload after one bounded recovery attempt; "
                    "the Child was not scientifically rejected. Progress and a sanitized provider diagnostic "
                    "were saved; resume can retry this role."
                )
            return content

    @staticmethod
    def _t4_response_content(message: object) -> str:
        """Normalize scalar or content-block responses from compatible providers."""

        values: list[object] = [
            message.get("content") if isinstance(message, dict) else getattr(message, "content", None),
        ]
        parts: list[str] = []
        for value in values:
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, list):
                for block in value:
                    if isinstance(block, str) and block.strip():
                        parts.append(block.strip())
                    elif isinstance(block, dict):
                        text = block.get("text") or block.get("content")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
        return "\n".join(parts)

    def _record_t4_evolution_activity(
        self,
        ctx: ExecutionContext,
        *,
        phase: EvolutionPhase,
        status: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        # This callback is the controller-owned boundary for a logical T4
        # phase.  Do not reset the heartbeat clock for route-level status
        # updates or provider retries inside the same phase.
        ctx.extra["t4_heartbeat_phase_key"] = f"evolution:{phase.value}"
        self._mark_t4_heartbeat_phase(ctx, f"evolution:{phase.value}")
        labels = {
            EvolutionPhase.EVIDENCE_ROUTING: "证据整理（Evidence Routing）",
            EvolutionPhase.OPPORTUNITY_MAP: "研究机会探索（Opportunity Map）",
            EvolutionPhase.FORMATION: "多视角 Idea 发散（Multi-route Generation）",
            EvolutionPhase.GENOME_FAMILY: "候选深化与谱系整理（Candidate Enrichment + Idea Genome / Family）",
            EvolutionPhase.SCORING: "独立评估：成熟度与科学上行空间",
            EvolutionPhase.EVOLUTION_PLANNING: "演化意图规划（Evolution Planning）",
            EvolutionPhase.OFFSPRING: "Child 探索与独立复评",
            EvolutionPhase.SURVIVAL: "保留多样性与高潜力方向（Survival Selection）",
        }
        if phase == EvolutionPhase.FORMATION and status in {"route_started", "route_completed", "route_reused"}:
            details = payload or {}
            try:
                completed = max(0, int(details.get("completed_routes") or 0))
            except (TypeError, ValueError):
                completed = 0
            try:
                total = max(0, int(details.get("total_routes") or 0))
            except (TypeError, ValueError):
                total = 0
            if status == "route_started":
                completed = min(completed, total) if total else completed
                action = "正在发散"
            elif status == "route_reused":
                action = "已复用"
            else:
                action = "已完成"
            ctx.extra["t4_evolution_activity"] = f"P0 多视角 Idea 发散 · {completed}/{total} 条路径{action}"
            ctx.extra["t4_evolution_current_deliverable"] = "初始候选池 P0（保留非重复的机制与问题表述）"
            ctx.extra["t4_evolution_following_phase"] = "候选谱系与差异整理"
            return
        if phase == EvolutionPhase.OFFSPRING and status.startswith("child_"):
            details = payload or {}
            child_id = str(details.get("child_id") or "").strip()
            parent_titles = details.get("parent_titles") if isinstance(details.get("parent_titles"), list) else []
            parent = " / ".join(str(item).strip() for item in parent_titles if str(item).strip()) or "当前 Parent"
            try:
                completed = max(0, int(details.get("completed") or 0))
                total = max(0, int(details.get("total") or 0))
            except (TypeError, ValueError):
                completed, total = 0, 0
            progress = f" · {completed}/{total}" if total else ""
            if status == "child_started":
                activity = f"正在为 {parent} 生成 Child{progress}"
            elif status == "child_scored":
                activity = f"{child_id or 'Child'} 正在完成独立评分{progress}"
            elif status == "child_survival":
                activity = f"{child_id or 'Child'} 已完成 Survival Selection{progress}"
            elif status == "child_deferred":
                activity = f"{parent} 的本轮 Child 已延后{progress}"
            elif status == "child_not_retained":
                activity = f"{parent} 的 Child 未通过计划约束{progress}"
            else:
                activity = f"{child_id or 'Child'} 已生成并保存{progress}"
            ctx.extra["t4_evolution_activity"] = activity
            ctx.extra["t4_evolution_current_deliverable"] = "当前 Child 的变更、独立评分与存活结果"
            ctx.extra["t4_evolution_following_phase"] = "更新 Candidate Population 与决策 Portfolio"
            return
        label = labels.get(phase, phase.value.replace("_", " ").title())
        status_label = {"started": "已开始", "completed": "已完成", "reused": "已复用", "rescoring": "重新评分中"}.get(status, status.replace("_", " "))
        activity_details = {
            EvolutionPhase.EVIDENCE_ROUTING: "整理可追溯证据、反例和可扩展线索",
            EvolutionPhase.OPPORTUNITY_MAP: "提出机制缺口、竞争解释与待验证研究机会",
            EvolutionPhase.FORMATION: "从不同认识视角形成彼此不重复的初始 Idea",
            EvolutionPhase.GENOME_FAMILY: "先补充候选的机制、验证与证据边界，再识别候选间的差异、并行方向与谱系",
            EvolutionPhase.SCORING: "区分当前成熟度与可能的科学上行空间",
            EvolutionPhase.EVOLUTION_PLANNING: "选择值得澄清、反转或跨域重构的科研意图",
            EvolutionPhase.OFFSPRING: "生成可证伪的 Child，并保留 Parent 作为对照",
            EvolutionPhase.SURVIVAL: "保留成熟方向、并行机制和高潜力 Wildcard",
        }
        ctx.extra["t4_evolution_activity"] = f"{label} · {status_label} · {activity_details.get(phase, '')}".rstrip(" ·")
        ctx.extra["t4_evolution_current_deliverable"] = {
            EvolutionPhase.EVIDENCE_ROUTING: "证据索引与可用性边界",
            EvolutionPhase.OPPORTUNITY_MAP: "研究机会清单（不是最终候选）",
            EvolutionPhase.FORMATION: "初始候选池 P0",
            EvolutionPhase.GENOME_FAMILY: "候选深化结果、Idea Family 与差异图谱",
            EvolutionPhase.SCORING: "双轴评估：当前成熟度 / 科学上行空间",
            EvolutionPhase.EVOLUTION_PLANNING: "Evolution 计划与保留理由",
            EvolutionPhase.OFFSPRING: "当前 Child 的变更、独立评分与存活结果",
            EvolutionPhase.SURVIVAL: "候选集 P1 与可比较 Portfolio",
        }.get(phase, "T4 阶段产物")
        ctx.extra["t4_evolution_following_phase"] = {
            EvolutionPhase.EVIDENCE_ROUTING: "研究机会探索",
            EvolutionPhase.OPPORTUNITY_MAP: "多视角 Idea 发散",
            EvolutionPhase.FORMATION: "候选深化与谱系整理",
            EvolutionPhase.GENOME_FAMILY: "独立评估",
            EvolutionPhase.SCORING: "演化意图规划",
            EvolutionPhase.EVOLUTION_PLANNING: "Child 探索",
            EvolutionPhase.OFFSPRING: "保留多样性与高潜力方向",
            EvolutionPhase.SURVIVAL: "Gate1 人工比较与选择",
        }.get(phase, "")

    def _render_t4_evolution_phase(
        self,
        *,
        phase: EvolutionPhase,
        status: str,
        payload: dict[str, object],
    ) -> None:
        """Render a compact Rich phase panel while preserving progress settings."""

        buffer = StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self.runtime_settings.ui.no_color,
            color_system=None if self.runtime_settings.ui.no_color else "truecolor",
            no_color=self.runtime_settings.ui.no_color,
            width=120,
            highlight=False,
        )
        render_t4_evolution_phase(phase, status, payload, console=console)
        rendered = buffer.getvalue().rstrip()
        if rendered:
            self.progress.emit(rendered, important=True)

    async def _maybe_finalize_t4_before_llm(self, ctx: ExecutionContext) -> bool:
        """Reuse only a pre-evolution legacy formal bundle.

        Native T4 must never jump from Gate1 to formal hypotheses or an
        experiment plan. The narrow path below exists solely for an older
        workspace that predates `ideation/evolution/state.json` and already
        contains an audited-style legacy formal bundle. It preserves that
        bundle without rewriting it; native workspaces always use the
        Pre-Novelty handoff and T4.5 formalization.
        """

        if not self._is_t4_ideation_agent(ctx):
            return False

        if not self.runtime_settings.agent_behavior.allow_legacy_t4_fallback:
            return False

        if has_current_t4_prerun_confirmation(ctx.workspace_dir):
            return False

        if self._t4_has_native_artifacts(ctx.workspace_dir):
            return False

        self._record_t4_execution_mode(
            ctx,
            mode="legacy_fallback",
            reason="explicit_runtime_setting_reuse_of_valid_legacy_bundle",
        )

        if (ctx.workspace_dir / "ideation" / "evolution" / "state.json").is_file():
            return False

        # A complete Gate1 selection now advances through the Pre-Novelty
        # handoff below.  Reusing legacy formal artifacts here would place
        # final hypotheses and an experiment plan before T4.5 has audited the
        # selected Candidate.
        if self._t4_gate1_user_selection_exists(ctx):
            brief = ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml"
            selected = ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json"
            if brief.exists() and brief.stat().st_size > 0 and selected.exists() and selected.stat().st_size > 0:
                return False

        expected_paths = [
            ctx.workspace_dir / "ideation" / "hypotheses.md",
            ctx.workspace_dir / "ideation" / "exp_plan.yaml",
            ctx.workspace_dir / "ideation" / "risks.md",
            ctx.workspace_dir / "ideation" / "idea_scorecard.yaml",
            ctx.workspace_dir / "ideation" / "idea_rationales.json",
            ctx.workspace_dir / "ideation" / "gate_decisions.json",
            ctx.workspace_dir / "ideation" / "rejected_ideas.md",
            ctx.workspace_dir / "ideation" / "_family_distribution.md",
            ctx.workspace_dir / "ideation" / "_candidate_directions.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in expected_paths):
            return False
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=expected_paths,
            inputs=self._t4_upstream_input_paths(ctx),
            event="t4_resume_prefinalize_skipped",
            reason="final_outputs_older_than_t4_inputs",
        ):
            return False
        if not self._t4_final_outputs_follow_gate1(ctx):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t4_resume_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Ideation Agent] T4 检测到已有 ideation 产物且校验通过，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_resume_prefinalize",
            {
                "outputs": [
                    str(path.relative_to(ctx.workspace_dir))
                    for path in expected_paths
                ],
            },
            action_type="t4_resume_prefinalize",
        )
        return True

    async def _maybe_advance_t4_pre_novelty_selection(self, ctx: ExecutionContext) -> bool:
        """Advance a confirmed complete Candidate to T4.5 without re-running T4.

        Gate1 already produced the LLM-authored Candidate and the deterministic
        Pre-Novelty compiler organized its draft hypotheses and provenance.  A
        second legacy T4 pass must not replace that bundle with formal
        hypotheses before novelty/collision review.
        """

        if not self._is_t4_ideation_agent(ctx) or not self._t4_gate1_user_selection_exists(ctx):
            return False
        required = [
            ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml",
            ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json",
            ctx.workspace_dir / "ideation" / "selected" / "hypothesis_lineage.json",
            ctx.workspace_dir / "ideation" / "selected" / "t45_search_targets.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in required):
            return False
        self.progress.emit(
            "Selected Candidate 已整理为 Pre-Novelty brief。ResearchOS 将保留当前 Population，并把 novelty/collision audit 交给 T4.5；正式 Hypothesis Bundle 和 Experiment Plan 只会在 T4.5 明确通过后生成。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_pre_novelty_ready",
            {"outputs": [str(path.relative_to(ctx.workspace_dir)) for path in required]},
            action_type="t4_pre_novelty_handoff",
        )
        return True

    async def _maybe_finalize_t4_gate1_before_llm(self, ctx: ExecutionContext) -> bool:
        """T4 resume: if Gate1 artifacts are ready, stop before another long LLM run."""

        if not self._is_t4_ideation_agent(ctx):
            return False
        if self._t4_gate1_user_selection_exists(ctx):
            return False
        # A Gate1 operation is an explicit new human decision.  Existing
        # complete cards describe the *previous* Population and must never
        # short-circuit a queued evolution, route regeneration, profile change,
        # or card-only repair before the native controller can inspect it.
        if isinstance(ctx.extra.get("t4_operation_request"), dict):
            return False
        ok, err = validate_t4_gate1_ready(ctx.workspace_dir)
        # Candidate research content is model-authored. Do not silently turn a
        # provider failure into a template-derived Gate1 deck: users need the
        # model's actual mechanism, H1/H2/H3, and research judgement.
        if not ok:
            self.log.debug("t4_gate1_prefinalize_skipped", reason=err)
            return False
        cards_ok, cards_err = validate_t4_portfolio_final_cards(ctx.workspace_dir)
        if not cards_ok:
            self.log.debug("t4_gate1_prefinalize_skipped", reason=cards_err)
            return False
        gate1_paths = self._t4_gate1_artifact_paths(ctx)
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=gate1_paths,
            inputs=self._t4_upstream_input_paths(ctx),
            event="t4_gate1_prefinalize_skipped",
            reason="gate1_artifacts_older_than_t4_inputs",
        ):
            return False
        self.progress.emit(
            "[轨迹] T4 Gate1 候选池已就绪：Pass1、Pass2、候选卡片和选择简报均已落盘，转入人工选择。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_gate1_ready",
            {
                "outputs": [
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                    "ideation/bridge_coverage_review.json",
                    "ideation/final_cards/portfolio_cards.json",
                ],
            },
            action_type="t4_gate1_ready",
        )
        return True

    def _maybe_finalize_t4_gate1_outputs(
        self,
        *,
        ctx: ExecutionContext,
        stop_reason: str,
        error_msg: str | None,
    ) -> tuple[str, str | None]:
        """Convert a partial/failed T4 run into a Gate1-ready success when possible."""

        if not self._is_t4_ideation_agent(ctx) or self._t4_gate1_user_selection_exists(ctx):
            return stop_reason, error_msg
        if ctx.extra.get("completion_mode") in {"t4_resume_prefinalize", "t4_gate1_ready"}:
            return stop_reason, error_msg
        if (
            ctx.extra.get("t4_execution_mode") == "evolutionary"
            and stop_reason != AgentResult.STOP_FINISHED
        ):
            # Native T4 already persists each completed phase.  A failed
            # generation must resume from that state rather than falling into
            # the legacy Gate1 projection and emitting a misleading event.
            return stop_reason, error_msg
        ok, err = validate_t4_gate1_ready(ctx.workspace_dir)
        # Keep provider failures resumable rather than manufacturing a
        # deterministic candidate deck. See the matching preflight path above.
        if not ok:
            self.log.debug("t4_gate1_finalize_skipped", reason=err)
            return stop_reason, error_msg
        cards_ok, cards_err = validate_t4_portfolio_final_cards(ctx.workspace_dir)
        if not cards_ok:
            self.log.debug("t4_gate1_finalize_skipped", reason=cards_err)
            return stop_reason, error_msg
        gate1_paths = self._t4_gate1_artifact_paths(ctx)
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=gate1_paths,
            inputs=self._t4_upstream_input_paths(ctx),
            event="t4_gate1_finalize_skipped",
            reason="gate1_artifacts_older_than_t4_inputs",
        ):
            return stop_reason, error_msg
        self.progress.emit(
            "[轨迹] T4 Gate1 候选池已就绪：Pass1、Pass2、候选卡片和选择简报均已落盘，暂停进入人工选择。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t4_gate1_ready",
            {
                "outputs": [
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                    "ideation/bridge_coverage_review.json",
                    "ideation/final_cards/portfolio_cards.json",
                ],
            },
            action_type="t4_gate1_ready",
        )
        return AgentResult.STOP_FINISHED, None

    @staticmethod
    def _t4_gate1_user_selection_exists(ctx: ExecutionContext) -> bool:
        from ..orchestration.state_machine import validate_t4_gate1_selection_file

        ok, _ = validate_t4_gate1_selection_file(ctx.workspace_dir)
        return ok

    def _t4_final_outputs_follow_gate1(self, ctx: ExecutionContext) -> bool:
        """Require final T4 artifacts to be produced after the formal Gate1 choice.

        A previous or interrupted T4 run may already have written final hypotheses
        before the user made a Gate1 decision. Reusing those files on resume would
        make the new formal gate cosmetic only, so final artifacts are considered
        reusable only when either Gate1 is not ready yet, or a recorded selection
        exists and the downstream final artifacts are newer than that selection.
        """

        selection_path = ctx.workspace_dir / "ideation" / "_gate1_user_selection.json"
        if not selection_path.exists() or selection_path.stat().st_size <= 0:
            ok, _ = validate_t4_gate1_ready(ctx.workspace_dir)
            cards_ok, _ = validate_t4_portfolio_final_cards(ctx.workspace_dir)
            if ok and cards_ok:
                self.log.info("t4_resume_prefinalize_skipped", reason="gate1_selection_missing")
                return False
            return True

        selection_mtime = selection_path.stat().st_mtime
        final_paths = [
            ctx.workspace_dir / "ideation" / "hypotheses.md",
            ctx.workspace_dir / "ideation" / "exp_plan.yaml",
            ctx.workspace_dir / "ideation" / "risks.md",
            ctx.workspace_dir / "ideation" / "idea_scorecard.yaml",
            ctx.workspace_dir / "ideation" / "idea_rationales.json",
            ctx.workspace_dir / "ideation" / "gate_decisions.json",
            ctx.workspace_dir / "ideation" / "rejected_ideas.md",
            ctx.workspace_dir / "ideation" / "selected_idea_brief.md",
        ]
        stale_paths = [
            str(path.relative_to(ctx.workspace_dir))
            for path in final_paths
            if path.exists() and path.stat().st_mtime <= selection_mtime
        ]
        if stale_paths:
            self.log.info(
                "t4_resume_prefinalize_skipped",
                reason="final_outputs_older_than_gate1_selection",
                stale_paths=stale_paths,
            )
            return False
        return True

    def _t4_gate1_artifact_paths(self, ctx: ExecutionContext) -> list[Path]:
        paths = [
            ctx.workspace_dir / "ideation" / "_pass1_forward_candidates.json",
            ctx.workspace_dir / "ideation" / "_pass2_grounding_review.json",
            ctx.workspace_dir / "ideation" / "_candidate_directions.json",
            ctx.workspace_dir / "ideation" / "_gate1_candidate_cards.md",
            ctx.workspace_dir / "ideation" / "_gate1_selection_brief.md",
            ctx.workspace_dir / "ideation" / "final_cards" / "portfolio_cards.json",
        ]
        bridge_review = ctx.workspace_dir / "ideation" / "bridge_coverage_review.json"
        if bridge_review.exists():
            paths.append(bridge_review)
        return paths

    @staticmethod
    def _t4_stop_reason_allows_gate1_recovery(stop_reason: str, error_msg: str | None) -> bool:
        if stop_reason not in {AgentResult.STOP_INTERRUPTED, AgentResult.STOP_ERROR, AgentResult.STOP_MAX_STEPS}:
            return False
        text = str(error_msg or "").casefold()
        return any(
            marker in text
            for marker in (
                "llm provider",
                "provider",
                "timeout",
                "temporarily unavailable",
                "暂时不可用",
                "连续超时",
                "all candidates failed",
            )
        )

    def _t4_last_error_allows_gate1_recovery(self, ctx: ExecutionContext) -> bool:
        resume_path = ctx.workspace_dir / "_runtime" / "resume" / "t4_resume_state.json"
        text = ""
        if resume_path.exists():
            try:
                text += resume_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
        state_path = ctx.workspace_dir / "state.yaml"
        if state_path.exists():
            try:
                text += "\n" + state_path.read_text(encoding="utf-8", errors="replace")[-12000:]
            except OSError:
                pass
        return self._t4_stop_reason_allows_gate1_recovery(AgentResult.STOP_INTERRUPTED, text)

    def _t4_upstream_input_paths(self, ctx: ExecutionContext) -> list[Path]:
        return [
            ctx.workspace_dir / "project.yaml",
            ctx.workspace_dir / "literature" / "synthesis.md",
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "domain_map.json",
            ctx.workspace_dir / "literature" / "bridge_domain_plan.json",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
            ctx.workspace_dir / "literature" / "missing_areas.md",
            ctx.workspace_dir / "ideation" / "survey_insights.json",
            ctx.workspace_dir / "user_seeds" / "seed_ideas.md",
            ctx.workspace_dir / "user_seeds" / "seed_constraints.md",
        ]

    def _t45_output_paths(self, ctx: ExecutionContext) -> list[Path]:
        paths = [
            ctx.workspace_dir / "ideation" / "novelty_audit.md",
        ]
        tuples_dir = ctx.workspace_dir / "ideation" / "_mechanism_tuples"
        if tuples_dir.exists():
            paths.extend(path for path in tuples_dir.rglob("*") if path.is_file())
        design_tuples_dir = ctx.workspace_dir / "ideation" / "_design_rationale_tuples"
        if design_tuples_dir.exists():
            paths.extend(path for path in design_tuples_dir.rglob("*") if path.is_file())
        collision_path = ctx.workspace_dir / "ideation" / "collision_cases.md"
        if collision_path.exists():
            paths.append(collision_path)
        # Formalized T4.5 outputs are freshness-relevant only when they are
        # explicitly bound to a passed novelty audit. Older workspaces can
        # legitimately contain a pre-existing formal bundle that was migrated
        # into a Pre-Novelty brief. Treating that source bundle as a T4.5
        # output makes it older than the migration artifacts and incorrectly
        # forces an LLM call on an otherwise valid audit resume.
        formalization_path = ctx.workspace_dir / "ideation" / "post_novelty_formalization.json"
        try:
            formalization = json.loads(formalization_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            formalization = {}
        if isinstance(formalization, dict) and formalization.get("semantics") == "t45_post_novelty_formalization" and formalization.get(
            "status"
        ) == "formalized_after_novelty_pass":
            for rel in (
                "ideation/hypotheses.md",
                "ideation/research_dossier.json",
                "ideation/exp_plan.yaml",
                "ideation/contribution_hypothesis_map.yaml",
                "ideation/validation_map.yaml",
                "ideation/kill_criteria.yaml",
                "ideation/proposal/research_proposal.md",
                "ideation/proposal/proposal_manifest.json",
                "ideation/post_novelty_formalization.json",
            ):
                path = ctx.workspace_dir / rel
                if path.exists():
                    paths.append(path)
        return paths

    def _t45_upstream_input_paths(self, ctx: ExecutionContext) -> list[Path]:
        paths = [
            ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml",
            ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json",
            ctx.workspace_dir / "ideation" / "selected" / "t45_search_targets.json",
            ctx.workspace_dir / "ideation" / "idea_scorecard.yaml",
            ctx.workspace_dir / "ideation" / "idea_rationales.json",
            ctx.workspace_dir / "ideation" / "gate_decisions.json",
            ctx.workspace_dir / "literature" / "synthesis.md",
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "comparison_table.csv",
        ]
        if ctx.extra.get("t45_legacy_migrated_brief"):
            # The generated legacy brief/lineage/search files have a newer
            # filesystem timestamp than a valid historical audit by design.
            # Their semantic source is hypotheses.md, which remains the input
            # that invalidates reuse when it changes.
            generated_paths = {
                ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml",
                ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json",
                ctx.workspace_dir / "ideation" / "selected" / "t45_search_targets.json",
            }
            paths = [path for path in paths if path not in generated_paths]
            paths.insert(0, ctx.workspace_dir / "ideation" / "hypotheses.md")
        return paths

    def _outputs_newer_than_inputs(
        self,
        ctx: ExecutionContext,
        *,
        outputs: list[Path],
        inputs: list[Path],
        event: str,
        reason: str,
    ) -> bool:
        existing_outputs = [path for path in outputs if path.exists() and path.stat().st_size > 0]
        if not existing_outputs:
            self.log.info(event, reason=f"{reason}:missing_outputs")
            return False
        existing_inputs = [path for path in inputs if path.exists() and path.stat().st_size > 0]
        if not existing_inputs:
            return True

        oldest_output_mtime = min(path.stat().st_mtime for path in existing_outputs)
        newer_inputs = [
            str(path.relative_to(ctx.workspace_dir))
            for path in existing_inputs
            if path.stat().st_mtime > oldest_output_mtime
        ]
        if newer_inputs:
            oldest_outputs = [
                str(path.relative_to(ctx.workspace_dir))
                for path in existing_outputs
                if path.stat().st_mtime == oldest_output_mtime
            ]
            self.log.info(
                event,
                reason=reason,
                newer_inputs=newer_inputs,
                oldest_outputs=oldest_outputs,
            )
            return False
        return True

    async def _maybe_finalize_t45_before_llm(self, ctx: ExecutionContext) -> bool:
        """T4.5 续跑时，已有审计和 mechanism tuples 合格则直接完成。"""

        if ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}:
            repair_blocked, repair_block_reason = self._t45_quality_repair_window_blocked(ctx)

            def _raise_if_repair_window_blocked() -> None:
                """Raise only after the deterministic pre-finalization fast path.

                A prior repair ledger can describe an error that a newer
                runtime has fixed.  Checking that ledger before
                ``validate_outputs`` used to prevent the runtime from
                publishing the now-valid final receipt, trapping a paused
                workspace in an obsolete repair gate.
                """

                if repair_blocked:
                    raise RecoverableRuntimePause(
                        "T45_REPAIR_WINDOW_PAUSED: "
                        + repair_block_reason
                        + "。为避免 resume 重复消耗额度，系统不会自动再次调用 Formalizer。"
                        "请先修正该诊断关联的 source artifact，或从 T4 重新选择/重构研究方向后再 resume。"
                    )
            if ctx.task_id == "T4.5-FORMALIZE":
                outputs = [
                    ctx.workspace_dir / "ideation" / "research_blueprint.yaml",
                    ctx.workspace_dir / "ideation" / "claim_registry.yaml",
                    ctx.workspace_dir / "ideation" / "exp_plan.yaml",
                    ctx.workspace_dir / "ideation" / "hypotheses.md",
                    ctx.workspace_dir / "ideation" / "proposal" / "research_proposal.md",
                ]
                inputs = [
                    ctx.workspace_dir / "project.yaml",
                    ctx.workspace_dir / "ideation" / "selected" / "selected_candidate.json",
                    ctx.workspace_dir / "ideation" / "hypothesis_brief.yaml",
                    ctx.workspace_dir / "ideation" / "novelty_audit.md",
                    ctx.workspace_dir / "ideation" / "t4_run_config.json",
                    ctx.workspace_dir / "literature" / "synthesis.md",
                ]
            else:
                outputs = [ctx.workspace_dir / "ideation" / "orientation_review.json"]
                inputs = [
                    ctx.workspace_dir / "ideation" / "research_blueprint.yaml",
                    ctx.workspace_dir / "ideation" / "claim_registry.yaml",
                    ctx.workspace_dir / "ideation" / "exp_plan.yaml",
                    ctx.workspace_dir / "ideation" / "hypotheses.md",
                    ctx.workspace_dir / "ideation" / "proposal" / "research_proposal.md",
                ]
            if any(not path.is_file() or path.stat().st_size <= 0 for path in outputs):
                _raise_if_repair_window_blocked()
                return False
            if not self._outputs_newer_than_inputs(
                ctx,
                outputs=outputs,
                inputs=inputs,
                event="t45_formalization_resume_prefinalize_skipped",
                reason="formalization_outputs_older_than_inputs",
            ):
                _raise_if_repair_window_blocked()
                return False
            ok, err = self.agent.validate_outputs(ctx)
            if not ok:
                self.log.info("t45_formalization_resume_prefinalize_skipped", reason=err)
                _raise_if_repair_window_blocked()
                return False
            self.progress.emit(
                "[Research Formalizer Agent] 已有研究正式化产物完整且校验通过，跳过重复 LLM 读取与确认。",
                important=True,
            )
            relative_outputs = [path.relative_to(ctx.workspace_dir).as_posix() for path in outputs]
            self._record_runtime_completion(
                ctx,
                "t45_formalization_resume_prefinalize",
                {"outputs": relative_outputs},
                action_type="t45_formalization_resume_prefinalize",
            )
            return True

        if ctx.task_id != "T4.5":
            return False

        required_paths = [
            ctx.workspace_dir / "ideation" / "novelty_audit.md",
            ctx.workspace_dir / "ideation" / "_mechanism_tuples",
        ]
        if any(not path.exists() for path in required_paths):
            return False
        if not self._outputs_newer_than_inputs(
            ctx,
            outputs=self._t45_output_paths(ctx),
            inputs=self._t45_upstream_input_paths(ctx),
            event="t45_resume_prefinalize_skipped",
            reason="novelty_outputs_older_than_t45_inputs",
        ):
            return False
        if ctx.extra.get("t45_legacy_migrated_brief"):
            legacy_ok, legacy_error = validate_legacy_t45_brief_source(ctx.workspace_dir)
            if not legacy_ok:
                self.log.info("t45_resume_prefinalize_skipped", reason=legacy_error)
                return False
        ok, err = validate_t45_fingerprint_report(ctx.workspace_dir)
        if not ok:
            self.log.info("t45_resume_prefinalize_skipped", reason=err)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t45_resume_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Novelty Auditor Agent] T4.5 检测到已有 novelty audit 且校验通过，跳过重复 LLM",
            important=True,
        )
        outputs = [
            "ideation/novelty_audit.md",
            "ideation/_mechanism_tuples",
        ]
        collision_path = ctx.workspace_dir / "ideation" / "collision_cases.md"
        if collision_path.exists():
            outputs.append("ideation/collision_cases.md")
        self._record_runtime_completion(
            ctx,
            "t45_resume_prefinalize",
            {"outputs": outputs},
            action_type="t45_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_external_wait_before_llm(self, ctx: ExecutionContext) -> bool:
        """Accept a final Writer Handoff before the state machine can enter T8."""

        if ctx.task_id != "T5-EXTERNAL-WAIT":
            return False

        receipt = accept_and_ingest_t5_handoff(ctx.workspace_dir)
        if not receipt.get("ok"):
            issues = receipt.get("errors") if isinstance(receipt.get("errors"), list) else []
            summary = "; ".join(
                f"{item.get('code')}: {item.get('path')}"
                for item in issues[:4]
                if isinstance(item, dict)
            )
            raise RecoverableRuntimePause(
                "WAITING_EXTERNAL_WRITER_HANDOFF: final Writer Handoff is not acceptable for T8"
                + (f"; {summary}" if summary else "")
            )
        ingest = validate_t8_ingest_artifacts(ctx.workspace_dir, receipt)
        if not ingest.get("ok"):
            issues = ingest.get("errors") if isinstance(ingest.get("errors"), list) else []
            summary = "; ".join(
                f"{item.get('code')}: {item.get('path')}"
                for item in issues[:4]
                if isinstance(item, dict)
            )
            raise RecoverableRuntimePause(
                "WAITING_EXTERNAL_WRITER_HANDOFF: T8 normalization is incomplete or stale"
                + (f"; {summary}" if summary else "")
            )

        report = {
            "version": "1.1",
            "semantics": "external_executor_wait_acceptance_report",
            "ok": True,
            "acceptance_path": "drafts/t5_t8_handoff.json",
            "experiment_evidence_pack": "drafts/experiment_evidence_pack.json",
            "result_to_claim": "drafts/result_to_claim.json",
            "handoff_id": receipt.get("handoff_id"),
            "ingest_fingerprint": receipt.get("ingest_fingerprint"),
            "metric_count": receipt.get("metric_count", 0),
            "claim_mapping_count": receipt.get("claim_mapping_count", 0),
            "status": receipt.get("status"),
            "message": "Modern Writer Handoff and deterministic T5-to-T8 ingest are ready.",
        }

        output_path = ctx.workspace_dir / "external_executor" / "wait_acceptance_report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.progress.emit(
            "[Experimenter Agent] T5-EXTERNAL-WAIT 已验收 Writer Handoff，并生成 T8 事实包/claim 映射；跳过 LLM 进入 T8",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "external_wait_prefinalize",
            {
                "outputs": [
                    "external_executor/wait_acceptance_report.json",
                    "drafts/t5_t8_handoff.json",
                    "drafts/experiment_evidence_pack.json",
                    "drafts/result_to_claim.json",
                ],
            },
            action_type="external_wait_prefinalize",
        )
        return True

    async def _maybe_finalize_resource_prepare_wait_before_llm(self, ctx: ExecutionContext) -> bool:
        """Accept a bounded external Phase B run before recompiling T5."""

        if ctx.task_id != "T5-RESOURCE-PREP-WAIT":
            return False

        selection_path = ctx.workspace_dir / "external_executor" / "report" / "executor_selection.json"
        report_path = ctx.workspace_dir / "external_executor" / "report" / "phase_B" / "resource_preparation_report.json"
        validation_path = ctx.workspace_dir / "external_executor" / "report" / "phase_B" / "validation_report.json"
        source_report_path = ctx.workspace_dir / "external_executor" / "report" / "phase_B" / "resource_source_report.json"
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RecoverableRuntimePause(
                "WAITING_RESOURCE_PREPARATION: external executor selection is unavailable; choose a resource-preparation executor again."
            ) from exc
        if not isinstance(selection, dict) or selection.get("execution_scope") != "resource_preparation":
            raise RecoverableRuntimePause(
                "WAITING_RESOURCE_PREPARATION: the current executor selection is not limited to resource preparation."
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RecoverableRuntimePause(
                "WAITING_RESOURCE_PREPARATION: Phase B is still running. Complete resource discovery, acquisition, static review, "
                "and validation before resuming ResearchOS."
            ) from exc
        readiness = report.get("resource_readiness") if isinstance(report.get("resource_readiness"), dict) else {}
        readiness_status = str(readiness.get("status") or "").strip()
        validation_ok = (
            validation.get("schema_version") == "resource_preparation_validation.v1"
            and validation.get("child_skill") == "resource-and-baseline-preparation"
            and validation.get("valid") is True
            and validation.get("status") == "pass"
            and validation.get("resource_preparation_report")
            == "external_executor/report/phase_B/resource_preparation_report.json"
        )
        source_report_ok = (
            isinstance(source_report, dict)
            and source_report.get("schema_version") == "resource_source_report.v1"
            and source_report.get("status") in {"complete", "partial"}
            and isinstance(source_report.get("counts"), dict)
        )
        if (
            report.get("schema_version") != "resource_preparation_report.v1"
            or report.get("child_skill") != "resource-and-baseline-preparation"
            or report.get("status") not in {"complete", "partial", "blocked"}
            or readiness_status not in {"ready", "partial", "blocked"}
            or not validation_ok
            or not source_report_ok
        ):
            raise RecoverableRuntimePause(
                "WAITING_RESOURCE_PREPARATION: the Phase B report, source record, or overall validation receipt is incomplete."
            )

        acceptance = {
            "version": "1.0",
            "semantics": "t5_resource_preparation_acceptance",
            "ok": True,
            "execution_scope": "resource_preparation",
            "resource_preparation_report": "external_executor/report/phase_B/resource_preparation_report.json",
            "resource_validation_report": "external_executor/report/phase_B/validation_report.json",
            "resource_source_report": "external_executor/report/phase_B/resource_source_report.json",
            "resource_readiness": readiness_status,
            "next_step": "T5-REBOOST-GATE",
            "note": (
                "Phase B resource preparation is accepted as provenance and readiness context only; "
                "it is not an experiment result or a T8 handoff."
            ),
        }
        output_path = ctx.workspace_dir / "external_executor" / "report" / "resource_preparation_acceptance.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.progress.emit(
            "[Experimenter Agent] T5 已接收外部 Phase B 资源准备记录；将重新编译交接，不把资源准备当作实验结果。",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "resource_preparation_wait_prefinalize",
            {"outputs": ["external_executor/report/resource_preparation_acceptance.json"]},
            action_type="resource_preparation_wait_prefinalize",
        )
        return True

    async def _maybe_finalize_t9_submission_before_hooks(self, ctx: ExecutionContext) -> bool:
        """Finish T9 from an already valid submission bundle before hooks/LLM.

        T9's compile-environment pre-hook is necessary when the bundle still
        needs work, but it should not block resume if the current workspace
        already contains a validator-clean `submission/bundle`. This also
        avoids launching the SubmissionAgent LLM merely to rediscover that the
        existing PDF/report are already valid.
        """

        if ctx.task_id != "T9" or self.agent.spec.name != "submission":
            return False

        bundle_dir = ctx.workspace_dir / "submission" / "bundle"
        required = [
            bundle_dir / "main.tex",
            bundle_dir / "references.bib",
            bundle_dir / "main.pdf",
            bundle_dir / "main.log",
            ctx.workspace_dir / "submission" / "compile_report.json",
            ctx.workspace_dir / "submission" / "migration_report.md",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in required):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t9_submission_prefinalize_skipped", reason=err)
            return False

        self.progress.emit(
            "[Submission Agent] T9 检测到已有投稿包且校验通过，跳过环境检查和重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t9_submission_prefinalize",
            {
                "outputs": [
                    "submission/bundle/main.tex",
                    "submission/bundle/main.pdf",
                    "submission/bundle/main.log",
                    "submission/compile_report.json",
                    "submission/migration_report.md",
                ],
            },
            action_type="t9_submission_prefinalize",
        )
        return True

    async def _maybe_finalize_paper_claim_audit_before_llm(
        self,
        ctx: ExecutionContext,
        policy: WorkspaceAccessPolicy,
    ) -> bool:
        """Run the final T8 paper-claim audit as a deterministic tool boundary."""

        if ctx.task_id != "T8-PAPER-CLAIM-AUDIT":
            return False

        required = [
            ctx.workspace_dir / "drafts" / "paper.tex",
            ctx.workspace_dir / "drafts" / "experiment_evidence_pack.json",
            ctx.workspace_dir / "drafts" / "result_to_claim.json",
        ]
        if any(not path.exists() or path.stat().st_size <= 0 for path in required):
            return False

        tool = AuditPaperClaimsTool(policy)
        result = await tool.execute(
            paper_path="drafts/paper.tex",
            evidence_pack_path="drafts/experiment_evidence_pack.json",
            result_to_claim_path="drafts/result_to_claim.json",
            output_path="drafts/paper_claim_audit.md",
        )
        if not result.ok:
            self.log.warning("paper_claim_audit_prefinalize_failed", error=result.error, content=result.content)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning("paper_claim_audit_prefinalize_validation_failed", error=err)
            return False

        self.progress.emit(
            "[Writer Agent] T8-PAPER-CLAIM-AUDIT 已用确定性工具完成，跳过 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "paper_claim_audit_prefinalize",
            {"outputs": ["drafts/paper_claim_audit.md", "drafts/paper_claim_audit.json"]},
            action_type="paper_claim_audit_prefinalize",
        )
        return True

    async def _maybe_finalize_t8_resource_before_llm(self, ctx: ExecutionContext) -> bool:
        """Build or reuse T8's fixed provenance-indexing outputs without LLM calls."""

        if ctx.task_id != "T8-RESOURCE":
            return False
        if ctx.mode not in {None, "resource_index"} and ctx.extra.get("phase") != "resource_index":
            return False
        ok, err = self.agent.validate_outputs(ctx)
        reused = bool(ok)
        if not ok:
            from .manuscript_recovery import build_t8_resource_outputs

            self.progress.emit(
                "[Writer Agent] T8-RESOURCE 正在确定性构建资源索引与证据对齐，不调用模型...",
                important=True,
            )
            built, build_error = await build_t8_resource_outputs(ctx.workspace_dir)
            if not built:
                self.log.warning(
                    "t8_resource_deterministic_build_failed",
                    validation_error=err,
                    build_error=build_error,
                )
                return False
            ok, err = self.agent.validate_outputs(ctx)
            if not ok:
                self.log.warning("t8_resource_deterministic_validation_failed", error=err)
                return False
        self.progress.emit(
            (
                "[Writer Agent] T8-RESOURCE 检测到资源索引产物已合格，跳过重复 LLM 并进入下一阶段"
                if reused
                else "[Writer Agent] T8-RESOURCE 资源索引与证据对齐已确定性完成"
            ),
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_resource_prefinalize" if reused else "t8_resource_deterministic",
            {
                "outputs": [
                    "drafts/manuscript_resource_index.json",
                    "drafts/section_plan.json",
                    "drafts/evidence_plan.json",
                    "drafts/figure_table_plan.json",
                    "drafts/cdr_claim_ledger.json",
                    "drafts/claim_ledger.json",
                    "drafts/figure_registry.json",
                    "drafts/alignment_matrix.json",
                ],
            },
            action_type="t8_resource_prefinalize" if reused else "t8_resource_deterministic",
        )
        return True

    def _maybe_complete_t8_resource_after_spurious_human_prompt(self, ctx: ExecutionContext) -> bool:
        """Finish T8-RESOURCE if the model asks a user question after completion."""

        if ctx.task_id != "T8-RESOURCE":
            return False
        if ctx.mode not in {None, "resource_index"} and ctx.extra.get("phase") != "resource_index":
            return False
        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t8_resource_spurious_human_prompt_not_finished", reason=err)
            return False
        self.progress.emit(
            "[Writer Agent] T8-RESOURCE 资源索引已通过校验；忽略模型的继续确认请求并交给状态机推进",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_resource_prefinalize",
            {
                "outputs": [
                    "drafts/manuscript_resource_index.json",
                    "drafts/section_plan.json",
                    "drafts/evidence_plan.json",
                    "drafts/figure_table_plan.json",
                    "drafts/cdr_claim_ledger.json",
                    "drafts/claim_ledger.json",
                    "drafts/figure_registry.json",
                    "drafts/alignment_matrix.json",
                ],
            },
            action_type="t8_resource_spurious_human_prompt_finalized",
        )
        return True

    async def _maybe_finalize_t8_section_plan_before_llm(
        self,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
    ) -> bool:
        """Repair/initialize T8 section state deterministically before LLM work.

        `T8-SECTION-PLAN` is a mechanical boundary: it should call
        initialize_manuscript_state and stop. If a previous run let the LLM
        hand-write an incompatible paper_state.json, resume should repair it
        from the already-approved outline/plans instead of spending another
        LLM run on the same deterministic job.
        """

        if ctx.task_id != "T8-SECTION-PLAN":
            return False
        if ctx.mode not in {None, "section_plan"} and ctx.extra.get("phase") != "section_plan":
            return False

        if not can_repair_t8_section_plan(ctx.workspace_dir):
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if ok:
            self.progress.emit(
                "[Writer Agent] T8-SECTION-PLAN 检测到 paper_state/section_outlines 已合格，跳过重复 LLM",
                important=True,
            )
            self._record_runtime_completion(
                ctx,
                "t8_section_plan_prefinalize",
                {
                    "outputs": [
                        "drafts/paper_state.json",
                        "drafts/section_outlines",
                    ],
                },
                action_type="t8_section_plan_prefinalize",
            )
            return True

        self.progress.emit(
            "[Writer Agent] T8-SECTION-PLAN 检测到已有计划文件但状态不合格，"
            "使用 initialize_manuscript_state 确定性修复...",
            important=True,
        )
        project = {}
        project_path = ctx.workspace_dir / "project.yaml"
        if project_path.exists():
            try:
                import yaml

                loaded = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    project = loaded
            except Exception:
                project = {}
        ok, err = await repair_t8_section_plan_outputs(
            ctx.workspace_dir,
            target_venue=str(project.get("target_venue") or ""),
        )
        if not ok:
            self.log.warning(
                "t8_section_plan_prefinalize_failed",
                error=err,
            )
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning("t8_section_plan_prefinalize_validation_failed", error=err)
            return False

        self.progress.emit(
            "[Writer Agent] T8-SECTION-PLAN 状态修复成功，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_section_plan_prefinalize",
            {
                "outputs": [
                    "drafts/paper_state.json",
                    "drafts/section_outlines",
                ],
            },
            action_type="t8_section_plan_prefinalize",
        )
        return True

    async def _maybe_finalize_t8_section_before_llm(self, ctx: ExecutionContext) -> bool:
        """Reuse one already committed T8 section when all shared inputs remain current."""

        if not ctx.task_id.startswith("T8-SEC-") or ctx.task_id == "T8-SECTION-PLAN":
            return False
        if ctx.mode not in {None, "section_draft"} and ctx.extra.get("phase") != "section_draft":
            return False
        from ..agents.writer import _validate_paper_state
        from ..tools.manuscript import normalize_section_id

        section_id = normalize_section_id(
            str(ctx.extra.get("section_id") or ctx.extra.get("section") or "")
        )
        if not section_id:
            return False
        state_path = ctx.workspace_dir / "drafts" / "paper_state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        entry = (
            state.get("sections", {}).get(section_id, {})
            if isinstance(state.get("sections"), dict)
            else {}
        )
        if not isinstance(entry, dict) or entry.get("status") not in {"written", "revised"}:
            return False
        state_ok, state_error = _validate_paper_state(ctx.workspace_dir)
        if not state_ok:
            self.log.info(
                "t8_section_prefinalize_skipped_stale_state",
                task=ctx.task_id,
                reason=state_error,
            )
            return False
        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t8_section_prefinalize_skipped", task=ctx.task_id, reason=err)
            return False
        output = f"drafts/sections/{section_id}.tex"
        self.progress.emit(
            f"[Writer Agent] {ctx.task_id} 检测到当前共享输入下已提交的合格章节，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_section_resume_prefinalize",
            {"outputs": [output], "section_id": section_id},
            action_type="t8_section_resume_prefinalize",
        )
        return True

    async def _maybe_finalize_t8_manuscript_before_llm(self, ctx: ExecutionContext) -> bool:
        """Refresh T8 assembled manuscript/audits before spending another LLM run.

        T8-DRAFT and T8-REVISE are artifact-first boundaries. If section files,
        patch lists, revision responses, and audits are already present, resume
        should first rebuild deterministic outputs from section files and then
        validate. This prevents stale craft audits from sending Writer into
        repeated section rewrites for old or soft checks.
        """

        if ctx.task_id not in {"T8-DRAFT", "T8-REVISE-1", "T8-REVISE-2"}:
            return False
        if ctx.mode not in {None, "draft", "revise"} and ctx.extra.get("phase") not in {"draft", "revise"}:
            return False
        if not can_refresh_t8_manuscript_outputs(ctx.workspace_dir):
            return False

        self.progress.emit(
            "[Writer Agent] T8 检测到已有章节草稿，先确定性重拼 manuscript 并刷新审计",
            important=True,
        )
        ok, err = await refresh_t8_manuscript_outputs(ctx.workspace_dir)
        if not ok:
            self.log.info("t8_manuscript_prefinalize_refresh_failed", reason=err)
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.info("t8_manuscript_prefinalize_validation_skipped", reason=err)
            return False

        self.progress.emit(
            "[Writer Agent] T8 manuscript 产物已合格，跳过重复 LLM",
            important=True,
        )
        self._record_runtime_completion(
            ctx,
            "t8_manuscript_prefinalize",
            {
                "outputs": [
                    "drafts/paper.tex",
                    "drafts/manuscript_audit.md",
                    "drafts/craft_audit.md",
                    "drafts/craft_audit.json",
                ],
            },
            action_type="t8_manuscript_prefinalize",
        )
        return True

    async def _maybe_prepare_t35_before_llm(
        self,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
    ) -> bool:
        """T3.5 may prebuild evidence scaffolding, but must not finish.

        Synthesis is a knowledge-heavy task. The tool can organize notes into a
        workbench and outline, yet final section claims must come from the
        Reader LLM after inspecting those artifacts.
        """

        if ctx.task_id != "T3.5":
            return False
        mode_params = get_agent_mode_params("reader", "synthesize")
        if not bool(mode_params.get("prebuild_workbench_before_llm", False)):
            return False
        build_literature_manifest(ctx.workspace_dir, write=True)
        note_files = [
            ctx.workspace_dir / card.rel_path
            for card in iter_literature_note_cards(ctx.workspace_dir, include_shallow=False)
        ]
        if not note_files:
            return False
        # A Cross-domain catalog is an independent synthesis input, not a
        # paper note.  Reusing a workbench based only on note mtimes used to
        # hide a newly retrieved B1/B2 track from T3.5 and all downstream
        # ideation.  Include the plan, index and per-track context/catalog
        # files in the freshness boundary while still allowing a zero-record
        # track to remain valid contextual input.
        bridge_inputs = [
            ctx.workspace_dir / "literature" / "bridge_domain_plan.json",
            ctx.workspace_dir / "literature" / "cross_domain_catalogs" / "index.json",
        ]
        for catalog_path in iter_bridge_catalog_paths(ctx.workspace_dir):
            bridge_inputs.append(catalog_path)
            bridge_inputs.append(catalog_path.parent / "bridge_context.json")
        synthesis_inputs = [path for path in [*note_files, *bridge_inputs] if path.is_file()]
        staged_outputs = [
            ctx.workspace_dir / "literature" / "synthesis_workbench.json",
            ctx.workspace_dir / "literature" / "synthesis_context.json",
            ctx.workspace_dir / "literature" / "synthesis_outline.md",
            ctx.workspace_dir / "literature" / "synthesis_draft.md",
        ]
        if all(path.exists() and path.stat().st_size > 0 for path in staged_outputs):
            newest_input_mtime = max((path.stat().st_mtime for path in synthesis_inputs), default=0)
            oldest_staged_mtime = min(path.stat().st_mtime for path in staged_outputs)
            if oldest_staged_mtime >= newest_input_mtime:
                self.progress.emit(
                    "[Synthesizer Agent] T3.5 使用已有结构化综合材料\n"
                    f"- 输入: 检测到 {len(note_files)} 份 paper notes 与 {len(bridge_inputs)} 个 Cross-domain 上下文入口，现有 workbench 未过期\n"
                    "- 输出: 完整审计 workbench、紧凑 reasoning index、outline 与 draft guidance\n"
                    "- 后续: LLM 先基于紧凑 index 复核，只对关键论断定向回查笔记后写 synthesis.md",
                    important=True,
                )
                actions = ctx.extra.setdefault("runtime_actions", [])
                if isinstance(actions, list):
                    actions.append(
                        {
                            "type": "t35_synthesis_workbench_reused",
                            "mode": "t35_workbench_reused",
                            "outputs": [
                                str(path.relative_to(ctx.workspace_dir))
                                for path in staged_outputs
                            ],
                        }
                    )
                ctx.extra["t35_workbench_prepared"] = True
                ctx.extra["t35_workbench_reused"] = True
                return True

        from ..tools.literature_synthesis import BuildSynthesisWorkbenchTool

        self.progress.emit(
            "[Synthesizer Agent] T3.5 先执行分阶段 synthesis workbench 生成，用于把 paper notes 组织成可审计综述材料",
            important=True,
        )
        tool = BuildSynthesisWorkbenchTool(policy)
        result = await tool.execute(write_final=False, render_draft=False)
        if not result.ok:
            self.log.warning("t35_workbench_failed", error=result.error, content=result.content)
            return False

        data = result.data if isinstance(result.data, dict) else {}
        outputs = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
        output_bits = [
            str(path)
            for path in (
                outputs.get("workbench"),
                outputs.get("context"),
                outputs.get("outline"),
                outputs.get("draft"),
            )
            if path
        ]
        summary_bits = [
            f"核心深读 {data.get('deep_read_note_count', data.get('note_count', 0))}",
            f"全文/部分全文去重 {data.get('note_count', 0)}",
            f"摘要轻读 {data.get('abstract_note_count', 0)}",
            f"Bridge 论文笔记 {data.get('bridge_note_count', 0)}",
            f"方法家族 {data.get('family_count', 0)}",
        ]
        citation_target = data.get("citation_coverage_target")
        if citation_target not in (None, ""):
            summary_bits.append(f"主张级全文/部分全文最低唯一引用 {citation_target}")
        self.progress.emit(
            "[Synthesizer Agent] T3.5 结构化综合摘要\n"
            f"- 输入: {'；'.join(summary_bits)}\n"
            f"- 输出: {'；'.join(output_bits) if output_bits else 'literature/synthesis_workbench.json / synthesis_context.json / synthesis_outline.md / synthesis_draft.md'}\n"
            "- 后续: LLM 将先复核紧凑 index；只有关键不确定点才回查对应笔记，再写最终 synthesis.md",
            important=True,
        )

        actions = ctx.extra.setdefault("runtime_actions", [])
        if isinstance(actions, list):
            actions.append(
                {
                    "type": "t35_synthesis_workbench_prepared",
                    "mode": "t35_workbench_prepared",
                    "outputs": list((result.data.get("outputs") or {}).values())
                    if isinstance(result.data.get("outputs"), dict)
                    else [],
                }
            )
        ctx.extra["t35_workbench_prepared"] = True
        return True

    async def _finalize_t2_from_raw(
        self,
        ctx: ExecutionContext,
        *,
        mode: str,
        min_raw_count: int,
        start_message: str,
        success_message: str,
    ) -> bool:
        if ctx.task_id != "T2":
            return False

        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        raw_count = self._count_jsonl_records(raw_path)
        if raw_count < min_raw_count:
            return False

        needs_finalize = any(
            not path.exists()
            for name, path in ctx.outputs_expected.items()
            if name != "papers_raw"
        )
        if not needs_finalize:
            ok, _err = self.agent.validate_outputs(ctx)
            manifest_ok, _manifest_err = validate_t2_finalize_manifest(ctx.workspace_dir)
            if ok and manifest_ok:
                self._record_runtime_completion(ctx, mode, {"raw_count": raw_count})
                return True
            # A raw-pool enrichment can occur after an interrupted finalize.
            # Existing files are then structurally valid but describe a stale
            # raw snapshot. Rebuild once so every downstream artifact and the
            # manifest agree on the same durable candidate pool.
            needs_finalize = True

        if not needs_finalize:
            return False

        # Finalization can make many network and filesystem steps.  Their
        # granular state is durable in scout_progress.md and the run trace,
        # but it should not drown out the research-facing CLI.  Normal mode
        # shows one audited result below; --verbose retains the live details.
        self.progress.emit(start_message, verbose_only=True)
        recovery = await finalize_t2_outputs(
            ctx.workspace_dir,
            progress_reporter=lambda message: self.progress.emit(message, verbose_only=True),
        )
        if not recovery.get("ok"):
            reason = recovery.get("reason") or "unknown"
            self.log.warning(f"{mode}_failed", reason=reason, recovery=recovery)
            self.progress.error_context(
                stage="T2 确定性收尾",
                agent=self.agent.spec.name,
                message=str(reason),
                log_path=str(ctx.workspace_dir / "_runtime" / "logs" / "researchos.log"),
            )
            return False

        ok, err = self.agent.validate_outputs(ctx)
        if not ok:
            self.log.warning(f"{mode}_validation_failed", error=err, recovery=recovery)
            self.progress.error_context(
                stage="T2 确定性收尾后校验",
                agent=self.agent.spec.name,
                message=str(err or "unknown"),
                log_path=str(ctx.workspace_dir / "_runtime" / "logs" / "researchos.log"),
            )
            return False

        pdf_counts = recovery.get("pdf_acquisition", {}).get("counts", {})
        available_pdfs = int(
            pdf_counts.get("available_local")
            or pdf_counts.get("parseable_local")
            or pdf_counts.get("available")
            or 0
        ) if isinstance(pdf_counts, dict) else 0
        self.progress.emit(
            "[Scout Agent] T2 收尾完成："
            f"原始 {int(recovery.get('raw_count') or raw_count)} 篇，"
            f"保留 {int(recovery.get('dedup_count') or 0)} 篇，"
            f"后备 {int(recovery.get('backlog_count') or 0)} 篇；"
            f"本地可解析 PDF {available_pdfs}/{int(recovery.get('dedup_count') or 0)}，"
            f"精读队列 {int(recovery.get('deep_read_queue_count') or 0)} 篇。"
            "完整检索与可得性记录已归档。",
            important=True,
        )
        t2_config = load_t2_finalize_config(ctx.workspace_dir)
        progress_rel = str(getattr(t2_config, "progress_file", "") or "literature/temp/scout_progress.md")
        if self.runtime_settings.ui.verbose:
            self.progress.progress_file_update(
                label="Scout/T2 收尾进度",
                path=progress_rel,
                bullets=summarize_progress_markdown(ctx.workspace_dir / progress_rel, max_items=4),
            )
        self._record_runtime_completion(ctx, mode, recovery)
        self.log.debug(f"{mode}_succeeded", recovery=recovery)
        return True

    def _record_runtime_completion(
        self,
        ctx: ExecutionContext,
        mode: str,
        details: dict[str, object],
        *,
        action_type: str = "t2_finalize_from_raw",
    ) -> None:
        ctx.extra["completion_mode"] = mode
        actions = ctx.extra.setdefault("runtime_actions", [])
        if isinstance(actions, list):
            actions.append(
                {
                    "type": action_type,
                    "mode": mode,
                    "raw_count": details.get("raw_count"),
                    "dedup_count": details.get("dedup_count"),
                    "trace_count": details.get("trace_count"),
                    "outputs": details.get("outputs"),
                }
            )

    @staticmethod
    def _count_jsonl_records(path: Path) -> int:
        if not path.exists() or path.stat().st_size <= 0:
            return 0
        count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        count += 1
        except OSError:
            return 0
        return count

    @staticmethod
    def _t2_finish_finalize_min_raw(ctx: ExecutionContext) -> int:
        config_default = load_t2_finalize_config(ctx.workspace_dir).finish_finalize_min_raw
        raw_value = ctx.extra.get("t2_finish_finalize_min_raw", config_default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return config_default
        return max(10, value)

    @staticmethod
    def _is_resume_run(ctx: ExecutionContext) -> bool:
        if ctx.extra.get("resume_reason") == "retry_after_failure" and not ctx.extra.get(
            "allow_t2_failure_recovery"
        ):
            return False
        return bool(
            ctx.extra.get("is_resume")
            or ctx.extra.get("resumed_from_run_id")
            or ctx.extra.get("resumed_from")
            or ctx.extra.get("resume_reason") in {"interrupted", "iteration"}
        )

    @staticmethod
    def _allow_t2_exit_recovery(ctx: ExecutionContext) -> bool:
        if ctx.extra.get("allow_t2_failure_recovery"):
            return True
        if ctx.extra.get("resume_reason") == "retry_after_failure":
            return False
        return bool(
            ctx.extra.get("is_resume")
            or ctx.extra.get("resumed_from_run_id")
            or ctx.extra.get("resumed_from")
            or ctx.extra.get("resume_reason") in {"interrupted", "iteration"}
        )

    @staticmethod
    def _normalize_tool_call_arguments(tc: ToolCall) -> tuple[dict[str, Any], dict[str, Any]]:
        """Apply narrowly scoped compatibility repairs before schema validation.

        ``read_file`` owns its response size from the active model context.  A
        few OpenAI-compatible models nevertheless emit old, advisory read-size
        fields such as ``limit``.  Rejecting those fields creates a transient
        red error even though the path is valid and the next identical call
        normally succeeds.  Drop only known no-op fields for this one tool;
        every other malformed parameter remains a normal validation failure.
        The removed values are returned so trace artifacts retain an audit
        record without treating the call as a researcher-visible failure.
        """

        arguments = dict(tc.arguments) if isinstance(tc.arguments, dict) else {}
        if tc.name != "read_file":
            return arguments, {}

        ignored_keys = {
            "limit",
            "max_chars",
            "max_length",
            "max_tokens",
        }
        removed = {
            key: arguments.pop(key)
            for key in sorted(ignored_keys)
            if key in arguments
        }
        if not removed:
            return arguments, {}
        return arguments, {
            "kind": "read_file_legacy_size_hint_ignored",
            "removed_fields": removed,
            "reason": "read_file response size is derived from the active model context",
        }

    async def _execute_one_tool_call(
        self,
        tc: ToolCall,
        tool_map: dict[str, Tool],
        *,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
        step: int,
        budget: BudgetTracker | None = None,
        tool_failure_cache: dict[tuple[str, str], Message] | None = None,
        run_logger: RunLogger | None = None,
        skill_tool_budget: dict[str, object] | None = None,
    ) -> Message:
        started = time.time()
        tool = tool_map.get(tc.name)
        if tool is None:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"ERROR: unknown tool '{tc.name}'. Available: {sorted(tool_map)}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="unknown_tool",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        budget_ok, budget_content, budget_data = self._consume_skill_tool_budget(
            tool_name=tc.name,
            state=skill_tool_budget,
        )
        if not budget_ok:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=budget_content,
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
                metadata={"data": budget_data, "error": "skill_tool_budget_reached"},
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data=budget_data,
                    error="skill_tool_budget_reached",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        t4_order_error = self._t4_artifact_write_order_error(ctx, tc)
        if t4_order_error:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"ERROR: {t4_order_error}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="t4_artifact_order_violation",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        if tool.requires_human_approval:
            # 高风险工具先经过 HumanInterface 审批。
            human_started = time.time()
            try:
                approved = await self.human.ask_approval(tool_name=tc.name, arguments=tc.arguments)
            except HumanInputUnavailable as exc:
                tool_msg = Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval input unavailable: {exc}",
                    is_error=True,
                    step=step,
                    metadata={"data": {"input_unavailable": True}, "error": "human_input_unavailable"},
                )
                if run_logger is not None:
                    run_logger.tool_result(
                        tc.name,
                        tc.arguments,
                        ok=False,
                        content=tool_msg.content,
                        data=tool_msg.metadata.get("data") or {},
                        error="human_input_unavailable",
                        duration_ms=tool_msg.duration_ms,
                        metadata=tool_msg.metadata,
                        step=step,
                    )
                return tool_msg
            except Exception as exc:
                tool_msg = Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=f"ERROR: approval failed: {exc!r}",
                    is_error=True,
                    step=step,
                )
                if run_logger is not None:
                    run_logger.tool_result(
                        tc.name,
                        tc.arguments,
                        ok=False,
                        content=tool_msg.content,
                        data={},
                        error="approval_failed",
                        duration_ms=tool_msg.duration_ms,
                        metadata=tool_msg.metadata,
                        step=step,
                    )
                return tool_msg
            finally:
                if budget is not None:
                    budget.exclude_wall_time(time.time() - human_started)
            if not approved:
                tool_msg = Message.tool(
                    tool_call_id=tc.id,
                    name=tc.name,
                    content="Rejected by human.",
                    is_error=True,
                    step=step,
                )
                if run_logger is not None:
                    run_logger.tool_result(
                        tc.name,
                        tc.arguments,
                        ok=False,
                        content=tool_msg.content,
                        data={},
                        error="human_rejected",
                        duration_ms=tool_msg.duration_ms,
                        metadata=tool_msg.metadata,
                        step=step,
                    )
                return tool_msg

        tool_arguments, argument_auto_repair = self._normalize_tool_call_arguments(tc)
        try:
            # 先用 pydantic schema 做参数校验。少数 OpenAI-compatible
            # providers 会给 read_file 附加其历史 schema 中的 ``limit``
            # 一类提示参数；它们不改变读文件的安全边界或分页语义，已经在
            # _normalize_tool_call_arguments 中被窄范围地移除并留痕。
            parsed = tool.parameters_schema(**tool_arguments)
        except Exception as exc:
            parameter_data: dict[str, Any] = {}
            parameter_content = f"Parameter validation error: {exc}"
            if tc.name == "write_structured_file":
                attempted_path = tool_arguments.get("path")
                attempted_schema = tool_arguments.get("schema_name")
                raw_arguments = str(tool_arguments.get("__raw__") or "")
                if raw_arguments:
                    path_match = re.search(r'"path"\s*:\s*"([^"\\]+)"', raw_arguments)
                    schema_match = re.search(r'"schema_name"\s*:\s*"([^"\\]+)"', raw_arguments)
                    attempted_path = attempted_path or (path_match.group(1) if path_match else None)
                    attempted_schema = attempted_schema or (schema_match.group(1) if schema_match else None)
                parse_detail = ""
                if raw_arguments and tool_arguments.get("__parse_error__"):
                    try:
                        json.loads(raw_arguments)
                    except json.JSONDecodeError as parse_exc:
                        parse_detail = (
                            f"Native tool JSON was incomplete or malformed at character {parse_exc.pos}: "
                            f"{parse_exc.msg}. "
                        )
                open_curly = raw_arguments.count("{")
                close_curly = raw_arguments.count("}")
                open_square = raw_arguments.count("[")
                close_square = raw_arguments.count("]")
                parameter_data = {
                    "path": attempted_path,
                    "schema_name": attempted_schema,
                    "required_fields": ["path", "schema_name", "format", "data"],
                    "repair_scope": "structured_file_parameter_validation",
                    "repairable": True,
                    "raw_arguments_length": len(raw_arguments),
                    "json_parse_failed": bool(tool_arguments.get("__parse_error__")),
                    "json_structure": {
                        "open_curly": open_curly,
                        "close_curly": close_curly,
                        "open_square": open_square,
                        "close_square": close_square,
                    },
                }
                parameter_content = (
                    "write_structured_file 参数无效。path 必须是非空 workspace 相对路径，"
                    "schema_name 必须是字符串，format 必须是 yaml/json/jsonl，data 必须是对象。"
                    f"本次 path={attempted_path!r}。"
                )
                if ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}:
                    # A parse failure may still expose the exact intended
                    # source.  Preserve that intent in diagnostics instead
                    # of incorrectly telling the model to restart at the
                    # blueprint after it was already writing the registry or
                    # experiment plan.
                    known_sources = {
                        "ideation/research_blueprint.yaml": ("research_blueprint", "yaml"),
                        "ideation/claim_registry.yaml": ("claim_registry", "yaml"),
                        "ideation/exp_plan.yaml": ("exp_plan", "yaml"),
                        "ideation/orientation_review.json": ("orientation_review", "json"),
                    }
                    required_path = str(attempted_path or "").strip()
                    required_schema = str(attempted_schema or "").strip()
                    required_format = ""
                    expected = known_sources.get(required_path)
                    if expected is not None:
                        required_schema, required_format = expected
                    else:
                        required_path = "ideation/research_blueprint.yaml"
                        required_schema, required_format = known_sources[required_path]
                    parameter_data.update(
                        {
                            "required_path": required_path,
                            "required_schema": required_schema,
                            "required_format": required_format,
                        }
                    )
                    parameter_content += (
                        "T4.5 的结构化来源必须按顺序建立或修复；"
                        f"当前只重发 {required_path}（{required_schema}/{required_format}）。"
                        "不要改用 write_file，也不要因此重写其它已通过来源。"
                    )
                    if raw_arguments and tool_arguments.get("__parse_error__"):
                        parameter_content += (
                            " "
                            + parse_detail
                            + "Do not try to repair this artifact in prose or by a text patch. "
                            "Do not copy the malformed raw arguments. Regenerate one complete, compact "
                            "model-authored write_structured_file call for the same source, keeping required fields "
                            "and concise descriptions, with every closing JSON delimiter and no explanatory text "
                            "inside the tool arguments."
                        )
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=parameter_content,
                is_error=True,
                step=step,
                metadata={"data": parameter_data, "error": "parameter_validation"},
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    tc.arguments,
                    ok=False,
                    content=tool_msg.content,
                    data=parameter_data,
                    error="parameter_validation",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        # T3.5 receives a compact synthesis index precisely to avoid replaying
        # the full workbench and bridge catalogs on every turn.  Some models
        # nevertheless issue the same read_file call repeatedly after already
        # receiving the complete result.  Return a small, truthful reuse note
        # for those calls.  This preserves all information on disk, keeps the
        # first read lossless, and permits a fresh read after runtime history
        # truncation (the guard is cleared above).
        if tc.name == "read_file" and ctx.task_id == "T3.5":
            read_path = str(tool_arguments.get("path") or "").replace("\\", "/").strip()
            try:
                read_offset = max(0, int(tool_arguments.get("offset") or 0))
            except (TypeError, ValueError):
                read_offset = 0
            requested_page = tool_arguments.get("max_chars")
            page_key = str(requested_page).strip() if requested_page is not None else "default"
            read_key = f"{read_path}#offset={read_offset}&max_chars={page_key}"
            large_index_paths = {
                "literature/synthesis_context.json",
                "literature/synthesis_workbench.json",
                "literature/cross_domain_catalogs/index.json",
            }
            is_bridge_catalog = read_path.startswith("literature/cross_domain_catalogs/") and read_path.endswith(
                ("/paper_catalog.json", "/bridge_context.json")
            )
            if read_path in large_index_paths or is_bridge_catalog:
                seen = ctx.extra.setdefault("_t35_large_read_seen", {})
                if isinstance(seen, dict) and read_key in seen:
                    first_step = seen.get(read_key)
                    reuse_data = {
                        "path": read_path,
                        "offset": read_offset,
                        "max_chars": requested_page,
                        "deduplicated": True,
                        "first_read_step": first_step,
                        "reason": "same_run_page_already_returned; use the existing tool result",
                    }
                    reuse_content = (
                        f"已在本次 T3.5 运行第 {first_step} 步返回 `{read_path}` 的 offset={read_offset} 页面。"
                        "不要重复读取同一页面；请直接使用已有内容。若需要后续内容，请使用更大的 offset 分页。"
                        "若运行时提示较早上下文已被省略，再重新调用该页面以恢复完整内容。"
                    )
                    tool_msg = Message.tool(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=reuse_content,
                        is_error=False,
                        step=step,
                        duration_ms=int((time.time() - started) * 1000),
                        metadata={"data": reuse_data, "error": None},
                    )
                    if run_logger is not None:
                        run_logger.tool_result(
                            tc.name,
                            model_dump(parsed),
                            ok=True,
                            content=reuse_content,
                            data=reuse_data,
                            error=None,
                            duration_ms=tool_msg.duration_ms,
                            metadata=tool_msg.metadata,
                            step=step,
                        )
                    return tool_msg

        failure_cache_key = self._tool_failure_cache_key(tc.name, model_dump(parsed))
        if failure_cache_key and tool_failure_cache is not None and failure_cache_key in tool_failure_cache:
            cached = tool_failure_cache[failure_cache_key]
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=(
                    "Skipped tool call because the same request already failed in this run.\n\n"
                    + (cached.content or "")
                ),
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
                metadata={
                    "data": {
                        "cached_failure": True,
                        "cache_key": failure_cache_key[1],
                        "original_step": cached.step,
                    },
                    "error": "cached_failure",
                },
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data=tool_msg.metadata.get("data") or {},
                    error="cached_failure",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        try:
            max_tool_timeout = self._timeout_for_tool(tc.name, tool)
            tool_timeout = min(tool.timeout_seconds, max_tool_timeout)
            # 工具自身可有细粒度超时，但 runtime 仍统一包一层 wait_for。
            tool_execute_started = time.time()
            try:
                result: ToolResult = await asyncio.wait_for(
                    tool.execute(**model_dump(parsed)),
                    timeout=tool_timeout,
                )
            finally:
                if budget is not None and tc.name == "ask_human":
                    budget.exclude_wall_time(time.time() - tool_execute_started)
        except asyncio.TimeoutError:
            timeout_data, timeout_content, timeout_error = self._resumable_tool_timeout_details(
                ctx=ctx,
                tool_name=tc.name,
                arguments=model_dump(parsed),
                timeout_seconds=tool_timeout,
            )
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=timeout_content,
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
                metadata={"data": timeout_data, "error": timeout_error},
            )
            self._remember_tool_failure(failure_cache_key, tool_msg, tool_failure_cache)
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data=timeout_data,
                    error=timeout_error,
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg
        except ToolAccessDenied as exc:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Access denied: {exc}",
                is_error=True,
                step=step,
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="access_denied",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg
        except ToolError as exc:
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool error: {exc}",
                is_error=True,
                step=step,
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="tool_error",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg
        except Exception as exc:
            self.log.exception("tool_crashed", tool=tc.name)
            tool_msg = Message.tool(
                tool_call_id=tc.id,
                name=tc.name,
                content=f"Tool crashed unexpectedly: {exc!r}",
                is_error=True,
                step=step,
                duration_ms=int((time.time() - started) * 1000),
            )
            if run_logger is not None:
                run_logger.tool_result(
                    tc.name,
                    model_dump(parsed),
                    ok=False,
                    content=tool_msg.content,
                    data={},
                    error="tool_crashed",
                    duration_ms=tool_msg.duration_ms,
                    metadata=tool_msg.metadata,
                    step=step,
                )
            return tool_msg

        # Record only a successful full read.  A transient filesystem/tool
        # error must remain retryable rather than poisoning the reuse guard.
        if tc.name == "read_file" and ctx.task_id == "T3.5":
            read_path = str(tool_arguments.get("path") or "").replace("\\", "/").strip()
            if (
                read_path in {
                    "literature/synthesis_context.json",
                    "literature/synthesis_workbench.json",
                    "literature/cross_domain_catalogs/index.json",
                }
                or (
                    read_path.startswith("literature/cross_domain_catalogs/")
                    and read_path.endswith(("/paper_catalog.json", "/bridge_context.json"))
                )
            ) and result.ok:
                seen = ctx.extra.setdefault("_t35_large_read_seen", {})
                if isinstance(seen, dict):
                    try:
                        read_offset = max(0, int(tool_arguments.get("offset") or 0))
                    except (TypeError, ValueError):
                        read_offset = 0
                    requested_page = tool_arguments.get("max_chars")
                    page_key = str(requested_page).strip() if requested_page is not None else "default"
                    read_key = f"{read_path}#offset={read_offset}&max_chars={page_key}"
                    seen[read_key] = step

        auto_persist_metadata = await self._maybe_auto_persist_t2_search_result(
            ctx=ctx,
            policy=policy,
            tool_name=tc.name,
            tool_arguments=model_dump(parsed),
            result=result,
        )
        self._observe_skill_remote_rate_limit(
            tool_name=tc.name,
            result=result,
            state=skill_tool_budget,
        )
        self._record_t2_search_ledger(
            ctx=ctx,
            tool_name=tc.name,
            tool_arguments=model_dump(parsed),
            result=result,
            auto_persist_metadata=auto_persist_metadata,
        )
        try:
            task_io = get_task_io(ctx.task_id)
        except KeyError:
            task_io = None
        self._annotate_optional_input_absence(
            ctx=ctx,
            task_io=task_io,
            tool_name=tc.name,
            arguments=model_dump(parsed),
            result=result,
        )
        if ctx.task_id == "T2" and tc.name in T2_AUTO_PERSIST_SEARCH_TOOLS and not result.ok:
            t2_config = load_t2_finalize_config(ctx.workspace_dir)
            self._log_t2_search_progress(
                ctx,
                t2_config,
                tool_name=tc.name,
                tool_arguments=model_dump(parsed),
                result=result,
                paper_count=0,
                persisted_delta=0,
                merged_count=0,
                raw_count_after=self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl"),
                append_status=str(result.error or "failed"),
            )
        if argument_auto_repair:
            result.data = {
                **(result.data if isinstance(result.data, dict) else {}),
                "argument_auto_repair": argument_auto_repair,
                # The normal CLI deliberately suppresses successful file
                # reads.  Keep this marker for --verbose/trace rather than
                # presenting a red transient error to a researcher.
                "display_disposition": "auto_repair",
            }

        content = result.content
        metadata = {"data": result.data, "error": result.error}
        content, cap_metadata = self._cap_tool_content_for_context(
            tc.name,
            content,
            task_id=ctx.task_id,
            tool_data=result.data if isinstance(result.data, dict) else None,
            tool_arguments=model_dump(parsed),
        )
        if cap_metadata:
            metadata["context_cap"] = cap_metadata
        if auto_persist_metadata:
            metadata["auto_persist_raw"] = auto_persist_metadata
            suffix = auto_persist_metadata.get("content_suffix")
            if suffix:
                content = f"{content}\n\n{suffix}" if content else suffix

        tool_msg = Message.tool(
            tool_call_id=tc.id,
            name=tc.name,
            content=content,
            is_error=not result.ok,
            step=step,
            duration_ms=int((time.time() - started) * 1000),
            metadata=metadata,
        )
        if not result.ok:
            self._remember_tool_failure(failure_cache_key, tool_msg, tool_failure_cache)
        self._record_tool_side_effect_metadata(ctx, tc.name, model_dump(parsed), result)
        self._emit_tool_progress(tc.name, result)
        if run_logger is not None:
            run_logger.tool_result(
                tc.name,
                model_dump(parsed),
                ok=result.ok,
                content=content,
                data=result.data,
                error=result.error,
                duration_ms=tool_msg.duration_ms,
                metadata=metadata,
                step=step,
            )
        return tool_msg

    @staticmethod
    def _annotate_optional_input_absence(
        *,
        ctx: ExecutionContext,
        task_io: dict[str, object] | None,
        tool_name: str,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> None:
        """Mark only declared optional reads as a non-blocking public skip."""

        if result.ok or tool_name != "read_file" or str(result.error or "") not in {"not_found", "file_not_found"}:
            return
        if not isinstance(task_io, dict):
            return
        requested = str(arguments.get("path") or "").strip().lstrip("./")
        inputs = task_io.get("inputs")
        if not requested or not isinstance(inputs, dict):
            return
        required = {str(key) for key in task_io.get("required_inputs") or []}
        for key, declared in inputs.items():
            if str(declared).lstrip("./") != requested:
                continue
            if str(key) in required:
                return
            result.data = {
                **(result.data if isinstance(result.data, dict) else {}),
                "optional_input": True,
                "optional_input_label": str(key),
                "path": requested,
                "display_disposition": "skipped",
            }
            return

    def _emit_tool_progress(self, tool_name: str, result: ToolResult) -> None:
        """Print deterministic progress summaries that users need during long runs."""

        if self.runtime_settings.ui.quiet:
            return
        data = result.data if isinstance(result.data, dict) else {}
        progress = str(data.get("progress") or "").strip()
        if tool_name == "save_paper_note" and progress:
            # The complete note summary (mechanism, implication, and resource
            # receipt) belongs in the Paper Note, catalog, and trace.  Showing
            # it after every paper produces an unreadable terminal and exposes
            # a transient "needs repair" state that is automatically handled
            # in the same Reader turn.  Keep compact milestones in normal
            # mode; detailed/verbose runs retain the full summary.
            if self.runtime_settings.ui.verbose:
                self.progress.emit(f"[Reader Agent] {summarize_reader_note_progress(data, progress=progress)}")
                return
            match = re.search(r"(\d+)\s*/\s*(\d+)", progress)
            if match is None:
                return
            completed, target = (int(match.group(1)), int(match.group(2)))
            if completed <= 0 or (completed != 1 and completed != target and completed % 5 != 0):
                return
            self.progress.emit(
                f"[Reader Agent] T3 阅读进度：{completed}/{target} 篇；"
                "笔记与资源线索已归档，继续处理。"
            )

    @staticmethod
    def _looks_like_human_interaction_request(message: Message) -> bool:
        """Detect text-only assistant turns that are actually waiting on a user.

        This is a runtime safety net. Prompts should still require explicit
        ask_human/gate usage, but if a model prints a question or choice menu
        without a tool call, continuing to the next LLM turn would silently
        skip the user interaction.
        """

        content = (message.content or "").strip()
        if not content:
            return False
        normalized = content.lower()

        # Plain status narration such as "我来检查已有材料" must not open an
        # input box. This safety net only catches explicit user-facing
        # requests to choose, confirm, answer, or provide missing information.
        strong_markers = (
            "请选择",
            "请输入",
            "请回答",
            "请确认",
            "请你确认",
            "请补充",
            "请提供",
            "请明确",
            "等待用户",
            "需要用户",
            "需要你回答",
            "需要你确认",
            "需要你选择",
            "请告诉我",
            "告诉我你的",
            "please choose",
            "please answer",
            "please confirm",
            "please provide",
            "provide your",
            "tell me your",
            "do you want me to",
            "waiting for user",
        )
        if any(marker in normalized for marker in strong_markers):
            return True

        question_lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip().endswith(("?", "？"))
        ]
        if question_lines:
            explicit_question_prefixes = (
                "请",
                "你是否",
                "是否",
                "要不要",
                "能否",
                "可否",
                "do you",
                "would you",
                "which",
                "what would you like",
            )
            for line in question_lines:
                lowered = line.lower()
                if lowered.startswith(explicit_question_prefixes):
                    return True

        return bool(
            re.search(r"(?m)^\s*(?:\[\d+\]|\d+[.)、])\s+.+", content)
            # A numbered completion summary often says that a check has been
            # "confirmed".  That is not an invitation for the researcher to
            # decide anything.  Require an actual selection/request phrase
            # here; explicit "请确认"/"请选择" were already caught above.
            and re.search(
                r"(?i)(请选择|please choose|需要(?:你)?选择|请作出选择|option\s*[:：]|继续还是|停止还是)",
                content,
            )
        )

    @staticmethod
    def _build_autobridged_human_question(content: str) -> str:
        """Explain why runtime is asking before forwarding model text."""

        return (
            "Runtime 检测到 Agent 正在请求人工选择/确认，但这一轮没有显式调用 ask_human。"
            "为避免跳过你的决策，ResearchOS 已暂停在这里。\n\n"
            "请根据下面 Agent 原始请求作答；如果这是误触发，可以回答“继续”，runtime 会把回答记录为人工输入。\n\n"
            f"--- Agent 原始请求 ---\n{content}"
        )

    @staticmethod
    def _ensure_ask_human_questions_are_self_contained(message: Message) -> None:
        """Make ask_human questions visible even when the model relies on prior text.

        Models often print a long draft/choice list in assistant content, then call
        ask_human with a short question like "请确认以上草案". In normal CLI mode
        assistant content is not always shown, so the user would see an input box
        without the actual draft. This keeps the human gate self-contained.
        """

        content = (message.content or "").strip()
        if not content:
            return
        for tool_call in message.tool_calls:
            if tool_call.name != "ask_human":
                continue
            raw_question = str(tool_call.arguments.get("question") or "").strip()
            if not raw_question:
                tool_call.arguments["question"] = content
                continue
            if AgentRunner._ask_human_question_depends_on_hidden_context(raw_question):
                tool_call.arguments["question"] = (
                    "下面是 Agent 本轮生成的完整上下文，请先阅读，再回答后面的人工输入问题。\n\n"
                    f"{content}\n\n"
                    "----- 需要你回答的问题 -----\n"
                    f"{raw_question}"
                )

    @staticmethod
    def _ask_human_question_depends_on_hidden_context(question: str) -> bool:
        normalized = re.sub(r"\s+", "", question.strip().lower())
        if not normalized:
            return True
        context_dependent_markers = (
            "以上",
            "上述",
            "上面",
            "前面",
            "如上",
            "以上草案",
            "上述草案",
            "以上project",
            "以上`project.yaml`",
            "以上5个",
            "以上五个",
            "这些方向",
            "这些候选",
            "请确认以上",
            "请确认上述",
            "请确认草案",
            "请确认以上`project.yaml`草案",
            "above",
            "aforementioned",
            "theabove",
            "confirmtheabove",
            "confirmthedraftabove",
        )
        return any(marker in normalized for marker in context_dependent_markers)

    @staticmethod
    def _record_tool_side_effect_metadata(
        ctx: ExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> None:
        """记录 validator 需要的运行期证据，例如 Docker 使用和代码重写次数。"""

        if tool_name == "docker_exec":
            ctx.extra["docker_exec_call_count"] = int(ctx.extra.get("docker_exec_call_count", 0) or 0) + 1
            if result.ok:
                ctx.extra["docker_exec_success_count"] = int(ctx.extra.get("docker_exec_success_count", 0) or 0) + 1
            return

        if tool_name == "latex_compile":
            ctx.extra["latex_compile_call_count"] = int(ctx.extra.get("latex_compile_call_count", 0) or 0) + 1
            if result.ok:
                ctx.extra["latex_compile_success_count"] = int(ctx.extra.get("latex_compile_success_count", 0) or 0) + 1
            return

        if tool_name == "bash_run":
            try:
                from ..skills.project_specialization.task_adapter import mark_project_skill_specialization_bash_call

                mark_project_skill_specialization_bash_call(
                    ctx,
                    command=str(arguments.get("command") or ""),
                    cwd=str(arguments.get("cwd") or "") or None,
                    ok=result.ok,
                )
            except Exception:
                pass
            return

        if tool_name not in {"write_file", "write_structured_file"} or not result.ok:
            return

        raw_path = arguments.get("path")
        if not isinstance(raw_path, str):
            return
        normalized_path = raw_path.strip().lstrip("./")
        counts = ctx.extra.setdefault("artifact_write_counts", {})
        if isinstance(counts, dict):
            counts[normalized_path] = int(counts.get(normalized_path, 0) or 0) + 1
        if ctx.task_id == "T5" and normalized_path == "pilot/pilot_code/run_pilot.py":
            ctx.extra["pilot_code_write_count"] = int(ctx.extra.get("pilot_code_write_count", 0) or 0) + 1

    @staticmethod
    def _is_recoverable_tool_pause(tool_name: str, tool_msg: Message) -> bool:
        """Return true for tool failures that should pause instead of burning retries."""

        if not tool_msg.metadata.get("is_error"):
            return False
        error = tool_msg.metadata.get("error")
        data = tool_msg.metadata.get("data")
        if isinstance(data, dict) and not error:
            error = data.get("error")
        # The survey supplement persists each completed query. A timeout can
        # therefore represent useful work, and must become one durable
        # checkpointed pause instead of another LLM tool-call retry.
        if tool_name == "expand_corpus_for_survey":
            return error == "timeout_resumable"
        if tool_name not in {"ask_human", "docker_exec", "latex_compile"}:
            return False
        if error == "human_input_unavailable":
            return True
        content = tool_msg.content or ""
        if isinstance(error, str) and error.startswith("waiting_environment"):
            return True
        return "WAITING_ENVIRONMENT" in content

    @staticmethod
    def _tool_failure_cache_key(tool_name: str, arguments: dict[str, object]) -> tuple[str, str] | None:
        if tool_name == "write_file":
            normalized_path = str(arguments.get("path") or "").strip().lstrip("./")
            schema_name = STRUCTURED_ONLY_WRITE_PATHS.get(normalized_path)
            if schema_name:
                # Cache by the canonical structured artifact, rather than the
                # literal filename.  A model must not evade the required
                # ``exp_plan.yaml`` contract by retrying the same payload as
                # ``exp_plan.yml`` after the tool has already supplied the
                # exact write_structured_file replacement call.
                return (tool_name, f"structured_output:{schema_name}")
        if tool_name not in TOOL_FAILURE_CACHE_NAMES:
            return None
        if tool_name == "fetch_paper_pdf":
            paper_id = str(arguments.get("paper_id") or "").strip().casefold()
            save_path = str(arguments.get("save_path") or "").strip().casefold()
            if paper_id:
                return (tool_name, f"paper_id:{paper_id}")
            if save_path:
                return (tool_name, f"save_path:{save_path}")
        if tool_name == "expand_corpus_for_survey":
            # A timed-out supplement retrieval persists after each completed
            # query. Repeating the same request in the same model turn cannot
            # make useful progress and obscures the resume instruction.
            return (tool_name, json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str))
        return None

    @staticmethod
    def _resumable_tool_timeout_details(
        *,
        ctx: ExecutionContext,
        tool_name: str,
        arguments: dict[str, object],
        timeout_seconds: float,
    ) -> tuple[dict[str, object], str, str]:
        """Expose a durable checkpoint when a resumable tool exceeds its cap."""

        default_content = f"Tool timed out after {timeout_seconds:g}s"
        if tool_name != "expand_corpus_for_survey":
            return {}, default_content, "timeout"
        checkpoint_rel = str(
            arguments.get("checkpoint_path") or "literature/survey_supplement/expansion_checkpoint.json"
        ).strip().lstrip("./")
        if not checkpoint_rel:
            return {}, default_content, "timeout"
        try:
            checkpoint_path = (ctx.workspace_dir / checkpoint_rel).resolve()
            workspace = ctx.workspace_dir.resolve()
            checkpoint_path.relative_to(workspace)
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return (
                {"resumable": True, "checkpoint_path": checkpoint_rel, "checkpoint_available": False},
                default_content
                + ". Targeted survey retrieval is resumable; rerun the same action after checking its workspace checkpoint.",
                "timeout_resumable",
            )
        if not isinstance(payload, dict):
            payload = {}
        completed = payload.get("completed_query_count")
        total = payload.get("query_count")
        phase = str(payload.get("phase") or "unknown")
        status = str(payload.get("status") or "interrupted")
        data: dict[str, object] = {
            "resumable": True,
            "checkpoint_path": checkpoint_rel,
            "checkpoint_available": True,
            "status": status,
            "phase": phase,
            "completed_query_count": completed,
            "query_count": total,
            "retrieved_record_count": payload.get("retrieved_record_count"),
        }
        progress = f"{completed}/{total}" if completed is not None and total is not None else "an unknown number of"
        content = (
            f"Targeted survey retrieval reached its {timeout_seconds:g}s operation budget after completing {progress} queries "
            f"(phase={phase}). Its checkpoint is saved at {checkpoint_rel}; resume will reuse completed queries and continue from the incomplete one."
        )
        return data, content, "timeout_resumable"

    def _timeout_for_tool(self, tool_name: str, tool: Tool) -> float:
        """Return the runtime timeout cap for a tool.

        Long-running experiment and LaTeX tools need their dedicated timeout
        budget; otherwise the global small-tool cap kills valid external-execution or submission work.
        """

        if tool_name == "docker_exec":
            return float(
                self.global_timeout.get("docker_operation")
                or self.global_timeout.get("max_tool_call")
                or tool.timeout_seconds
            )
        if tool_name == "latex_compile":
            return float(
                self.global_timeout.get("latex_compile")
                or self.global_timeout.get("max_compile")
                or self.global_timeout.get("docker_operation")
                or self.global_timeout.get("max_tool_call")
                or tool.timeout_seconds
            )
        if tool_name == "expand_corpus_for_survey":
            # A survey supplement is a resumable multi-query retrieval plus
            # PDF/note materialization workflow.  It must not inherit a
            # generic small-tool cap such as 60 seconds, which is shorter
            # than one child multi-source search may legitimately require.
            return float(
                self.global_timeout.get("survey_supplement_retrieval")
                or tool.timeout_seconds
            )
        return float(self.global_timeout.get("max_tool_call") or tool.timeout_seconds)

    @staticmethod
    def _remember_tool_failure(
        key: tuple[str, str] | None,
        message: Message,
        cache: dict[tuple[str, str], Message] | None,
    ) -> None:
        if key is not None and cache is not None:
            cache[key] = message

    def _cap_tool_content_for_context(
        self,
        tool_name: str,
        content: str,
        *,
        task_id: str | None = None,
        tool_data: dict[str, object] | None = None,
        tool_arguments: dict[str, object] | None = None,
    ) -> tuple[str, dict[str, object] | None]:
        """Bound one tool result before it enters the next LLM turn.

        Artifact files are the source of truth.  A tool reply is only a
        reasoning view, so a large successful read must become a navigable
        preview rather than silently consuming the context needed to interpret
        it.  The receipt is intentionally actionable: it names the durable
        path and an exact/near-exact continuation page when available.
        """

        limit = TOOL_CONTEXT_CONTENT_LIMITS.get(tool_name)
        cap_reason = "tool_context_content_limit"
        if tool_name == "read_file":
            read_limit = TASK_READ_FILE_CONTEXT_CHAR_CAPS.get(
                str(task_id or ""), DEFAULT_READ_FILE_CONTEXT_CHAR_CAP
            )
            if limit is None:
                limit = read_limit
            else:
                limit = min(limit, read_limit)
            cap_reason = "task_read_file_context_budget"
        if limit is None or len(content) <= limit:
            return content, None

        capped = content[:limit]
        if tool_name == "extract_pdf_text":
            capped = AgentRunner._rewrite_pdf_metadata_after_runtime_cap(
                capped,
                original_chars=len(content),
                limit=limit,
            )
        receipt: dict[str, object] = {
            "original_chars": len(content),
            "shown_chars": limit,
            "reason": cap_reason,
            "task_id": task_id,
        }
        if tool_name == "read_file":
            data = tool_data or {}
            arguments = tool_arguments or {}
            path = str(data.get("path") or arguments.get("path") or "").strip()
            try:
                offset = int(data.get("offset", arguments.get("offset", 0)) or 0)
            except (TypeError, ValueError):
                offset = 0
            already_paged = bool(data.get("truncated"))
            if already_paged:
                next_offset = data.get("next_offset")
            else:
                next_offset = offset + limit
            continuation = {
                "path": path,
                "offset": next_offset,
                "max_chars": min(32_000, max(8_000, limit // 2)),
            }
            receipt["continuation"] = continuation
            continuation_text = (
                f" Full content remains at `{path or '<unknown path>'}`. "
                "Use grep_search for a concept first; if a sequential read is necessary, call "
                f"read_file(path=\"{path}\", offset={next_offset}, max_chars={continuation['max_chars']})."
                if path and next_offset is not None
                else " Full content remains in the workspace; use grep_search or a smaller read_file page."
            )
        else:
            continuation_text = " Use narrower parameters if more detail is needed."
        capped += (
            f"\n\n[Runtime] Tool output truncated before LLM context: "
            f"{limit}/{len(content)} chars shown.{continuation_text}"
        )
        return capped, receipt

    @staticmethod
    def _rewrite_pdf_metadata_after_runtime_cap(
        content: str,
        *,
        original_chars: int,
        limit: int,
    ) -> str:
        """Prevent capped PDF previews from still advertising complete reads."""

        if "[PDF extraction metadata]" not in content:
            return content
        replacements = {
            r"(?m)^- preview_truncated_by_max_chars: false$": "- preview_truncated_by_max_chars: true",
            r"(?m)^- complete_pdf_read: true$": "- complete_pdf_read: false",
            r"(?m)^- covers_full_pdf: true$": "- covers_full_pdf: false",
            r"(?m)^- next_start_page: none$": "- next_start_page: unknown_due_to_runtime_truncation",
        }
        rewritten = content
        for pattern, replacement in replacements.items():
            rewritten = re.sub(pattern, replacement, rewritten)
        rewritten = re.sub(
            r"(?m)^- note: .*$",
            (
                "- note: Runtime truncated this PDF preview before the LLM saw the full tool output; "
                "do not mark the note FULL-TEXT from this call. Re-read narrower page ranges until "
                "every chunk is visible and final Reading Coverage says truncation is resolved."
            ),
            rewritten,
        )
        return (
            rewritten
            + f"\n- runtime_context_truncated: true ({limit}/{original_chars} chars shown)"
        )

    async def _maybe_auto_persist_t2_search_result(
        self,
        *,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
    ) -> dict[str, object] | None:
        """T2 中的检索结果自动落盘到 papers_raw.jsonl。"""
        if ctx.task_id != "T2" or tool_name not in T2_AUTO_PERSIST_SEARCH_TOOLS or not result.ok:
            return None

        t2_config = load_t2_finalize_config(ctx.workspace_dir)
        papers = result.data.get("papers")
        edge_persist = self._persist_t2_citation_edges_if_present(
            ctx=ctx,
            policy=policy,
            tool_name=tool_name,
            result=result,
        )
        if not isinstance(papers, list) or not papers:
            self._log_t2_search_progress(
                ctx,
                t2_config,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                result=result,
                paper_count=0,
                persisted_delta=0,
                merged_count=0,
                raw_count_after=self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl"),
                append_status="no_papers" if result.ok else str(result.error or "failed"),
            )
            return edge_persist

        papers = self._annotate_t2_search_bucket(
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            result=result,
            papers=papers,
        )

        save_tool = SavePapersRawTool(policy)
        save_result = await save_tool.execute(papers=papers, append=True)
        if not save_result.ok:
            raw_count_after = self._count_jsonl_records(
                ctx.workspace_dir / "literature" / "papers_raw.jsonl"
            )
            self._log_t2_search_progress(
                ctx,
                t2_config,
                tool_name=tool_name,
                tool_arguments=tool_arguments,
                result=result,
                paper_count=len(papers),
                persisted_delta=0,
                merged_count=0,
                raw_count_after=raw_count_after,
                append_status="raw_append_failed",
            )
            return {
                "ok": False,
                "error": save_result.error,
                "raw_count_after": raw_count_after,
                "content_suffix": f"[Runtime] 自动保存 papers_raw 失败: {save_result.content}",
            }

        raw_delta = int(save_result.data.get("count", 0) or 0)
        merged_count = int(save_result.data.get("merged_count", 0) or 0)
        retained_count = raw_delta + merged_count
        raw_count_after = self._count_jsonl_records(ctx.workspace_dir / "literature" / "papers_raw.jsonl")
        content_suffix = (
            f"[Runtime] 已自动保留 {retained_count} 篇到 literature/papers_raw.jsonl"
            f"（新增 {raw_delta}，合并重复 {merged_count}）"
        )
        if edge_persist and edge_persist.get("content_suffix"):
            content_suffix += "\n" + str(edge_persist["content_suffix"])
        self._log_t2_search_progress(
            ctx,
            t2_config,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            result=result,
            paper_count=len(papers),
            persisted_delta=raw_delta,
            merged_count=merged_count,
            raw_count_after=raw_count_after,
            append_status="ok",
        )
        return {
            "ok": True,
            "count": raw_delta,
            "merged_count": merged_count,
            "retained_count": retained_count,
            "raw_count_after": raw_count_after,
            "mode": save_result.data.get("mode", "append"),
            "content_suffix": content_suffix,
        }

    @staticmethod
    def _log_t2_search_progress(
        ctx: ExecutionContext,
        t2_config: object,
        *,
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
        paper_count: int,
        persisted_delta: int,
        merged_count: int,
        raw_count_after: int | None,
        append_status: str,
    ) -> None:
        if not getattr(t2_config, "progress_enabled", True) or not getattr(
            t2_config,
            "progress_update_on_tool_results",
            True,
        ):
            return
        query = str(
            result.data.get("query")
            or tool_arguments.get("query")
            or tool_arguments.get("search_query")
            or ""
        ).strip()
        if not query:
            query = "[query unavailable]"
        try:
            progress_rel = str(getattr(t2_config, "progress_file", "") or "literature/temp/scout_progress.md")
            ScoutProgressLogger(
                ctx.workspace_dir,
                progress_rel,
            ).log_runtime_event(
                "search_result",
                query=query,
                source=tool_name,
                bucket=tool_arguments.get("query_bucket")
                or tool_arguments.get("search_bucket")
                or result.data.get("query_bucket"),
                bridge=tool_arguments.get("bridge_id") or result.data.get("bridge_id"),
                reported_paper_count=paper_count,
                persisted_raw_delta=persisted_delta,
                merged_raw_count=merged_count,
                raw_count_after=raw_count_after,
                append_status=append_status,
            )
            self.progress.progress_file_update(
                label="Scout/T2 检索进度",
                path=progress_rel,
                bullets=summarize_progress_markdown(ctx.workspace_dir / progress_rel, max_items=4),
            )
        except Exception:
            return

    @staticmethod
    def _normalized_t2_query(value: object) -> str:
        return " ".join(str(value or "").casefold().split())

    def _record_t2_search_ledger(
        self,
        *,
        ctx: ExecutionContext,
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
        auto_persist_metadata: dict[str, object] | None,
    ) -> None:
        """Keep compact, factual T2 search state across history truncation.

        The ledger is not a relevance judgment and never replaces
        ``papers_raw.jsonl``. It only records already completed retrieval
        operations so Scout does not restart its query plan after reading a
        large raw page.
        """

        if ctx.task_id != "T2" or tool_name not in T2_AUTO_PERSIST_SEARCH_TOOLS:
            return
        query = str(
            result.data.get("query")
            or tool_arguments.get("query")
            or tool_arguments.get("search_query")
            or ""
        ).strip()
        if not query:
            return
        ledger = ctx.extra.setdefault("t2_search_ledger", [])
        if not isinstance(ledger, list):
            ledger = []
            ctx.extra["t2_search_ledger"] = ledger
        key = (tool_name, self._normalized_t2_query(query))
        for item in ledger:
            if not isinstance(item, dict):
                continue
            if (str(item.get("tool") or ""), str(item.get("query_key") or "")) == key:
                return
        papers = result.data.get("papers")
        ledger.append(
            {
                "tool": tool_name,
                "query": query,
                "query_key": key[1],
                "bucket": str(
                    tool_arguments.get("query_bucket")
                    or tool_arguments.get("search_bucket")
                    or result.data.get("query_bucket")
                    or ""
                ).strip(),
                "bridge_id": str(tool_arguments.get("bridge_id") or result.data.get("bridge_id") or "").strip(),
                "returned": len(papers) if isinstance(papers, list) else 0,
                "persisted": int((auto_persist_metadata or {}).get("retained_count") or 0),
                "ok": bool(result.ok),
            }
        )

    @staticmethod
    def _is_t2_raw_pool_read(tool_call: ToolCall, tool_data: dict[str, object]) -> bool:
        if tool_call.name != "read_file":
            return False
        path = str(tool_data.get("path") or tool_call.arguments.get("path") or "").replace("\\", "/")
        return path.lstrip("./") == "literature/papers_raw.jsonl"

    def _hydrate_t2_search_ledger_from_raw(self, ctx: ExecutionContext) -> None:
        """Restore compact retrieval facts after a T2 resume.

        The raw JSONL is the durable source of retrieval provenance. This reads
        only structured provenance fields and never scores relevance, filters
        papers, or creates scholarly content. It lets a resumed Scout retain
        the completed query/source coverage after prior chat history has gone.
        """

        if ctx.task_id != "T2" or ctx.extra.get("t2_search_ledger_hydrated"):
            return
        ctx.extra["t2_search_ledger_hydrated"] = True
        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if not raw_path.exists():
            return
        ledger = ctx.extra.setdefault("t2_search_ledger", [])
        if not isinstance(ledger, list):
            ledger = []
            ctx.extra["t2_search_ledger"] = ledger
        known = {
            (str(item.get("tool") or ""), str(item.get("query_key") or ""))
            for item in ledger
            if isinstance(item, dict)
        }
        try:
            with raw_path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
                    queries = [record.get("source_query"), provenance.get("source_query")]
                    if isinstance(record.get("source_queries"), list):
                        queries.extend(record["source_queries"])
                    tools = [record.get("source_tool"), provenance.get("source_tool")]
                    if isinstance(record.get("source_tools"), list):
                        tools.extend(record["source_tools"])
                    normalized_queries = [
                        str(query).strip()
                        for query in queries
                        if self._normalized_t2_query(query)
                    ]
                    normalized_tools = [str(tool).strip() for tool in tools if str(tool or "").strip()]
                    if not normalized_queries or not normalized_tools:
                        continue
                    bucket = str(
                        record.get("query_bucket")
                        or record.get("search_bucket")
                        or provenance.get("query_bucket")
                        or provenance.get("search_bucket")
                        or ""
                    ).strip()
                    bridge_id = str(record.get("bridge_id") or provenance.get("bridge_id") or "").strip()
                    for tool in dict.fromkeys(normalized_tools):
                        for query in dict.fromkeys(normalized_queries):
                            key = (tool, self._normalized_t2_query(query))
                            if key in known:
                                continue
                            known.add(key)
                            ledger.append(
                                {
                                    "tool": tool,
                                    "query": query,
                                    "query_key": key[1],
                                    "bucket": bucket,
                                    "bridge_id": bridge_id,
                                    "returned": 0,
                                    "persisted": 0,
                                    "ok": True,
                                    "recovered_from_raw": True,
                                }
                            )
        except OSError:
            return

    def _t2_raw_pool_checkpoint_message(
        self,
        *,
        ctx: ExecutionContext,
        tool_data: dict[str, object],
        step: int,
    ) -> Message | None:
        """Return a durable, compact resume instruction after a raw-pool page."""

        raw_path = ctx.workspace_dir / "literature" / "papers_raw.jsonl"
        if not raw_path.exists():
            return None
        self._hydrate_t2_search_ledger_from_raw(ctx)
        offset = int(tool_data.get("offset") or 0)
        size = int(tool_data.get("size") or raw_path.stat().st_size)
        next_offset = min(
            size,
            int(tool_data.get("next_offset") or offset + int(tool_data.get("max_chars") or 0)),
        )
        truncated = bool(tool_data.get("truncated"))
        raw_count = self._count_jsonl_records(raw_path)
        ledger = ctx.extra.get("t2_search_ledger")
        entries = [entry for entry in ledger if isinstance(entry, dict)] if isinstance(ledger, list) else []
        unique_queries = {
            str(entry.get("query_key") or "")
            for entry in entries
            if str(entry.get("query_key") or "")
        }
        sources = sorted({str(entry.get("tool") or "") for entry in entries if entry.get("tool")})
        buckets = sorted({str(entry.get("bucket") or "") for entry in entries if entry.get("bucket")})
        ctx.extra["t2_last_raw_page"] = {
            "offset": offset,
            "next_offset": next_offset,
            "size": size,
            "raw_count": raw_count,
        }
        page_note = (
            f"本页为 {offset}:{next_offset}/{size}；下一页 offset={next_offset}。"
            if truncated
            else "当前 raw 文件已完整展示。"
        )
        return Message.user(
            "[Runtime T2 检索检查点] 已完成的检索必须保留，不要重新初始化、expand_queries 或重跑已完成的来源/query。"
            f"当前 papers_raw={raw_count} 条；已完成 {len(unique_queries)} 条不同 query、"
            f"{len(entries)} 次来源检索；来源={', '.join(sources) or '未记录'}；"
            f"检索主题类型={', '.join(buckets) or '未记录'}。{page_note}"
            "请基于刚读到的 title/abstract/source_query 继续 semantic_screen；需要更多记录时只读取下一页。"
            "完成必要筛选后调用 finish_task，让 runtime 做去重、核验和队列收尾。"
            "此检查点是运行事实，不是论文相关性或最终筛选结论。",
            step=step,
        )

    def _persist_t2_citation_edges_if_present(
        self,
        *,
        ctx: ExecutionContext,
        policy: "WorkspaceAccessPolicy",
        tool_name: str,
        result: ToolResult,
    ) -> dict[str, object] | None:
        """Persist raw one-hop citation edges independently of neighbor paper resolution."""

        if ctx.task_id != "T2" or tool_name != "fetch_outgoing_citations" or not result.ok:
            return None
        source_id = str(result.data.get("source_id") or "").strip()
        if not source_id:
            return None
        edges: list[list[str]] = []
        for key in ("referenced_works", "related_works"):
            values = result.data.get(key)
            if not isinstance(values, list):
                continue
            for target in values:
                target_id = str(target or "").strip()
                if target_id and target_id != source_id:
                    edges.append([source_id, target_id])
        if not edges:
            return None

        try:
            path = policy.resolve_write("literature/citation_edges.json")
            existing: list[object] = []
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(loaded, list):
                        existing = loaded
                except Exception:
                    existing = []
            seen: set[tuple[str, str]] = set()
            merged: list[object] = []
            for item in [*existing, *edges]:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    merged.append(item)
                    continue
                left, right = str(item[0] or ""), str(item[1] or "")
                if not left or not right or left == right:
                    continue
                key = (left, right)
                if key in seen:
                    continue
                seen.add(key)
                merged.append([left, right])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "content_suffix": f"[Runtime] 自动保存 citation_edges 失败: {exc}",
            }
        return {
            "ok": True,
            "edge_count": len(edges),
            "content_suffix": f"[Runtime] 已自动追加 {len(edges)} 条到 literature/citation_edges.json",
        }

    @staticmethod
    def _annotate_t2_search_bucket(
        *,
        tool_name: str,
        tool_arguments: dict[str, object],
        result: ToolResult,
        papers: list[object],
    ) -> list[object]:
        """Preserve explicit Scout query-bucket labels in raw paper records.

        The runtime does not infer academic relevance from keywords. It only
        carries labels supplied by the LLM/tool metadata as retrieval
        provenance. Domain-map and deep-read admission still require Scout
        LLM's semantic_screen.
        """

        bucket = _normalize_t2_query_bucket(
            tool_arguments.get("search_bucket")
            or tool_arguments.get("query_bucket")
            or result.data.get("search_bucket")
            or result.data.get("query_bucket")
        )
        bridge_id = str(
            tool_arguments.get("bridge_id")
            or result.data.get("bridge_id")
            or ""
        ).strip()
        query = str(tool_arguments.get("query") or result.data.get("query") or "").strip()
        if not bucket and not query and not bridge_id:
            return papers

        annotated: list[object] = []
        for paper in papers:
            if not isinstance(paper, dict):
                annotated.append(paper)
                continue
            record = dict(paper)
            if bucket and not record.get("search_bucket"):
                record["search_bucket"] = bucket
            if bucket and not record.get("query_bucket"):
                record["query_bucket"] = bucket
            if bucket and not record.get("source_bucket"):
                if bucket == "adjacent_field":
                    record["source_bucket"] = "adjacent"
                elif bucket == "theory_bridge":
                    record["source_bucket"] = "adjacent"
                elif bucket in {"core", "snowball", "seed"}:
                    record["source_bucket"] = bucket
            if bucket in {"adjacent_field", "theory_bridge"}:
                record.setdefault("cross_domain_retrieval_candidate", True)
                record.setdefault("adjacent_field", True)  # deprecated provenance alias
                record.setdefault("retrieval_intent", "cross_domain_bridge")
            elif bucket:
                record.setdefault("retrieval_intent", "primary")
            if bridge_id:
                record.setdefault("bridge_id", bridge_id)
                record.setdefault("retrieval_intent", "cross_domain_bridge")
            if query:
                record.setdefault("source_query", query)
            record.setdefault("source_tool", tool_name)
            provenance = record.get("provenance")
            if not isinstance(provenance, dict):
                provenance = {}
            provenance.setdefault("source_tool", tool_name)
            if query:
                provenance.setdefault("source_query", query)
            if bucket:
                provenance.setdefault("query_bucket", bucket)
                provenance.setdefault("search_bucket", bucket)
            if bridge_id:
                provenance.setdefault("bridge_id", bridge_id)
            record["provenance"] = provenance
            annotated.append(record)
        return annotated

    def _parse_llm_response(self, resp: object, *, step: int) -> Message:
        choice = resp.raw.choices[0].message
        content = getattr(choice, "content", None) or None
        tool_calls: list[ToolCall] = []
        raw_tool_calls = getattr(choice, "tool_calls", None) or []
        for tool_call in raw_tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                # Native function calls occasionally lose a closing ``]``
                # immediately before an otherwise present object closer.  It
                # is a transport-level typo, not an invitation for runtime
                # code to edit a research artifact.  Recover only that
                # uniquely determined delimiter before falling back to a
                # model-authored retry.  In particular, never append missing
                # content, infer a field value, or close a truncated object.
                arguments = self._repair_lossless_native_tool_json(tool_call.function.arguments)
                if arguments is None:
                    arguments = {
                        "__raw__": tool_call.function.arguments,
                        "__parse_error__": True,
                    }
            tool_calls.append(
                ToolCall(id=tool_call.id, name=tool_call.function.name, arguments=arguments)
            )
        # 某些 OpenAI-compatible provider 会把工具调用吐成文本片段而不是原生 tool_calls。
        # 这里做一次兜底解析，尽量把 DSML 风格的伪调用恢复成真实 ToolCall。
        if not tool_calls and content:
            recovered_content, recovered_calls = self._recover_textual_tool_calls(content)
            if recovered_calls:
                content = recovered_content
                tool_calls = recovered_calls
        return Message.assistant(content=content, tool_calls=tool_calls, step=step)

    @staticmethod
    def _repair_lossless_native_tool_json(raw: str) -> dict[str, object] | None:
        """Recover only lossless native-tool JSON delimiter defects.

        Some OpenAI-compatible endpoints occasionally append one extra `}` to
        an otherwise complete native tool argument.  `raw_decode` lets us
        accept that lossless syntax repair.  A second observed endpoint defect
        omits a `]` immediately before a matching `}` while emitting every
        field value and every object closer.  The missing bracket is uniquely
        determined by the delimiter stack, so inserting it changes no model
        authored research content.  Truncation, missing commas, unfinished
        strings or values, multiple missing object closers, concatenated
        objects, and every ambiguous structure remain hard failures for the
        model to repair.
        """

        try:
            value, end = json.JSONDecoder().raw_decode(str(raw or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            value = None
            end = 0
        if isinstance(value, dict):
            suffix = str(raw or "")[end:].strip()
            if suffix and all(char in "}]" for char in suffix):
                return value

        repaired = AgentRunner._insert_unambiguous_array_closers(str(raw or ""))
        if repaired is None:
            # A few OpenAI-compatible endpoints emit the complete argument
            # value but omit the final object closer.  When the scanner proves
            # that the remaining stack contains only object openers and the
            # input does not end after a comma, colon, or opener, appending
            # those closers is a lossless syntax repair.  It does not infer a
            # field or fabricate research content.
            repaired = AgentRunner._append_unambiguous_object_closers(str(raw or ""))
        elif repaired != raw:
            # Array recovery can leave one or more enclosing object closers
            # absent. Complete those only under the same conservative rule.
            repaired = AgentRunner._append_unambiguous_object_closers(repaired) or repaired
        if repaired is None or repaired == raw:
            return None
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _append_unambiguous_object_closers(raw: str) -> str | None:
        """Close only complete JSON objects whose final ``}`` was omitted.

        This deliberately refuses an unfinished string, array, property,
        comma, or colon.  The helper is therefore limited to a transport
        delimiter defect such as ``{"path":"x","data":{"n":1}`` and
        cannot turn a genuinely truncated value into a plausible artifact.
        """

        if not raw:
            return None
        stack: list[str] = []
        in_string = False
        escaped = False
        last_significant = ""
        for char in raw:
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                last_significant = char
                continue
            if char.isspace():
                continue
            last_significant = char
            if char in "{[":
                stack.append(char)
            elif char == "}":
                if not stack or stack[-1] != "{":
                    return None
                stack.pop()
            elif char == "]":
                if not stack or stack[-1] != "[":
                    return None
                stack.pop()
        # A single omitted outer object closer is the only repair we can
        # establish without guessing whether the provider also omitted a
        # field value or nested object. Multiple missing object closers remain
        # a model-authored repair, not a transport repair.
        if in_string or len(stack) != 1 or stack[0] != "{":
            return None
        if last_significant in {"", ",", ":", "{", "["}:
            return None
        return raw + ("}" * len(stack))

    @staticmethod
    def _repair_trailing_json_delimiters(raw: str) -> dict[str, object] | None:
        """Compatibility name for callers predating lossless array recovery."""

        return AgentRunner._repair_lossless_native_tool_json(raw)

    @staticmethod
    def _insert_unambiguous_array_closers(raw: str) -> str | None:
        """Insert a missing ``]`` only when an object closer proves its slot.

        The scanner deliberately understands JSON strings and escapes.  It
        refuses any end-of-input balancing, any missing object close, or a
        conflicting ``]``.  Therefore it can repair ``[{...}}`` to
        ``[{...}]}``, but cannot turn a genuinely truncated tool call into a
        plausible-looking object.
        """

        if not raw:
            return None
        stack: list[str] = []
        output: list[str] = []
        in_string = False
        escaped = False
        inserted = False
        for char in raw:
            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                output.append(char)
                continue
            if char in "{[":
                stack.append(char)
                output.append(char)
                continue
            if char == "}":
                # An array cannot be closed by ``}``.  If it is the immediate
                # opener, exactly one missing `]` is the only syntactically
                # valid repair at this position.
                if stack and stack[-1] == "[":
                    output.append("]")
                    stack.pop()
                    inserted = True
                if not stack or stack[-1] != "{":
                    return None
                stack.pop()
                output.append(char)
                continue
            if char == "]":
                if not stack or stack[-1] != "[":
                    return None
                stack.pop()
                output.append(char)
                continue
            output.append(char)
        if in_string or stack or not inserted:
            return None
        return "".join(output)

    def _recover_textual_tool_calls(self, content: str) -> tuple[str | None, list[ToolCall]]:
        """从文本中恢复 DSML 风格的伪工具调用。"""
        invoke_re = re.compile(
            r"<[^>\n]*invoke\s+name=\"(?P<name>[^\"]+)\"[^>]*>(?P<body>.*?)</[^>\n]*invoke>",
            re.DOTALL,
        )
        param_re = re.compile(
            r"<[^>\n]*parameter\s+name=\"(?P<name>[^\"]+)\"[^>]*>(?P<value>.*?)</[^>\n]*parameter>",
            re.DOTALL,
        )

        tool_calls: list[ToolCall] = []
        for match in invoke_re.finditer(content):
            arguments: dict[str, object] = {}
            for param_match in param_re.finditer(match.group("body")):
                key = param_match.group("name").strip()
                value = self._coerce_textual_tool_value(param_match.group("value"))
                arguments[key] = value
            tool_calls.append(ToolCall.create(match.group("name").strip(), arguments))

        if not tool_calls:
            return content, []

        cleaned = invoke_re.sub("", content)
        cleaned = re.sub(r"<[^>\n]*tool_calls[^>]*>|</[^>\n]*tool_calls>", "", cleaned)
        cleaned = re.sub(r"<[^>\n]*minimax:tool_call[^>]*>|</[^>\n]*minimax:tool_call>", "", cleaned)
        cleaned = cleaned.strip() or None
        return cleaned, tool_calls

    def _coerce_textual_tool_value(self, raw_value: str) -> object:
        """尽量把文本参数恢复成工具 schema 更容易接受的类型。"""
        value = raw_value.strip()
        if not value:
            return ""
        if value.startswith("{") or value.startswith("["):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        lowered = value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return value

    def _maybe_truncate(
        self,
        messages: list[Message],
        binding: ModelBinding,
        *,
        task_id: str | None = None,
    ) -> list[Message]:
        """按 message group 粒度做上下文裁剪。"""
        config = self.llm.get_truncation_config()
        limit = self.llm.get_context_window(binding)
        # ``context_window_fallback`` is a total capacity estimate, not a
        # promise that every provider request near that number will complete
        # promptly.  Keep an independent, user-visible cap for retained
        # conversation/tool history.  It never changes PDF page extraction or
        # durable notes: omitted turns remain available as workspace artifacts.
        try:
            configured_input_cap = int(config.get("max_input_tokens", 0) or 0)
        except (TypeError, ValueError):
            configured_input_cap = 0
        if configured_input_cap > 0:
            limit = min(limit, configured_input_cap)
        trigger_ratio = float(config.get("trigger_ratio", 0.8))
        target_ratio = float(config.get("target_ratio", 0.6))
        if task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}:
            limit = min(limit, T45_HISTORY_MAX_INPUT_TOKENS)
            trigger_ratio = T45_HISTORY_TRIGGER_RATIO
            target_ratio = T45_HISTORY_TARGET_RATIO
        elif task_id in READER_HISTORY_MAX_INPUT_TOKENS:
            limit = min(limit, READER_HISTORY_MAX_INPUT_TOKENS[task_id])
            trigger_ratio = READER_HISTORY_TRIGGER_RATIO
            target_ratio = READER_HISTORY_TARGET_RATIO
        trigger = int(limit * trigger_ratio)
        target = int(limit * target_ratio)
        current = self.llm.count_tokens([m.to_openai_dict() for m in messages], binding)
        if current <= trigger:
            return messages

        groups = self._split_into_groups(messages)
        kept = [groups[0]]
        total = self._count_group_tokens(groups[0], binding)
        for group in reversed(groups[1:]):
            group_tokens = self._count_group_tokens(group, binding)
            if total + group_tokens > target:
                break
            kept.insert(1, group)
            total += group_tokens

        omitted = len(groups) - len(kept)
        if omitted <= 0:
            return messages

        note = Message.user(
            (
                f"[Runtime] 由于上下文过长，已省略较早的 {omitted} 轮交互。"
                + (
                    "T4.5 已保留当前结构化来源、hypotheses 和 Proposal；不要复用旧写入片段，"
                    "请先 read_file 当前目标 artifact，再做一次完整、定向的修复。"
                    if task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}
                    else "如需回忆先前信息，请读取相关 artifact。"
                )
            ),
            step=messages[-1].step,
        )
        flattened: list[Message] = []
        flattened.extend(kept[0])
        flattened.append(note)
        for group in kept[1:]:
            flattened.extend(group)
        return flattened

    @staticmethod
    def _t45_checkpoint_repair_feedback(
        *,
        tool_name: str,
        data: dict[str, object],
        fallback_error: str,
    ) -> str:
        """Turn a failed read-only T4.5 checkpoint into the next-turn repair plan."""

        error = " ".join(str(data.get("validation_error") or fallback_error).split())[:1400]
        targets = data.get("repair_targets")
        if isinstance(targets, list) and targets:
            target_list = [str(item) for item in targets if str(item).strip()]
        elif tool_name == "validate_t45_research_package":
            target_list = ["ideation/hypotheses.md", "ideation/proposal/research_proposal.md"]
        else:
            target_list = [
                "ideation/research_blueprint.yaml",
                "ideation/claim_registry.yaml",
                "ideation/exp_plan.yaml",
            ]
        target_text = ", ".join(target_list) or "the named source artifact"
        if bool(data.get("initialization_required")):
            return (
                "[Runtime T4.5 formalization initialization] This is the normal blank-slate opening after a Candidate "
                "selection, not a failed research package.\n"
                f"Pending source contract: {target_text}\n"
                "These paths do not exist yet, so do not probe them with read_file. First create only "
                "ideation/research_blueprint.yaml with write_structured_file, then call the checkpoint. Derive "
                "claim_registry.yaml from the accepted blueprint, call the checkpoint again, and only then map those "
                "claims into exp_plan.yaml. This dependency order preserves research coherence and keeps a blank-slate "
                "initialization from becoming one oversized, fragile tool call. Only after valid=true may you write prose."
            )
        return (
            "[Runtime T4.5 checkpoint repair] The read-only checkpoint executed successfully but `valid=false`; "
            "this is an actionable research-package failure, not a green success.\n"
            f"Failure: {error}\n"
            f"Repair scope: {target_text}\n"
            "Before another checkpoint, read every target that you will modify. Repair the stated issue in the current "
            "artifact and preserve all already-valid material. For hypotheses or Proposal, write a complete current "
            "document, never a heading-only fragment, outline, or shorter replacement of a complete document. "
            "Do not call the same checkpoint again until the relevant source has actually changed."
        )

    def _repair_openai_tool_message_sequence(self, messages: list[Message]) -> list[Message]:
        """Ensure assistant tool_calls are immediately followed by tool messages.

        OpenAI-compatible providers reject histories where an assistant message
        declares tool_calls but any corresponding tool result is missing. This
        can happen after cancellation, gate auto-bridging, manual trace repair,
        or future truncation changes. We repair at the provider boundary instead
        of letting one malformed history make every fallback model fail.
        """

        repaired: list[Message] = []
        changed = False
        idx = 0
        while idx < len(messages):
            message = messages[idx]
            if message.role == Role.TOOL:
                changed = True
                preview = (message.content or "").strip()
                if len(preview) > 500:
                    preview = preview[:497] + "..."
                repaired.append(
                    Message.user(
                        "[Runtime] Omitted an orphan tool result from the provider message history "
                        "because it was not immediately attached to an assistant tool_call. "
                        f"tool={message.name or 'unknown_tool'} tool_call_id={message.tool_call_id or ''}. "
                        f"Preview: {preview}",
                        step=message.step,
                    )
                )
                idx += 1
                continue
            repaired.append(message)
            idx += 1
            if message.role != Role.ASSISTANT or not message.tool_calls:
                continue

            expected_ids = [tool_call.id for tool_call in message.tool_calls]
            seen_ids: set[str] = set()
            while idx < len(messages) and messages[idx].role == Role.TOOL:
                tool_message = messages[idx]
                if tool_message.tool_call_id:
                    seen_ids.add(tool_message.tool_call_id)
                repaired.append(tool_message)
                idx += 1

            missing_ids = [tool_call_id for tool_call_id in expected_ids if tool_call_id not in seen_ids]
            if not missing_ids:
                continue
            changed = True
            name_by_id = {tool_call.id: tool_call.name for tool_call in message.tool_calls}
            for tool_call_id in missing_ids:
                repaired.append(
                    Message.tool(
                        tool_call_id=tool_call_id,
                        name=name_by_id.get(tool_call_id, "unknown_tool"),
                        content=(
                            "ERROR: tool result was unavailable because the previous ResearchOS "
                            "turn was interrupted or repaired before the tool response was recorded. "
                            "Do not assume this tool succeeded; inspect persisted artifacts or call "
                            "the tool again if needed."
                        ),
                        is_error=True,
                        step=message.step,
                        metadata={
                            "error": "missing_tool_result_repaired",
                            "data": {"runtime_repaired": True},
                        },
                    )
                )

        if changed:
            self.log.warning("repaired_missing_tool_messages_before_llm")
        return repaired

    def _split_into_groups(self, messages: list[Message]) -> list[list[Message]]:
        """把消息拆成“assistant + tool results”为一组的逻辑轮次。"""
        if not messages:
            return []
        groups: list[list[Message]] = []
        first = [messages[0]]
        idx = 1
        if idx < len(messages) and messages[idx].role == Role.USER:
            first.append(messages[idx])
            idx += 1
        groups.append(first)

        while idx < len(messages):
            message = messages[idx]
            if message.role == Role.ASSISTANT:
                group = [message]
                idx += 1
                while idx < len(messages) and messages[idx].role == Role.TOOL:
                    group.append(messages[idx])
                    idx += 1
                groups.append(group)
                continue
            groups.append([message])
            idx += 1
        return groups

    def _count_group_tokens(self, group: list[Message], binding: ModelBinding) -> int:
        return self.llm.count_tokens([message.to_openai_dict() for message in group], binding)

    @staticmethod
    def _mark_runtime_recovery(
        ctx: ExecutionContext,
        *,
        kind: str,
        error: str | None,
        details: dict[str, object] | None = None,
    ) -> None:
        """Attach a machine-readable, non-success recovery reason to this run.

        The runner cannot persist a state-machine Gate itself because it is
        intentionally reusable outside the full pipeline.  It can, however,
        preserve an explicit signal on ``AgentResult`` so the StateMachine can
        turn the interruption into a durable human decision.  This prevents a
        validation/budget/provider pause from being confused with Ctrl-C.
        """

        ctx.extra["_runtime_recovery_signal"] = {
            "schema_version": "1.0.0",
            "kind": str(kind),
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
            "error_summary": " ".join(str(error or "").split())[:1200],
            "details": dict(details or {}),
        }

    @staticmethod
    def _mark_explicit_runtime_pause(ctx: ExecutionContext, *, kind: str, decision: str) -> None:
        """Remember that the researcher already chose to pause this run.

        The StateMachine must not immediately replace an explicit "pause" in
        an inline runtime prompt with a second generic recovery Gate.  This is
        distinct from unavailable stdin, where no human decision was obtained
        and a durable Gate is still required on resume.
        """

        ctx.extra["_runtime_explicit_pause"] = {
            "kind": str(kind),
            "decision": str(decision),
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
        }

    @staticmethod
    def _apply_runtime_recovery_window(eff: EffectiveConfig, ctx: ExecutionContext) -> EffectiveConfig:
        """Apply one explicitly approved bounded recovery window.

        This only changes operational limits.  It never changes evidence
        policy, schemas, tool permissions, or the Agent's scientific claims.
        A new process normally gives a task a fresh budget anyway; the bounded
        increment makes the user-selected "one more window" meaningful for
        nodes that configure finite runtime limits.
        """

        recovery = ctx.extra.get("runtime_recovery")
        if not isinstance(recovery, dict):
            return eff
        if recovery.get("target_task") != ctx.task_id:
            return eff
        if recovery.get("action") != "extend_recovery_window":
            return eff

        requested = recovery.get("resource_window")
        window = requested if isinstance(requested, dict) else {}
        try:
            ratio = float(window.get("increase_ratio", 0.25) or 0.25)
        except (TypeError, ValueError):
            ratio = 0.25
        ratio = max(0.05, min(ratio, 0.50))
        if eff.unlimited_budget:
            ctx.extra["runtime_recovery_window_applied"] = {
                "mode": "fresh_unlimited_run",
                "increase_ratio": ratio,
            }
            return eff

        def expanded(value: int, minimum: int) -> int:
            return int(value) + max(minimum, int(int(value) * ratio))

        expanded_eff = replace(
            eff,
            max_steps=expanded(eff.max_steps, 4),
            max_tokens=expanded(eff.max_tokens, 10_000),
            max_wall_seconds=expanded(eff.max_wall_seconds, 120),
        )
        ctx.extra["runtime_recovery_window_applied"] = {
            "mode": "bounded_extension",
            "increase_ratio": ratio,
            "before": {
                "max_steps": eff.max_steps,
                "max_tokens": eff.max_tokens,
                "max_wall_seconds": eff.max_wall_seconds,
            },
            "after": {
                "max_steps": expanded_eff.max_steps,
                "max_tokens": expanded_eff.max_tokens,
                "max_wall_seconds": expanded_eff.max_wall_seconds,
            },
        }
        return expanded_eff

    @staticmethod
    def _runtime_recovery_prompt(ctx: ExecutionContext) -> str:
        """Return an operational recovery instruction for every Agent family.

        It deliberately does not synthesize any research content.  The Agent
        still owns the explanation, hypothesis, prose, and repair judgement;
        this message only tells it why an already-approved retry exists and
        how to avoid discarding valid work.
        """

        recovery = ctx.extra.get("runtime_recovery")
        if not isinstance(recovery, dict) or recovery.get("target_task") != ctx.task_id:
            return ""
        action = str(recovery.get("action") or "retry_targeted_repair")
        error = " ".join(str(recovery.get("error_summary") or "").split())[:1200]
        existing = recovery.get("existing_outputs")
        existing_outputs = [str(item) for item in existing[:12]] if isinstance(existing, list) else []
        lines = [
            "[本轮恢复决策] 研究者已明确批准一次可恢复的续跑。",
            "先读取现有产物与诊断，在保留可用内容的前提下只处理实际受影响的问题；不要把一次修复变成整轮重写。",
            "不得为了通过校验伪造引用、证据、数据、实验结果或科研解释。",
        ]
        if action == "extend_recovery_window":
            lines.append("本轮额外资源/修复窗口已被批准；它只用于有针对性的诊断、修复和复核。")
        else:
            lines.append("本轮是定向修复窗口；优先复核上次失败原因对应的来源文件和结构化产物。")
        if recovery.get("path"):
            # The receipt is a controller-private audit artifact.  Its useful
            # facts are injected below, while telling the Agent to read its
            # path causes an avoidable access_denied tool call on every
            # resumed task.
            lines.append("- 恢复决策与诊断摘要已由 controller 注入；不要读取 `_runtime/` 下的内部记录。")
        if error:
            lines.append(f"- 上次可恢复问题：{error}")
        if existing_outputs:
            lines.append("- 已有输出：`" + "`, `".join(existing_outputs) + "`")
        return "\n".join(lines)

    def _build_result(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        stop_reason: str,
        error_msg: str | None,
        started: float,
        trace_file: Path,
        eff: EffectiveConfig,
        last_model_used: str | None,
        last_endpoint_used: str | None,
    ) -> AgentResult:
        outputs = {name: path for name, path in ctx.outputs_expected.items() if path.exists()}
        ok = stop_reason == AgentResult.STOP_FINISHED
        if last_model_used is None:
            last_model_used = str(ctx.extra.get("t4_evolution_last_model") or "") or None
        if last_endpoint_used is None:
            last_endpoint_used = str(ctx.extra.get("t4_evolution_last_endpoint") or "") or None
        metadata: dict[str, object] = {}
        if ctx.extra.get("completion_mode"):
            metadata["completion_mode"] = ctx.extra.get("completion_mode")
        if isinstance(ctx.extra.get("runtime_actions"), list):
            metadata["runtime_actions"] = ctx.extra.get("runtime_actions")
        explicit_pause = ctx.extra.get("_runtime_explicit_pause")
        if isinstance(explicit_pause, dict):
            metadata["runtime_explicit_pause"] = dict(explicit_pause)
        runtime_recovery = ctx.extra.get("_runtime_recovery_signal")
        if isinstance(runtime_recovery, dict):
            metadata["runtime_recovery"] = dict(runtime_recovery)
        message = {
            AgentResult.STOP_FINISHED: "Agent 成功完成",
            AgentResult.STOP_MAX_STEPS: "达到最大步数",
            AgentResult.STOP_BUDGET: "超出预算",
            AgentResult.STOP_ERROR: f"错误: {error_msg or 'unknown'}",
            AgentResult.STOP_INTERRUPTED: "被中断",
            AgentResult.STOP_HUMAN_REJECT: "被用户拒绝",
        }[stop_reason]
        if ok and metadata.get("completion_mode") == "t2_finish_finalize":
            message = "Agent 成功完成（T2 finish_task 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t2_recovery":
            message = "Agent 成功完成（T2 recovery 自动补全）"
        elif ok and metadata.get("completion_mode") == "t2_resume_prefinalize":
            message = "Agent 成功完成（T2 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t3_resume_prefinalize":
            message = "Agent 成功完成（T3 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t36_visuals_resume_prefinalize":
            message = "Agent 成功完成（T3.6 taxonomy visual 已验证，跳过重复生成）"
        elif ok and metadata.get("completion_mode") == "t36_visuals_deterministic":
            message = "Agent 成功完成（T3.6 taxonomy visual 已确定性编译并验证）"
        elif ok and metadata.get("completion_mode") == "t36_compile_resume_prefinalize":
            message = "Agent 成功完成（T3.6 survey PDF 已验证，跳过重复编译）"
        elif ok and metadata.get("completion_mode") == "t36_compile_deterministic":
            message = "Agent 成功完成（T3.6 已确定性编译并验证 survey PDF）"
        elif ok and metadata.get("completion_mode") == "t4_resume_prefinalize":
            message = "Agent 成功完成（T4 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t4_gate1_ready":
            message = "Agent 成功完成（T4 Gate1 候选池已就绪）"
        elif ok and metadata.get("completion_mode") == "t4_pre_novelty_ready":
            message = "Agent 成功完成（已生成 Pre-Novelty brief，进入 T4.5）"
        elif ok and metadata.get("completion_mode") == "t45_resume_prefinalize":
            message = "Agent 成功完成（T4.5 resume 确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t5_reboost_resume_prefinalize":
            message = "Agent 成功完成（T5 reboost 已有 handoff 复用）"
        elif ok and metadata.get("completion_mode") == "t5_reboost_timeout_recovery":
            message = "Agent 成功完成（T5 reboost 超时后确定性收尾）"
        elif ok and metadata.get("completion_mode") == "t8_resource_prefinalize":
            message = "Agent 成功完成（T8 resource index 已验证，交给状态机推进）"
        elif ok and metadata.get("completion_mode") == "t8_section_plan_prefinalize":
            message = "Agent 成功完成（T8 section-plan 确定性修复/收尾）"
        elif ok and metadata.get("completion_mode") == "t9_submission_prefinalize":
            message = "Agent 成功完成（T9 已有投稿包确定性收尾）"
        elif ok and metadata.get("completion_mode") == "project_skill_specialization_reused":
            message = "Agent 成功完成（项目专属 Skill Suite 指纹未变化，复用已验证产物）"
        return AgentResult(
            ok=ok,
            message=message,
            outputs_produced=outputs,
            steps_used=budget.steps,
            tokens_in=budget.tokens_in,
            tokens_out=budget.tokens_out,
            cost_usd=budget.cost_usd,
            duration_seconds=time.time() - started,
            stop_reason=stop_reason,
            error=error_msg,
            trace_file=trace_file,
            llm_profile=eff.llm_profile,
            llm_tier=eff.llm_tier,
            llm_model_used=last_model_used,
            llm_endpoint_used=last_endpoint_used,
            metadata=metadata,
        )

    async def _maybe_offer_budget_extension(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        exc: BudgetExceeded,
        used_extensions: int,
    ) -> tuple[bool, int]:
        """在预算触顶时给长任务一个人工扩限机会。"""

        policy = self.budget_escalation_policy or {}
        if not policy.get("enabled", False):
            return False, used_extensions

        enabled_tasks = set(policy.get("tasks") or [])
        if enabled_tasks and ctx.task_id not in enabled_tasks:
            return False, used_extensions

        raw_max_extensions = policy.get("max_extensions_per_run")
        # `null` / 缺省 / 负数 表示“不设上限”，但每次都仍然要经过人工 gate 确认。
        if raw_max_extensions is None:
            max_extensions = None
        else:
            max_extensions = int(raw_max_extensions)
            if max_extensions < 0:
                max_extensions = None
        if max_extensions is not None and used_extensions >= max_extensions:
            return False, used_extensions

        steps_ratio = float(policy.get("steps_increase_ratio", 0.25) or 0.25)
        token_ratio = float(policy.get("token_increase_ratio", 0.5) or 0.5)
        wall_ratio = float(policy.get("wall_seconds_increase_ratio", 0.5) or 0.5)

        if exc.dimension == "steps":
            delta = max(20, int(budget.max_steps * steps_ratio))
        elif exc.dimension == "tokens":
            delta = max(100000, int(budget.max_tokens * token_ratio))
        elif exc.dimension == "wall_seconds":
            delta = max(600, int(budget.max_wall_seconds * wall_ratio))
        else:
            return False, used_extensions

        unit = {
            "steps": "steps",
            "tokens": "tokens",
            "wall_seconds": "seconds",
        }[exc.dimension]
        snapshot = budget.snapshot()
        # 把当前已落盘的关键输出一起展示出来，方便用户判断“现在停会损失什么”。
        existing_outputs = [
            str(path.relative_to(ctx.workspace_dir))
            for path in ctx.outputs_expected.values()
            if path.exists()
        ]
        human_started = time.time()
        try:
            result = await self.human.present_gate(
                gate_id="runtime_budget_extension",
                presentation={
                    "_title": "预算上限已触发",
                    "_description": "当前任务已达到预算上限。你可以选择扩限后继续，或停止本次运行。",
                    "task_id": ctx.task_id,
                    "run_id": ctx.run_id,
                    "extensions_used": used_extensions,
                    "dimension": exc.dimension,
                    "used": exc.used,
                    "limit": exc.limit,
                    "current_budget": snapshot,
                    "existing_outputs": existing_outputs,
                    "suggested_extension": {
                        "dimension": exc.dimension,
                        "delta": delta,
                        "new_limit": int(exc.limit + delta),
                        "unit": unit,
                    },
                },
                options=[
                    {
                        "id": "extend",
                        "label": f"继续，并增加 {delta} {unit}",
                    },
                    {
                        "id": "stop",
                        "label": "停止本次运行",
                    },
                ],
            )
        except HumanInputUnavailable as exc:
            raise RecoverableRuntimePause(str(exc)) from exc
        finally:
            budget.exclude_wall_time(time.time() - human_started)
        option_id = str((result or {}).get("option_id") or "stop")
        if option_id != "extend":
            self._mark_explicit_runtime_pause(
                ctx,
                kind="budget",
                decision=option_id,
            )
            return False, used_extensions

        # 只扩当前触顶维度，避免一次确认后无节制地放大全部预算。
        budget.extend_limit(exc.dimension, delta)
        self.log.info(
            "budget_extended",
            task_id=ctx.task_id,
            dimension=exc.dimension,
            delta=delta,
            old_limit=exc.limit,
            new_limit=exc.limit + delta,
        )
        return True, used_extensions + 1

    @staticmethod
    def _record_validation_failure(ctx: ExecutionContext, error: str) -> int:
        """Track the consecutive identical validator error for circuit breaking."""

        normalized = " ".join(str(error or "unknown validation error").split()).casefold()
        previous = str(ctx.extra.get("last_validation_error") or "").casefold()
        count = int(ctx.extra.get("same_validation_error_count") or 0)
        count = count + 1 if normalized == previous else 1
        ctx.extra["last_validation_error"] = normalized
        ctx.extra["same_validation_error_count"] = count
        return count

    @staticmethod
    def _uses_t45_quality_repair_loop(ctx: ExecutionContext) -> bool:
        """Whether this run uses T4.5's source-aware, non-counted repair loop."""

        return ctx.task_id in {"T4.5-FORMALIZE", "T4.5-REVIEW"}

    @staticmethod
    def _uses_t36_quality_repair_loop(ctx: ExecutionContext) -> bool:
        """Whether a T3.6 writing phase uses source-aware repair progress."""

        return ctx.task_id in {"T3.6-ASSEMBLE", "T3.6-REVIEW"} or ctx.task_id.startswith("T3.6-SEC-")

    @staticmethod
    def _is_t45_repairable_warning(error: object) -> bool:
        """Whether an internal quality target should stay out of the normal UI."""

        return str(error or "").startswith("T45_REPAIRABLE_WARNING:")

    async def _maybe_adjudicate_t36_semantic_failure(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        budget: BudgetTracker,
        error: str,
        run_logger: RunLogger,
    ) -> dict[str, object]:
        """Independently review only allowlisted T3.6 prose false positives."""

        if not self._uses_t36_quality_repair_loop(ctx):
            return {}

        candidates = [str(error or "").strip(), *collect_t36_semantic_errors(ctx.workspace_dir)]
        accepted_errors = accepted_t36_semantic_errors(ctx.workspace_dir)
        checks: list[dict[str, object]] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen or candidate in accepted_errors:
                continue
            seen.add(candidate)
            scope = t36_semantic_adjudication_scope(candidate)
            if scope is None:
                continue
            checks.append(
                {
                    "validator_error": candidate,
                    "artifact": str(scope["artifact"]),
                    "eligible_requirement": str(scope["requirement"]),
                    "dependency_paths": list(scope["dependency_paths"]),
                }
            )
        if not checks:
            return {}

        source_paths = list(
            dict.fromkeys(
                str(relative_path)
                for check in checks
                for relative_path in check["dependency_paths"]
            )
        )
        source_payload: dict[str, str] = {}
        for relative_path in source_paths:
            path = ctx.workspace_dir / relative_path
            try:
                # The reviewed TeX or review memo matters. Supporting source
                # state is bounded to prevent an independent check from
                # inheriting an entire survey-writing transcript.
                limit = 64_000 if relative_path.endswith(("survey.tex", "survey_review.md")) else 18_000
                source_payload[relative_path] = path.read_text(encoding="utf-8", errors="replace")[:limit]
            except OSError:
                source_payload[relative_path] = "[unreadable or missing]"

        system = (
            "You are an independent T3.6 survey semantic adjudicator. For EVERY supplied check, decide only whether "
            "the current survey prose ALREADY satisfies that exact requirement despite a deterministic surface-form "
            "validator reporting failure. You are not the author: do not rewrite, infer missing argument, invent a "
            "citation, or downgrade an actual weakness. Never waive files, schemas, state fingerprints, section structure, "
            "citation coverage/diversity/alignment, bibliography integrity, source provenance, internal-process leakage, "
            "graphics, LaTeX syntax, compiler reports, or PDF validation. Return exactly one JSON object: "
            "{\"decisions\":[{\"verdict\":\"satisfied|needs_repair|inconclusive\","
            "\"validator_error\":\"exact check error\",\"artifact\":\"exact check artifact\","
            "\"evidence\":[{\"quote\":\"exact quote from artifact\",\"explanation\":\"why it satisfies the requirement\"}],"
            "\"reason\":\"brief explanation\"}]}. Return one decision per supplied check. A satisfied decision needs one to "
            "three exact quotes from the named artifact, each at least 20 characters. Otherwise return needs_repair or "
            "inconclusive with no evidence."
        )
        user = json.dumps(
            {
                "checks": [
                    {
                        "validator_error": check["validator_error"],
                        "eligible_requirement": check["eligible_requirement"],
                        "artifact": check["artifact"],
                        "hard_boundary": "A satisfied result may waive only this exact prose check while all listed source hashes remain unchanged.",
                    }
                    for check in checks
                ],
                "source_artifacts": source_payload,
            },
            ensure_ascii=False,
        )
        try:
            self.progress.emit(
                "[T3.6 Semantic Adjudication] 正在独立复核当前综述是否仅被中英文词面规则误判。",
                important=True,
            )
            run_logger.event(
                "T36_SEMANTIC_ADJUDICATION_CALL",
                task=ctx.task_id,
                step=budget.steps,
                validator_errors=[str(check["validator_error"])[:500] for check in checks],
                artifacts=[str(check["artifact"]) for check in checks],
            )
            response = await self._await_llm_with_progress(
                ctx=ctx,
                step=budget.steps,
                progress_step_limit="unlimited" if eff.unlimited_budget else eff.max_steps,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=None,
                temperature=0.0,
                tier=eff.llm_tier,
                profile=eff.llm_profile,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
                timeout=self._llm_request_timeout_seconds(),
                max_retries_per_model=self._llm_retry_overrides()[0],
                retry_base_delay=self._llm_retry_overrides()[1],
                # This is a bounded, typed adjudication rather than an
                # open-ended critique.  Reasoning-capable providers otherwise
                # may spend the full completion budget thinking and return an
                # empty final message, which looks like a validator failure.
                reasoning_effort="low",
            )
            budget.add_tokens(response.tokens_in, response.tokens_out, response.cost_usd)
            raw_content = self._semantic_adjudication_response_text(response)
            decision = self._parse_t45_semantic_adjudication_json(raw_content)
        except (LLMProviderError, OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            run_logger.event(
                "T36_SEMANTIC_ADJUDICATION_UNAVAILABLE",
                task=ctx.task_id,
                step=budget.steps,
                error=str(exc)[:500],
            )
            self.progress.emit(
                "[T3.6 Semantic Adjudication] 本轮未获得可核验结论；保持已有硬校验和定向修复。",
                important=True,
            )
            return {
                "feedback": (
                    "\n\n独立语义复核本轮不可用或未返回可校验 JSON；没有放松任何 hard gate。"
                    "请按当前具体诊断修复；下一次相关 source 发生变化后可再次复核。"
                )
            }

        raw_decisions = decision.get("decisions")
        decisions = raw_decisions if isinstance(raw_decisions, list) else [decision]
        expected = {str(check["validator_error"]): check for check in checks}
        accepted_receipts: list[dict[str, object]] = []
        repair_reasons: list[str] = []
        seen_decisions: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict):
                continue
            validator_error = str(item.get("validator_error") or "").strip()
            if validator_error not in expected or validator_error in seen_decisions:
                continue
            seen_decisions.add(validator_error)
            check = expected[validator_error]
            artifact = str(check["artifact"])
            verdict = str(item.get("verdict") or "").strip().casefold()
            reason = str(item.get("reason") or "").strip()
            if verdict != "satisfied" or str(item.get("artifact") or "").strip() != artifact:
                repair_reasons.append(
                    f"- {validator_error}: {reason[:900] if reason else '独立复核未确认当前综述已满足该要求。'}"
                )
                continue
            evidence = item.get("evidence")
            normalized_evidence = [
                {
                    "quote": str(evidence_item.get("quote") or "").strip(),
                    "explanation": str(evidence_item.get("explanation") or "").strip(),
                }
                for evidence_item in (evidence if isinstance(evidence, list) else [])
                if isinstance(evidence_item, dict)
            ]
            try:
                receipt = persist_t36_semantic_adjudication(
                    ctx.workspace_dir,
                    validator_error=validator_error,
                    artifact=artifact,
                    requirement=str(check["eligible_requirement"]),
                    evidence=normalized_evidence,
                    adjudicator_reason=reason,
                    model=response.model_used,
                )
            except ValueError as exc:
                repair_reasons.append(
                    f"- {validator_error}: 返回的满足证据无法在当前文件逐字核验（{str(exc)[:300]}）。"
                )
                continue
            accepted_receipts.append(receipt)

        repair_reasons.extend(
            f"- {validator_error}: 独立复核没有返回该项的有效结论。"
            for validator_error in expected
            if validator_error not in seen_decisions
        )
        if accepted_receipts:
            run_logger.event(
                "T36_SEMANTIC_ADJUDICATION_ACCEPTED",
                task=ctx.task_id,
                step=budget.steps,
                validator_errors=[str(receipt["validator_error"])[:500] for receipt in accepted_receipts],
                artifacts=[str(receipt["artifact"]) for receipt in accepted_receipts],
                model=response.model_used,
                receipt="_runtime/t36_semantic_adjudications.json",
            )
            self.progress.emit(
                "[T3.6 Semantic Adjudication] 已用当前文件中的可核验原文确认语义项；引用、证据和编译门仍完整执行。",
                important=True,
            )
        else:
            run_logger.event(
                "T36_SEMANTIC_ADJUDICATION_REJECTED",
                task=ctx.task_id,
                step=budget.steps,
                validator_errors=[str(check["validator_error"])[:500] for check in checks],
                verdict="needs_repair_or_invalid",
            )
            self.progress.emit(
                "[T3.6 Semantic Adjudication] 独立复核未确认当前文本满足要求；继续定向修复。",
                important=True,
            )

        feedback = ""
        if accepted_receipts:
            feedback += (
                "\n\n独立语义复核已以当前文件的可核验原文确认部分语义要求；"
                "仅对这些精确错误、且仅在来源哈希未变化时有效。其它 hard gate 仍完整执行。"
            )
        if repair_reasons:
            feedback += "\n\n独立语义复核仍要求补足以下实际综述写作问题：\n" + "\n".join(repair_reasons)
        return {"accepted": bool(accepted_receipts), "receipts": accepted_receipts, "feedback": feedback}

    async def _maybe_adjudicate_t45_semantic_failure(
        self,
        *,
        ctx: ExecutionContext,
        eff: EffectiveConfig,
        budget: BudgetTracker,
        error: str,
        run_logger: RunLogger,
    ) -> dict[str, object]:
        """Ask an independent LLM only for ambiguous, current prose failures.

        This is intentionally a fallback after deterministic validation, never
        a replacement for it. The allowlist in ``semantic_adjudication_scope``
        excludes schema, evidence, lineage, audit-verdict, identifier,
        experiment-mapping, anti-padding, and audit-language rules. An accepted
        answer must quote the exact current artifact; persistence binds that
        answer to hashes of every source relevant to the check.
        """

        if ctx.task_id not in {"T4.5-FORMALIZE", "T4.5-REVIEW"}:
            return {}

        # The regular validator exposes its first failure.  Once that failure
        # is known to be prose-only, collect every other current, eligible
        # prose issue from exactly the same source package.  A single
        # independent review prevents one false-negative at a time from
        # becoming a long finish -> repair -> finish loop.
        candidate_errors = [str(error or "").strip()]
        candidate_errors.extend(collect_t45_semantic_errors(ctx.workspace_dir))
        accepted_errors = accepted_t45_semantic_errors(ctx.workspace_dir)
        checks: list[dict[str, object]] = []
        seen_errors: set[str] = set()
        for candidate_error in candidate_errors:
            if not candidate_error or candidate_error in seen_errors or candidate_error in accepted_errors:
                continue
            seen_errors.add(candidate_error)
            scope = semantic_adjudication_scope(candidate_error)
            if scope is None:
                continue
            checks.append(
                {
                    "validator_error": candidate_error,
                    "artifact": str(scope["artifact"]),
                    "eligible_requirement": str(scope["requirement"]),
                    "dependency_paths": list(scope["dependency_paths"]),
                }
            )
        if not checks:
            return {}

        source_paths = list(
            dict.fromkeys(
                str(relative_path)
                for check in checks
                for relative_path in check["dependency_paths"]
            )
        )
        source_payload: dict[str, str] = {}
        for relative_path in source_paths:
            path = ctx.workspace_dir / relative_path
            try:
                # Preserve the complete researcher-facing artifact while
                # bounding supporting sources.  The adjudicator needs the
                # prose in context, not an unbounded workspace dump.
                limit = 36_000 if relative_path in {
                    "ideation/proposal/research_proposal.md",
                    "ideation/hypotheses.md",
                } else 14_000
                source_payload[relative_path] = path.read_text(
                    encoding="utf-8", errors="replace"
                )[:limit]
            except OSError:
                source_payload[relative_path] = "[unreadable or missing]"

        system = (
            "You are an independent T4.5 Semantic Adjudicator. For EVERY supplied check, decide only whether "
            "the current researcher-facing prose ALREADY satisfies that exact named requirement despite a "
            "deterministic surface-form validator reporting failure. You are not the author and must not rewrite, "
            "invent, strengthen, or infer missing research content. Never waive schema, file existence, audit "
            "verdict, selection lineage, explicit IDs, claim-to-experiment mappings, component tests, evidence "
            "boundaries, anti-repetition checks, or internal-audit-language restrictions. If prose is merely "
            "plausible but incomplete, return needs_repair. Return one JSON object only: "
            "{\"decisions\":[{\"verdict\":\"satisfied|needs_repair|inconclusive\","
            "\"validator_error\":\"exact check error\",\"artifact\":\"exact check artifact\","
            "\"evidence\":[{\"quote\":\"exact quote from artifact\","
            "\"explanation\":\"why this quote satisfies the requirement\"}],\"reason\":\"brief explanation\"}]}. "
            "Return one decision for each supplied check and no additional checks. For satisfied, provide one to "
            "three exact quotes, each at least 20 characters, copied verbatim from the declared artifact. For "
            "needs_repair or inconclusive, evidence may be empty."
        )
        user = json.dumps(
            {
                "checks": [
                    {
                        "validator_error": check["validator_error"],
                        "eligible_requirement": check["eligible_requirement"],
                        "artifact": check["artifact"],
                        "hard_boundary": "A satisfied verdict can waive only this exact prose error for unchanged source hashes.",
                    }
                    for check in checks
                ],
                "source_artifacts": source_payload,
            },
            ensure_ascii=False,
        )
        try:
            self.progress.emit(
                "[T4.5 Semantic Adjudication] 正在独立复核当前正文是否已满足被词面规则误判的学术表达要求。",
                important=True,
            )
            run_logger.event(
                "T45_SEMANTIC_ADJUDICATION_CALL",
                task=ctx.task_id,
                step=budget.steps,
                validator_errors=[str(check["validator_error"])[:500] for check in checks],
                artifacts=[str(check["artifact"]) for check in checks],
            )
            response = await self._await_llm_with_progress(
                ctx=ctx,
                step=budget.steps,
                progress_step_limit="unlimited" if eff.unlimited_budget else eff.max_steps,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=None,
                temperature=0.0,
                tier=eff.llm_tier,
                profile=eff.llm_profile,
                model_override=eff.llm_model_override,
                endpoint_override=eff.llm_endpoint_override,
                max_context_override=eff.llm_max_context_override,
                timeout=self._llm_request_timeout_seconds(),
                max_retries_per_model=self._llm_retry_overrides()[0],
                retry_base_delay=self._llm_retry_overrides()[1],
                # See the matching T3.6 call above: a concise, typed verdict
                # should prioritize a usable final JSON object.
                reasoning_effort="low",
            )
            budget.add_tokens(response.tokens_in, response.tokens_out, response.cost_usd)
            raw_content = self._semantic_adjudication_response_text(response)
            decision = self._parse_t45_semantic_adjudication_json(raw_content)
        except (LLMProviderError, OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            run_logger.event(
                "T45_SEMANTIC_ADJUDICATION_UNAVAILABLE",
                task=ctx.task_id,
                step=budget.steps,
                error=str(exc)[:500],
            )
            self.progress.emit(
                "[T4.5 Semantic Adjudication] 本轮未获得可核验结论；保持原有硬校验与定向修复。",
                important=True,
            )
            return {
                "feedback": (
                    "\n\n独立语义复核本轮不可用或未返回可校验 JSON；未放松任何 hard gate。"
                    "请根据当前确定性错误和已注入的质量目标修复；下一次有新的相关正文修改时可再次触发复核。"
                )
            }

        raw_decisions = decision.get("decisions")
        # Retain compatibility with an already-issued single-decision response
        # during a resumed run. New calls receive the batch contract above.
        decisions = raw_decisions if isinstance(raw_decisions, list) else [decision]
        expected = {str(check["validator_error"]): check for check in checks}
        accepted_receipts: list[dict[str, object]] = []
        repair_reasons: list[str] = []
        seen_decisions: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict):
                continue
            validator_error = str(item.get("validator_error") or "").strip()
            if validator_error not in expected or validator_error in seen_decisions:
                continue
            seen_decisions.add(validator_error)
            check = expected[validator_error]
            artifact = str(check["artifact"])
            verdict = str(item.get("verdict") or "").strip().casefold()
            reason = str(item.get("reason") or "").strip()
            if verdict != "satisfied" or str(item.get("artifact") or "").strip() != artifact:
                repair_reasons.append(
                    f"- {validator_error}: {reason[:900] if reason else '独立复核未确认正文已满足该要求。'}"
                )
                continue
            evidence = item.get("evidence")
            normalized_evidence = [
                {
                    "quote": str(evidence_item.get("quote") or "").strip(),
                    "explanation": str(evidence_item.get("explanation") or "").strip(),
                }
                for evidence_item in (evidence if isinstance(evidence, list) else [])
                if isinstance(evidence_item, dict)
            ]
            try:
                receipt = persist_t45_semantic_adjudication(
                    ctx.workspace_dir,
                    validator_error=validator_error,
                    artifact=artifact,
                    requirement=str(check["eligible_requirement"]),
                    evidence=normalized_evidence,
                    adjudicator_reason=reason,
                    model=response.model_used,
                )
            except ValueError as exc:
                repair_reasons.append(
                    f"- {validator_error}: 返回的满足证据无法在当前正文逐字核验（{str(exc)[:300]}）。"
                )
                continue
            accepted_receipts.append(receipt)

        missing_decisions = [
            validator_error
            for validator_error in expected
            if validator_error not in seen_decisions
        ]
        repair_reasons.extend(
            f"- {validator_error}: 独立复核没有返回该项的有效结论。"
            for validator_error in missing_decisions
        )
        if accepted_receipts:
            run_logger.event(
                "T45_SEMANTIC_ADJUDICATION_ACCEPTED",
                task=ctx.task_id,
                step=budget.steps,
                validator_errors=[str(receipt["validator_error"])[:500] for receipt in accepted_receipts],
                artifacts=[str(receipt["artifact"]) for receipt in accepted_receipts],
                model=response.model_used,
                receipt="_runtime/t45_semantic_adjudications.json",
            )
            self.progress.emit(
                "[T4.5 Semantic Adjudication] 已用当前正文的可核验原文确认语义项；其余质量门继续执行。",
                important=True,
            )
        else:
            run_logger.event(
                "T45_SEMANTIC_ADJUDICATION_REJECTED",
                task=ctx.task_id,
                step=budget.steps,
                validator_errors=[str(check["validator_error"])[:500] for check in checks],
                verdict="needs_repair_or_invalid",
            )
            self.progress.emit(
                "[T4.5 Semantic Adjudication] 独立复核未确认当前文本已满足要求；保持定向修复。",
                important=True,
            )

        feedback = ""
        if accepted_receipts:
            feedback += (
                "\n\n独立语义复核已以当前文件中的可核验原文确认部分语义要求；"
                "runtime 仅对已记录的错误、且仅在相关来源哈希不变时放行。其它 hard gate 仍完整执行。"
            )
        if repair_reasons:
            feedback += "\n\n独立语义复核仍要求补足以下实际研究论证（请修复这些 source artifact）：\n" + "\n".join(repair_reasons)
        return {
            "accepted": bool(accepted_receipts),
            "receipts": accepted_receipts,
            "feedback": feedback,
        }

    @staticmethod
    def _parse_t45_semantic_adjudication_json(content: str) -> dict[str, object]:
        """Parse one JSON object from an OpenAI-compatible reviewer response."""

        candidate = str(content or "").strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate).strip()
        decoder = json.JSONDecoder()
        for index, character in enumerate(candidate):
            if character != "{":
                continue
            try:
                value, _end = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ValueError("semantic adjudicator did not return a JSON object")

    @staticmethod
    def _semantic_adjudication_response_text(response: object) -> str:
        """Recover final text from compatible chat-message representations.

        Some reasoning-capable OpenAI-compatible endpoints return text blocks
        rather than a scalar ``content`` value.  The adjudicator uses a strict
        JSON contract, so losing those blocks creates a false "unavailable"
        outcome and unnecessarily sends the author back into a rewrite loop.
        """

        try:
            message = response.raw.choices[0].message  # type: ignore[attr-defined]
        except (AttributeError, IndexError, TypeError):
            return ""
        values: list[object] = [getattr(message, "content", None)]
        if isinstance(message, dict):
            values.append(message.get("content"))
        parts: list[str] = []
        for value in values:
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, list):
                for block in value:
                    if isinstance(block, str) and block.strip():
                        parts.append(block.strip())
                    elif isinstance(block, dict):
                        text = block.get("text") or block.get("content")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
            if parts:
                break
        return "\n".join(parts)

    @staticmethod
    def _t36_quality_source_fingerprint(
        workspace_dir: Path,
        *,
        source_paths: tuple[str, ...] = T36_QUALITY_SOURCE_ARTIFACTS,
    ) -> dict[str, str]:
        """Fingerprint the writable T3.6 sources, including section trees.

        Derived ``survey.tex``, audit reports, and PDFs are intentionally not
        considered progress. Re-running assembly changes those artifacts even
        when the LLM has not repaired the source section that caused the
        failure.
        """

        fingerprints: dict[str, str] = {}
        for relative_path in source_paths:
            path = workspace_dir / relative_path
            if path.is_dir():
                digest = hashlib.sha256()
                try:
                    files = sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
                    for candidate in files:
                        rel = candidate.relative_to(workspace_dir).as_posix()
                        digest.update(rel.encode("utf-8"))
                        digest.update(b"\0")
                        digest.update(hashlib.sha256(candidate.read_bytes()).digest())
                    fingerprints[relative_path] = digest.hexdigest()
                except OSError:
                    fingerprints[relative_path] = "unreadable"
                continue
            if not path.is_file():
                fingerprints[relative_path] = "missing"
                continue
            try:
                fingerprints[relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                fingerprints[relative_path] = "unreadable"
        return fingerprints

    @staticmethod
    def _t36_quality_repair_source_scope(ctx: ExecutionContext, error: str) -> tuple[str, ...]:
        """Return the sources that could resolve one current survey diagnostic."""

        normalized = str(error or "").casefold()
        if ctx.task_id.startswith("T3.6-SEC-"):
            section_id = ctx.task_id.removeprefix("T3.6-SEC-").lower().replace("-", "_")
            return (
                f"drafts/survey/sections/{section_id}.tex",
                "drafts/survey/survey_state.json",
                f"drafts/survey/section_outlines/{section_id}.md",
                "literature/related_work.bib",
            )
        if "survey_review" in normalized:
            return (
                "drafts/survey/survey_review.md",
                "drafts/survey/survey_review_actions.json",
                "drafts/survey/sections",
                "drafts/survey/survey_plan.json",
                "drafts/survey/survey_state.json",
            )
        if any(marker in normalized for marker in ("citation", "bibliography", "references.bib")):
            return (
                "drafts/survey/sections",
                "literature/related_work.bib",
                "drafts/survey/survey_plan.json",
                "drafts/survey/survey_state.json",
            )
        if any(marker in normalized for marker in ("survey_plan", "taxonomy", "compact_theme")):
            return (
                "drafts/survey/survey_plan.json",
                "drafts/survey/survey_state.json",
                "drafts/survey/sections",
            )
        return T36_QUALITY_SOURCE_ARTIFACTS

    @classmethod
    def _record_t36_quality_repair_attempt(cls, *, ctx: ExecutionContext, error: str) -> bool:
        """Persist T3.6 repair progress so resume cannot erase no-progress state."""

        signature = cls._t45_quality_error_signature(error)
        scope = cls._t36_quality_repair_source_scope(ctx, error)
        current = cls._t36_quality_source_fingerprint(ctx.workspace_dir, source_paths=scope)
        ledger, _key, entry = cls._t36_quality_repair_entry(ctx)
        previous_error = str(entry.get("last_error_signature") or "")
        previous = entry.get("last_source_fingerprint")
        no_source_progress = (
            signature == previous_error
            and isinstance(previous, dict)
            and previous == current
        )
        entry["attempt_count"] = int(entry.get("attempt_count") or 0) + 1
        entry["last_error_signature"] = signature
        entry["last_source_fingerprint"] = current
        entry["source_scope"] = list(scope)
        entry["blocked_no_source_progress"] = no_source_progress
        ctx.extra["t36_quality_repair_attempt_count"] = int(entry["attempt_count"])
        cls._write_t36_quality_repair_ledger(ctx.workspace_dir, ledger)
        return no_source_progress

    @classmethod
    def _t36_quality_repair_baseline(cls, workspace_dir: Path) -> str:
        inputs = cls._t36_quality_source_fingerprint(
            workspace_dir,
            source_paths=T36_QUALITY_REPAIR_BASELINE_ARTIFACTS,
        )
        payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_t36_quality_repair_ledger(workspace_dir: Path) -> dict[str, object]:
        path = workspace_dir / T36_QUALITY_REPAIR_LEDGER_REL_PATH
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            data = {}
        entries = data.get("entries") if isinstance(data, dict) else None
        return {
            "semantics": "t36_quality_repair_ledger",
            "version": 1,
            "entries": dict(entries) if isinstance(entries, dict) else {},
        }

    @staticmethod
    def _write_t36_quality_repair_ledger(workspace_dir: Path, ledger: dict[str, object]) -> None:
        path = workspace_dir / T36_QUALITY_REPAIR_LEDGER_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def _t36_quality_repair_entry(cls, ctx: ExecutionContext) -> tuple[dict[str, object], str, dict[str, object]]:
        ledger = cls._load_t36_quality_repair_ledger(ctx.workspace_dir)
        baseline = cls._t36_quality_repair_baseline(ctx.workspace_dir)
        key = f"{ctx.task_id}:{baseline}"
        entries = ledger["entries"]
        assert isinstance(entries, dict)
        entry = entries.get(key)
        if not isinstance(entry, dict):
            entry = {
                "task_id": ctx.task_id,
                "baseline": baseline,
                "attempt_count": 0,
            }
            entries[key] = entry
        return ledger, key, entry

    @classmethod
    def _t36_quality_repair_window_blocked(cls, ctx: ExecutionContext) -> tuple[bool, str]:
        if not cls._uses_t36_quality_repair_loop(ctx):
            return False, ""
        _ledger, _key, entry = cls._t36_quality_repair_entry(ctx)
        scope = tuple(str(item) for item in entry.get("source_scope", []) if isinstance(item, str))
        previous_sources = entry.get("last_source_fingerprint")
        sources_unchanged = bool(scope) and isinstance(previous_sources, dict) and (
            cls._t36_quality_source_fingerprint(ctx.workspace_dir, source_paths=scope) == previous_sources
        )
        if bool(entry.get("blocked_no_source_progress")) and sources_unchanged:
            return True, "同一诊断对应的 survey source artifact 尚未发生变化"
        return False, ""

    def _pause_t36_quality_repair_before_llm(self, ctx: ExecutionContext) -> None:
        """Keep a resumed T3.6 task from repeating an unchanged repair loop."""

        blocked, reason = self._t36_quality_repair_window_blocked(ctx)
        if blocked:
            raise RecoverableRuntimePause(
                "T36_REPAIR_WINDOW_PAUSED: "
                + reason
                + "。为避免 resume 重复消耗额度，系统不会自动再次调用 Survey Writer。"
                "请修正该诊断关联的 source artifact，或改变文献基础、模板或已确认的综述范围后再 resume。"
            )

    @staticmethod
    def _t45_quality_source_fingerprint(
        workspace_dir: Path,
        *,
        source_paths: tuple[str, ...] = T45_QUALITY_SOURCE_ARTIFACTS,
    ) -> dict[str, str]:
        """Fingerprint the source artifacts relevant to one T4.5 repair."""

        fingerprints: dict[str, str] = {}
        for relative_path in source_paths:
            path = workspace_dir / relative_path
            if not path.is_file():
                fingerprints[relative_path] = "missing"
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                fingerprints[relative_path] = "unreadable"
            else:
                fingerprints[relative_path] = digest
        return fingerprints

    @staticmethod
    def _t45_quality_repair_source_scope(error: str) -> tuple[str, ...]:
        """Return sources whose changes can resolve the current diagnostic.

        The old all-source fingerprint treated an edit to any T4.5 file as
        progress. That allowed a model to keep changing a review receipt while
        a Proposal or hypotheses failure remained unchanged. Scope is kept
        deliberately conservative for cross-artifact failures: it permits the
        smallest synchronized set described by repair feedback, but not an
        unrelated display-only write.
        """

        raw = str(error or "")
        if AgentRunner._is_t45_repairable_warning(raw):
            named = tuple(
                dict.fromkeys(
                    match.group(1).strip()
                    for match in re.finditer(r"(?m)^-\s+\[[^\]]+\]\s+([^:\s]+):", raw)
                    if match.group(1).strip() in T45_QUALITY_SOURCE_ARTIFACTS
                )
            )
            if named:
                return named

        normalized = raw.casefold()
        if any(marker in normalized for marker in ("hypotheses.md", "short assertion", " in hypotheses.md is missing:")):
            return ("ideation/hypotheses.md",)
        if any(
            marker in normalized
            for marker in (
                "research_proposal.md",
                "proposal sections",
                "prior research, gap",
                "central insight",
                "research design and evaluation",
                "expected contributions",
                "risks, limitations",
                "utd proposal",
                "ccf-a proposal",
                "hybrid proposal",
                "practical significance",
                "affected actor",
            )
        ):
            return ("ideation/proposal/research_proposal.md",)
        if any(marker in normalized for marker in ("experiment plan", "exp_plan.yaml", "experiment mapped")):
            return ("ideation/research_blueprint.yaml", "ideation/exp_plan.yaml")
        if any(
            marker in normalized
            for marker in (
                "research_blueprint.yaml",
                "challenge",
                "technical components",
                "component references",
                "design rationale",
                "simpler alternative",
                "cross-level link",
                "utd formalization",
                "ccf-a formalization",
                "evaluation.",
                "technical_risks",
                "novelty_risks",
                "data_or_experimental_risks",
            )
        ):
            return ("ideation/research_blueprint.yaml", "ideation/claim_registry.yaml")
        if any(marker in normalized for marker in ("claim_registry.yaml", "active claim", "active_claim_ids", "claim references")):
            return ("ideation/research_blueprint.yaml", "ideation/claim_registry.yaml")
        if any(
            marker in normalized
            for marker in (
                "orientation-aware review scores",
                "mandatory floor",
                "cross_level_integration",
            )
        ):
            return T45_RESEARCH_CONTENT_SOURCE_ARTIFACTS
        return T45_QUALITY_SOURCE_ARTIFACTS

    @staticmethod
    def _t45_quality_error_signature(error: str) -> str:
        """Normalize volatile validator details before comparing a repair loop."""

        normalized = " ".join(str(error or "unknown validation error").split()).casefold()
        normalized = re.sub(r"\b[0-9a-f]{12,}\b", "<digest>", normalized)
        return re.sub(r"\b\d+\b", "<number>", normalized)

    @classmethod
    def _t45_quality_repair_baseline(cls, workspace_dir: Path) -> str:
        inputs = cls._t45_quality_source_fingerprint(
            workspace_dir,
            source_paths=T45_QUALITY_REPAIR_BASELINE_ARTIFACTS,
        )
        payload = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_t45_quality_repair_ledger(workspace_dir: Path) -> dict[str, object]:
        path = workspace_dir / T45_QUALITY_REPAIR_LEDGER_REL_PATH
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            data = {}
        entries = data.get("entries") if isinstance(data, dict) else None
        return {
            "semantics": "t45_quality_repair_ledger",
            "version": 1,
            "entries": dict(entries) if isinstance(entries, dict) else {},
        }

    @staticmethod
    def _write_t45_quality_repair_ledger(workspace_dir: Path, ledger: dict[str, object]) -> None:
        path = workspace_dir / T45_QUALITY_REPAIR_LEDGER_REL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def _t45_quality_repair_entry(cls, ctx: ExecutionContext) -> tuple[dict[str, object], str, dict[str, object]]:
        ledger = cls._load_t45_quality_repair_ledger(ctx.workspace_dir)
        baseline = cls._t45_quality_repair_baseline(ctx.workspace_dir)
        key = f"{ctx.task_id}:{baseline}"
        entries = ledger["entries"]
        assert isinstance(entries, dict)
        entry = entries.get(key)
        if not isinstance(entry, dict):
            entry = {
                "task_id": ctx.task_id,
                "baseline": baseline,
                "attempt_count": 0,
            }
            entries[key] = entry
        return ledger, key, entry

    @classmethod
    def _t45_quality_repair_window_blocked(cls, ctx: ExecutionContext) -> tuple[bool, str]:
        """Prevent resume from repeating a diagnosed no-progress loop."""

        ledger, _key, entry = cls._t45_quality_repair_entry(ctx)
        _ = ledger
        scope = tuple(str(item) for item in entry.get("source_scope", []) if isinstance(item, str))
        previous_sources = entry.get("last_source_fingerprint")
        sources_unchanged = bool(scope) and isinstance(previous_sources, dict) and (
            cls._t45_quality_source_fingerprint(ctx.workspace_dir, source_paths=scope) == previous_sources
        )
        if bool(entry.get("blocked_no_source_progress")) and sources_unchanged:
            return True, "同一诊断对应的 source artifact 尚未发生变化"
        return False, ""

    @classmethod
    def _record_t45_quality_repair_attempt(cls, *, ctx: ExecutionContext, error: str) -> bool:
        """Persist T4.5 source-aware repair state across resume and restarts.

        A previous implementation kept these values only in ``ctx.extra``.
        Each ``resume`` therefore reset the no-progress observation and
        could turn an unchanged package into an unbounded sequence of LLM
        rewrites.  The ledger preserves the source-aware no-progress
        test and the total window for the same research decision.
        """

        signature = cls._t45_quality_error_signature(error)
        scope = cls._t45_quality_repair_source_scope(error)
        current = cls._t45_quality_source_fingerprint(ctx.workspace_dir, source_paths=scope)
        ledger, _key, entry = cls._t45_quality_repair_entry(ctx)
        previous_signature = str(entry.get("last_error_signature") or "")
        previous_sources = entry.get("last_source_fingerprint")
        no_source_progress = (
            signature == previous_signature
            and isinstance(previous_sources, dict)
            and previous_sources == current
        )
        entry["attempt_count"] = int(entry.get("attempt_count") or 0) + 1
        entry["last_error_signature"] = signature
        entry["last_source_fingerprint"] = current
        entry["source_scope"] = list(scope)
        entry["blocked_no_source_progress"] = no_source_progress
        attempts = int(entry["attempt_count"])
        ctx.extra["t45_quality_repair_attempt_count"] = attempts
        cls._write_t45_quality_repair_ledger(ctx.workspace_dir, ledger)
        return no_source_progress

    @staticmethod
    def _t36_quality_repair_feedback(*, ctx: ExecutionContext, error: str, base: str) -> str:
        """Turn one T3.6 rejection into a source-specific writing task."""

        common = (
            "这是 T3.6 的定向修复，不是重写整个 survey 或直接编辑派生的 `drafts/survey/survey.tex`。"
            "先读取当前错误对应的 source artifact、`drafts/survey/survey_audit.json` 与相关 section outline；"
            "保留已经通过的章节、真实引用、模板和 Evidence boundary。"
            "不能用 citation padding、无关文献、abstract-only 线索支撑强论断，不能伪造 audit、compile report 或 PDF。"
        )
        normalized = str(error or "").casefold()
        if ctx.task_id.startswith("T3.6-SEC-"):
            section_id = ctx.task_id.removeprefix("T3.6-SEC-").lower().replace("-", "_")
            return (
                base
                + common
                + f"只编辑 `drafts/survey/sections/{section_id}.tex`，并读取其 outline。"
                "使用自然学术段落而不是关键词堆砌；保留首次出现术语的清晰定义。"
                "完成后调用 `update_survey_section_state`，再 `finish_task`。"
            )
        if ctx.task_id == "T3.6-REVIEW":
            if "survey_review.md 缺少审阅维度" in error:
                return (
                    base
                    + common
                    + "只完善 `drafts/survey/survey_review.md` 的缺失审阅维度，并同步 `survey_review_actions.json` 的具体 section action。"
                    "可使用规范中文、英文或清晰的中英双语标题；每个维度必须给出当前文本中的证据、判断和实际采取的动作，"
                    "不能只添加英文关键词。随后重新 assemble、audit，并更新 action 的输入指纹。"
                )
            return (
                base
                + common
                + "读取 `survey_review.md`、`survey_review_actions.json`、audit 和相关 source section。"
                "只修正 review 指出的具体章节、引用或模板来源；再调用 assemble_survey、audit_survey_coverage 和 bind_survey_review。"
            )
        if ctx.task_id == "T3.6-ASSEMBLE":
            if "compact_theme_content_absorbed" in normalized:
                return (
                    base
                    + common
                    + "读取 `survey_state.json.shared_facts.theme_coverage_contract`。"
                    "只在 taxonomy 与 comparison source sections 中补足每个紧凑 taxonomy class 的定义/关系与比较性讨论；"
                    "可用等价专业表述，不要机械复制 class label。随后重新 assemble 和 audit。"
                )
            if "survey_language_consistency" in normalized:
                return (
                    base
                    + common
                    + "依据 `writing_template.json` 的 writing_language 检查被点名章节。"
                    "中文稿可保留必要英文技术术语、专有名词和首次中英对照，但论证句必须以中文为主；"
                    "英文稿同理。不要为了通过统计规则删除必要术语或引用键。随后重新 assemble 和 audit。"
                )
            citation_context = AgentRunner._t36_citation_repair_context(ctx.workspace_dir)
            return (
                base
                + common
                + "读取 `drafts/survey/survey_audit.md` 和 JSON，并只编辑被该 audit check 指向的 section、plan、state 或 bibliography 来源。"
                "逐条打开 audit 列出的 source_file，先核验主题、对象、方法、证据等级与当前句子是否真正匹配。"
                "FULL/PARTIAL 仅在核验后支持具体论断；ABSTRACT-ONLY 仅可用于背景、趋势、范围或证据边界。"
                "完成来源修改后必须调用 `assemble_survey` 和 `audit_survey_coverage`，不要先反复运行派生步骤。\n"
                + citation_context
            )
        return base + common

    @staticmethod
    def _t36_citation_repair_context(workspace_dir: Path) -> str:
        """Inject deterministic, evidence-bounded T3.6 repair facts into the writer.

        The audit JSON remains the complete source of truth.  This compact
        excerpt prevents a failed validation cycle from degenerating into a
        vague instruction to "add more citations", while leaving the writer
        responsible for note-level semantic verification.
        """

        audit_path = workspace_dir / "drafts" / "survey" / "survey_audit.json"
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return "未能读取结构化 citation repair guidance；先读取 `drafts/survey/survey_audit.json` 后再修复。"
        if not isinstance(audit, dict):
            return "citation repair guidance 格式无效；先读取 `drafts/survey/survey_audit.json` 后再修复。"
        repair_guidance = audit.get("repair_guidance") if isinstance(audit.get("repair_guidance"), dict) else {}
        diversity = repair_guidance.get("citation_diversity") if isinstance(repair_guidance.get("citation_diversity"), dict) else {}
        contract = diversity.get("coverage_contract") if isinstance(diversity.get("coverage_contract"), dict) else {}
        lines = ["以下是本轮审计直接注入的 citation utilization 事实："]
        if contract:
            lines.append(
                "- 范围内可追溯条目 {eligible} 个；正文实际使用 {current} 个（{actual:.0%}）。这是诊断，不是覆盖配额。".format(
                    eligible=int(contract.get("eligible_traceable_keys") or 0),
                    current=int(contract.get("cited_traceable_keys") or 0),
                    actual=float(contract.get("actual_traceable_coverage_ratio") or 0),
                )
            )
        queue = diversity.get("section_review_queue") if isinstance(diversity.get("section_review_queue"), list) else []
        if queue:
            lines.append("- 若当前 section 存在实质缺口或引用集中，可按需审阅下列候选；使用前必须打开 source_file 核验：")
            for item in queue:
                if not isinstance(item, dict):
                    continue
                candidates = item.get("candidate_notes_to_verify") if isinstance(item.get("candidate_notes_to_verify"), list) else []
                compact = "; ".join(
                    "{key} [{level}; {path}]".format(
                        key=str(candidate.get("bib_key") or "?"),
                        level=str(candidate.get("evidence_level") or "UNKNOWN"),
                        path=str(candidate.get("source_file") or "missing"),
                    )
                    for candidate in candidates[:6]
                    if isinstance(candidate, dict)
                )
                lines.append(f"  - {item.get('section_id')}: {compact or '完整队列见 audit JSON'}")
        warnings = repair_guidance.get("quality_warnings") if isinstance(repair_guidance.get("quality_warnings"), list) else []
        if warnings:
            lines.append("- 同时处理本轮可安全修复的 warning：")
            for warning in warnings:
                if isinstance(warning, dict):
                    lines.append(f"  - {warning.get('check')}: {warning.get('action')}")
        return "\n".join(lines)

    @staticmethod
    def _validation_repair_feedback(
        *,
        ctx: ExecutionContext,
        error: str,
        resumed_after_extension: bool = False,
    ) -> str:
        """Give the LLM an artifact-specific repair contract, not vague retry prose."""

        prefix = (
            "已获准继续校验修复。"
            if resumed_after_extension
            else "输出校验未通过。"
        )
        base = (
            f"{prefix} 最后错误：{error}\n"
            "只修复该错误涉及的最小 artifact，保留其它已合格字段；修复后再次调用 finish_task。"
        )
        if AgentRunner._uses_t45_quality_repair_loop(ctx):
            return AgentRunner._t45_quality_repair_feedback(error=error, base=base)
        if AgentRunner._uses_t36_quality_repair_loop(ctx):
            return AgentRunner._t36_quality_repair_feedback(
                ctx=ctx,
                error=error,
                base=base,
            )
        if (
            ctx.task_id == "SKILL_literature-evidence-scout"
            and (
                "literature-evidence-scout identifier contract failed" in error
                or "literature-evidence-scout report" in error
            )
        ):
            return (
                base
                + "只读取 `literature/skill_evidence_records.json` 和 `literature/skill_evidence_scout.md`，修复保留记录的标识符或报告一致性契约。"
                "每条保留记录只能复制本轮工具已经返回的 DOI、arXiv ID、OpenAlex ID、Semantic Scholar paperId，"
                "或这些 ID 对应的 canonical paper landing URL；不得根据标题构造检索 URL、补猜标识符，或保留任何“needs resolution”状态。"
                "报告中的 `retained_record_count`、稳定标识符记录数量、以及“all N papers”一类总数必须全部等于 JSON 数组长度；"
                "排除的 title-only lead 不能计入 retained records。"
                "若已返回数据没有可用稳定标识符，正确修复是把 records 写为 `[]`，并在报告中如实说明检索边界和未保留原因。"
                "不要继续远程检索来绕过已达到的工具预算；写回两个声明的输出后调用 finish_task。"
            )
        if ctx.task_id != "T4":
            if ctx.task_id == "T4.5":
                proposal_context = (
                    "先读取 `ideation/novelty_audit.md`、`ideation/selected/selected_candidate.json`、"
                    "`ideation/hypotheses.md`、`ideation/research_dossier.json`、`ideation/exp_plan.yaml`、"
                    "`ideation/validation_map.yaml` 与 `ideation/kill_criteria.yaml`。"
                    "Proposal 只能整合这些已落盘材料和经审计的专业解释；未知事实保持 `unknown` 或 "
                    "`proposed_not_verified`，不得把计划、常识或预期结果写成实证事实。"
                )
                if any(marker in error for marker in ("research_proposal.md", "formal H1", "missing sections")):
                    return (
                        base
                        + proposal_context
                        + "只重写 `ideation/proposal/research_proposal.md` 中缺失或过短的部分。它必须具有八个一级部分、"
                        "正式 `H1` 标题，以及不少于 6000 个字符的连贯研究方案；补足机制、研究设计、验证、贡献、条件性现实含义、"
                        "资源伦理风险、实施路线和证据边界，但不要重复粘贴其他 artifact。"
                    )
                if any(marker in error for marker in ("proposal_manifest.json", "section source", "section_source_map", "T4/T4.5 sources")):
                    return (
                        base
                        + proposal_context
                        + "只修复 `ideation/proposal/proposal_manifest.json`。使用实际存在的路径，令每个 `section_source_map` 条目"
                        "非空并列入 `traceability.source_artifacts`，保留 selected Candidate、formal hypotheses、dossier、"
                        "experiment plan、novelty audit、validation map 和 kill criteria。保持 `t5_handoff.role` 为 "
                        "`planning_context_not_results`，并保留 required baselines、claim boundaries、kill criteria 和 unknown fields。"
                    )
                if "post_novelty_formalization.json" in error:
                    return (
                        base
                        + proposal_context
                        + "只修复 `ideation/post_novelty_formalization.json` 的 artifacts 路径清单，保留已有正式产物。"
                        "它必须列出 hypotheses、research_dossier、exp_plan、contribution_hypothesis_map、validation_map、"
                        "kill_criteria、research_proposal 和 proposal_manifest，不能改写审计 verdict。"
                    )
                return base + proposal_context + "根据上述具体校验原因修复唯一受影响的 T4.5 artifact。"
            if ctx.task_id == "T3.6-ASSEMBLE":
                if "has_abstract_environment" in error:
                    return (
                        base
                        + "这是模板接口校验，不是让你把摘要环境手工塞进 section 的请求。"
                        "`drafts/survey/sections/abstract.tex` 只能保存摘要正文，所有其它 section 也不得包含 "
                        "`\\begin{abstract}`、`\\end{abstract}` 或 `\\ABSTRACT{...}`。"
                        "模板接口由 `assemble_survey` 负责：标准模板投影为 abstract 环境，INFORMS4 投影为 `\\ABSTRACT{...}`。"
                        "不要直接编辑派生的 `survey.tex`；只需保留/恢复模板中立的摘要正文后重新调用 assemble_survey 和 audit_survey_coverage。"
                    )
                citation_context = AgentRunner._t36_citation_repair_context(ctx.workspace_dir)
                return (
                    base
                    + "读取 `drafts/survey/survey_audit.md` 和 `drafts/survey/survey_audit.json`。"
                    "若失败为 `citation_diversity`，先检查是否为单一来源过度集中；coverage_contract 本身只是信息利用诊断。"
                    "section_review_queue 只提供可能的替代材料；先打开候选 source_file，逐句核验论文主题、对象、方法、证据等级和当前论断是否匹配。"
                    "FULL/PARTIAL 仅在核验后支持具体论断；ABSTRACT-ONLY 仅可用于背景、趋势、范围或证据边界。"
                    "先合并真正重复的表达，再使用确实支持已有历史、比较、边界或方法句的候选。不得用 citation padding、"
                    "无关论文、abstract-only 线索支撑强论断或新编造事实来改变覆盖数字。"
                    "只编辑受影响的 `drafts/survey/sections/*.tex`，然后调用 assemble_survey 与 audit_survey_coverage；"
                    "不要直接编辑派生的 `survey.tex`。若逐节核验后仍没有安全替代来源，写 `drafts/survey/survey_assemble_repair_plan.md`，"
                    "逐条记录拒用的 bib_key、source_file、原因、需要补检的具体主题和受影响 section，再 finish_task 以进入人工恢复决策。\n"
                    + citation_context
                )
            if ctx.task_id.startswith("T3.6-SEC-"):
                return (
                    base
                    + "这是一轮综述 prose 重写，而不是关键词替换：读取当前 section、该节 outline 和 citation pool；"
                    "保留已核验 citation 及其原有语义，不添加新事实或引用。将标签式冒号、破折号串联、\\paragraph、"
                    "以及 First/Second 的罗列骨架改写为自然衔接的完整段落。每段应推进一个论点，并用因果、对比或边界"
                    "连接相邻段落；不要为了命中 validator 的词面标签而写 'Definition:'、'Gap:' 一类句式。"
                )
            return base

        idea_match = re.search(r"idea\s+([A-Za-z][A-Za-z0-9_-]*)", error)
        idea_id = idea_match.group(1) if idea_match else "对应 idea"
        if "idea_scorecard.yaml" in error:
            details = (
                "读取 `ideation/idea_scorecard.yaml`，定位 `ideas[]` 中 "
                f"`idea.id == \"{idea_id}\"` 的记录。"
            )
            if "design_rationale" in error:
                details += (
                    "在 `idea.cdr_tuple.design_rationale` 写出模型根据该候选问题、机制、"
                    "跨论文观察所得的设计理由：解释 artifact 为什么必须采取当前结构，"
                    "不是复述实现步骤。"
                )
            elif "contribution_type" in error or "contribution_character" in error or "contribution_strength" in error:
                details += (
                    "补全该选中 idea 的 `cdr_tuple.contribution_type`、"
                    "`selection_rationale.contribution_character`（或 `idea.contribution_character`）"
                    "及 `idea.contribution_strength`，并让三者与当前 mechanism 和 design_rationale 一致。"
                )
            else:
                details += "按报错字段补全该 idea，同时保留完整 CDR、评分和 decision 记录。"
            return (
                base
                + details
                + "使用 `write_structured_file(path=\"ideation/idea_scorecard.yaml\", "
                "schema_name=\"idea_scorecard\", format=\"yaml\", data=...)` 重写通过 schema 的完整对象。"
                "研究性文字必须由你依据已落盘证据归纳；不要让确定性工具代写假设、机制、设计理由或评分依据。"
            )
        structured_targets = {
            "idea_rationales.json": ("ideation/idea_rationales.json", "idea_rationales", "json"),
            "exp_plan.yaml": ("ideation/exp_plan.yaml", "exp_plan", "yaml"),
            "gate_decisions.json": ("ideation/gate_decisions.json", "gate_decisions", "json"),
        }
        for marker, (path, schema, fmt) in structured_targets.items():
            if marker in error:
                return (
                    base
                    + f"读取 `{path}`，只补报错指出的字段，然后使用 "
                    f"`write_structured_file(path=\"{path}\", schema_name=\"{schema}\", format=\"{fmt}\", data=...)`。"
                    "不要改写无关的已合格 artifact。"
                )
        if "_candidate_directions.json" in error:
            return (
                base
                + "读取 `ideation/_candidate_directions.json`，只修复真正的结构、身份、谱系、来源边界或正式评分传输错误。"
                "保留当前 Candidate、已写出的假设和已有证据；不要为了通过 Gate1 凭空补写论文级机制、引用、实验配置或评分理由。"
                "缺少标题说明、innovation delta、basis_sources 解释、Profile Fit、legacy 七维兼容评分、评分 rationale，"
                "或只有一条 provisional hypothesis 时，应记录为可见的 enrichment / focused-evolution 工作，而不是把整轮阻断。"
                "若需要完善科研解释，明确请求一次针对该 Candidate 的 LLM enrichment；runtime 展示层不得用固定模板代写科研内容。"
            )
        return (
            base
            + "T4 的研究字段必须由你根据已有候选、论文笔记和 scorecard 归纳修复；"
            "先读取报错文件，确认 schema 和字段路径，再写回最小完整修复。"
        )

    @staticmethod
    def _t45_quality_repair_feedback(*, error: str, base: str) -> str:
        """Turn a T4.5 quality-gate rejection into a bounded source repair.

        The Formalizer must repair researcher-facing source artifacts, not
        metadata compiled deterministically from them.  Keeping the scope
        explicit prevents a short Proposal fix from overwriting the novelty
        audit or an experiment-plan failure from becoming a wholesale rewrite.
        """

        common = (
            "这是 T4.5 统一质量 Gate 的定向修复，不是重新执行 T4、重新检索论文或重写 novelty_audit.md。"
            "先读取 `ideation/orientation_config.yaml` 和报错涉及的 source artifact；保留已通过字段、Candidate、"
            "novelty audit 与其它未受影响产物。不要直接写 `proposal_manifest.json`、"
            "`post_novelty_formalization.json`、`research_dossier.json`、`contribution_hypothesis_map.yaml`、"
            "`validation_map.yaml` 或 `kill_criteria.yaml`，"
            "这些文件由 runtime 从通过验证的 source artifacts 确定性编译。"
            "若修改 researcher-facing prose，保留已定义术语的一致写法，并在受影响文档中首次展开新增的非显然缩写。"
        )
        if AgentRunner._is_t45_repairable_warning(error):
            guidance = str(error).removeprefix("T45_REPAIRABLE_WARNING:").strip()
            return (
                "以下为内部质量修订目标；这些内容不会作为用户侧 warning 展示。"
                + common
                + "只修复其中标明的 researcher-facing source artifact，保留已经通过的研究契约。"
                + "\n\n"
                + guidance
            )
        normalized = error.casefold()

        blueprint_markers = (
            "research_blueprint.yaml",
            "challenge",
            "technical components",
            "component references",
            "design rationale",
            "simpler alternative",
            "cross-level link",
            "utd formalization",
            "ccf-a formalization",
            "evaluation.",
            "technical_risks",
            "novelty_risks",
            "data_or_experimental_risks",
        )
        registry_markers = (
            "claim_registry.yaml",
            "active claim",
            "active_claim_ids",
            "claim references",
            "duplicate active claim",
        )
        plan_markers = (
            "experiment plan",
            "exp_plan.yaml",
            "experiment mapped",
        )
        hypothesis_markers = (
            "hypotheses.md",
            "research claims and hypotheses",
            "short assertion",
            " in hypotheses.md is missing:",
            "audit labels",
        )
        proposal_markers = (
            "research_proposal.md",
            "proposal ",
            "proposal sections",
            "prior research, gap",
            "central insight",
            "research design and evaluation",
            "expected contributions",
            "risks, limitations",
            "utd proposal",
            "ccf-a proposal",
            "hybrid proposal",
            "practical significance",
            "affected actor",
        )

        if "evaluation.ablations or evaluation.mechanism_tests" in normalized:
            return (
                base
                + common
                + "只读取并修复 `ideation/research_blueprint.yaml`。这项共同契约校验读取的是 "
                "`evaluation.ablations` 和 `evaluation.mechanism_tests`，不读取 `exp_plan.yaml` 来判断组件测试覆盖。"
                "对错误列出的每个 `COMPn`，在两者之一加入一个实质测试对象，带完全相同的 "
                "`component_id`（或 `component_ref`）和 `planned_test`；说明移除/改变该组件或观察其机制路径时"
                "要比较什么、用什么观察来支持或证伪。"
                "用 `write_structured_file(path=\"ideation/research_blueprint.yaml\", schema_name=\"research_blueprint\", "
                "format=\"yaml\", data=...)` 完整写回。不要仅修改 `exp_plan.yaml`、不要写正文，"
                "随后立即调用 `validate_t45_formalization_sources`；它返回 valid=true 后才可写 hypotheses 或 Proposal。"
            )
        if any(marker in normalized for marker in proposal_markers):
            central_insight_instruction = ""
            if "central insight" in normalized:
                central_insight_instruction = (
                    "在技术方案章节的第一个 `COMPn` 之前，以 `### Central Insight`、`### Core Insight`、"
                    "`### 核心洞见` 或 `### 核心洞察` 写出完整中心洞察段落；不要只添加标题。"
                )
            return (
                base
                + common
                + "只读取 blueprint、claim registry、exp plan 和当前 `ideation/proposal/research_proposal.md`，"
                "然后只修复 Proposal 中报错指向的章节。Proposal 必须以研究者可读的方式解释问题、现实动机、研究缺口、"
                "central insight、技术方案与设计理由、可证伪 claims、实验设计、baseline/ablation/robustness/mechanism 验证、"
                "预期贡献、现实中的受影响主体、风险和 fallback/kill criteria。不得以重复文本凑长度，"
                "不得把审计 verdict、T4.5、true_collision、candidate_id 等内部过程语言作为内容主体。"
                + central_insight_instruction
            )
        if any(marker in normalized for marker in plan_markers):
            return (
                base
                + common
                + "只读取并修复 `ideation/exp_plan.yaml`，必要时同步 `ideation/research_blueprint.yaml` 的 "
                "`evaluation.ablations` 或 `evaluation.mechanism_tests`。每个 active claim 必须映射到至少一个实验；"
                "每个主要技术组件必须有 ablation 或 mechanism test；保留真实 baseline、竞争解释、证伪条件和资源边界，"
                "不得把预期结果写成已观察结果。结构化文件必须用 `write_structured_file` 按原 schema 完整写回。"
            )
        if any(marker in normalized for marker in hypothesis_markers):
            return (
                base
                + common
                + "只读取 `ideation/claim_registry.yaml` 与 `ideation/hypotheses.md`，只重写受影响的 claim block。"
                "每个 active claim 块必须有与 registry 一致的 claim ID，并明确写出 Rationale、Mechanism、"
                "Expected Observation、Evaluation、Competing Explanation 和 Falsification；`### H1 [removed]` "
                "不是有效假设。补足研究论证而不是重复段落，也不要把 T4.5、Level、collision 或 candidate_id 等内部审计语言写进正文。"
            )
        if any(marker in normalized for marker in blueprint_markers) or any(
            marker in normalized for marker in registry_markers
        ):
            return (
                base
                + common
                + "读取 `ideation/research_blueprint.yaml` 与 `ideation/claim_registry.yaml`，只修复错误关联的结构化字段和交叉引用。"
                "技术挑战、组件、design rationale、active claims、风险与评测必须形成可追溯链；UTD 需要实质技术构件，"
                "CCF-A 需要完整计算方法与设计理由，Hybrid 需要从技术设计到真实世界结果的明确跨层机制。"
                "使用 `write_structured_file` 写回完整且 schema-valid 的对象；不要修改 prose，除非该错误明确指向 prose。"
            )
        if "orientation_review.json" in normalized or "orientation-aware review" in normalized or "review scores" in normalized:
            return (
                base
                + common
                + "先读取 `ideation/orientation_review.json` 中的逐项诊断，并回读它指出的 blueprint、claim registry、"
                "exp plan、hypotheses 或 Proposal source。先修复实际薄弱点，再以 `write_structured_file` 更新 review。"
                "不得只把 status 改为 accepted 或虚增 score；只有各 focus score 和 mandatory floor 已由具体、可验证的"
                "研究内容支撑时，才写 `status: accepted`。"
            )
        return (
            base
            + common
            + "读取 `ideation/research_blueprint.yaml`、`ideation/claim_registry.yaml`、`ideation/exp_plan.yaml`、"
            "`ideation/hypotheses.md`、`ideation/proposal/research_proposal.md` 和（Review 阶段）"
            "`ideation/orientation_review.json`，依据错误文本定位唯一需要修复的 source artifact。"
            "修复后重新读取写入结果，再调用 finish_task，让 runtime 重新运行全部质量 Gate。"
        )

    async def _maybe_offer_validation_retry_extension(
        self,
        *,
        ctx: ExecutionContext,
        budget: BudgetTracker,
        last_error: str,
        failures: int,
        retry_limit: int,
        used_extensions: int,
    ) -> tuple[bool, int, int]:
        """Offer a recoverable gate before pausing on validation retry exhaustion."""

        policy = self.budget_escalation_policy or {}
        if not policy.get("enabled", False):
            return False, retry_limit, used_extensions

        enabled_tasks = set(policy.get("tasks") or [])
        if enabled_tasks and ctx.task_id not in enabled_tasks:
            return False, retry_limit, used_extensions

        raw_max_extensions = policy.get("max_validation_extensions_per_run")
        if raw_max_extensions is None:
            raw_max_extensions = policy.get("max_extensions_per_run")
        if raw_max_extensions is None:
            max_extensions = None
        else:
            max_extensions = int(raw_max_extensions)
            if max_extensions < 0:
                max_extensions = None
        if max_extensions is not None and used_extensions >= max_extensions:
            return False, retry_limit, used_extensions

        delta = max(1, int(policy.get("validation_retry_increase", 2) or 2))
        existing_outputs = [
            str(path.relative_to(ctx.workspace_dir))
            for path in ctx.outputs_expected.values()
            if path.exists()
        ]
        human_started = time.time()
        try:
            result = await self.human.present_gate(
                gate_id="runtime_validation_retry_extension",
                presentation={
                    "_title": "输出校验仍未通过",
                    "_description": (
                        "当前任务已耗尽自动修复轮次。你可以增加少量校验修复轮次继续，"
                        "或暂停后人工检查 artifact 再 resume。"
                    ),
                    "task_id": ctx.task_id,
                    "run_id": ctx.run_id,
                    "failures": failures,
                    "retry_limit": retry_limit,
                    "last_error": last_error,
                    "existing_outputs": existing_outputs,
                    "suggested_extension": {
                        "validation_retry_delta": delta,
                        "new_retry_limit": retry_limit + delta,
                    },
                },
                options=[
                    {
                        "id": "extend",
                        "label": f"继续修复，并增加 {delta} 次校验机会",
                    },
                    {
                        "id": "stop",
                        "label": "暂停，稍后 resume",
                    },
                ],
            )
        except HumanInputUnavailable:
            return False, retry_limit, used_extensions
        finally:
            budget.exclude_wall_time(time.time() - human_started)

        option_id = str((result or {}).get("option_id") or "stop")
        if option_id != "extend":
            self._mark_explicit_runtime_pause(
                ctx,
                kind="validation",
                decision=option_id,
            )
            return False, retry_limit, used_extensions

        new_limit = retry_limit + delta
        self.log.info(
            "validation_retry_limit_extended",
            task_id=ctx.task_id,
            failures=failures,
            old_limit=retry_limit,
            new_limit=new_limit,
        )
        return True, new_limit, used_extensions + 1
