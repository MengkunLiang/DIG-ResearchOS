#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from _common import *

def severity_for(category):
    return 'blocking' if category in {'method_definition','formal_result_candidate','ablation_result_candidate','framework_figure','result_table','result_figure'} else 'warning'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace'); ap.add_argument('--snapshot',required=True); ap.add_argument('--inventory',required=True); ap.add_argument('--output'); args=ap.parse_args()
    ws=resolve_workspace(args.workspace); snap=load_json(resolve_in_workspace(ws,args.snapshot)); inv=load_json(resolve_in_workspace(ws,args.inventory)); issues=[]; checked=[]
    for item in inv.get('items',[]):
        cat=item.get('category'); paths=list(item.get('artifact_paths',[])); refs=item.get('artifact_refs',[])
        for ref in refs:
            if isinstance(ref,dict) and ref.get('path') and ref['path'] not in paths: paths.append(ref['path'])
        for raw in paths:
            try:
                p=resolve_in_workspace(ws,raw)
                if p.is_symlink():
                    target=p.resolve(); target.relative_to(ws.resolve())
                if not p.exists(): raise FileNotFoundError(raw)
                rec={'item_id':item.get('item_id'),'path':relpath(ws,p),'exists':True,'size_bytes':p.stat().st_size,'sha256':sha256_file(p)}
                matching=[r for r in refs if isinstance(r,dict) and r.get('path')==raw]
                for r in matching:
                    if r.get('sha256') and r['sha256']!=rec['sha256']: raise ValueError(f'checksum mismatch for {raw}')
                    if r.get('size_bytes') is not None and int(r['size_bytes'])!=rec['size_bytes']: raise ValueError(f'size mismatch for {raw}')
                checked.append(rec)
            except Exception as e:
                issues.append({'id':stable_id('INT',item.get('item_id'),raw),'severity':severity_for(cat),'item_id':item.get('item_id'),'path':raw,'message':str(e)})
    status='blocked' if any(x['severity']=='blocking' for x in issues) else ('partial' if issues else 'pass')
    payload={'schema_version':'writer_handoff_integrity.v1','handoff_id':snap['handoff_id'],'input_fingerprint':snap['input_fingerprint'],'status':status,'checked':checked,'issues':issues,'created_at':utc_now()}
    out=output_path(ws,args.output,'external_executor/writer_handoff_integrity.json'); dump_json_atomic(out,payload); print(json.dumps({'status':status,'issues':len(issues)})); return 2 if status=='blocked' else 0
if __name__=='__main__': sys.exit(main())
