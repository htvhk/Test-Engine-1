from __future__ import annotations
import gzip, json
from pathlib import Path
import numpy as np
from te1_b1.dataset import prepare_split


def test_prepare_single_contract_row(tmp_path: Path):
    row = {"fen":"8/8/8/8/8/8/4k3/4K3 w - - 0 1","split":"train","teacher_pov":"side-to-move","side_to_move":"w","position_id":"p1","nnue_observation_key":"o1","game_id":"g1","teacher_cp":0,"teacher_wdl":[0.1,0.8,0.1],"game_result_for_side_to_move":0,"phase":"endgame","material_bucket":"equal","tacticality":"quiet","source_kind":"raw-pgn"}
    inp=tmp_path/'x.jsonl.gz'; out=tmp_path/'x.npz'
    with gzip.open(inp,'wt') as h: h.write(json.dumps(row)+'\n')
    report=prepare_split(inp,out,'train')
    assert report['rows']==1
    with np.load(out) as data:
        assert data['white'].shape==(1,31)
        assert data['teacher_wdl'].shape==(1,3)
