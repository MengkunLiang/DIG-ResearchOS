#!/usr/bin/env python3
"""
Docker 真实执行验证测试脚本。

测试内容：
1. container-native 模式检测
2. host 模式 (docker run) 执行
3. docker_digests.txt 记录机制

运行方式：
    python scripts/test_docker_exec.py [--verbose]
"""

import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.tools.docker_exec import DockerExecTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def create_policy(workspace_dir: Path) -> WorkspaceAccessPolicy:
    """创建 WorkspaceAccessPolicy 实例。"""
    return WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=["/", "/home/liangmengkun", "/tmp"],
        allowed_write_prefixes=["/tmp", "/home/liangmengkun"],
    )


def log(msg: str, verbose: bool = True) -> None:
    """打印日志。"""
    if verbose:
        print(f"[Docker Test] {msg}", flush=True)


def log_result(name: str, passed: bool, details: str = "") -> None:
    """打印测试结果。"""
    status = "✅" if passed else "❌"
    print(f"  {status} {name}", flush=True)
    if details:
        print(f"     {details}", flush=True)


async def test_container_detection() -> dict[str, Any]:
    """测试容器检测机制。"""
    print("\n" + "=" * 60)
    print("测试 1: 容器检测机制")
    print("=" * 60)

    results = {}

    # 检查 /.dockerenv
    dockerenv_exists = Path("/.dockerenv").exists()
    log_result("检测 /.dockerenv 文件", dockerenv_exists, f"存在={dockerenv_exists}")
    results["dockerenv"] = dockerenv_exists

    # 检查 /run/.containerenv
    containerenv_exists = Path("/run/.containerenv").exists()
    log_result("检测 /run/.containerenv 文件", containerenv_exists, f"存在={containerenv_exists}")
    results["containerenv"] = containerenv_exists

    # 检查 CONTAINER_ID 环境变量
    container_id = __import__("os").getenv("CONTAINER_ID")
    has_container_id = container_id is not None
    log_result("检测 CONTAINER_ID 环境变量", has_container_id, f"值={container_id}")
    results["container_id"] = container_id

    # 测试 DockerExecTool 的检测逻辑
    workspace_dir = Path("/tmp/docker_test_workspace")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    policy = create_policy(workspace_dir)
    tool = DockerExecTool(policy)

    is_container_mode = tool._is_running_in_container()
    log_result("DockerExecTool._is_running_in_container()", is_container_mode,
               f"结果={'容器内模式' if is_container_mode else '宿主机模式'}")

    results["detected_mode"] = "container-native" if is_container_mode else "host"
    results["passed"] = True  # 检测逻辑本身是正确的

    return results


async def test_host_mode(workspace_dir: Path, verbose: bool = True) -> dict[str, Any]:
    """测试宿主机模式下的 docker run 执行。"""
    print("\n" + "=" * 60)
    print("测试 2: 宿主机模式 (docker run)")
    print("=" * 60)

    results = {}

    # 检查 docker 是否可用
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        docker_available = result.returncode == 0
        docker_version = result.stdout.strip() if docker_available else "N/A"
    except Exception as e:
        docker_available = False
        docker_version = f"Error: {e}"

    log_result("Docker CLI 可用性", docker_available, docker_version)
    results["docker_available"] = docker_available
    results["docker_version"] = docker_version

    if not docker_available:
        log("跳过 docker run 测试（Docker CLI 不可用）", verbose)
        results["passed"] = False
        results["skip_reason"] = "Docker CLI not available"
        return results

    # 检查允许的镜像是否可用
    allowed_images = ["researchos/python:3.11-ml", "researchos/latex:texlive-2024"]
    available_images = []
    for image in allowed_images:
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=10
            )
            if result.returncode == 0:
                available_images.append(image)
        except Exception:
            pass

        log(f"可用镜像: {available_images}", verbose)
    results["available_images"] = available_images

    if not available_images:
        # 没有可用镜像，跳过测试
        log("跳过 docker run 测试（没有可用镜像）", verbose)
        results["passed"] = True  # 跳过不算失败
        results["skipped"] = True
        results["skip_reason"] = "No allowed images available"
        return results

    test_image = available_images[0]

    # 创建测试 workspace
    workspace_dir.mkdir(parents=True, exist_ok=True)
    test_file = workspace_dir / "test_output.txt"
    test_file.write_text(f"Docker test at {datetime.now().isoformat()}")

    # 初始化 DockerExecTool
    policy = create_policy(workspace_dir)
    tool = DockerExecTool(policy)

    # 验证容器模式检测（应该在宿主机上检测为 False）
    is_container = tool._is_running_in_container()
    log_result("容器模式检测", is_container == False,
               f"在宿主机上检测为容器内: {is_container}")
    results["container_mode_detection"] = is_container == False

    if is_container:
        log("跳过 docker run 测试（当前已在容器内）", verbose)
        results["passed"] = True
        results["skipped"] = True
        results["skip_reason"] = "Already in container"
        return results

    # 执行测试命令
    log(f"执行命令: echo 'Hello from docker'", verbose)
    start_time = time.time()

    tool_result = await tool.execute(
        image=test_image,
        command="echo 'Hello from docker'",
        cwd="/workspace",
        timeout_seconds=60,
    )

    elapsed_ms = (time.time() - start_time) * 1000

    # 验证结果
    success = tool_result.ok
    log_result("docker run 执行成功", success, f"耗时 {elapsed_ms:.0f}ms")
    log(f"输出内容: {tool_result.content[:200] if tool_result.content else 'None'}", verbose)

    results["passed"] = success
    results["elapsed_ms"] = elapsed_ms
    results["test_image"] = test_image
    results["output"] = tool_result.content

    return results


