#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from _common import *

def source_refs(section):
    refs=[]
    for d in walk_dicts(section):
        ident=item_id(d)
        if ident: refs.append(ident)
    return list(dict.fromkeys(refs))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace')
    for x in ('snapshot','inventory','claim-map','t7-index','integrity','output'): ap.add_argument('--'+x)
    a=ap.parse_args(); ws=resolve_workspace(a.workspace)
    snap=load_json(resolve_in_workspace(ws,a.snapshot or 'external_executor/writer_handoff_snapshot.json')); inv=load_json(resolve_in_workspace(ws,a.inventory or 'external_executor/writer_handoff_inventory.json')); cmap=load_json(resolve_in_workspace(ws,getattr(a,'claim_map') or 'external_executor/writer_handoff_claim_map.json')); idx=load_json(resolve_in_workspace(ws,getattr(a,'t7_index') or 'external_executor/writer_handoff_t7_index.json')); integ=load_json(resolve_in_workspace(ws,a.integrity or 'external_executor/writer_handoff_integrity.json')); sec=snap.get('sections',{})
    risks=[]; limitations=[]; failed=[]
    for name in ('open_risks','resource_risks','open_blockers'):
        for x in section_items(sec.get(name)): risks.append({'risk_id':item_id(x) or stable_id('RISK',name,canonical_hash(x)[:8]),'summary':x.get('summary') or x.get('message') or x.get('description') or name,'severity':x.get('severity','unknown'),'source_refs':[item_id(x)] if item_id(x) else [],'status':x.get('status','open')})
    for name in ('material_gaps',):
        for x in section_items(sec.get(name)): limitations.append({'limitation_id':item_id(x) or stable_id('LIM',name,canonical_hash(x)[:8]),'summary':x.get('summary') or x.get('message') or x.get('description') or name,'scope':x.get('scope','evidence'),'source_refs':[item_id(x)] if item_id(x) else []})
    for x in section_items(sec.get('failed_trials')): failed.append({'work_id':item_id(x) or stable_id('FAIL',canonical_hash(x)[:8]),'status':x.get('status','failed'),'summary':x.get('summary') or x.get('failure_reason') or 'failed trial','source_refs':[item_id(x)] if item_id(x) else []})
    claims=[]
    for c in cmap.get('claims',[]):
        claims.append({'claim_id':c['claim_id'],'candidate_summary':c.get('question_or_summary',''),'evidence_ceiling':c['evidence_ceiling'],'audit_status':'pending_T7','support_refs':c.get('support_refs',[]),'counterevidence_refs':c.get('counterevidence_refs',[]),'limitations':c.get('limitations',[]),'risks':c.get('risks',[]),'eligible_sections':[]})
    must=[]
    for c in cmap.get('claims',[]):
        for text in c.get('must_not_claim',[]): must.append({'boundary_id':stable_id('MNC',c['claim_id'],text),'statement':text,'reason':'upstream claim boundary','source_refs':[c['claim_id']]})
    for text in snap.get('handoff_claim_boundaries',[])+snap.get('handoff_must_not_claim',[]):
        if isinstance(text,dict):
            statement=text.get('statement') or text.get('claim') or text.get('summary') or json.dumps(text,ensure_ascii=False)
            boundary_refs=[str(x) for x in listify(text.get('source_refs'))]
        else:
            statement=str(text); boundary_refs=[]
        must.append({'boundary_id':stable_id('MNC','handoff',statement),'statement':statement,'reason':'T5 handoff claim boundary','source_refs':boundary_refs})
    unique={x['boundary_id']:x for x in must}; must=list(unique.values())
    handoff={'schema_version':'writer_handoff_report.v1','handoff_id':snap['handoff_id'],'status':'partial','pre_audit':True,'input_fingerprint':snap['input_fingerprint'],'source_snapshot':{'path':'external_executor/writer_handoff_snapshot.json','fingerprint':snap['input_fingerprint']},'method_summary':{'status':'pending_summary','summary':'','realized_method_package_ref':'realized_method_package','source_refs':source_refs(sec.get('realized_method_package',{}))},'implementation_summary':{'status':'pending_summary','summary':'','source_refs':source_refs(sec.get('implementations',{}))},'results':{'main_results':[],'ablation_results':[],'diagnostic_findings':[],'robustness_and_efficiency':[]},'figures':[x for x in inv.get('items',[]) if x['category'] in {'framework_figure','result_figure'}],'tables':[x for x in inv.get('items',[]) if x['category']=='result_table'],'claim_candidates':claims,'must_not_claim':must,'limitations':limitations,'open_risks':risks,'failed_and_unusable_work':failed,'recovery_notes':[],'recommended_storyline_update':{'status':'pre_audit_suggestion','summary':'','emphasize':[],'deemphasize':[],'caveats':[],'source_refs':[]},'t7_ingest_index':{'path':'external_executor/writer_handoff_t7_index.json','schema_version':idx.get('schema_version'),'input_fingerprint':idx.get('input_fingerprint')},'integrity_validation':{'path':'external_executor/writer_handoff_integrity.json','status':integ.get('status'),'issue_ids':[x.get('id') for x in integ.get('issues',[])]},'blocking_issues':[x for x in integ.get('issues',[]) if x.get('severity')=='blocking'],'warnings':[x for x in integ.get('issues',[]) if x.get('severity')!='blocking'],'handoff_gate':{'status':'partial_for_T7_audit','reasons':['report initialized; evidence-bound summaries require completion'],'recommended_next_action':'return_to_root_with_partial_handoff'},'created_at':utc_now()}
    out=output_path(ws,a.output,'external_executor/writer_handoff_report.json'); dump_json_atomic(out,handoff); print(json.dumps({'handoff_id':handoff['handoff_id'],'claims':len(claims)}))
if __name__=='__main__': sys.exit(main())
