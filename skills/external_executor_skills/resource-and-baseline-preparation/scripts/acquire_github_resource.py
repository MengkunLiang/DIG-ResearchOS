#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from _common import (
    assert_write_allowed,
    dump_json_atomic,
    load_json,
    redact_url,
    relpath,
    resolve_workspace,
    tree_manifest,
    utc_now,
)

GIT_RESOURCE_HOSTS = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "huggingface.co",
    "modelscope.cn",
    "www.modelscope.cn",
}


def git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"GIT_ASKPASS", "SSH_ASKPASS"}}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    })
    return env


def run_git(cwd: Path, *args: str, timeout: int = 180) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True, timeout=timeout, env=git_env(),
    )
    return proc.stdout.strip()


def load_policy(workspace: Path) -> dict:
    preflight_path = workspace / "external_executor" / "report" / "resource_preflight.json"
    if not preflight_path.exists():
        raise SystemExit("external_executor/report/resource_preflight.json is required before acquisition")
    preflight = load_json(preflight_path)
    if preflight.get("status") == "blocked":
        raise SystemExit("resource preflight is blocked")
    return preflight.get("policy_snapshot", {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire one immutable public Git revision without executing content.")
    parser.add_argument("--workspace")
    parser.add_argument("--repo-url", required=True)
    parser.add_argument("--revision", required=True, help="Commit SHA or immutable tag; resolved commit is recorded")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    policy = load_policy(workspace)
    if policy.get("effective_mode") not in {"github_allowed", "github_and_reimplementation"}:
        raise SystemExit("Acquisition mode does not permit public Git acquisition")
    if not policy.get("effective_network_allowed"):
        raise SystemExit("network_allowed is false")

    url = redact_url(args.repo_url)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.strip("/"):
        raise SystemExit("Only public HTTPS Git repository URLs are accepted")
    hostname = urlparse(url).hostname or ""
    allowed_domains = {str(x).lower() for x in policy.get("allowed_domains", [])}
    if hostname.lower() not in allowed_domains:
        raise SystemExit(f"Domain {hostname} is not allowed")
    if hostname.lower() not in GIT_RESOURCE_HOSTS:
        raise SystemExit(f"Domain {hostname} is allowed for search but not supported by this Git acquisition helper")

    destination = workspace / "resources" / "Remote_acquisition" / args.candidate_id
    assert_write_allowed(workspace, destination)
    if destination.exists():
        if not args.force:
            raise SystemExit(f"Destination exists: {destination}")
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="researchos-git-home-") as temp_home:
        env = git_env()
        env["HOME"] = temp_home
        subprocess.run(["git", "init", str(destination)], check=True, capture_output=True, text=True, env=env, timeout=30)
        run_git(destination, "remote", "add", "origin", url)
        run_git(destination, "fetch", "--depth", "1", "--no-tags", "origin", args.revision, timeout=300)
        run_git(destination, "checkout", "--detach", "FETCH_HEAD", timeout=300)
        commit = run_git(destination, "rev-parse", "HEAD")
        tree = run_git(destination, "rev-parse", "HEAD^{tree}")

    manifest = tree_manifest(destination)
    provenance = {
        "schema_version": "remote_resource_acquisition.v1",
        "candidate_id": args.candidate_id,
        "created_at": utc_now(),
        "source_category": "Remote_acquisition",
        "platform_host": hostname.lower(),
        "source_url": url,
        "requested_revision": args.revision,
        "resolved_commit": commit,
        "git_tree": tree,
        "destination_path": relpath(workspace, destination),
        "manifest_sha256": manifest["manifest_sha256"],
        "submodules_initialized": False,
        "lfs_smudge_enabled": False,
        "repository_content_executed": False,
        "next_required_action": "static_review",
    }
    dump_json_atomic(destination / "RESOURCE_PROVENANCE.json", provenance)
    print(f"acquired {url}@{commit} -> {relpath(workspace, destination)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
