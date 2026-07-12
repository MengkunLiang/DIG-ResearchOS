#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from _common import *

def refs_from_item(item):
    refs=[]
    ident=item_id(item)
    if ident: refs.append(ident)
    for r in listify(item.get('evidence_refs'))+listify(item.get('artifact_refs')):
        if isinstance(r,str): refs.append(r)
        elif isinstance(r,dict) and item_id(r): refs.append(item_id(r))
    return list(dict.fromkeys(refs))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--snapshot',required=True); ap.add_argument('--inventory',required=True); ap.add_argument('--output',required=True); args=ap.parse_args()
    snap=load_json(Path(args.snapshot)); inv=load_json(Path(args.inventory)); sec=snap.get('sections',{}); claims=[]
    inv_by_cat={}
    for x in inv.get('items',[]): inv_by_cat.setdefault(x['category'],[]).append(x)
    runs=section_items(sec.get('experiment_runs')); diags=section_items(sec.get('result_diagnoses')); attrs=section_items(sec.get('module_attributions'))
    for c in section_items(sec.get('claim_evidence_matrix')):
        cid=str(c.get('claim_id') or stable_id('CLM',canonical_hash(c)[:12])); exps=set(str(x) for x in listify(c.get('experiment_ids') or c.get('experiment_id')) if x)
        related=[r for r in runs if not exps or str(r.get('experiment_id')) in exps or cid in [str(x) for x in listify(r.get('claim_ids') or r.get('claim_id'))]]
        formal=[r for r in related if str(r.get('run_type')).lower()=='formal' and str(r.get('status')).lower() in {'completed','complete','success','passed'}]
        diagnostic=[r for r in related if str(r.get('run_type')).lower() in {'diagnostic','small_scale','ablation','robustness','efficiency'} and str(r.get('status')).lower() in {'completed','complete','success','passed'}]
        failed=[r for r in related if str(r.get('status')).lower() not in {'completed','complete','success','passed'}]
        support=[x for r in formal+diagnostic for x in refs_from_item(r)]
        counter=[x for r in failed for x in refs_from_item(r)]
        for d in diags:
            for imp in section_items(d.get('claim_implications')):
                if str(imp.get('claim_id'))==cid:
                    target=counter if str(imp.get('status','')).lower() in {'weakened','refuted','unsupported','narrowed'} else support
                    target += [str(x) for x in listify(imp.get('evidence_refs'))]
        for a in attrs:
            for m in section_items(a.get('mechanism_attributions')):
                if str(m.get('related_claim_id') or '')==cid and str(m.get('status')) in {'weakened','contradicted','unresolved'}:
                    counter += [str(x) for x in listify(m.get('evidence_refs'))]
        ceiling='formal_candidate' if formal else ('diagnostic_only' if diagnostic else ('method_definition_only' if c.get('type')=='method_definition' else 'unsupported'))
        claims.append({'claim_id':cid,'question_or_summary':c.get('claim') or c.get('summary') or c.get('reviewer_question') or '', 'required':bool(c.get('required',True)),'experiment_ids':sorted(exps),'support_refs':list(dict.fromkeys(support)),'counterevidence_refs':list(dict.fromkeys(counter)),'required_baseline_status':c.get('required_baseline_status','pending_T7_check'),'evidence_ceiling':ceiling,'limitations':listify(c.get('limitations')),'risks':listify(c.get('risks')),'must_not_claim':listify(c.get('must_not_claim')),'audit_status':'pending_T7'})
    payload={'schema_version':'writer_handoff_claim_map.v1','handoff_id':snap['handoff_id'],'input_fingerprint':snap['input_fingerprint'],'claims':claims,'created_at':utc_now()}
    dump_json_atomic(Path(args.output),payload); print(json.dumps({'claims':len(claims)}))
if __name__=='__main__': sys.exit(main())
