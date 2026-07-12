#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from _common import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--report',required=True); ap.add_argument('--write-back',action='store_true'); args=ap.parse_args(); p=Path(args.report); r=load_json(p); reasons=[]; blocked=[]
    if not r.get('pre_audit'): blocked.append('pre_audit flag must be true')
    if r.get('integrity_validation',{}).get('status')=='blocked': blocked.append('core artifact integrity blocked')
    if r.get('blocking_issues'): blocked.append('blocking issues remain')
    if not r.get('t7_ingest_index',{}).get('path'): blocked.append('T7 index missing')
    for key in ('method_summary','implementation_summary','claim_candidates','must_not_claim','limitations','open_risks','failed_and_unusable_work'):
        if key not in r: blocked.append(f'missing {key}')
    partial=False
    if r.get('method_summary',{}).get('status')!='complete': reasons.append('method summary incomplete'); partial=True
    if r.get('implementation_summary',{}).get('status')!='complete': reasons.append('implementation summary incomplete'); partial=True
    if any(c.get('audit_status')!='pending_T7' for c in r.get('claim_candidates',[])): blocked.append('claim audit status exceeds child authority')
    if any(c.get('evidence_ceiling')=='unsupported' and c.get('support_refs') for c in r.get('claim_candidates',[])): reasons.append('unsupported claim has refs requiring review'); partial=True
    if r.get('integrity_validation',{}).get('status')=='partial': reasons.append('artifact integrity partial'); partial=True
    if blocked: status='blocked_for_T7_audit'; overall='blocked'; action='repair_handoff'; reasons=blocked+reasons
    elif partial: status='partial_for_T7_audit'; overall='partial'; action='return_to_root_with_partial_handoff'
    else: status='ready_for_T7_audit'; overall='complete'; action='return_to_root_for_final_validation'
    r['status']=overall; r['handoff_gate']={'status':status,'reasons':reasons,'recommended_next_action':action,'computed_at':utc_now()}
    if args.write_back: dump_json_atomic(p,r)
    print(json.dumps(r['handoff_gate'])); return 2 if status=='blocked_for_T7_audit' else 0
if __name__=='__main__': sys.exit(main())
