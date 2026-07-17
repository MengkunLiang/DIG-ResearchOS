#!/usr/bin/env python3
"""Capture a secret-safe, deterministic execution environment snapshot."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from _common import atomic_write_json, canonical_sha256, now_utc, require_allowed, require_under_workspace_path, resolve_in_workspace, workspace_root


def _memory_bytes() -> int | None:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, ValueError, OSError):
        return None


def _accelerators() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    nvidia_root = Path("/proc/driver/nvidia/gpus")
    if nvidia_root.is_dir():
        for gpu in sorted(nvidia_root.iterdir()):
            info: dict[str, Any] = {"vendor": "nvidia", "id": gpu.name}
            info_file = gpu / "information"
            if info_file.is_file():
                for line in info_file.read_text(encoding="utf-8", errors="replace").splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        if key.strip() in {"Model", "GPU UUID"}:
                            info[key.strip().lower().replace(" ", "_")] = value.strip()
            result.append(info)
    return result


def snapshot() -> dict[str, Any]:
    packages = sorted(
        ({"name": dist.metadata.get("Name", dist.name), "version": dist.version} for dist in importlib.metadata.distributions()),
        key=lambda item: (str(item["name"]).lower(), str(item["version"])),
    )
    payload = {
        "schema_version": "external_executor_environment.v1",
        "captured_at": now_utc(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "executable": sys.executable,
        },
        "hardware": {
            "cpu_count": os.cpu_count(),
            "memory_bytes": _memory_bytes(),
            "accelerators": _accelerators(),
        },
        "packages": packages,
    }
    fingerprint_basis = {key: value for key, value in payload.items() if key != "captured_at"}
    payload["environment_fingerprint"] = canonical_sha256(fingerprint_basis)
    payload["hardware"]["hardware_fingerprint"] = canonical_sha256(payload["hardware"])
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        root = workspace_root(args.workspace)
        output = resolve_in_workspace(root, args.output)
        require_allowed(root, output)
        require_under_workspace_path(root, output, "external_executor/raw_results", label="environment output")
        atomic_write_json(output, snapshot())
        print(json.dumps({"output": output.relative_to(root).as_posix(), "status": "complete"}, sort_keys=True))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
