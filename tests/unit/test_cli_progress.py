from __future__ import annotations

from pathlib import Path

from researchos.runtime.progress import (
    CliProgressEmitter,
    build_tool_narrative,
    describe_output_artifact,
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


def test_log_scout_progress_skipped_result_is_not_reported_as_failure():
    summary, output_path = summarize_tool_result(
        tool_name="log_scout_progress",
        ok=True,
        content="进度记录已跳过",
        data={"skipped": True, "reason": "search_result 进度缺少非空 query、source 或显式 count"},
        error=None,
    )

    assert summary == "Scout 进度记录已跳过：search_result 进度缺少非空 query、source 或显式 count"
    assert output_path == "literature/temp/scout_progress.md"


def test_save_paper_note_result_names_the_paper_without_raw_payload():
    summary, output_path = summarize_tool_result(
        tool_name="save_paper_note",
        ok=True,
        content="raw note body should not be shown",
        data={
            "path": "literature/paper_notes/W123.md",
            "queue_rank": 9,
            "original_queue_rank": 12,
            "paper_title": "Causal-Invariant Cross-Domain Out-of-Distribution Recommendation",
            "paper_year": 2025,
            "paper_venue": "TestConf",
            "note_status": "FULL-TEXT",
            "status": "complete",
            "progress": "9/15 target notes complete",
        },
        error=None,
    )

    assert "Causal-Invariant Cross-Domain Out-of-Distribution Recommendation" in summary
    assert "#12" in summary
    assert "2025" in summary
    assert "TestConf" in summary
    assert "状态 FULL-TEXT" in summary
    assert "精读 9/15 篇" in summary
    assert "target notes complete" not in summary
    assert "raw note body" not in summary
    assert output_path == "literature/paper_notes/W123.md"


def test_save_paper_note_failure_keeps_paper_identity():
    summary, output_path = summarize_tool_result(
        tool_name="save_paper_note",
        ok=False,
        content="missing required section",
        data={
            "path": "literature/paper_notes/W123.md",
            "queue_rank": 3,
            "paper_title": "A Paper That Needs Repair",
            "status": "incomplete",
            "progress": "2/5 target notes complete",
        },
        error="note_incomplete",
    )

    assert "A Paper That Needs Repair" in summary
    assert "论文阅读笔记已保存但需修补" in summary
    assert "精读 2/5 篇" in summary
    assert "问题：note_incomplete" in summary
    assert output_path == "literature/paper_notes/W123.md"


def test_progress_emitter_quiet_keeps_only_important_messages(capsys):
    emitter = CliProgressEmitter(quiet=True)
    emitter.emit("normal")
    emitter.emit("important", important=True)

    out = capsys.readouterr().out
    assert "normal" not in out
    assert "important" in out


def test_agent_done_explains_actual_artifacts_and_next_step():
    messages: list[str] = []
    emitter = CliProgressEmitter(emit_fn=messages.append)

    emitter.agent_done(
        task_id="T2",
        agent="scout",
        ok=True,
        stop_reason="finished",
        summary="完成检索、去重和阅读队列构建。",
        artifacts=[
            "literature/search_log.md",
            "literature/deep_read_queue.jsonl",
            "literature/papers_backlog.jsonl",
        ],
        next_step=None,
    )

    rendered = "\n".join(messages)
    assert "阶段总结" in rendered
    assert "完成了什么：完成检索、去重和阅读队列构建。" in rendered
    assert "literature/search_log.md：检索、去重、回填、候选切分和覆盖缺口的审计记录。" in rendered
    assert "literature/deep_read_queue.jsonl：T3 的结构化精读优先队列与排序依据。" in rendered
    assert "literature/papers_backlog.jsonl：未进入当前 active pool 的可追溯候选" in rendered
    assert "下一步：进入 T2 文献覆盖 Gate" in rendered


def test_output_artifact_description_covers_writing_evidence_supplement():
    assert "章节级证据补充" in describe_output_artifact(
        "drafts/section_outlines/introduction_evidence_supplement.md",
        task_id="T8-SEC-INTRODUCTION",
    )


def test_format_cli_message_adds_spacing_before_blocks():
    assert format_cli_message("[Tool] echo 完成").startswith("\n[Tool]")
    assert format_cli_message("plain line") == "plain line"


def test_format_cli_message_drops_generic_next_step_line():
    assert format_cli_message("下一步：状态机将根据当前节点配置进入下一阶段") == ""
    block = format_cli_message("[Runtime] 完成\n下一步：状态机将根据当前节点配置进入下一阶段")
    assert "下一步" not in block
    assert "[Runtime] 完成" in block


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


def test_t4_progress_aggregates_routine_reads_and_emits_auditable_trace():
    messages: list[str] = []
    emitter = CliProgressEmitter(emit_fn=messages.append)
    emitter.agent_start(
        task_id="T4",
        agent="ideation",
        phase="-",
        objective="生成候选方向",
        inputs=[],
        expected_outputs=[],
        expected_artifacts="Gate1 候选池",
        llm_tier="heavy",
        step_limit="20",
    )
    narrative = build_tool_narrative(
        task_id="T4",
        agent="ideation",
        tool_name="read_file",
        arguments={"path": "literature/synthesis.md"},
    )
    emitter.tool_call(agent="ideation", tool_name="read_file", narrative=narrative)
    emitter.tool_result(
        agent="ideation",
        tool_name="read_file",
        ok=True,
        result_summary="已读取完整文件",
    )
    write_narrative = build_tool_narrative(
        task_id="T4",
        agent="ideation",
        tool_name="write_file",
        arguments={"path": "ideation/_pass1_forward_candidates.json"},
    )
    emitter.tool_call(agent="ideation", tool_name="write_file", narrative=write_narrative)
    emitter.tool_result(
        agent="ideation",
        tool_name="write_file",
        ok=True,
        result_summary="文件写入完成",
        output_path="ideation/_pass1_forward_candidates.json",
    )
    emitter.llm_request_started(task_id="T4", step=2)

    combined = "\n".join(messages)
    assert "执行轨迹：准备证据包 -> 生成候选 -> 接地复核 -> 写入 Gate1 卡片 -> 等待人工选择" in combined
    assert "正在核验上游证据和文献笔记 section" in combined
    assert "[T4 Gate1] 1/6 写入中 · Pass 1 原始候选池" in combined
    assert "[T4 Gate1] 1/6 已保存 · Pass 1 原始候选池" in combined
    assert "[运行中] T4 · step 2 | 模型请求已提交" in combined
    assert "[Tool] read_file 完成" not in combined
    assert "[Tool] write_file 完成" not in combined
