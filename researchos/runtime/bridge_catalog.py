from __future__ import annotations

"""Read and migrate Cross-domain catalogs without treating them as paper notes.

Cross-domain retrieval is deliberately distinct from reading. New workspaces
store B1/B2/... context and retrieved metadata under
``literature/cross_domain_catalogs/<bridge-id>/``. Actual Bridge paper notes
remain under ``literature/bridge_notes/`` and retain their normal evidence
permissions. Older workspaces placed catalog JSON beside those notes; this
module migrates that data non-destructively and keeps a read fallback so a
resume can never lose an already-retrieved Cross-domain track.
"""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from ..literature_identity import is_paper_note_file


BRIDGE_NOTE_ROOT_REL_PATH = "literature/bridge_notes"
CROSS_DOMAIN_CATALOG_ROOT_REL_PATH = "literature/cross_domain_catalogs"
BRIDGE_PLAN_REL_PATH = "literature/bridge_domain_plan.json"
CROSS_DOMAIN_CATALOG_INDEX_REL_PATH = "literature/cross_domain_catalogs/index.json"

# Historical layouts used ``bridge_notes`` for two different concepts. Keep
# these constants only for compatibility reads and migration, never for new
# writes. Public aliases preserve import compatibility for out-of-tree code.
LEGACY_BRIDGE_CATALOG_ROOT_REL_PATH = BRIDGE_NOTE_ROOT_REL_PATH
LEGACY_BRIDGE_CATALOG_INDEX_REL_PATH = "literature/bridge_notes/index.json"
BRIDGE_ROOT_REL_PATH = CROSS_DOMAIN_CATALOG_ROOT_REL_PATH
BRIDGE_INDEX_REL_PATH = CROSS_DOMAIN_CATALOG_INDEX_REL_PATH


def bridge_id_key(value: object) -> str:
    """Return a stable lookup key while preserving the user-facing bridge ID.

    Older T2 traces have used both ``B1`` and ``b1``. The plan is authoritative
    for display, but association must not disappear merely because a provider or
    a legacy workspace changed capitalization or surrounding whitespace.
    """

    return re.sub(r"\s+", "", str(value or "")).casefold()


def iter_bridge_catalog_paths(workspace_dir: Path) -> list[Path]:
    """Return canonical catalog files, with legacy-only tracks as a fallback.

    The canonical root wins per bridge ID. That matters after migration: the
    old files intentionally remain in place for auditability, but must not
    duplicate the same abstract-only atoms or prompt context.
    """

    workspace = Path(workspace_dir)
    selected: list[Path] = []
    seen_bridge_keys: set[str] = set()
    for root_rel in (CROSS_DOMAIN_CATALOG_ROOT_REL_PATH, LEGACY_BRIDGE_CATALOG_ROOT_REL_PATH):
        root = workspace / root_rel
        if not root.is_dir():
            continue
        for catalog_path in sorted(root.glob("*/paper_catalog.json")):
            catalog, _ = _read_json_object(catalog_path)
            # Invalid or incomplete canonical JSON should remain available for
            # repair, but must not mask a valid legacy fallback or create
            # synthetic context merely from its directory name.
            if (
                not isinstance(catalog, dict)
                or not bridge_id_key(catalog.get("bridge_id"))
                or not isinstance(catalog.get("records"), list)
            ):
                continue
            bridge_key = bridge_id_key(catalog.get("bridge_id"))
            if not bridge_key or bridge_key in seen_bridge_keys:
                continue
            seen_bridge_keys.add(bridge_key)
            selected.append(catalog_path)
    return selected


