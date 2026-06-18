from __future__ import annotations

from pathlib import Path

from researchos.runtime.progress import (
    CliProgressEmitter,
    build_tool_narrative,
    format_cli_message,
    summarize_tool_result,
)


def test_tool_narrative_summarizes_search_without_raw_json(tmp_path: Path):
    narrative = build_tool_narrative(
        task_id="T2",
        agent="scout",
        tool_name="arxiv_search",
        arguments={"query": "graph neural networks for uplift modeling", "max_results": 12},
        workspace_dir=tmp_path,
    )

    assert "扩展当前主题的候选文献" in narrative.purpose
    assert "query=graph neural networks for uplift modeling" in narrative.input_summary
    assert narrative.output_path == "literature/papers_raw.jsonl"
    assert "{" not in narrative.input_summary


def test_tool_result_summary_uses_counts_not_full_payload():
    papers = [
        {
            "title": f"Paper {idx}",
            "abstract": "long abstract " * 50,
            "url": f"https://example.test/{idx}",
        }
        for idx in range(12)
    ]
    summary, output_path = summarize_tool_result(
        tool_name="arxiv_search",
        ok=True,
        content="raw content should not be shown",
        data={"papers": papers},
        error=None,
        metadata={"auto_persist_raw": {"count": 5, "merged_count": 2, "raw_count_after": 80}},
    )

    assert summary == "返回 12 条候选，新增落盘 5 条，合并重复 2 条，papers_raw 当前 80 条"
    assert output_path == "literature/papers_raw.jsonl"
    assert "long abstract" not in summary
    assert "Paper 0" not in summary


def test_progress_emitter_quiet_keeps_only_important_messages(capsys):
    emitter = CliProgressEmitter(quiet=True)
    emitter.emit("normal")
    emitter.emit("important", important=True)

    out = capsys.readouterr().out
    assert "normal" not in out
    assert "important" in out


def test_format_cli_message_adds_spacing_before_blocks():
    assert format_cli_message("[Tool] echo 完成").startswith("\n[Tool]")
    assert format_cli_message("plain line") == "plain line"


def test_format_cli_message_keeps_streaming_progress_compact():
    text = "[Agent] Abstract sweep progress: 10/42 candidates, notes=7, metadata_only=3"
    assert format_cli_message(text) == text
    assert format_cli_message(text, previous_kind="block").startswith("\n[Agent]")


def test_progress_emitter_does_not_add_blank_between_consecutive_streams():
    messages: list[str] = []
    emitter = CliProgressEmitter(emit_fn=messages.append)
    emitter.emit("[Tool] echo 完成: ok")
    emitter.emit("[Agent] Abstract sweep progress: 10/42 candidates, notes=7, metadata_only=3")
    emitter.emit("[Agent] Abstract sweep progress: 20/42 candidates, notes=14, metadata_only=6")

    assert messages[0].startswith("\n[Tool]")
    assert messages[1].startswith("\n[Agent] Abstract sweep progress")
    assert not messages[2].startswith("\n")
