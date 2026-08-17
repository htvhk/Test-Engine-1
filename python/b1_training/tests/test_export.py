from __future__ import annotations
from pathlib import Path
import torch
from te1_b1.export import compare_float_and_quantized, export_te1nn, load_te1nn_as_model
from te1_b1.features import encode_fen
from te1_b1.model import CandidateSpec, Te1Nnue


def test_te1nn_roundtrip(tmp_path: Path):
    torch.manual_seed(7)
    model = Te1Nnue(CandidateSpec("test", 128, 32)).eval()
    path = tmp_path / "test.te1nn"
    export_te1nn(model, path)
    loaded = load_te1nn_as_model(path)
    w, b, stm = encode_fen("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    report = compare_float_and_quantized(model, loaded, torch.tensor([w]), torch.tensor([b]), torch.tensor([stm]))
    assert report["max_wdl_probability_error"] < 0.01
    assert report["max_cp_error"] < 15.0