def migrate_legacy_bridge_catalogs(workspace_dir: Path) -> dict[str, Any]:
    """Copy old catalog projections into the dedicated root without deletion.

    The migration copies only catalog/context files. It never moves Markdown
    paper notes out of ``bridge_notes`` and never overwrites a newer canonical
    catalog. Conflict paths remain available through the legacy read fallback.
    """

    workspace = Path(workspace_dir).resolve()
    legacy_root = workspace / LEGACY_BRIDGE_CATALOG_ROOT_REL_PATH
    canonical_root = workspace / CROSS_DOMAIN_CATALOG_ROOT_REL_PATH
    result: dict[str, Any] = {
        "canonical_root": CROSS_DOMAIN_CATALOG_ROOT_REL_PATH,
        "legacy_root": LEGACY_BRIDGE_CATALOG_ROOT_REL_PATH,
        "copied": [],
        "already_present": [],
        "conflicts": [],
        "index_migrated": False,
    }
    if not legacy_root.is_dir():
        return result

    canonical_root.mkdir(parents=True, exist_ok=True)
    catalog_names = ("bridge_context.json", "paper_catalog.json", "_bridge_context.md")
    for legacy_dir in sorted(path for path in legacy_root.iterdir() if path.is_dir()):
        if not any((legacy_dir / name).is_file() for name in catalog_names):
            continue
        canonical_dir = canonical_root / legacy_dir.name
        for name in catalog_names:
            source = legacy_dir / name
            if not source.is_file():
                continue
            destination = canonical_dir / name
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                result["copied"].append(destination.relative_to(workspace).as_posix())
            elif _same_file_content(source, destination):
                result["already_present"].append(destination.relative_to(workspace).as_posix())
            else:
                result["conflicts"].append(
                    {
                        "legacy": source.relative_to(workspace).as_posix(),
                        "canonical": destination.relative_to(workspace).as_posix(),
                        "reason": "canonical_file_differs",
                    }
                )

    legacy_index = legacy_root / "index.json"
    canonical_index = canonical_root / "index.json"
    if legacy_index.is_file() and not canonical_index.exists():
        payload = _read_json(legacy_index)
        if payload:
            _atomic_write_json(canonical_index, _migrate_index_paths(payload))
        else:
            shutil.copy2(legacy_index, canonical_index)
        result["index_migrated"] = True
        result["copied"].append(canonical_index.relative_to(workspace).as_posix())
    elif canonical_index.exists():
        result["already_present"].append(canonical_index.relative_to(workspace).as_posix())
    if result["copied"] or result["conflicts"]:
        report_path = canonical_root / "_legacy_migration_report.json"
        _atomic_write_json(
            report_path,
            {
                "schema_version": "1.0.0",
                "semantics": "cross_domain_catalog_non_destructive_legacy_migration",
                **result,
            },
        )
        result["report_path"] = report_path.relative_to(workspace).as_posix()
    return result


def validate_active_bridge_catalogs(workspace_dir: Path) -> tuple[bool, str | None]:
    """Validate the *information track* for an explicitly configured bridge.

    This is deliberately narrower than a reading validator.  A confirmed
    Cross-domain lane needs a durable context/catalog projection so later
    synthesis, survey, ideation, and executor Skills can discover it.  It
    does **not** need a deep-reading note, a non-empty paper list, or a
    candidate that survives the mainline queue.  An empty retrieved list is a
    useful, explicit outcome and still leaves the bridge name, rationale, and
    planned queries available to the LLM as bounded creative context.

    Legacy catalog files are migrated before the check, so an older workspace
    can resume without losing a previously retrieved bridge track.
    """

    workspace = Path(workspace_dir).resolve()
    plan_path = workspace / BRIDGE_PLAN_REL_PATH
    if not plan_path.exists() or plan_path.stat().st_size <= 0:
        # Bridge-free and pre-bridge-plan workspaces are both supported.  T1
        # normally writes an explicit empty plan, but T2/T3 direct debugging
        # and older projects must remain resumable.
        return True, None
    plan = _read_json(plan_path)
    if not plan:
        return False, "bridge_domain_plan.json exists but is not readable JSON"
    if str(plan.get("source") or "").strip().casefold() == "none":
        return True, None
    domains = plan.get("bridge_domains") if isinstance(plan.get("bridge_domains"), list) else []
    configured = [
        item
        for item in domains
        if isinstance(item, dict) and bridge_id_key(item.get("bridge_id"))
    ]
    if not configured:
        return True, None

    migrate_legacy_bridge_catalogs(workspace)
    index_path = workspace / CROSS_DOMAIN_CATALOG_INDEX_REL_PATH
    index = _read_json(index_path)
    if not index or not isinstance(index.get("bridges"), list):
        return False, (
            "configured Cross-domain bridge lanes have no readable "
            "literature/cross_domain_catalogs/index.json; refresh the bridge catalog, "
            "but do not require deep-reading notes"
        )
    indexed: dict[str, dict[str, Any]] = {}
    for item in index.get("bridges") or []:
        if not isinstance(item, dict):
            continue
        key = bridge_id_key(item.get("bridge_id"))
        if key:
            indexed[key] = item
    missing = [
        str(item.get("bridge_id") or "").strip()
        for item in configured
        if bridge_id_key(item.get("bridge_id")) not in indexed
    ]
    if missing:
        return False, (
            "cross-domain catalog index is missing configured bridge lanes: "
            + ", ".join(missing)
            + "; a zero-record catalog is valid, but the contextual lane must remain visible"
        )

    broken_paths: list[str] = []
    for bridge in configured:
        bridge_id = str(bridge.get("bridge_id") or "").strip()
        item = indexed.get(bridge_id_key(bridge_id)) or {}
        for field in ("context_path", "catalog_path"):
            relative = str(item.get(field) or "").strip()
            if not relative:
                broken_paths.append(f"{bridge_id}:{field}")
                continue
            candidate = (workspace / relative).resolve()
            try:
                candidate.relative_to(workspace)
            except ValueError:
                broken_paths.append(f"{bridge_id}:{field}:outside_workspace")
                continue
            if not candidate.is_file():
                broken_paths.append(f"{bridge_id}:{field}:{relative}")
                continue
            payload, payload_error = _read_json_object(candidate)
            if payload is None:
                broken_paths.append(f"{bridge_id}:{field}:{payload_error or 'invalid_json'}")
                continue
            if bridge_id_key(payload.get("bridge_id")) != bridge_id_key(bridge_id):
                broken_paths.append(f"{bridge_id}:{field}:bridge_id_mismatch")
                continue
            if field == "catalog_path" and not isinstance(payload.get("records"), list):
                broken_paths.append(f"{bridge_id}:{field}:records_must_be_list")
    if broken_paths:
        return False, "cross-domain catalog contains missing/broken context paths: " + "; ".join(broken_paths)
    return True, None


