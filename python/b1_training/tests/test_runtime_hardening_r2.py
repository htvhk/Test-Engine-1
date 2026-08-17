from __future__ import annotations

from te1_b1.model import CandidateSpec
from scripts.train_production import checkpoint_signature, validate_saved_compile_selection, validate_completed_run_payload


def test_checkpoint_signature_changes_when_gpu_identity_changes() -> None:
    spec = CandidateSpec("k32-w128-h32-crelu", 128, 32)
    base = {
        "epochs": 40,
        "torch_version": "x",
        "cuda_runtime": "y",
        "hardware": {"device_name": "T4", "compute_capability": [7, 5]},
    }
    other = {
        **base,
        "hardware": {"device_name": "L4", "compute_capability": [8, 9]},
    }
    a = checkpoint_signature("p", "s", spec, 1, "eager", base)
    b = checkpoint_signature("p", "s", spec, 1, "eager", other)
    assert a != b


def test_compile_selection_rejects_selected_mode_disagreement() -> None:
    reports = []
    selected = {}
    for name in ("k32-w128-h32-crelu", "k32-w256-h32-crelu"):
        reports.append({
            "status": "PASS",
            "candidate": {"name": name},
            "selected_mode": "eager",
        })
        selected[name] = "eager"
    saved = {"status": "PASS", "signature": "sig", "reports": reports, "selected_modes": selected}
    _, normalized = validate_saved_compile_selection(saved, "sig")
    assert normalized == selected
    saved["selected_modes"] = {**selected, "k32-w128-h32-crelu": "default"}
    import pytest
    with pytest.raises(RuntimeError, match="disagrees"):
        validate_saved_compile_selection(saved, "sig")


def test_completed_run_summary_must_match_hashed_checkpoint(tmp_path) -> None:
    import torch
    from te1_b1.io_utils import sha256_file
    spec = CandidateSpec("k32-w128-h32-crelu", 128, 32)
    signature = "sig"
    best = tmp_path / "best.pt"
    development = {"composite": 0.5}
    torch.save({
        "signature": signature,
        "epoch": 3,
        "spec": spec.to_dict(),
        "seed": 7,
        "mode": "eager",
        "development": development,
    }, best)
    digest = sha256_file(best)
    run = {
        "status": "PASS",
        "candidate": spec.to_dict(),
        "seed": 7,
        "mode": "eager",
        "best_epoch": 3,
        "best_development": development,
        "best_checkpoint": str(best),
        "best_checkpoint_sha256": digest,
    }
    completed = {
        "status": "PASS",
        "signature": signature,
        "best_checkpoint": str(best),
        "best_checkpoint_sha256": digest,
        "run": run,
    }
    assert validate_completed_run_payload(completed, signature, spec, 7, "eager", tmp_path) == run
    completed["run"] = {**run, "best_development": {"composite": 0.1}}
    import pytest
    with pytest.raises(RuntimeError, match="disagrees"):
        validate_completed_run_payload(completed, signature, spec, 7, "eager", tmp_path)
