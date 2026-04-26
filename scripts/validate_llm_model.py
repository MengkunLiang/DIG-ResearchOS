#!/usr/bin/env python
from __future__ import annotations

"""独立的 LLM 模型验证脚本。

用途：
1. 在不跑整条 T2/T3 pipeline 的情况下，单独验证某个 profile/tier/model 是否可用；
2. 观察候选链路、单次延迟、超时/失败率和最终回包；
3. 复用 ResearchOS 自己的 LLMClient 路由逻辑，避免脚本和正式运行配置不一致。
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from researchos.runtime.errors import LLMProviderError
from researchos.runtime.llm_client import LLMClient
from scripts._script_env import ensure_script_llm_env


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证 ResearchOS 当前 LLM 路由是否可用。")
    parser.add_argument(
        "--model-routing",
        default=str(PROJECT_ROOT / "config" / "model_routing.yaml"),
        help="model_routing.yaml 路径",
    )
    parser.add_argument("--profile", default="default", help="要验证的 profile，默认 default")
    parser.add_argument("--tier", default="medium", help="要验证的 tier，默认 medium")
    parser.add_argument("--model", default=None, help="可选：强制覆盖模型名")
    parser.add_argument("--endpoint", default=None, help="可选：强制覆盖 endpoint")
    parser.add_argument("--max-context", type=int, default=None, help="可选：强制覆盖上下文窗口")
    parser.add_argument("--temperature", type=float, default=0.0, help="验证时使用的温度，默认 0")
    parser.add_argument("--timeout", type=int, default=45, help="每次调用的超时时间（秒）")
    parser.add_argument("--attempts", type=int, default=3, help="实际 chat 验证轮数，默认 3")
    parser.add_argument(
        "--max-retries-per-model",
        type=int,
        default=1,
        help="每个候选模型内部重试次数；验证时默认 1，便于更快暴露真实错误",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with exactly: OK",
        help="测试 prompt；默认要求模型只返回 OK",
    )
    parser.add_argument(
        "--selftest-only",
        action="store_true",
        help="只做 endpoint 最小连通性检查，不做真实 chat",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="可选：把结果额外写到 JSON 文件",
    )
    return parser.parse_args()


def _extract_text(raw: Any) -> str:
    """尽量从 LiteLLM 响应对象里取出文本，便于人眼快速判断是否真回包。"""

    try:
        choices = getattr(raw, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(str(item.get("text", "")))
                    return "".join(parts).strip()
    except Exception:
        pass
    return ""


def _print_candidate_chain(client: LLMClient, args: argparse.Namespace) -> list[tuple[str, str]]:
    """把当前将要尝试的候选模型链路打印出来。"""

    resolved = client.resolve(
        profile=args.profile,
        tier=args.tier,
        model_override=args.model,
        endpoint_override=args.endpoint,
        max_context_override=args.max_context,
    )
    rows: list[tuple[str, str]] = []
    print("\n=== Candidate Chain ===")
    for idx, (binding, endpoint) in enumerate(resolved, start=1):
        qualified = binding.qualified(endpoint)
        rows.append((qualified, endpoint.name))
        print(
            f"{idx}. model={qualified} | endpoint={endpoint.name} | "
            f"provider={endpoint.provider} | max_context={binding.max_context}"
        )
    return rows


async def _run_selftest(client: LLMClient, args: argparse.Namespace) -> dict[str, Any]:
    """执行 endpoint 级最小连通性检查。"""

    started = time.time()
    results = await client.selftest([args.profile])
    duration_ms = int((time.time() - started) * 1000)
    print("\n=== Endpoint Selftest ===")
    for endpoint_name, item in results.items():
        status = "OK" if item.get("ok") else "FAIL"
        print(
            f"- {endpoint_name}: {status} | latency={item.get('latency_ms', 0)}ms | "
            f"error={item.get('error')}"
        )
    return {"duration_ms": duration_ms, "results": results}


async def _run_chat_attempts(client: LLMClient, args: argparse.Namespace) -> dict[str, Any]:
    """执行真实 chat 验证，并统计成功率与失败原因。"""

    messages = [
        {
            "role": "system",
            # 这里刻意要求极简输出，减少验证阶段的 token 干扰。
            "content": "You are a connectivity validation probe. Keep answers extremely short.",
        },
        {"role": "user", "content": args.prompt},
    ]

    attempts: list[dict[str, Any]] = []
    print("\n=== Chat Validation ===")
    for idx in range(1, args.attempts + 1):
        started = time.time()
        try:
            response = await client.chat(
                messages=messages,
                tools=None,
                temperature=args.temperature,
                tier=args.tier,
                profile=args.profile,
                model_override=args.model,
                endpoint_override=args.endpoint,
                max_context_override=args.max_context,
                timeout=args.timeout,
                max_retries_per_model=args.max_retries_per_model,
                retry_base_delay=1.0,
            )
            preview = _extract_text(response.raw)[:160]
            item = {
                "attempt": idx,
                "ok": True,
                "duration_ms": int((time.time() - started) * 1000),
                "model_used": response.model_used,
                "endpoint_used": response.endpoint_used,
                "tokens_in": response.tokens_in,
                "tokens_out": response.tokens_out,
                "cost_usd": response.cost_usd,
                "preview": preview,
            }
            print(
                f"- attempt {idx}: OK | duration={item['duration_ms']}ms | "
                f"model={item['model_used']} | endpoint={item['endpoint_used']} | "
                f"tokens={item['tokens_in']} in / {item['tokens_out']} out | preview={preview!r}"
            )
        except Exception as exc:
            item = {
                "attempt": idx,
                "ok": False,
                "duration_ms": int((time.time() - started) * 1000),
                "error_type": exc.__class__.__name__,
                "error": str(exc),
            }
            print(
                f"- attempt {idx}: FAIL | duration={item['duration_ms']}ms | "
                f"{item['error_type']}: {item['error']}"
            )
        attempts.append(item)

    success_count = sum(1 for item in attempts if item["ok"])
    print("\n=== Summary ===")
    print(f"success={success_count}/{len(attempts)}")
    if success_count:
        durations = [item["duration_ms"] for item in attempts if item["ok"]]
        print(f"min/avg/max latency={min(durations)}/{sum(durations)//len(durations)}/{max(durations)} ms")
    else:
        print("no successful chat response")
    return {"attempts": attempts, "success_count": success_count, "total": len(attempts)}


async def _async_main(args: argparse.Namespace) -> int:
    ensure_script_llm_env(PROJECT_ROOT)
    client = LLMClient(Path(args.model_routing).resolve())

    results: dict[str, Any] = {
        "profile": args.profile,
        "tier": args.tier,
        "model_override": args.model,
        "endpoint_override": args.endpoint,
        "timeout": args.timeout,
        "attempts": args.attempts,
        "candidate_chain": _print_candidate_chain(client, args),
    }

    results["selftest"] = await _run_selftest(client, args)
    if not args.selftest_only:
        results["chat_validation"] = await _run_chat_attempts(client, args)

    if args.json_out:
        output_path = Path(args.json_out).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON result written to: {output_path}")

    chat_result = results.get("chat_validation")
    if args.selftest_only:
        return 0 if all(item.get("ok") for item in results["selftest"]["results"].values()) else 2
    if not chat_result or chat_result["success_count"] == 0:
        return 2
    if chat_result["success_count"] < chat_result["total"]:
        return 1
    return 0


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_async_main(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except LLMProviderError as exc:
        message = str(exc)
        if "litellm is not installed" in message:
            print(
                "\nLLM validation failed: litellm is not installed. "
                "Please activate the ResearchOS environment or run "
                "`pip install -r requirements.txt` first."
            )
        else:
            print(f"\nLLM validation failed: {message}")
        return 2
    except Exception as exc:
        print(f"\nunexpected error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
