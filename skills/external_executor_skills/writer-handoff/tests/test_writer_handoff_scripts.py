from __future__ import annotations
import hashlib,json,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; SCRIPTS=ROOT/'scripts'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def ref(ws,p,aid):
    q=ws/p; return {'artifact_id':aid,'path':p,'sha256':sha(q),'size_bytes':q.stat().st_size,'evidence_level':'raw_result'}
def run(script,*args,check=True):
    cp=subprocess.run([sys.executable,str(SCRIPTS/script),*map(str,args)],text=True,capture_output=True,timeout=20)
    if check and cp.returncode!=0: raise AssertionError(f'{script} failed\nOUT:{cp.stdout}\nERR:{cp.stderr}')
    return cp

def make_ws():
    ws=Path(tempfile.mkdtemp(prefix='writer-handoff-')); ext=ws/'external_executor'; ext.mkdir(); (ws/'project.yaml').write_text('project_id: test\n'); (ext/'AGENTS.md').write_text('safe'); (ext/'allowed_paths.txt').write_text('allow: external_executor\n')
    for p,txt in [('workdir/code/model.py','class Model: pass\n'),('configs/formal.json','{"seed": 1}\n'),('logs/ours.log','done\n'),('logs/base.log','done\n'),('raw_results/ours.json','{"acc": 0.8}\n'),('raw_results/base.json','{"acc": 0.7}\n'),('env/ours.json','{"python":"3.11"}\n'),('figures/framework.svg','<svg/>\n'),('figures/main.svg','<svg/>\n'),('tables/main.csv','method,acc\nours,0.8\nbase,0.7\n'),('scripts/plot.py','print("plot")\n')]:
        q=ext/p; q.parent.mkdir(parents=True,exist_ok=True); q.write_text(txt)
    (ext/'handoff_pack.json').write_text(json.dumps({'schema_version':'external_executor_handoff.v1','context_reboost':{'writer_handoff_contract':['method','results','risks'],'claim_boundaries':['do not claim universal superiority']}}))
    (ext/'expected_outputs_schema.json').write_text(json.dumps({'schema_version':'external_executor_schema.v1','writer_handoff':{'required':['claim_candidates','must_not_claim']}}))
    oursrefs=[ref(ws,'external_executor/configs/formal.json','CFG-O'),ref(ws,'external_executor/logs/ours.log','LOG-O'),ref(ws,'external_executor/raw_results/ours.json','MET-O'),ref(ws,'external_executor/env/ours.json','ENV-O')]
    baserefs=[ref(ws,'external_executor/configs/formal.json','CFG-B'),ref(ws,'external_executor/logs/base.log','LOG-B'),ref(ws,'external_executor/raw_results/base.json','MET-B'),ref(ws,'external_executor/env/ours.json','ENV-B')]
    result={'schema_version':'external_executor_result.v1','context_alignment':{'status':'pass'},'resource_readiness':{'status':'ready'},'baseline_reproduction':{'status':'complete','items':[{'baseline_id':'B1','status':'reproduced'}]},'claim_evidence_matrix':{'status':'complete','items':[{'claim_id':'C1','claim':'Ours improves accuracy under the locked protocol.','experiment_id':'E1','must_not_claim':['Do not claim universal superiority.']}]},'experiment_plan':{'status':'complete','protocol_fingerprint':'P1'},'experiment_runs':{'status':'complete','items':[{'run_id':'RUN-O','experiment_id':'E1','claim_id':'C1','method_id':'ours','run_type':'formal','status':'completed','protocol_fingerprint':'P1','dataset':'D','split':'test','seed':1,'repeat':1,'code_version':'CODE1','resource_version':'RES1','artifact_refs':oursrefs},{'run_id':'RUN-B','experiment_id':'E1','claim_id':'C1','method_id':'B1','run_type':'formal','status':'completed','protocol_fingerprint':'P1','dataset':'D','split':'test','seed':1,'repeat':1,'code_version':'CODE1','resource_version':'RES1','artifact_refs':baserefs}]},'implementation_reviews':{'status':'complete','items':[{'review_id':'REV1','status':'pass','approved_for':'formal'}]},'implementations':{'status':'complete','items':[{'implementation_id':'IM1','module_mappings':{'items':[{'module_id':'M1','code_paths':['external_executor/workdir/code/model.py'],'config_keys':['use_m1']}]}}]},'result_diagnoses':{'status':'complete','items':[{'diagnosis_id':'D1','claim_implications':{'items':[{'claim_id':'C1','status':'supported_candidate','evidence_refs':['RUN-O','RUN-B']}]}}]},'module_attributions':{'status':'complete','items':[{'attribution_id':'A1','mechanism_attributions':{'items':[{'mechanism_id':'MECH1','status':'consistent','evidence_refs':['RUN-O']}]}}]},'iteration_decisions':{'status':'complete','items':[{'decision_id':'DEC1','decision':'stop_and_report'}]},'evidence_package':{'status':'complete','package_id':'EP1','fingerprint':'EPFP'},'realized_method_package':{'status':'complete','package_id':'RMP1','final_method_name':'Method','implemented_modules':[{'module_id':'M1','code_paths':['external_executor/workdir/code/model.py'],'config_keys':['use_m1']}],'limitations':['tested on D only']},'final_framework_figure':{'status':'ready_for_T7_audit','figure_id':'F-FW','rendered_files':['external_executor/figures/framework.svg'],'evidence_mapping':[{'figure_element':'M1','source_ref':'M1'}]},'figure_table_inventory':{'status':'complete','items':[{'figure_id':'F1','type':'result_figure','path':'external_executor/figures/main.svg','source_result':'RUN-O','source_config':'external_executor/configs/formal.json','source_log':'external_executor/logs/ours.log','plot_script':'external_executor/scripts/plot.py','evidence_level':'raw_result'},{'table_id':'T1','type':'main_comparison_table','path':'external_executor/tables/main.csv','source_result':'RUN-O','source_config':'external_executor/configs/formal.json','source_log':'external_executor/logs/ours.log','evidence_level':'raw_result'}]},'open_risks':{'status':'open','items':[{'risk_id':'R1','summary':'Single seed only','severity':'high'}]},'material_gaps':{'status':'partial','items':[{'limitation_id':'L1','summary':'No second dataset'}]},'failed_trials':{'items':[{'run_id':'RUN-F','status':'failed','failure_reason':'OOM'}]},'unrelated':{'keep':True}}
    (ext/'result_pack.json').write_text(json.dumps(result)); (ext/'run_manifest.json').write_text(json.dumps({'schema_version':'external_executor_manifest.v1','artifacts':oursrefs+baserefs})); return ws

