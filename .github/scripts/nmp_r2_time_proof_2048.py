from __future__ import annotations
import argparse, importlib.util, json, os, sys
from pathlib import Path
from typing import Any

P=Path(__file__).with_name("nmp_proof_2048_entry.py")
S=importlib.util.spec_from_file_location("te1_nmp_r1_for_r2",P)
if S is None or S.loader is None: raise RuntimeError("cannot load frozen R1 proof")
E=importlib.util.module_from_spec(S); S.loader.exec_module(E); R=E.proof
ProofError=R.ProofError
C=Path("diagnostics/nmp_r2_time_proof_2048/NMP_R2_TIME_PROOF_CONTRACT.json")
SC="TE1-ALPHA26-NMP-R2-TIME-PROOF-v1"; SO=SC.replace("-v1","-OPENINGS-v1")
SP=SC.replace("-v1","-PREFLIGHT-v1"); SS=SC.replace("-v1","-SHARD-v1"); SF=SC.replace("-v1","-FINAL-v1")
BASE="7820b54d511afbf5dd2d38a3f686af97c14de639"; TREE="465e442fb26f8ad5ee6a793f35edb22d7f66f8b0"
MODE="TIME"

def contract()->tuple[dict[str,Any],str]:
    raw=C.read_bytes(); x=json.loads(raw)
    if x.get("schema")!=SC or x.get("campaign_id")!="alpha26-nmp-r2-time-proof-2048g-v1": raise ProofError("R2 contract identity drift")
    if x.get("baseline")!={"commit":BASE,"tree":TREE}: raise ProofError("R2 baseline drift")
    if x.get("arms")!={"TIME":{"games":2048,"go":"movetime","mode":"TIME","move_overhead_ms":0,"movetime_ms":200,"pairs":1024}}: raise ProofError("R2 TIME arm drift")
    if x.get("opening_selection")!={"historical_overlap_allowed":False,"time_confirmatory":{"pairs":1024,"valid_rank_start":1280,"valid_rank_stop_exclusive":2304}}: raise ProofError("R2 opening drift")
    if x.get("sharding")!={"pairs_per_shard":16,"shards":64,"total_games":2048,"total_pairs":1024}: raise ProofError("R2 sharding drift")
    if x["feature"]["default_remains_off_during_campaign"] is not True or x["feature"]["parameters_frozen_from_r1"] is not True: raise ProofError("R2 feature drift")
    if x["rerun_policy"]["admissible_run_attempt"]!=1 or x["statistics"]["z_95_two_sided"]!=R.Z95: raise ProofError("R2 statistical/rerun drift")
    p=x["r1_prerequisite"]
    req={"run_id":"32240977886","run_attempt":1,"final_json_sha256":"757bf5e0d9c16e675407bbeee41b5ad2eec6dbe0ab15a356c7f986d17bec4744","preflight_manifest_sha256":"b7aa109214bdecfb1993d5234217627d4b5a10fafcce7a39531b491af9d90e33","binary_sha256":"8d3920a6d244b040874c8fe95f9ea04a7c6fb2288d0680f9c88bbd3156710136","decision":"INCONCLUSIVE","default_on_authorized":False,"operational_failures":0}
    if any(p.get(k)!=v for k,v in req.items()) or p["nodes"]["paired_statistics"]["ci95_lower"]!=0.5183795622080772: raise ProofError("R1 prerequisite contract drift")
    return x,R.sha256_bytes(raw)

def prior(final:Path, manifest:Path|None=None, binary:Path|None=None)->dict[str,Any]:
    x,_=contract(); p=x["r1_prerequisite"]
    if R.sha256_file(final)!=p["final_json_sha256"]: raise ProofError("R1 final digest mismatch")
    f=R.load_json(final)
    req={"schema":"TE1-ALPHA26-NMP-R1-PROOF-FINAL-v1","campaign_id":p["campaign_id"],"contract_sha256":p["contract_sha256"],"run_id":p["run_id"],"run_attempt":1,"source_head":p["source_head"],"source_tree":p["source_tree"],"binary_sha256":p["binary_sha256"],"total_pairs":1024,"total_games":2048,"operational_failures":0,"decision":"INCONCLUSIVE","prior_512_games_pooled":False,"default_on_authorized":False}
    if any(f.get(k)!=v for k,v in req.items()) or f.get("NODES")!=p["nodes"]: raise ProofError("R1 final semantics mismatch")
    if manifest is not None:
        if R.sha256_file(manifest)!=p["preflight_manifest_sha256"]: raise ProofError("R1 preflight digest mismatch")
        m=R.load_json(manifest)
        for k,v in {"run_id":p["run_id"],"run_attempt":1,"source_head":p["source_head"],"source_tree":p["source_tree"],"binary_sha256":p["binary_sha256"],"status":"PASS"}.items():
            if m.get(k)!=v: raise ProofError(f"R1 preflight mismatch: {k}")
    if binary is not None and (not binary.is_file() or R.sha256_file(binary)!=p["binary_sha256"]): raise ProofError("R1 binary mismatch")
    return f

