from researchos.runtime.budget import BudgetTracker
from researchos.runtime.errors import BudgetExceeded
from researchos.runtime.message import Message, Role, ToolCall, is_empty_assistant


def test_budget_tracker_limits():
    budget = BudgetTracker(max_steps=1, max_tokens=3, max_wall_seconds=100)
    budget.tick_step()
    budget.check()
    budget.add_tokens(2, 2, 0.1)
    try:
        budget.check()
        assert False, "Expected BudgetExceeded"
    except BudgetExceeded as exc:
        assert exc.dimension == "tokens"


def test_budget_tracker_can_exclude_human_wait_time():
    budget = BudgetTracker(max_steps=10, max_tokens=100, max_wall_seconds=10)
    budget.started_at -= 100
    budget.exclude_wall_time(95)

    budget.check()
    snapshot = budget.snapshot()
    assert snapshot["elapsed_s"] < 10
    assert snapshot["excluded_wall_s"] >= 95


def test_budget_tracker_unlimited_budget_still_records_usage():
    budget = BudgetTracker(max_steps=0, max_tokens=0, max_wall_seconds=0, unlimited_budget=True)
    budget.started_at -= 100
    budget.tick_step()
    budget.add_tokens(10, 20, 0.5)

    budget.check()
    snapshot = budget.snapshot()
    assert snapshot["unlimited_budget"] is True
    assert snapshot["steps"] == 1
    assert snapshot["tokens_total"] == 30
    assert snapshot["cost_usd"] == 0.5


def test_message_and_toolcall_openai_contract():
    tool_call = ToolCall.create("echo", {"text": "hi"})
    message = Message.assistant(tool_calls=[tool_call], step=1)
    payload = message.to_openai_dict()
    assert payload["role"] == Role.ASSISTANT.value
    assert payload["tool_calls"][0]["function"]["name"] == "echo"


def test_empty_assistant_detection():
    assert is_empty_assistant(Message.assistant())
    assert not is_empty_assistant(Message.assistant(content="hello"))