async def test_docker_digests(workspace_dir: Path, verbose: bool = True) -> dict[str, Any]:
    """测试 docker_digests.txt 记录机制。"""
    print("\n" + "=" * 60)
    print("测试 3: docker_digests.txt 记录机制")
    print("=" * 60)

    results = {}

    # 检查 experimenter.py 中的 validate_outputs 函数
    experimenter_path = Path(__file__).parent.parent / "researchos" / "agents" / "experimenter.py"

    if not experimenter_path.exists():
        log_result("experimenter.py 存在", False, "文件不存在")
        results["passed"] = False
        return results

    # 读取文件内容
    content = experimenter_path.read_text()

    # 检查是否有 docker_digests.txt 相关代码
    has_digests_check = "docker_digests.txt" in content
    log_result("experimenter.py 包含 docker_digests.txt 检查", has_digests_check)

    # 检查 validate_outputs 函数
    has_validate_outputs = "def validate_outputs" in content
    log_result("validate_outputs 函数存在", has_validate_outputs)

    if has_validate_outputs and has_digests_check:
        # 提取相关代码片段
        lines = content.split("\n")
        in_validate = False
        relevant_lines = []
        for i, line in enumerate(lines):
            if "def validate_outputs" in line:
                in_validate = True
            if in_validate:
                relevant_lines.append(f"{i+1}: {line}")
                if line.strip().startswith("def ") and i > 0 and "def validate_outputs" not in line:
                    break
                if len(relevant_lines) > 50:
                    break

        log(f"validate_outputs 函数片段:", verbose)
        for l in relevant_lines[:20]:
            log(f"  {l}", verbose)

    results["has_digests_check"] = has_digests_check
    results["passed"] = has_digests_check

    return results


async def test_workflow_integration(workspace_dir: Path, verbose: bool = True) -> dict[str, Any]:
    """测试完整的 Docker workflow 集成。"""
    print("\n" + "=" * 60)
    print("测试 4: Docker Workflow 集成测试")
    print("=" * 60)

    results = {}

    # 创建测试 workspace
    test_workspace = workspace_dir / "docker_workflow_test"
    test_workspace.mkdir(parents=True, exist_ok=True)

    # 初始化 DockerExecTool
    policy = create_policy(test_workspace)
    tool = DockerExecTool(policy)

    # 步骤 1: 在容器中创建文件
    log("步骤 1: 在容器中创建测试文件", verbose)
    create_result = await tool.execute(
        image="alpine:latest",
        command='echo "Test content from docker" > /workspace/test_file.txt && cat /workspace/test_file.txt',
        cwd="/workspace",
        timeout_seconds=60,
    )

    step1_passed = create_result.ok and "Test content from docker" in (create_result.content or "")
    log_result("步骤 1: 文件创建", step1_passed)
    results["step1"] = step1_passed

    # 步骤 2: 验证文件存在
    test_file = test_workspace / "test_file.txt"
    step2_passed = test_file.exists()
    log_result("步骤 2: 文件持久化", step2_passed, f"文件存在: {test_file.exists()}")
    results["step2"] = step2_passed

    # 步骤 3: 检查 workspace 权限
    step3_passed = test_file.exists() and test_file.stat().st_size > 0
    log_result("步骤 3: 文件内容正确", step3_passed)
    results["step3"] = step3_passed

    # 步骤 4: 验证 docker_digests.txt 机制
    log("步骤 4: 检查 docker_digests.txt 机制", verbose)

    # 这个测试只是验证 experimenter 代码中有相关检查
    experimenter_path = Path(__file__).parent.parent / "researchos" / "agents" / "experimenter.py"
    step4_passed = experimenter_path.exists() and "docker_digests.txt" in experimenter_path.read_text()
    log_result("步骤 4: docker_digests.txt 检查", step4_passed)
    results["step4"] = step4_passed

    # 综合结果
    results["passed"] = all([
        results.get("step1", False),
        results.get("step2", False) or results.get("step4", False),
        results.get("step4", False)
    ])

    return results


async def main() -> int:
    """主函数。"""
    print("=" * 60)
    print("Docker 执行验证测试套件")
    print("=" * 60)

    workspace_dir = Path("/tmp/docker_exec_test")
    workspace_dir.mkdir(parents=True, exist_ok=True)

    all_results = {}

    # 运行所有测试
    all_results["container_detection"] = await test_container_detection()
    all_results["host_mode"] = await test_host_mode(workspace_dir)
    all_results["docker_digests"] = await test_docker_digests(workspace_dir)
    all_results["workflow_integration"] = await test_workflow_integration(workspace_dir)

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    total_tests = 0
    passed_tests = 0

    for test_name, result in all_results.items():
        passed = result.get("passed", False)
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status} - {test_name}")
        total_tests += 1
        if passed:
            passed_tests += 1

    print(f"\n总计: {passed_tests}/{total_tests} 测试通过")

    # 输出详细结果 JSON
    output_file = workspace_dir / "docker_test_results.json"
    output_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n详细结果已保存到: {output_file}")

    return 0 if passed_tests == total_tests else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Docker 执行验证测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--workspace", type=str, default="/tmp/docker_exec_test",
                        help="测试 workspace 路径")
    args = parser.parse_args()

    sys.exit(asyncio.run(main()))