def pipeline(ws):
    ext=ws/'external_executor'
    run('preflight_handoff.py','--workspace',ws)
    run('build_handoff_snapshot.py','--workspace',ws)
    run('inventory_handoff_materials.py','--snapshot',ext/'writer_handoff_snapshot.json','--output',ext/'writer_handoff_inventory.json')
    run('validate_artifact_refs.py','--workspace',ws,'--snapshot','external_executor/writer_handoff_snapshot.json','--inventory','external_executor/writer_handoff_inventory.json')
    run('build_claim_evidence_map.py','--snapshot',ext/'writer_handoff_snapshot.json','--inventory',ext/'writer_handoff_inventory.json','--output',ext/'writer_handoff_claim_map.json')
    run('build_t7_ingest_index.py','--snapshot',ext/'writer_handoff_snapshot.json','--inventory',ext/'writer_handoff_inventory.json','--claim-map',ext/'writer_handoff_claim_map.json','--integrity',ext/'writer_handoff_integrity.json','--output',ext/'writer_handoff_t7_index.json')
    run('initialize_writer_handoff.py','--workspace',ws)

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.base=make_ws(); pipeline(cls.base)
    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.base,ignore_errors=True)
    def clone(self):
        p=Path(tempfile.mkdtemp(prefix='wh-clone-')); shutil.copytree(self.base,p/'ws'); return p/'ws'
    def test_snapshot_inventory_claim_map_and_index(self):
        ext=self.base/'external_executor'; snap=json.loads((ext/'writer_handoff_snapshot.json').read_text()); self.assertEqual(snap['sections']['evidence_package']['package_id'],'EP1')
        inv=json.loads((ext/'writer_handoff_inventory.json').read_text()); cats={x['category'] for x in inv['items']}; self.assertIn('formal_result_candidate',cats); self.assertIn('framework_figure',cats)
        cmap=json.loads((ext/'writer_handoff_claim_map.json').read_text()); self.assertEqual(cmap['claims'][0]['evidence_ceiling'],'formal_candidate'); self.assertIn('RUN-O',cmap['claims'][0]['support_refs'])
        idx=json.loads((ext/'writer_handoff_t7_index.json').read_text()); self.assertTrue(idx['consumers']['T7-AUDIT'])
    def test_ready_gate_validate_and_narrow_apply(self):
        ws=self.clone(); ext=ws/'external_executor'; p=ext/'writer_handoff_report.json'; r=json.loads(p.read_text()); r['method_summary'].update(status='complete',summary='The realized method contains M1.',source_refs=['RMP1','M1']); r['implementation_summary'].update(status='complete',summary='M1 is mapped to code and config.',source_refs=['IM1','M1'])
        r['results']['main_results']=[{'result_id':'RES1','summary':'Formal candidate under P1.','source_refs':['RUN-O','RUN-B'],'evidence_level':'formal_candidate'}]
        r['open_risks'][0]['source_refs']=['R1']; r['limitations'][0]['source_refs']=['L1']; r['recommended_storyline_update'].update(summary='Emphasize the bounded setting pending T7 audit.',source_refs=['D1','A1','DEC1'])
        p.write_text(json.dumps(r)); run('compute_handoff_gate.py','--report',p,'--write-back'); rr=json.loads(p.read_text()); self.assertEqual(rr['handoff_gate']['status'],'ready_for_T7_audit')
        run('validate_writer_handoff.py','--workspace',ws); run('apply_writer_handoff.py','--workspace',ws); result=json.loads((ext/'result_pack.json').read_text()); self.assertTrue(result['unrelated']['keep']); self.assertEqual(result['writer_handoff']['handoff_id'],rr['handoff_id']); shutil.rmtree(ws.parent,ignore_errors=True)
    def test_rejects_authority_escalation_and_unpropagated_risk(self):
        ws=self.clone(); ext=ws/'external_executor'; p=ext/'writer_handoff_report.json'; r=json.loads(p.read_text()); r['claim_candidates'][0]['audited']=True; r['open_risks']=[]; p.write_text(json.dumps(r)); cp=run('validate_writer_handoff.py','--workspace',ws,check=False); self.assertNotEqual(cp.returncode,0); self.assertIn('forbidden authority field',cp.stdout); self.assertIn('upstream risk not propagated',cp.stdout); shutil.rmtree(ws.parent,ignore_errors=True)
if __name__=='__main__': unittest.main()
