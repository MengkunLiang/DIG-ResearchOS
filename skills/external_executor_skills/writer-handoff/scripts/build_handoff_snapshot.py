#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from _common import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace'); ap.add_argument('--output'); args=ap.parse_args()
    ws=resolve_workspace(args.workspace); ext=ws/'external_executor'
    result=load_json(ext/'result_pack.json'); handoff=load_json(ext/'handoff_pack.json') if (ext/'handoff_pack.json').exists() else {}; expected=load_json(ext/'expected_outputs_schema.json') if (ext/'expected_outputs_schema.json').exists() else {}; manifest=load_json(ext/'run_manifest.json') if (ext/'run_manifest.json').exists() else {}
    contract=get_nested(handoff,'context_reboost.writer_handoff_contract','writer_handoff_contract',default=[])
    boundaries=get_nested(handoff,'context_reboost.claim_boundaries','claim_boundaries',default=[])
    must_not=get_nested(handoff,'context_reboost.must_not_claim','must_not_claim',default=[])
    selected=['context_alignment','resource_readiness','baseline_reproduction','claim_evidence_matrix','experiment_plan','experiment_runs','implementation_reviews','implementations','result_diagnoses','module_attributions','iteration_decisions','evidence_package','evidence_packaging','realized_method_package','final_framework_figure','figure_table_inventory','failed_trials','scope_change_requests','open_blockers','open_risks','resource_risks','material_gaps']
    sections={k:result.get(k) for k in selected if k in result}
    identity={'result_schema':result.get('schema_version'),'manifest_schema':manifest.get('schema_version'),'handoff_schema':handoff.get('schema_version'),'expected_schema':expected.get('schema_version'),'contract':contract,'sections':sections,'manifest':manifest}
    fp=canonical_hash(identity); hid=stable_id('WH',fp[:16])
    payload={'schema_version':'writer_handoff_snapshot.v1','handoff_id':hid,'input_fingerprint':fp,'source_versions':{k:v for k,v in identity.items() if k.endswith('_schema')},'writer_handoff_contract':contract,'handoff_claim_boundaries':listify(boundaries),'handoff_must_not_claim':listify(must_not),'expected_requirements':get_nested(expected,'writer_handoff','properties.writer_handoff',default={}), 'sections':sections,'manifest':manifest,'source_result_pack_path':'external_executor/result_pack.json','created_at':utc_now()}
    out=output_path(ws,args.output,'external_executor/writer_handoff_snapshot.json'); dump_json_atomic(out,payload); print(json.dumps({'handoff_id':hid,'input_fingerprint':fp}))
if __name__=='__main__': sys.exit(main())
