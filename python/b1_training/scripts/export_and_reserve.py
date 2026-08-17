from __future__ import annotations

import argparse
import gzip
import json
import shutil
from pathlib import Path
import sys

import torch
import torch.nn.functional as F


# Make each CLI entry point runnable directly, independent of notebook PYTHONPATH state.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from te1_b1.dataset import PreparedDataset
from te1_b1.export import compare_float_and_quantized, export_te1nn, load_te1nn_as_model
from te1_b1.io_utils import sha256_file, strict_dump
from te1_b1.metrics import evaluate
from te1_b1.model import CandidateSpec, Te1Nnue


def load_best(run: dict, device: torch.device) -> Te1Nnue:
    checkpoint = torch.load(run["best_checkpoint"], map_location="cpu", weights_only=False)
    spec = CandidateSpec(**checkpoint["spec"])
    model = Te1Nnue(spec)
    model.load_state_dict(checkpoint["model"])
    return model.to(device).eval()


def write_references(path: Path, dataset: PreparedDataset, float_model: Te1Nnue, quant_model: Te1Nnue, count: int = 512) -> None:
    count = min(count, len(dataset))
    indices = torch.arange(count, dtype=torch.long)
    white = dataset.tensors["white"].index_select(0, indices)
    black = dataset.tensors["black"].index_select(0, indices)
    stm = dataset.tensors["stm"].index_select(0, indices)
    float_model = float_model.cpu(); quant_model = quant_model.cpu()
    with torch.no_grad():
        fl, fc = float_model(white, black, stm); ql, qc = quant_model(white, black, stm)
        fp = F.softmax(fl.float(), dim=1); qp = F.softmax(ql.float(), dim=1)
        fcp = 600.0 * torch.atanh(fc.float().clamp(-0.999999, 0.999999))
        qcp = 600.0 * torch.atanh(qc.float().clamp(-0.999999, 0.999999))
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for i in range(count):
            row = {"white_features": white[i].tolist(), "black_features": black[i].tolist(), "white_to_move": bool(stm[i]), "float_wdl": fp[i].tolist(), "float_cp": float(fcp[i]), "quantized_wdl": qp[i].tolist(), "quantized_cp": float(qcp[i])}
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--training-report", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1024)
    args = parser.parse_args()
    training = json.loads(args.training_report.read_text())
    if training.get("status") != "PASS": raise RuntimeError("training report is not PASS")
    family_winners = training["family_winners"]
    frozen_provisional = dict(training["provisional_winner"])
    reserve = PreparedDataset(args.prepared_root / "reserve.npz")
    development = PreparedDataset(args.prepared_root / "development.npz")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    shutil.rmtree(args.output_root, ignore_errors=True); args.output_root.mkdir(parents=True)
    candidates = []
    for run in family_winners:
        model = load_best(run, device)
        spec = model.spec
        dev_metrics = evaluate(model, development, device, args.batch_size)
        reserve_metrics = evaluate(model, reserve, device, args.batch_size)
        network_path = args.output_root / "networks" / f"{spec.name}.te1nn"
        export_meta = export_te1nn(model.cpu(), network_path)
        quant_model = load_te1nn_as_model(network_path)
        sample = development.get_batch(development.batch_indices(512, shuffle_seed=None)[0], 512)
        quant = compare_float_and_quantized(model.cpu(), quant_model, sample["white"], sample["black"], sample["stm"])
        quant_pass = quant["max_wdl_probability_error"] <= 0.03 and quant["max_cp_error"] <= 50.0 and quant["mean_cp_error"] <= 10.0
        dev_value = float(dev_metrics["composite"]); reserve_value = float(reserve_metrics["composite"])
        reserve_limit = dev_value + max(0.03, 0.25 * dev_value)
        reserve_pass = reserve_value <= reserve_limit
        if not quant_pass: raise RuntimeError(f"quantization gate failed for {spec.name}: {quant}")
        if not reserve_pass: raise RuntimeError(f"reserve generalization gate failed for {spec.name}: dev={dev_value}, reserve={reserve_value}, limit={reserve_limit}")
        refs = args.output_root / "reference-vectors" / f"{spec.name}.jsonl.gz"
        write_references(refs, development, model.cpu(), quant_model, 512)
        # Keep exactly the selected float checkpoint per architecture in the release.
        selected_checkpoint = args.output_root / "float-checkpoints" / f"{spec.name}.pt"
        selected_checkpoint.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(run["best_checkpoint"], selected_checkpoint)
        candidates.append({"candidate": spec.to_dict(), "seed": run["seed"], "development": dev_metrics, "reserve": reserve_metrics, "reserve_limit": reserve_limit, "quantization": quant, "network_sha256": sha256_file(network_path), "reference_sha256": sha256_file(refs), "float_checkpoint_sha256": sha256_file(selected_checkpoint), "export": export_meta})
    result = {"status": "PASS", "provisional_winner_frozen_before_reserve": frozen_provisional, "reserve_policy": "reserve may veto catastrophic generalization but cannot reorder the development-selected provisional winner", "candidates": candidates}
    strict_dump(result, args.report)
    print(json.dumps({"status": "PASS", "provisional": frozen_provisional, "candidates": [{"name": c["candidate"]["name"], "dev": c["development"]["composite"], "reserve": c["reserve"]["composite"], "network_sha256": c["network_sha256"]} for c in candidates]}, indent=2))


if __name__ == "__main__":
    main()
