from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import numpy as np
import pytest
import torch

from te1_b1.dataset import CP_TARGET_CLIP, audit_raw_identity_isolation, prepare_split
from te1_b1.export import export_te1nn, load_te1nn_as_model
from te1_b1.model import CandidateSpec, Te1Nnue


def _row(split: str, pid: str, obs: str, gid: str, cp: int = 0) -> dict[str, object]:
    return {
        "fen": "8/8/8/8/8/8/4k3/4K3 w - - 0 1",
        "split": split,
        "teacher_pov": "side-to-move",
        "side_to_move": "w",
        "position_id": pid,
        "nnue_observation_key": obs,
        "game_id": gid,
        "teacher_cp": cp,
        "teacher_wdl": [0.1, 0.8, 0.1],
        "game_result_for_side_to_move": 0,
        "phase": "endgame",
        "material_bucket": "equal",
        "tacticality": "quiet",
        "source_kind": "raw-pgn",
    }


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_cp_regression_target_is_clipped_but_raw_score_is_preserved(tmp_path: Path):
    inp = tmp_path / "x.jsonl.gz"
    out = tmp_path / "x.npz"
    _write(inp, [_row("train", "p", "o", "g", cp=31999)])
    report = prepare_split(inp, out, "train")
    assert report["cp_clipped_rows"] == 1
    with np.load(out) as data:
        assert int(data["teacher_cp"][0]) == CP_TARGET_CLIP
        assert int(data["teacher_cp_raw"][0]) == 31999


def test_global_identity_and_whole_game_split_isolation(tmp_path: Path):
    paths = {}
    for split, suffix in (("train", "t"), ("development", "d"), ("reserve", "r")):
        path = tmp_path / f"{split}.jsonl.gz"
        _write(path, [_row(split, f"p-{suffix}", f"o-{suffix}", f"g-{suffix}")])
        paths[split] = path
    report = audit_raw_identity_isolation(paths)
    assert report["unique_position_ids"] == 3
    assert report["whole_game_split_isolation"] is True

    _write(paths["reserve"], [_row("reserve", "p-r2", "o-r2", "g-t")])
    with pytest.raises(ValueError, match="whole-game split isolation failure"):
        audit_raw_identity_isolation(paths)


def test_te1nn_loader_rejects_incompatible_feature_metadata(tmp_path: Path):
    model = Te1Nnue(CandidateSpec("test", 128, 32)).eval()
    path = tmp_path / "x.te1nn"
    export_te1nn(model, path)
    raw = bytearray(path.read_bytes())
    metadata_len = struct.unpack_from("<I", raw, 12)[0]
    metadata_start = 16
    metadata = json.loads(bytes(raw[metadata_start : metadata_start + metadata_len]))
    metadata["feature_set"] = "BAD-FEATURE-SET"
    replacement = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert len(replacement) == metadata_len
    raw[metadata_start : metadata_start + metadata_len] = replacement
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="incompatible TE1NN feature metadata"):
        load_te1nn_as_model(path)
