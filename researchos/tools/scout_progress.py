"""Scout Agent 进度日志工具。

在每次工具调用后自动记录中间进度，便于用户了解检索状态。
日志写入 `literature/temp/scout_progress.md`，工具层追加，用户无需手动调用。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


class ScoutProgressLogger:
    """Scout Agent 进度日志写入器。"""

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = Path(workspace_dir)
        self.log_dir = self.workspace_dir / "literature" / "temp"
        self.log_file = self.log_dir / "scout_progress.md"

    def _ensure_log_dir(self) -> None:
        """确保日志目录存在。"""
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp(self) -> str:
        """返回当前时间戳。"""
        return datetime.now().strftime("%H:%M:%S")

    def log_step(self, step: str, detail: str) -> None:
        """记录一个步骤。

        Args:
            step: 步骤名称，如 "expand_queries"、"search"、"dedup"
            detail: 步骤详情
        """
        self._ensure_log_dir()
        timestamp = self._timestamp()
        entry = f"\n[{timestamp}] **{step}**: {detail}"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(entry)

    def log_init(self, total_queries: int | None = None, topic: str | None = None) -> None:
        """记录初始化。"""
        self._ensure_log_dir()
        timestamp = self._timestamp()
        lines = [
            f"\n[{timestamp}] **START** Scout Agent 开始执行",
            f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        if topic:
            lines.append(f"  主题: {topic}")
        if total_queries:
            lines.append(f"  检索式数量: {total_queries}")
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def log_queries_expanded(self, queries: list[str]) -> None:
        """记录扩展后的检索式。"""
        self.log_step("expand_queries", f"生成 {len(queries)} 条检索式")
        for i, q in enumerate(queries, 1):
            self.log_step("  query", f"  [{i}] {q}")

    def log_search_start(self, query: str, source: str | None = None) -> None:
        """记录开始检索。"""
        src = f" (数据源: {source})" if source else ""
        self.log_step("search", f"开始检索: {query}{src}")

    def log_search_result(self, query: str, count: int, source: str) -> None:
        """记录检索结果。"""
        self.log_step(
            "search_result",
            f"检索 '{query}' → {count} 篇 (来源: {source})",
        )

    def log_search_error(self, query: str, error: str) -> None:
        """记录检索错误。"""
        self.log_step("search_error", f"检索 '{query}' 失败: {error}")

    def log_dedup(self, before: int, after: int, method: str = "doi+title") -> None:
        """记录去重结果。"""
        rate = f"{(1 - after / max(1, before)) * 100:.1f}%"
        self.log_step(
            "dedup",
            f"去重: {before} 篇 → {after} 篇 ({method}, 去除 {rate})",
        )

    def log_score(self, count: int) -> None:
        """记录评分结果。"""
        self.log_step("score", f"完成 {count} 篇论文评分")

    def log_write_file(self, filename: str, count: int | None = None) -> None:
        """记录文件写入。"""
        detail = f"写入 {filename}"
        if count is not None:
            detail += f" ({count} 条)"
        self.log_step("write_file", detail)

    def log_finish(self, papers_raw: int, papers_dedup: int) -> None:
        """记录完成。

        Deprecated for live Scout progress. Runtime validation should decide
        task success. Kept for compatibility with old artifacts.
        """
        self._ensure_log_dir()
        timestamp = self._timestamp()
        lines = [
            f"\n[{timestamp}] **FINISH** Scout Agent 执行完成",
            f"  papers_raw: {papers_raw} 篇",
            f"  papers_dedup: {papers_dedup} 篇",
            f"  结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def log_finish_requested(self, papers_raw: int, papers_dedup: int, detail: str = "") -> None:
        """记录 Agent 请求收尾，不把它伪装成任务成功。"""
        self._ensure_log_dir()
        timestamp = self._timestamp()
        lines = [
            f"\n[{timestamp}] **FINISH_REQUESTED** Scout Agent 请求 runtime 收尾",
            f"  papers_raw_current: {papers_raw} 篇",
            f"  papers_dedup_current: {papers_dedup} 篇",
            "  状态: waiting_for_runtime_finalize_and_validation",
        ]
        if detail:
            lines.append(f"  说明: {detail}")
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def read_progress(self) -> str | None:
        """读取当前进度日志内容。"""
        if self.log_file.exists():
            return self.log_file.read_text(encoding="utf-8")
        return None
