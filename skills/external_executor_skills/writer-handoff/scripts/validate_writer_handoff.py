#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys,re
from _common import *
FORBIDDEN={'audited','audit_passed','final_claim','paper_ready','t8_ready','scope_change_approved','executor_status','accepted','proven'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace'); ap.add_argument('--report'); args=ap.parse_args(); ws=resolve_workspace(args.workspace); ext=ws/'external_executor'; rp=resolve_in_workspace(ws,args.report or 'external_executor/writer_handoff_report.json'); r=load_json(rp); errors=[]
    required=['schema_version','handoff_id','status','pre_audit','input_fingerprint','source_snapshot','method_summary','implementation_summary','results','figures','tables','claim_candidates','must_not_claim','limitations','open_risks','failed_and_unusable_work','recovery_notes','recommended_storyline_update','t7_ingest_index','integrity_validation','blocking_issues','warnings','handoff_gate']
    for k in required:
        if k not in r: errors.append(f'missing required field: {k}')
    if r.get('pre_audit') is not True: errors.append('pre_audit must be true')
    if schema_major(r.get('schema_version'))!=1: errors.append('unsupported report schema')

    # Honor project-compiled writer-handoff required fields when expressed as a list.
    expected_path=ext/'expected_outputs_schema.json'
    if expected_path.exists():
        try:
            expected_doc=load_json(expected_path)
            req=get_nested(expected_doc,'writer_handoff.required','properties.writer_handoff.required',default=[])
            for key in req if isinstance(req,list) else []:
                if key not in r: errors.append(f'expected schema required field missing: {key}')
        except Exception as e:
            errors.append(f'could not parse expected output requirements: {e}')
    if r.get('source_snapshot',{}).get('fingerprint')!=r.get('input_fingerprint'):
        errors.append('source snapshot fingerprint mismatch')
    for d in walk_dicts(r):
        for k,v in d.items():
            if k.lower() in FORBIDDEN and v not in (False,None,'pending_T7','pending'):
                errors.append(f'forbidden authority field: {k}')
    allowed_ceiling={'formal_candidate','diagnostic_only','method_definition_only','unsupported'}
    snap=load_json(ext/'writer_handoff_snapshot.json'); inv=load_json(ext/'writer_handoff_inventory.json'); cmap=load_json(ext/'writer_handoff_claim_map.json'); ids=known_ids(snap,inv,cmap)
    for c in r.get('claim_candidates',[]):
        if c.get('audit_status')!='pending_T7': errors.append(f"claim {c.get('claim_id')} audit_status must be pending_T7")
        if c.get('evidence_ceiling') not in allowed_ceiling: errors.append(f"claim {c.get('claim_id')} invalid evidence ceiling")
        for ref in c.get('support_refs',[])+c.get('counterevidence_refs',[]):
            if str(ref) not in ids: errors.append(f"claim {c.get('claim_id')} unknown evidence ref: {ref}")
    # Risk propagation by stable IDs
    upstream=[]
    sec=snap.get('sections',{})
    for name in ('open_risks','resource_risks','open_blockers'):
        upstream += [item_id(x) for x in section_items(sec.get(name)) if item_id(x)]
    covered=set()
    for name in ('open_risks','limitations','must_not_claim'):
        for x in r.get(name,[]): covered.update(str(y) for y in x.get('source_refs',[]))
    for rid in upstream:
        if rid not in covered: errors.append(f'upstream risk not propagated: {rid}')
    gate=r.get('handoff_gate',{}).get('status')
    expected={'complete':'ready_for_T7_audit','partial':'partial_for_T7_audit','blocked':'blocked_for_T7_audit'}
    if r.get('status') in expected and gate!=expected[r['status']]: errors.append('status/gate mismatch')
    idx=resolve_in_workspace(ws,r.get('t7_ingest_index',{}).get('path',''))
    if not idx.exists(): errors.append('T7 ingest index missing')
    else:
        idx_doc=load_json(idx)
        if idx_doc.get('input_fingerprint')!=r.get('input_fingerprint'): errors.append('T7 index fingerprint mismatch')
    if errors:
        print('\n'.join(errors)); return 2
    print(json.dumps({'status':'valid','handoff_id':r.get('handoff_id'),'gate':gate})); return 0
if __name__=='__main__': sys.exit(main())
