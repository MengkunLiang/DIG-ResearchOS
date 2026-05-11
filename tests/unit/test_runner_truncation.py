from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext
from researchos.runtime.message import Message
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import MockHumanInterface, MockLLMClient
from researchos.tools.registry import ToolRegistry


class MinimalAgent(Agent):
    def __init__(self):
        super().__init__(AgentSpec(name="truncate", model_tier="medium", tool_names=[]))

    def system_prompt(self, ctx):
        return "system"

    def initial_user_message(self, ctx):
        return "user"


def test_truncation_keeps_system_and_note(tmp_workspace):
    llm = MockLLMClient([], context_window=50)
    runner = AgentRunner(MinimalAgent(), ToolRegistry(), llm, MockHumanInterface())
    messages = [Message.system("system"), Message.user("user")]
    for idx in range(10):
        messages.append(Message.assistant(content="x" * 50, step=idx))
        messages.append(Message.user("follow-up", step=idx))
    truncated = runner._maybe_truncate(messages, llm.resolve(profile=None, tier="medium", model_override=None)[0][0])
    assert truncated[0].role.value == "system"
    assert any("已省略较早的" in (msg.content or "") for msg in truncated if msg.role.value == "user")


def test_pdf_tool_content_is_capped_before_context():
    content, metadata = AgentRunner._cap_tool_content_for_context(
        "extract_pdf_text",
        "x" * 20_000,
    )

    assert len(content) < 11_000
    assert "Tool output truncated" in content
    assert metadata == {
        "original_chars": 20_000,
        "shown_chars": 10_000,
        "reason": "tool_context_content_limit",
    }
