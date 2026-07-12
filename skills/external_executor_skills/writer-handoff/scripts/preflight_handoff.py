#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from _common import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace'); ap.add_argument('--output'); args=ap.parse_args()
    ws=resolve_workspace(args.workspace); ext=ws/'external_executor'; issues=[]; warnings=[]
    files=['AGENTS.md','allowed_paths.txt','handoff_pack.json','expected_outputs_schema.json','result_pack.json','run_manifest.json']
    loaded={}
    for name in files:
        p=ext/name
        if not p.exists():
            (issues if name in {'AGENTS.md','allowed_paths.txt','result_pack.json'} else warnings).append({'id':f'missing-{name}','message':f'Missing {name}'})
        elif p.suffix=='.json':
            try: loaded[name]=load_json(p)
            except Exception as e: issues.append({'id':f'invalid-{name}','message':str(e)})
    result=loaded.get('result_pack.json',{})
    for name,obj in loaded.items():
        major=schema_major(obj.get('schema_version')) if isinstance(obj,dict) else None
        if major not in {None,1}: issues.append({'id':f'unsupported-{name}','message':f'Unsupported major {major}'})
    for sec in ('realized_method_package','final_framework_figure','figure_table_inventory'):
        if sec not in result: warnings.append({'id':f'missing-{sec}','message':f'{sec} absent; handoff will be partial'})
    pkg=result.get('evidence_package') or result.get('evidence_packaging') or {}
    if not pkg and not any(k in result for k in ('realized_method_package','figure_table_inventory')):
        issues.append({'id':'evidence-package-unresolved','message':'No evidence package or packaged sections can be resolved'})
    out=output_path(ws,args.output,'external_executor/writer_handoff_preflight.json')
    payload={'schema_version':'writer_handoff_preflight.v1','status':'blocked' if issues else ('partial' if warnings else 'pass'),'workspace':str(ws),'checked_files':files,'issues':issues,'warnings':warnings,'input_fingerprint':canonical_hash({'files':{k:v for k,v in loaded.items()},'issues':issues,'warnings':warnings}),'created_at':utc_now()}
    dump_json_atomic(out,payload); print(json.dumps(payload,ensure_ascii=False))
    return 2 if issues else 0
if __name__=='__main__':
    import json,sys; sys.exit(main())
