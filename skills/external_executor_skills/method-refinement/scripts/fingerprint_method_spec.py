#!/usr/bin/env python3
from __future__ import annotations

import argparse
from copy import deepcopy

from _common import canonical_json_hash, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, utc_now

VOLATILE_KEYS = {"generated_at", "spec_fingerprint", "snapshot_ref", "spec_content_fingerprint"}


def strip_volatile(value):
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Fingerprint and snapshot a method implementation specification.")
    parser.add_argument("--workspace")
    parser.add_argument("--spec", default="external_executor/method_implementation_spec.json")
    parser.add_argument("--output", default="external_executor/report/phase_D/method_spec_fingerprint.json")
    parser.add_argument("--snapshot-dir", default="external_executor/method_specs")
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    spec_path = resolve_in_workspace(ws, args.spec)
    spec = load_json(spec_path)
    semantic = strip_volatile(spec)
    fingerprint = canonical_json_hash(semantic)
    version = int(spec.get("spec_version") or 0)
    snapshot_name = f"method-spec-v{version:03d}-{fingerprint[:12]}.json"
    snapshot_path = resolve_in_workspace(ws, f"{args.snapshot_dir.rstrip('/')}/{snapshot_name}")

    updated = deepcopy(spec)
    updated["spec_fingerprint"] = fingerprint
    updated["snapshot_ref"] = snapshot_path.relative_to(ws).as_posix()
    if args.write_back:
        dump_json_atomic(spec_path, updated)
    dump_json_atomic(snapshot_path, updated)

    record = {
        "schema_version": "method_spec_fingerprint.v1",
        "generated_at": utc_now(),
        "spec_id": spec.get("spec_id"),
        "spec_version": version,
        "fingerprint": fingerprint,
        "canonicalization": "sorted compact JSON excluding volatile metadata",
        "spec_ref": spec_path.relative_to(ws).as_posix(),
        "snapshot_ref": snapshot_path.relative_to(ws).as_posix(),
    }
    dump_json_atomic(resolve_in_workspace(ws, args.output), record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
