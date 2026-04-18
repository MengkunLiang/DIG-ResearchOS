import subprocess
import sys


def test_debug_hello_agent_mock(tmp_path):
    workspace = tmp_path / "workspace"
    result = subprocess.run(
        [sys.executable, "scripts/debug_hello_agent.py", "--mock", "--workspace", str(workspace)],
        cwd="/home/liangmengkun/ResearchOS",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "Hello, Runtime!"
    assert (workspace / "_runtime" / "traces" / "hello_debug_run.jsonl").exists()

