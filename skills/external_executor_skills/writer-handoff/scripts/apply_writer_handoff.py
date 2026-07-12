#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from _common import *

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--workspace'); ap.add_argument('--report'); args=ap.parse_args(); ws=resolve_workspace(args.workspace); ext=ws/'external_executor'; report=load_json(resolve_in_workspace(ws,args.report or 'external_executor/writer_handoff_report.json')); result_path=ext/'result_pack.json'; assert_write_allowed(ws,result_path); result=load_json(result_path); result['writer_handoff']=report; dump_json_atomic(result_path,result); print(json.dumps({'applied':'writer_handoff','handoff_id':report.get('handoff_id')}))
if __name__=='__main__': sys.exit(main())
