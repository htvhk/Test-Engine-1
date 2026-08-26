#!/usr/bin/env python3
"""Fail-closed parser and canonicalizer for Gate F1 raw evidence."""
from __future__ import annotations
import hashlib, json, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path
IDS=['startpos','ruy_lopez','nimzo_indian','sicilian_classical','queens_gambit','caro_kann_advance','english_four_knights','kings_indian','kiwipete_family','quiet_middlegame']
ORDINARY_LIMIT=30000-128

def quantile(values:list[int], numerator:int)->int:
    # Frozen nearest-rank rule: sorted[ceil(p*N)-1].
    return sorted(values)[max(0,(numerator*len(values)+99)//100-1)]
def digest(data:bytes)->str:return hashlib.sha256(data).hexdigest()
def summarize(profile:dict)->dict:
    samples=profile['samples']; errors=[int(s['error']) for s in samples]
    if int(profile['inspected']) != len(samples)+sum(int(v) for v in profile['suppressed'].values()): raise SystemExit('accounting does not reconcile')
    if any(abs(int(s['error']))>=2*ORDINARY_LIMIT for s in samples):raise SystemExit('special/sentinel error admitted')
    keys=Counter(s['pawn_key'] for s in samples); by_depth=Counter(str(s['depth']) for s in samples); by_bound=Counter(s['bound'] for s in samples); sides=Counter(s['side'] for s in samples)
    return {'inspected':profile['inspected'],'eligible':len(samples),'suppressed':profile['suppressed'],'error':{'min':min(errors) if errors else None,'max':max(errors) if errors else None,'negative':sum(x<0 for x in errors),'zero':sum(x==0 for x in errors),'positive':sum(x>0 for x in errors),'sum':sum(errors),'count':len(errors),'quantiles_nearest_rank':{f'p{p:02}':quantile(errors,p) for p in (5,25,50,75,95)} if errors else {}},'by_depth':dict(sorted(by_depth.items())),'by_bound':dict(sorted(by_bound.items())),'side_to_move':dict(sorted(sides.items())),'pawn_keys':{'observations':len(samples),'unique':len(keys),'repeated_observations':sum(v for v in keys.values() if v>1),'frequencies':dict(sorted(keys.items()))}}
def main():
    if len(sys.argv)!=6:raise SystemExit('usage: parse RAW1 RAW2 PARITY OUT_JSON TRIGGER_SHA')
    raw1,raw2,parity_path,out,trigger=sys.argv[1:]
    b1=Path(raw1).read_bytes();b2=Path(raw2).read_bytes()
    if b1!=b2:raise SystemExit('collection ON raw evidence is not repeatable')
    rows=[]
    for line in b1.decode().splitlines():
      if line.startswith('TE1_CORRECTION_RAW\t'):
       fields=dict(item.split('=',1) for item in line.split('\t')[1:]); rows.append({'id':fields['id'],'semantic':fields['semantic'],'profile':json.loads(fields['profile'])})
    if [r['id'] for r in rows]!=IDS:raise SystemExit('suite identity/order drift')
    summaries=[{'id':r['id'],**summarize(r['profile'])} for r in rows]
    eligible=sum(r['eligible'] for r in summaries)
    if eligible<=0:raise SystemExit('eligible raw signal is dormant')
    merged={'inspected':sum(r['inspected'] for r in summaries),'suppressed':dict(Counter({k:sum(x['suppressed'].get(k,0) for x in summaries) for k in {k for x in summaries for k in x['suppressed']}})),'samples':[s for r in rows for s in r['profile']['samples']]}
    aggregate=summarize(merged)
    parity=json.loads(Path(parity_path).read_text())
    if parity.get('schema')!='TE1-ALPHA26-CORRECTION-HISTORY-GATE-F1-PARITY-v1' or parity.get('status')!='PASS' or parity.get('positions')!=10:raise SystemExit('control/OFF parity failed')
    canonical='\n'.join(json.dumps({'id':r['id'],'profile':r['profile']},sort_keys=True,separators=(',',':')) for r in rows)+'\n'
    obj={'schema':'TE1-ALPHA26-CORRECTION-HISTORY-GATE-F1-RAW-SIGNAL-v1','gate':'F1','decision':'PASS','diagnostic_only':True,'gate_f_closed':False,'production_tuning_authorized':False,'lineage':{'feature_base':'0c39989d17e1de7aae54e3db3b23039f1ae12990','source_commit':'9fd558f6c37f1843d2b3444900e757c36f9df353','source_tree':'10ae3046ffbd577ac525980c52f718e641f6b0bb','search_blob':'dfdc4000a2c986732f39a9901cdfea286e1489d8','search_sha256':'12d6c0ba791ddab9e3a9333cb9ca32338ef62462fb0bd3bf242749b48eff677c','trigger_sha':trigger},'suite':{'positions':10,'depth':8,'threads':1,'deterministic':True},'canonical_serialization':'one compact sort_keys JSON object per position plus LF, position order frozen; SHA-256 over UTF-8 bytes','raw_evidence_sha256':digest(canonical.encode()),'repeat_raw_file_sha256':digest(b1),'sign_balance_observed_not_gated':True,'neutrality':{'control_vs_instrumented_off':parity,'instrumented_off_vs_on':'PASS (asserted by reporter on full semantic tuple)','on_repeat':'PASS'},'aggregate':aggregate,'rows':summaries}
    Path(out).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n',newline='\n')
    Path(out).with_suffix('.canonical.txt').write_text(canonical,newline='\n')
    print('TE1_GATE_F1_PASS',obj['raw_evidence_sha256'],json.dumps(aggregate['error'],sort_keys=True))
if __name__=='__main__':main()