def cross_domain_catalogs_are_plan_only(workspace_dir: Path) -> bool:
    """Return whether the canonical catalog root adds no material to a plan.

    T4 intentionally ignores a materialized zero-record catalog because it is
    a deterministic projection of ``bridge_domain_plan.json``.  That
    normalization is safe only for a structurally valid projection.  A broken
    JSON file, an incomplete track, or an unknown payload under the catalog
    root must retain the regular directory fingerprint so a recovery path
    cannot silently reuse a Population against corrupted inputs.
    """

    workspace = Path(workspace_dir)
    root = workspace / CROSS_DOMAIN_CATALOG_ROOT_REL_PATH
    if not root.exists():
        return True
    if not root.is_dir():
        return False

    index_path = root / "index.json"
    track_catalogs = sorted(root.glob("*/paper_catalog.json"))
    track_contexts = sorted(root.glob("*/bridge_context.json"))
    indexed_by_bridge: dict[str, dict[str, Any]] = {}
    if track_catalogs or track_contexts:
        if not index_path.is_file():
            return False
        index, _ = _read_json_object(index_path)
        bridges = index.get("bridges") if isinstance(index, dict) else None
        if not isinstance(bridges, list) or any(
            not isinstance(item, dict) or not bridge_id_key(item.get("bridge_id"))
            for item in bridges
        ):
            return False
        indexed_by_bridge = {
            bridge_id_key(item.get("bridge_id")): item
            for item in bridges
            if isinstance(item, dict)
        }
    elif index_path.exists():
        index, _ = _read_json_object(index_path)
        if not isinstance(index, dict) or not isinstance(index.get("bridges"), list):
            return False

    known_files = {"index.json", "bridge_context.json", "paper_catalog.json", "_bridge_context.md", "_DIR_GUIDE.md"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name not in known_files and not path.name.startswith("_"):
            return False

    contexts_by_parent = {path.parent for path in track_contexts}
    for catalog_path in track_catalogs:
        catalog, _ = _read_json_object(catalog_path)
        if (
            not isinstance(catalog, dict)
            or not bridge_id_key(catalog.get("bridge_id"))
            or not isinstance(catalog.get("records"), list)
            or catalog_path.parent not in contexts_by_parent
        ):
            return False
        context, _ = _read_json_object(catalog_path.parent / "bridge_context.json")
        if (
            not isinstance(context, dict)
            or bridge_id_key(context.get("bridge_id")) != bridge_id_key(catalog.get("bridge_id"))
        ):
            return False
        index_entry = indexed_by_bridge.get(bridge_id_key(catalog.get("bridge_id")))
        if not isinstance(index_entry, dict):
            return False
        if not _index_path_matches_catalog_file(
            workspace, index_entry.get("context_path"), catalog_path.parent / "bridge_context.json"
        ):
            return False
        if not _index_path_matches_catalog_file(workspace, index_entry.get("catalog_path"), catalog_path):
            return False
        if catalog.get("records"):
            return False
    return not any(path.parent not in {item.parent for item in track_catalogs} for path in track_contexts)


def resolve_catalog_canonical_note_path(workspace_dir: Path, value: object) -> Path | None:
    """Resolve a live catalog note link without granting catalog-only authority.

    Catalog links are advisory.  A non-empty string is not enough to count as
    a readable note: the file must exist within one of ResearchOS's note roots
    and pass the standard paper-note filter.  Stale or external links remain
    bounded catalog context rather than becoming phantom claim evidence.
    """

    raw = str(value or "").strip()
    if not raw:
        return None
    workspace = Path(workspace_dir).resolve()
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else workspace / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(workspace)
    except (OSError, ValueError):
        return None
    note_roots = (
        workspace / "literature" / "deep_read_notes",
        workspace / "literature" / "shallow_read_notes",
        workspace / BRIDGE_NOTE_ROOT_REL_PATH,
    )
    try:
        if not any(resolved.is_relative_to(root.resolve()) for root in note_roots):
            return None
    except OSError:
        return None
    return resolved if is_paper_note_file(resolved) else None


def _index_path_matches_catalog_file(workspace: Path, raw_path: object, expected: Path) -> bool:
    """Require a catalog index entry to resolve to its exact generated file."""

    raw = str(raw_path or "").strip()
    if not raw:
        return False
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else workspace / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(workspace.resolve())
        return resolved == expected.resolve()
    except (OSError, ValueError):
        return False


def load_bridge_catalog_summaries(
    workspace_dir: Path,
    *,
    records_per_bridge: int = 3,
    abstract_excerpt_chars: int = 520,
) -> list[dict[str, Any]]:
    """Return bounded, provenance-preserving summaries for all bridge tracks.

    The result is suitable for a prompt, source inventory, or renderer. Each
    sample retains its explicit usage boundary. It is deliberately not a
    citation map and callers must not use it as direct proof of a mechanism,
    result, or implementation detail.
    """

    workspace = Path(workspace_dir)
    plan = _read_json(workspace / BRIDGE_PLAN_REL_PATH)
    plan_entries = plan.get("bridge_domains") if isinstance(plan.get("bridge_domains"), list) else []

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def ensure(raw_id: object) -> dict[str, Any] | None:
        key = bridge_id_key(raw_id)
        if not key:
            return None
        if key not in merged:
            merged[key] = {
                "bridge_id": str(raw_id).strip(),
                "name": "",
                "rationale": "",
                "priority": "",
                "planned_queries": [],
                "status": "catalog_not_materialized",
                "context_path": "",
                "catalog_path": "",
                "record_count": 0,
                "abstract_record_count": 0,
                "metadata_record_count": 0,
                "canonical_note_count": 0,
                "sample_records": [],
                "usage_boundary": (
                    "Bridge catalog content is supplementary transfer context. It may guide analogy, historical framing, "
                    "taxonomy boundaries, baseline discovery, validation questions, and reading priority; it is not direct "
                    "support for a mechanism, result, or method equivalence without a linked canonical reading note."
                ),
            }
            order.append(key)
        return merged[key]

    for raw in plan_entries:
        if not isinstance(raw, dict):
            continue
        target = ensure(raw.get("bridge_id"))
        if target is None:
            continue
        target["bridge_id"] = str(raw.get("bridge_id") or target["bridge_id"]).strip()
        target["name"] = str(raw.get("name") or raw.get("domain") or target["name"]).strip()
        target["rationale"] = str(raw.get("rationale") or raw.get("why") or target["rationale"]).strip()
        target["priority"] = str(raw.get("priority") or target["priority"]).strip()
        target["planned_queries"] = _string_list(raw.get("planned_queries") or raw.get("query_plan") or raw.get("queries"))

    # Load legacy metadata first, then let the canonical index override it.
    # This lets an old workspace resume while reporting the new canonical paths
    # as soon as migration or T2 refresh has materialized them.
    for index_rel in (LEGACY_BRIDGE_CATALOG_INDEX_REL_PATH, CROSS_DOMAIN_CATALOG_INDEX_REL_PATH):
        index = _read_json(workspace / index_rel)
        index_entries = index.get("bridges") if isinstance(index.get("bridges"), list) else []
        for raw in index_entries:
            if not isinstance(raw, dict):
                continue
            target = ensure(raw.get("bridge_id"))
            if target is None:
                continue
            for key in ("name", "rationale", "priority", "status", "context_path", "catalog_path"):
                value = raw.get(key)
                if value not in (None, ""):
                    target[key] = str(value).strip()
            queries = _string_list(raw.get("planned_queries"))
            if queries:
                target["planned_queries"] = queries

    for catalog_path in iter_bridge_catalog_paths(workspace):
        catalog = _read_json(catalog_path)
        target = ensure(catalog.get("bridge_id") or catalog_path.parent.name)
        if target is None:
            continue
        context_path = catalog_path.parent / "bridge_context.json"
        context = _read_json(context_path)
        if context:
            target["bridge_id"] = str(context.get("bridge_id") or target["bridge_id"]).strip()
            target["name"] = str(context.get("name") or target["name"]).strip()
            target["rationale"] = str(context.get("rationale") or target["rationale"]).strip()
            target["priority"] = str(context.get("priority") or target["priority"]).strip()
            queries = _string_list(context.get("planned_queries"))
            if queries:
                target["planned_queries"] = queries
            target["usage_boundary"] = str(context.get("usage_boundary") or target["usage_boundary"]).strip()
        target["context_path"] = _relative(workspace, context_path) if context_path.is_file() else str(target["context_path"] or "")
        target["catalog_path"] = _relative(workspace, catalog_path)
        records = catalog.get("records") if isinstance(catalog.get("records"), list) else []
        target["record_count"] = len(records)
        target["abstract_record_count"] = sum(
            bool(str(item.get("abstract") or "").strip()) for item in records if isinstance(item, dict)
        )
        target["canonical_note_count"] = sum(
            resolve_catalog_canonical_note_path(workspace, item.get("canonical_note_path")) is not None
            for item in records
            if isinstance(item, dict)
        )
        target["metadata_record_count"] = max(0, int(target["record_count"]) - int(target["abstract_record_count"]))
        if target.get("status") in {"", "catalog_not_materialized"}:
            target["status"] = "retrieved_but_deferred" if records else "no_retrieved_material"
        samples: list[dict[str, Any]] = []
        for raw in records[: max(0, records_per_bridge)]:
            if not isinstance(raw, dict):
                continue
            abstract = " ".join(str(raw.get("abstract") or "").split())
            samples.append(
                {
                    "paper_id": str(raw.get("paper_id") or raw.get("canonical_id") or "").strip(),
                    "title": str(raw.get("title") or "Untitled retrieved bridge record").strip(),
                    "year": raw.get("year"),
                    "venue": str(raw.get("venue") or "").strip(),
                    "abstract_excerpt": abstract[: max(0, abstract_excerpt_chars)],
                    "reading_status": str(raw.get("reading_status") or "not_read").strip(),
                    "canonical_note_path": str(raw.get("canonical_note_path") or "").strip(),
                    "usage_boundary": str(
                        raw.get("usage_boundary") or ("abstract_only_inspiration" if abstract else "metadata_only_discovery")
                    ).strip(),
                }
            )
        target["sample_records"] = samples

    return [merged[key] for key in order]


def _read_json(path: Path) -> dict[str, Any]:
    payload, _ = _read_json_object(path)
    return payload or {}


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read one JSON object while retaining an actionable parse diagnostic."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None, "unreadable"
    except (ValueError, json.JSONDecodeError):
        return None, "invalid_json"
    if not isinstance(value, dict):
        return None, "json_object_required"
    return value, None


def _same_file_content(left: Path, right: Path) -> bool:
    try:
        if left.stat().st_size != right.stat().st_size:
            return False
        with left.open("rb") as left_handle, right.open("rb") as right_handle:
            while True:
                left_chunk = left_handle.read(65536)
                right_chunk = right_handle.read(65536)
                if left_chunk != right_chunk:
                    return False
                if not left_chunk:
                    return True
    except OSError:
        return False


def _migrate_index_paths(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite only catalog-owned locations, never linked paper-note paths."""

    migrated = dict(payload)
    bridges = payload.get("bridges") if isinstance(payload.get("bridges"), list) else []
    rewritten: list[Any] = []
    old_prefix = f"{LEGACY_BRIDGE_CATALOG_ROOT_REL_PATH}/"
    new_prefix = f"{CROSS_DOMAIN_CATALOG_ROOT_REL_PATH}/"
    for item in bridges:
        if not isinstance(item, dict):
            rewritten.append(item)
            continue
        copy = dict(item)
        for key in ("context_path", "catalog_path"):
            value = copy.get(key)
            if isinstance(value, str) and value.startswith(old_prefix):
                copy[key] = new_prefix + value[len(old_prefix):]
        rewritten.append(copy)
    if rewritten:
        migrated["bridges"] = rewritten
    migrated["catalog_root"] = CROSS_DOMAIN_CATALOG_ROOT_REL_PATH
    migrated["migrated_from_legacy_catalog_root"] = LEGACY_BRIDGE_CATALOG_ROOT_REL_PATH
    return migrated


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        try:
            Path(temporary_name).unlink(missing_ok=True)
        except OSError:
            pass


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _relative(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()
