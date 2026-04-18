from __future__ import annotations


class ResearchOSError(Exception):
    """Root exception for all ResearchOS errors."""


class RuntimeError_(ResearchOSError):
    """Fatal runtime error that should abort orchestration."""


class ConfigurationError(RuntimeError_):
    """Configuration file is missing or malformed."""


class LLMProviderError(RuntimeError_):
    """All configured LLM candidates failed."""


class WorkspaceError(RuntimeError_):
    """Workspace directory or runtime layout is invalid."""


class AgentError(ResearchOSError):
    """Agent-scoped failure."""


class BudgetExceeded(AgentError):
    def __init__(self, dimension: str, limit: float, used: float):
        self.dimension = dimension
        self.limit = limit
        self.used = used
        super().__init__(f"Budget exceeded on {dimension}: {used}/{limit}")


class MaxStepsReached(AgentError):
    """Agent max step count reached."""


class OutputValidationFailed(AgentError):
    def __init__(self, agent_name: str, reasons: list[str]):
        self.agent_name = agent_name
        self.reasons = reasons
        detail = reasons[-1] if reasons else ""
        super().__init__(
            f"Agent {agent_name} failed validation {len(reasons)} times: {detail}"
        )


class EmptyReplyStorm(AgentError):
    """LLM produced too many consecutive empty responses."""


class HumanRejected(AgentError):
    """Human rejected a gate or approval."""


class ToolError(ResearchOSError):
    """Base class for tool-level failures."""


class ToolParameterError(ToolError):
    """Tool parameters failed validation."""


class ToolTimeout(ToolError):
    def __init__(self, tool_name: str, timeout_s: float):
        self.tool_name = tool_name
        self.timeout_s = timeout_s
        super().__init__(f"Tool {tool_name} timed out after {timeout_s}s")


class ToolAccessDenied(ToolError):
    """Tool attempted to access a disallowed path."""


class ToolRuntimeError(ToolError):
    def __init__(self, tool_name: str, underlying: Exception):
        self.tool_name = tool_name
        self.underlying = underlying
        super().__init__(f"Tool {tool_name} crashed: {underlying!r}")

