#!/usr/bin/env python3
from __future__ import annotations

import argparse

from _common import canonical_json_hash, dump_json_atomic, load_json, resolve_in_workspace, resolve_workspace, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate a deterministic protocol fingerprint and component hashes.")
    parser.add_argument("--workspace")
    parser.add_argument("--protocol", default="external_executor/report/protocol_snapshot.json")
    parser.add_argument("--output", default="external_executor/report/protocol_fingerprint.json")
    parser.add_argument("--write-back", action="store_true")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    protocol_path = resolve_in_workspace(ws, args.protocol)
    output = resolve_in_workspace(ws, args.output)
    snapshot = load_json(protocol_path)
    protocol = snapshot.get("protocol", {})
    component_hashes = {key: canonical_json_hash(value) for key, value in sorted(protocol.items())}
    fingerprint = canonical_json_hash(protocol)
    data = {
        "schema_version": "protocol_fingerprint.v1",
        "generated_at": utc_now(),
        "protocol_version": snapshot.get("protocol_version"),
        "algorithm": "sha256(canonical-json(protocol))",
        "fingerprint": fingerprint,
        "component_hashes": component_hashes,
        "protocol_ref": protocol_path.relative_to(ws).as_posix(),
    }
    dump_json_atomic(output, data)
    if args.write_back:
        snapshot["protocol_fingerprint"] = fingerprint
        snapshot["component_hashes"] = component_hashes
        dump_json_atomic(protocol_path, snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
