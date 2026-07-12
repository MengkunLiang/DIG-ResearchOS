#!/usr/bin/env python3
"""Shared stdlib-only helpers for writer-handoff."""
from __future__ import annotations
import hashlib, json, os, re, tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def load_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)


def dump_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False, allow_nan=False)
            fh.write('\n'); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def resolve_workspace(value: str | None) -> Path:
    if value:
        ws = Path(value).expanduser().resolve()
        if not (ws/'project.yaml').exists() or not (ws/'external_executor').is_dir():
            raise FileNotFoundError(f'Invalid workspace: {ws}')
        return ws
    cur = Path.cwd().resolve()
    for p in [cur, *cur.parents]:
        if (p/'project.yaml').exists() and (p/'external_executor').is_dir(): return p
    raise FileNotFoundError('Could not locate workspace')


def resolve_in_workspace(ws: Path, value: str) -> Path:
    text = str(value).replace('<workspace>', str(ws)).strip()
    p = Path(text).expanduser()
    if not p.is_absolute(): p = ws/p
    r = p.resolve(strict=False); r.relative_to(ws.resolve())
    return r


def relpath(ws: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(ws.resolve()).as_posix()


def is_within(path: Path, root: Path) -> bool:
    try: path.resolve(strict=False).relative_to(root.resolve(strict=False)); return True
    except ValueError: return False


def parse_allowed_paths(ws: Path) -> tuple[list[Path], list[Path]]:
    p = ws/'external_executor'/'allowed_paths.txt'
    if not p.exists(): raise FileNotFoundError(p)
    allowed, denied = [], []
    for raw in p.read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        target = allowed
        for prefix in ('deny:', 'forbid:', '!', '-'):
            if line.lower().startswith(prefix): target=denied; line=line[len(prefix):].strip(); break
        else:
            for prefix in ('allow:', 'write:', '+'):
                if line.lower().startswith(prefix): line=line[len(prefix):].strip(); break
        if any(c in line for c in '*?['): line = re.split(r'[\*\?\[]', line, maxsplit=1)[0].rstrip('/') or '.'
        if line: target.append(resolve_in_workspace(ws, line))
    return allowed, denied


def assert_write_allowed(ws: Path, path: Path) -> None:
    allowed, denied = parse_allowed_paths(ws); r = path.resolve(strict=False)
    if any(is_within(r,d) for d in denied): raise PermissionError(f'Denied path: {r}')
    if not allowed or not any(is_within(r,a) for a in allowed): raise PermissionError(f'Outside allowed paths: {r}')


def canonical_hash(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def slugify(value: Any, fallback='item') -> str:
    s=re.sub(r'[^A-Za-z0-9]+','-',str(value)).strip('-').lower(); return s[:64] or fallback


def stable_id(prefix: str, *parts: Any) -> str:
    raw='|'.join(str(x) for x in parts); return f'{prefix}-{slugify(parts[0] if parts else prefix)}-{hashlib.sha256(raw.encode()).hexdigest()[:10]}'


def schema_major(value: Any) -> int | None:
    if not isinstance(value,str): return None
    m=re.search(r'(?:^|[._-])v?(\d+)(?:$|[._-])',value) or re.search(r'v(\d+)$',value)
    return int(m.group(1)) if m else None


def listify(v: Any) -> list[Any]:
    if v is None: return []
    return v if isinstance(v,list) else [v]


def section_items(section: Any) -> list[dict[str,Any]]:
    if isinstance(section,list): return [x for x in section if isinstance(x,dict)]
    if isinstance(section,dict):
        for k in ('items','runs','records','experiments','figures','tables','claims'):
            if isinstance(section.get(k),list): return [x for x in section[k] if isinstance(x,dict)]
    return []


def get_nested(data: Any, *paths: str, default=None):
    for path in paths:
        cur=data; ok=True
        for part in path.split('.'):
            if isinstance(cur,dict) and part in cur: cur=cur[part]
            else: ok=False; break
        if ok: return cur
    return default


def walk_dicts(value: Any) -> Iterable[dict[str,Any]]:
    if isinstance(value,dict):
        yield value
        for v in value.values(): yield from walk_dicts(v)
    elif isinstance(value,list):
        for v in value: yield from walk_dicts(v)


def collect_paths(value: Any) -> list[str]:
    out=[]
    path_keys={'path','raw_log','raw_log_path','config_path','metric_output','metric_output_path','environment_path','plot_script','editable_source','source_table','rendered_file'}
    for d in walk_dicts(value):
        for k,v in d.items():
            if k in path_keys and isinstance(v,str) and v: out.append(v)
            if k in {'paths','rendered_files','code_paths','config_paths','artifact_paths'} and isinstance(v,list): out += [x for x in v if isinstance(x,str)]
    return list(dict.fromkeys(out))


def collect_artifact_ref_dicts(value: Any) -> list[dict[str,Any]]:
    out=[]
    for d in walk_dicts(value):
        if isinstance(d.get('path'),str) and any(k in d for k in ('sha256','artifact_id','size_bytes','evidence_level')):
            out.append(d)
    return out


def item_id(item: dict[str,Any]) -> str | None:
    for k in ('artifact_id','run_id','experiment_id','claim_id','diagnosis_id','attribution_id','implementation_id','figure_id','table_id','risk_id','limitation_id','decision_id','module_id','item_id'):
        if item.get(k): return str(item[k])
    return None


def known_ids(snapshot: dict[str,Any], inventory: dict[str,Any] | None=None, claim_map: dict[str,Any] | None=None) -> set[str]:
    ids=set()
    for d in walk_dicts(snapshot):
        ident=item_id(d)
        if ident: ids.add(ident)
        for k in ('source_ids','evidence_refs','artifact_refs','support_refs','counterevidence_refs'):
            if isinstance(d.get(k),list):
                for x in d[k]:
                    if isinstance(x,str): ids.add(x)
                    elif isinstance(x,dict) and item_id(x): ids.add(item_id(x))
    for src in (inventory or {}, claim_map or {}):
        for d in walk_dicts(src):
            ident=item_id(d)
            if ident: ids.add(ident)
    return ids


def output_path(ws: Path, value: str | None, default: str) -> Path:
    p=resolve_in_workspace(ws, value or default); assert_write_allowed(ws,p); return p