def valid_openings(binary:Path,x:dict[str,Any])->list[str]:
    stop=x["opening_selection"]["time_confirmatory"]["valid_rank_stop_exclusive"]; v=[]; e=R.new_engine(binary,False,x)
    try:
        for _,fen in R.fetch_book(x):
            try: n=R.set_fen_position(e,fen,[])
            except R.r3.IllegalMoveError: continue
            if n.split()[:4]==fen.split()[:4]: v.append(fen)
            if len(v)==stop: break
    finally: e.close()
    if len(v)!=stop: raise ProofError(f"need {stop} valid openings, got {len(v)}")
    for h in x["historical_opening_selections"]:
        if R.opening_hash(v[h["valid_rank_start"]:h["valid_rank_stop_exclusive"]])!=h["sha256"]: raise ProofError(f"historical opening drift: {h['label']}")
    return v

def freeze(v:list[str],x:dict[str,Any],cs:str)->dict[str,Any]:
    q=x["opening_selection"]["time_confirmatory"]; a,b=q["valid_rank_start"],q["valid_rank_stop_exclusive"]; f=v[a:b]
    if len(f)!=1024 or len(set(f))!=1024 or set(v[:a])&set(f): raise ProofError("R2 opening freshness/cardinality failure")
    o=[{"valid_rank":i,"fen":fen,"fen_sha256":R.sha256_bytes(fen.encode("ascii"))} for i,fen in zip(range(a,b),f,strict=True)]
    return {"schema":SO,"campaign_id":x["campaign_id"],"contract_sha256":cs,"mode":MODE,"valid_rank_range":[a,b],"pairs":1024,"selection_sha256":R.opening_hash(f),"openings":o}

def atomic(path:Path,data:bytes,mode:int|None=None)->None:
    path.parent.mkdir(parents=True,exist_ok=True); t=path.with_suffix(path.suffix+".tmp")
    with t.open("wb") as s: s.write(data); s.flush(); os.fsync(s.fileno())
    os.replace(t,path)
    if mode is not None: path.chmod(mode)

def contract_check(_:argparse.Namespace)->int:
    _,cs=contract(); print("NMP_R2_CONTRACT_OK",json.dumps({"contract_sha256":cs,**R.source_identity()},sort_keys=True)); return 0

def prior_check(a:argparse.Namespace)->int:
    f=prior(Path(a.final),Path(a.preflight_manifest),Path(a.binary))
    print("NMP_R2_R1_OK",json.dumps({"run_id":f["run_id"],"nodes_ci95_lower":f["NODES"]["paired_statistics"]["ci95_lower"]},sort_keys=True)); return 0

def preflight(a:argparse.Namespace)->int:
    x,cs=contract(); ident=R.source_identity(); rid,att=R.require_first_attempt()
    b=Path(a.binary); pf=Path(a.prior_final); pm=Path(a.prior_preflight_manifest); old=prior(pf,pm,b)
    op=freeze(valid_openings(b,x),x,cs); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    atomic(out/"te1",b.read_bytes(),0o755); atomic(out/"nmp-r1-final.json",pf.read_bytes()); atomic(out/"nmp-r1-preflight.json",pm.read_bytes())
    R.write_json(out/"nmp-r2-time-openings.json",op)
    m={"schema":SP,"campaign_id":x["campaign_id"],"contract_sha256":cs,"run_id":rid,"run_attempt":att,**ident,"binary_sha256":R.sha256_file(out/"te1"),"r1_final_json_sha256":R.sha256_file(out/"nmp-r1-final.json"),"r1_preflight_manifest_sha256":R.sha256_file(out/"nmp-r1-preflight.json"),"r1_nodes_ci95_lower":old["NODES"]["paired_statistics"]["ci95_lower"],"openings_file_sha256":R.sha256_file(out/"nmp-r2-time-openings.json"),"time_selection_sha256":op["selection_sha256"],"status":"PASS"}
    R.write_json(out/"nmp-r2-time-preflight.json",m); print("NMP_R2_PREFLIGHT",json.dumps(m,sort_keys=True)); return 0

