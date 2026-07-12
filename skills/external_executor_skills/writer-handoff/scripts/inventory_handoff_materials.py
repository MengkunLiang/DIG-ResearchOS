#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from _common import *

def add(items, category, source, status=None, evidence='unsupported', consumers=None, limitations=None):
    if not isinstance(source,dict): return
    sid=item_id(source) or stable_id('SRC',category,canonical_hash(source)[:12])
    refs=collect_artifact_ref_dicts(source); paths=collect_paths(source)
    items.append({'item_id':stable_id('HI',category,sid),'category':category,'status':status or source.get('status','available'),'source_ids':[sid],'artifact_refs':refs,'artifact_paths':paths,'evidence_level':evidence,'t7_consumers':consumers or [],'limitations':limitations or listify(source.get('limitations'))})

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--snapshot',required=True); ap.add_argument('--output',required=True); args=ap.parse_args()
    snap=load_json(Path(args.snapshot)); sec=snap.get('sections',{}); items=[]
    add(items,'method_definition',sec.get('realized_method_package',{}),evidence='method_definition',consumers=['T7-INGEST','T7-METHOD-AUDIT','T7-CLAIMS'])
    add(items,'framework_figure',sec.get('final_framework_figure',{}),evidence='method_definition',consumers=['T7-METHOD-AUDIT','T7-CLAIMS'])
    inv=sec.get('figure_table_inventory',{})
    for x in section_items(inv):
        typ=str(x.get('type') or x.get('item_type') or x.get('category') or '').lower()
        cat='result_table' if 'table' in typ else ('framework_figure' if 'framework' in typ else 'result_figure')
        add(items,cat,x,evidence=str(x.get('evidence_level') or 'audited_candidate'),consumers=['T7-INGEST','T7-AUDIT','T7-CLAIMS'])
    for run in section_items(sec.get('experiment_runs')):
        rt=str(run.get('run_type','')).lower(); st=str(run.get('status','')).lower()
        if st not in {'completed','complete','success','passed'}: cat='failed_or_unusable_run'
        elif rt=='formal': cat='formal_result_candidate'
        elif rt=='ablation': cat='ablation_result_candidate'
        else: cat='diagnostic_result'
        ev='raw_result' if cat in {'formal_result_candidate','ablation_result_candidate'} else ('diagnostic_hint' if cat=='diagnostic_result' else 'unsupported')
        add(items,cat,run,evidence=ev,consumers=['T7-INGEST','T7-AUDIT','T7-CLAIMS'])
    for x in section_items(sec.get('implementations')): add(items,'implementation_evidence',x,evidence='method_definition',consumers=['T7-METHOD-AUDIT'])
    for section,cat in [('failed_trials','failed_or_unusable_run'),('open_risks','open_risk'),('resource_risks','open_risk'),('open_blockers','open_risk'),('material_gaps','limitation')]:
        for x in section_items(sec.get(section)): add(items,cat,x,evidence='unsupported',consumers=['T7-AUDIT','T7-CLAIMS'])
    out={'schema_version':'writer_handoff_inventory.v1','handoff_id':snap['handoff_id'],'input_fingerprint':snap['input_fingerprint'],'items':items,'counts':{},'created_at':utc_now()}
    for x in items: out['counts'][x['category']]=out['counts'].get(x['category'],0)+1
    dump_json_atomic(Path(args.output),out); print(json.dumps(out['counts']))
if __name__=='__main__': sys.exit(main())
