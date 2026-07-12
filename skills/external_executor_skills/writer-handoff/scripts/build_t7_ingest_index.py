#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from _common import *

def main():
    ap=argparse.ArgumentParser()
    for x in ('snapshot','inventory','claim-map','integrity','output'): ap.add_argument('--'+x,required=True)
    a=ap.parse_args(); snap=load_json(Path(a.snapshot)); inv=load_json(Path(a.inventory)); cmap=load_json(Path(getattr(a,'claim_map'))); integ=load_json(Path(a.integrity))
    consumers={'T7-INGEST':[],'T7-AUDIT':[],'T7-METHOD-AUDIT':[],'T7-POST-NOVELTY':[],'T7-CLAIMS':[]}
    for item in inv.get('items',[]):
        entry={'item_id':item['item_id'],'category':item['category'],'source_ids':item.get('source_ids',[]),'artifact_paths':item.get('artifact_paths',[]),'evidence_level':item.get('evidence_level'),'status':item.get('status')}
        for c in item.get('t7_consumers',[]): consumers.setdefault(c,[]).append(entry)
    consumers['T7-CLAIMS'].append({'category':'claim_map','path':'external_executor/writer_handoff_claim_map.json','claim_ids':[c['claim_id'] for c in cmap.get('claims',[])]})
    payload={'schema_version':'writer_handoff_t7_index.v1','handoff_id':snap['handoff_id'],'input_fingerprint':snap['input_fingerprint'],'source_versions':snap.get('source_versions',{}),'integrity_status':integ.get('status'),'consumers':consumers,'source_paths':{'snapshot':'external_executor/writer_handoff_snapshot.json','inventory':'external_executor/writer_handoff_inventory.json','claim_map':'external_executor/writer_handoff_claim_map.json','integrity':'external_executor/writer_handoff_integrity.json','result_pack':'external_executor/result_pack.json','run_manifest':'external_executor/run_manifest.json'},'created_at':utc_now()}
    dump_json_atomic(Path(a.output),payload); print(json.dumps({k:len(v) for k,v in consumers.items()}))
if __name__=='__main__': sys.exit(main())