def load_pf(d:Path):
    x,cs=contract(); ident=R.source_identity(); rid,att=R.require_first_attempt()
    b=d/"te1"; ff=d/"nmp-r1-final.json"; pm=d/"nmp-r1-preflight.json"; opath=d/"nmp-r2-time-openings.json"; m=R.load_json(d/"nmp-r2-time-preflight.json"); op=R.load_json(opath); old=prior(ff,pm,b)
    req={"schema":SP,"campaign_id":x["campaign_id"],"contract_sha256":cs,"run_id":rid,"run_attempt":att,**ident,"binary_sha256":R.sha256_file(b),"r1_final_json_sha256":R.sha256_file(ff),"r1_preflight_manifest_sha256":R.sha256_file(pm),"r1_nodes_ci95_lower":old["NODES"]["paired_statistics"]["ci95_lower"],"openings_file_sha256":R.sha256_file(opath),"status":"PASS"}
    for k,v in req.items():
        if m.get(k)!=v: raise ProofError(f"R2 preflight mismatch: {k}")
    if op.get("schema")!=SO or op.get("contract_sha256")!=cs or m.get("time_selection_sha256")!=op.get("selection_sha256"): raise ProofError("R2 opening freeze mismatch")
    return x,m,op,old,cs,b

def shard(a:argparse.Namespace)->int:
    n=int(a.shard); out=Path(a.output)
    try:
        x,m,op,_,cs,b=load_pf(Path(a.preflight)); pps=x["sharding"]["pairs_per_shard"]
        if not 0<=n<x["sharding"]["shards"]: raise ProofError("invalid shard")
        sel=op["openings"][n*pps:(n+1)*pps]
        if len(sel)!=pps: raise ProofError("incomplete shard")
        val=R.new_engine(b,False,x); pairs=[]
        try:
            for o in sel:
                g1=R.play_game(b,x,MODE,o,True); g2=R.play_game(b,x,MODE,o,False); R.reconcile_game(val,x,o,g1); R.reconcile_game(val,x,o,g2)
                pts=R.game_points(g1["on_result"])+R.game_points(g2["on_result"]); pairs.append({"opening_valid_rank":o["valid_rank"],"opening_fen_sha256":o["fen_sha256"],"games":[g1,g2],"pair_on_points":pts,"pair_normalized_score":pts/2})
        finally: val.close()
        games=[g for p in pairs for g in p["games"]]; w=sum(g["on_result"]=="W" for g in games); d=sum(g["on_result"]=="D" for g in games); l=len(games)-w-d; pe={str(i/2):0 for i in range(5)}
        for p in pairs: pe[str(p["pair_on_points"])]+=1
        z={"schema":SS,"status":"PASS","campaign_id":x["campaign_id"],"contract_sha256":cs,"run_id":m["run_id"],"run_attempt":m["run_attempt"],"source_head":m["source_head"],"source_tree":m["source_tree"],"binary_sha256":m["binary_sha256"],"r1_final_json_sha256":m["r1_final_json_sha256"],"openings_file_sha256":m["openings_file_sha256"],"time_selection_sha256":m["time_selection_sha256"],"mode":MODE,"shard":n,"pairs":pairs,"games":len(games),"on_wdl":{"win":w,"draw":d,"loss":l},"on_score":w+.5*d,"penta":pe,"operational_failures":0}
        R.write_json(out,z); print("NMP_R2_SHARD",json.dumps({k:v for k,v in z.items() if k!="pairs"},sort_keys=True)); return 0
    except BaseException as e:
        z={"schema":SS,"status":"BLOCKED_CORRECTNESS","mode":MODE,"shard":n,"error_type":type(e).__name__,"error":str(e),"operational_failures":1}; R.write_json(out,z); print("NMP_R2_BLOCKED",json.dumps(z,sort_keys=True),file=sys.stderr); return 2

def decision(t:dict[str,Any],n:dict[str,Any])->str:
    if float(n["ci95_lower"])<=.5: raise ProofError("R1 NODES prerequisite does not clear frozen gate")
    if float(t["ci95_lower"])>.5: return "PASS_DEFAULT_ON"
    if float(t["ci95_upper"])<.5: return "FAIL_NMP"
    return "INCONCLUSIVE"

def aggregate(a:argparse.Namespace)->int:
    out=Path(a.output)
    try:
        x,m,op,old,cs,_=load_pf(Path(a.preflight)); root=Path(a.shards); files=sorted(root.rglob("*.json")) if root.exists() else []; count=x["sharding"]["shards"]; pps=x["sharding"]["pairs_per_shard"]; rep={}; blocked=[]
        for path in files:
            z=R.load_json(path)
            if z.get("schema")!=SS: raise ProofError(f"foreign JSON: {path}")
            n=int(z.get("shard",-1))
            if n in rep: raise ProofError(f"duplicate shard {n}")
            rep[n]=z
            if z.get("status")!="PASS": blocked.append({"shard":n,"status":z.get("status"),"error":z.get("error")})
        miss=sorted(set(range(count))-set(rep)); foreign=sorted(set(rep)-set(range(count)))
        if miss or foreign or blocked:
            z={"schema":SF,"campaign_id":x["campaign_id"],"contract_sha256":cs,"decision":"BLOCKED_CORRECTNESS","missing_shards":miss,"foreign_shards":foreign,"blocked_shards":blocked,"r1_results_pooled":False,"default_on_authorized":False}; R.write_json(out,z); print("NMP_R2_FINAL",json.dumps(z,sort_keys=True)); return 2
        ident={"campaign_id":x["campaign_id"],"contract_sha256":cs,"run_id":m["run_id"],"run_attempt":m["run_attempt"],"source_head":m["source_head"],"source_tree":m["source_tree"],"binary_sha256":m["binary_sha256"],"r1_final_json_sha256":m["r1_final_json_sha256"],"openings_file_sha256":m["openings_file_sha256"],"time_selection_sha256":m["time_selection_sha256"],"mode":MODE,"operational_failures":0}
        frozen={int(o["valid_rank"]):o for o in op["openings"]}; seen=set(); pairs=[]; base=x["opening_selection"]["time_confirmatory"]["valid_rank_start"]
        for n in range(count):
            z=rep[n]
            if any(z.get(k)!=v for k,v in ident.items()) or len(z.get("pairs",[]))!=pps or z.get("games")!=2*pps: raise ProofError(f"shard {n} identity/cardinality drift")
            er=set(range(base+n*pps,base+(n+1)*pps)); ar={int(p["opening_valid_rank"]) for p in z["pairs"]}
            if ar!=er: raise ProofError(f"shard {n} rank slice drift")
            for p in z["pairs"]:
                rank=int(p["opening_valid_rank"]); o=frozen.get(rank)
                if rank in seen or o is None or p.get("opening_fen_sha256")!=o["fen_sha256"] or len(p.get("games",[]))!=2: raise ProofError(f"pair evidence drift at rank {rank}")
                for g in p["games"]: R.validate_game_record(x,p,g)
                if {g.get("off_color") for g in p["games"]}!={"white","black"}: raise ProofError("color reversal drift")
                pts=sum(R.game_points(g["on_result"]) for g in p["games"])
                if abs(pts-float(p["pair_on_points"]))>1e-12 or abs(pts/2-float(p["pair_normalized_score"]))>1e-12: raise ProofError("pair score drift")
                seen.add(rank); pairs.append(p)
        if seen!=set(frozen) or len(pairs)!=1024: raise ProofError("R2 opening/pair set not exact")
        ts=R.summarize_arm(MODE,pairs); nodes=old["NODES"]; dec=decision(ts["paired_statistics"],nodes)
        z={"schema":SF,"campaign_id":x["campaign_id"],"contract_sha256":cs,"run_id":m["run_id"],"run_attempt":m["run_attempt"],"source_head":m["source_head"],"source_tree":m["source_tree"],"binary_sha256":m["binary_sha256"],"r1_final_json_sha256":m["r1_final_json_sha256"],"openings_file_sha256":m["openings_file_sha256"],"time_selection_sha256":m["time_selection_sha256"],"total_pairs":1024,"total_games":2048,"operational_failures":0,"R1_NODES_PREREQUISITE":nodes,"R2_TIME":ts,"decision":dec,"r1_results_pooled":False,"default_on_authorized":dec=="PASS_DEFAULT_ON"}
        R.write_json(out,z); print("NMP_R2_FINAL",json.dumps(z,sort_keys=True)); return 0
    except BaseException as e:
        z={"schema":SF,"decision":"BLOCKED_CORRECTNESS","error_type":type(e).__name__,"error":str(e),"r1_results_pooled":False,"default_on_authorized":False}; R.write_json(out,z); print("NMP_R2_FINAL_BLOCKED",json.dumps(z,sort_keys=True),file=sys.stderr); return 2

def parser():
    p=argparse.ArgumentParser(description="TE1 Alpha 2.6 NMP R2 TIME-only 2048-game confirmation"); s=p.add_subparsers(dest="command",required=True)
    q=s.add_parser("contract-check"); q.set_defaults(func=contract_check)
    q=s.add_parser("prior-check"); q.add_argument("--final",required=True); q.add_argument("--preflight-manifest",required=True); q.add_argument("--binary",required=True); q.set_defaults(func=prior_check)
    q=s.add_parser("preflight"); q.add_argument("--binary",required=True); q.add_argument("--prior-final",required=True); q.add_argument("--prior-preflight-manifest",required=True); q.add_argument("--out",required=True); q.set_defaults(func=preflight)
    q=s.add_parser("shard"); q.add_argument("--preflight",required=True); q.add_argument("--shard",required=True,type=int); q.add_argument("--output",required=True); q.set_defaults(func=shard)
    q=s.add_parser("aggregate"); q.add_argument("--preflight",required=True); q.add_argument("--shards",required=True); q.add_argument("--output",required=True); q.set_defaults(func=aggregate)
    return p

def main()->int:
    a=parser().parse_args(); return int(a.func(a))
if __name__=="__main__": raise SystemExit(main())
