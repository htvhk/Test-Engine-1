from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import random
import time
from pathlib import Path
import sys

import numpy as np
import torch


# Make each CLI entry point runnable directly, independent of notebook PYTHONPATH state.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from te1_b1.dataset import PreparedDataset
from te1_b1.io_utils import sha256_file, strict_dump
from te1_b1.losses import composite_loss
from te1_b1.metrics import evaluate
from te1_b1.model import CANDIDATE_SPECS, CandidateSpec, Te1Nnue, parameter_count

MODES = ("eager", "default", "reduce-overhead")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def compile_model(model: Te1Nnue, mode: str):
    if mode == "eager":
        return model
    if not hasattr(torch, "compile"):
        raise RuntimeError("torch.compile is unavailable")
    return torch.compile(model, backend="inductor", mode=mode, fullgraph=True)


def optimizer_for(model: Te1Nnue, lr: float) -> torch.optim.Optimizer:
    return torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)


def optimizer_state_to_device(optimizer: torch.optim.Optimizer, device: torch.device) -> None:
    for state in optimizer.state.values():
        for key, value in list(state.items()):
            if torch.is_tensor(value):
                state[key] = value.to(device)


def move_batch(batch: dict[str, torch.Tensor], device: torch.device) -> dict[str, torch.Tensor]:
    return {key: value.to(device, non_blocking=True) for key, value in batch.items()}


def train_epoch(
    run_model,
    base_model: Te1Nnue,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler,
    dataset: PreparedDataset,
    device: torch.device,
    batch_size: int,
    shuffle_seed: int,
) -> dict[str, float]:
    run_model.train()
    batches = dataset.batch_indices(batch_size, shuffle_seed=shuffle_seed)
    loss_sum = 0.0
    seen = 0
    started = time.perf_counter()
    for batch_no, indices in enumerate(batches):
        valid = min(batch_size, len(dataset) - batch_no * batch_size)
        batch = move_batch(dataset.get_batch(indices, valid), device)
        optimizer.zero_grad(set_to_none=True)
        with torch.amp.autocast(device_type="cuda", dtype=torch.float16, enabled=device.type == "cuda"):
            logits, cp_norm = run_model(batch["white"], batch["black"], batch["stm"])
            loss, _ = composite_loss(
                logits, cp_norm, batch["teacher_wdl"], batch["teacher_cp_norm"],
                batch["result_class"], batch["sample_mask"],
            )
        if not torch.isfinite(loss):
            raise RuntimeError("non-finite training loss")
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        torch.nn.utils.clip_grad_norm_(base_model.parameters(), 5.0)
        scaler.step(optimizer)
        scaler.update()
        with torch.no_grad():
            base_model.feature.weight[-1].zero_()
        loss_sum += float(loss.item()) * valid
        seen += valid
    if device.type == "cuda":
        torch.cuda.synchronize()
    elapsed = time.perf_counter() - started
    return {"loss": loss_sum / max(1, seen), "seconds": elapsed, "positions_per_second": seen / max(elapsed, 1e-9)}


def same_weight_semantic_check(spec: CandidateSpec, mode: str, dataset: PreparedDataset, device: torch.device, batch_size: int, seed: int) -> dict[str, object]:
    set_seed(seed)
    eager_model = Te1Nnue(spec).to(device)
    candidate_model = Te1Nnue(spec).to(device)
    candidate_model.load_state_dict(copy.deepcopy(eager_model.state_dict()))
    run_candidate = compile_model(candidate_model, mode)
    indices = dataset.batch_indices(batch_size, shuffle_seed=seed)[0]
    batch = move_batch(dataset.get_batch(indices, batch_size), device)

    eager_model.zero_grad(set_to_none=True); candidate_model.zero_grad(set_to_none=True)
    with torch.amp.autocast(device_type="cuda", dtype=torch.float16, enabled=device.type == "cuda"):
        eager_logits, eager_cp = eager_model(batch["white"], batch["black"], batch["stm"])
        eager_loss, _ = composite_loss(eager_logits, eager_cp, batch["teacher_wdl"], batch["teacher_cp_norm"], batch["result_class"], batch["sample_mask"])
        cand_logits, cand_cp = run_candidate(batch["white"], batch["black"], batch["stm"])
        cand_loss, _ = composite_loss(cand_logits, cand_cp, batch["teacher_wdl"], batch["teacher_cp_norm"], batch["result_class"], batch["sample_mask"])
    eager_loss.backward(); cand_loss.backward()
    if device.type == "cuda": torch.cuda.synchronize()
    logits_error = float((eager_logits.float() - cand_logits.float()).abs().max().item())
    cp_error = float((eager_cp.float() - cand_cp.float()).abs().max().item())
    loss_error = abs(float(eager_loss.detach().float().item()) - float(cand_loss.detach().float().item()))
    max_grad_abs_error = 0.0; max_grad_relative_error = 0.0; min_grad_cosine = 1.0
    for (name_a, param_a), (name_b, param_b) in zip(eager_model.named_parameters(), candidate_model.named_parameters()):
        if name_a != name_b or param_a.grad is None or param_b.grad is None:
            raise RuntimeError(f"gradient pairing failure: {name_a} vs {name_b}")
        ga = param_a.grad.detach().float(); gb = param_b.grad.detach().float()
        error = float((ga - gb).abs().max().item())
        reference = max(float(ga.abs().max().item()), 1e-8)
        relative = error / reference
        denom = float(torch.linalg.vector_norm(ga).item() * torch.linalg.vector_norm(gb).item())
        cosine = float(torch.sum(ga * gb).item() / denom) if denom > 0.0 else 1.0
        max_grad_abs_error = max(max_grad_abs_error, error)
        max_grad_relative_error = max(max_grad_relative_error, relative)
        min_grad_cosine = min(min_grad_cosine, cosine)
    passed = logits_error <= 2e-3 and cp_error <= 2e-3 and loss_error <= 2e-4 and max_grad_relative_error <= 0.02 and min_grad_cosine >= 0.999
    return {
        "status": "PASS" if passed else "FAIL",
        "max_logits_abs_error": logits_error,
        "max_cp_normalized_abs_error": cp_error,
        "loss_abs_error": loss_error,
        "max_unscaled_gradient_abs_error": max_grad_abs_error,
        "max_unscaled_gradient_relative_error": max_grad_relative_error,
        "min_unscaled_gradient_cosine": min_grad_cosine,
    }


def full_epoch_mode_benchmark(spec: CandidateSpec, mode: str, train: PreparedDataset, dev: PreparedDataset, device: torch.device, batch_size: int, lr: float, seed: int, warmup_steps: int) -> dict[str, object]:
    set_seed(seed)
    base = Te1Nnue(spec).to(device)
    initial_state = copy.deepcopy(base.state_dict())
    run_model = compile_model(base, mode)
    # Compile/warm-up is intentionally excluded from timed throughput and then weights are reset.
    warm_optimizer = optimizer_for(base, lr)
    warm_scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    batches = train.batch_indices(batch_size, shuffle_seed=seed + 11)
    for batch_no, indices in enumerate(batches[:warmup_steps]):
        batch = move_batch(train.get_batch(indices, batch_size), device)
        warm_optimizer.zero_grad(set_to_none=True)
        with torch.amp.autocast(device_type="cuda", dtype=torch.float16, enabled=device.type == "cuda"):
            logits, cp_norm = run_model(batch["white"], batch["black"], batch["stm"])
            loss, _ = composite_loss(logits, cp_norm, batch["teacher_wdl"], batch["teacher_cp_norm"], batch["result_class"], batch["sample_mask"])
        warm_scaler.scale(loss).backward(); warm_scaler.step(warm_optimizer); warm_scaler.update()
    if device.type == "cuda": torch.cuda.synchronize()
    base.load_state_dict(initial_state)
    optimizer = optimizer_for(base, lr)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    epoch = train_epoch(run_model, base, optimizer, scaler, train, device, batch_size, seed + 101)
    if device.type == "cuda": torch.cuda.synchronize()
    dev_started = time.perf_counter()
    development = evaluate(run_model, dev, device, batch_size)
    if device.type == "cuda": torch.cuda.synchronize()
    development_seconds = time.perf_counter() - dev_started
    workflow_seconds = float(epoch["seconds"]) + development_seconds
    workflow_pps = (len(train) + len(dev)) / max(workflow_seconds, 1e-9)
    return {"mode": mode, "epoch": epoch, "development": development, "development_seconds": development_seconds, "workflow_seconds": workflow_seconds, "workflow_positions_per_second": workflow_pps}


def benchmark_modes(spec: CandidateSpec, train: PreparedDataset, dev: PreparedDataset, device: torch.device, batch_size: int, lr: float, seed: int, warmup_steps: int) -> dict[str, object]:
    results: list[dict[str, object]] = []
    eager_reference = None
    for mode in MODES:
        record: dict[str, object] = {"mode": mode}
        try:
            semantic = {"status": "PASS", "max_logits_abs_error": 0.0, "max_cp_normalized_abs_error": 0.0} if mode == "eager" else same_weight_semantic_check(spec, mode, train, device, batch_size, seed)
            record["same_weight_semantics"] = semantic
            if semantic["status"] != "PASS":
                raise RuntimeError("same-weight semantic check failed")
            benchmark = full_epoch_mode_benchmark(spec, mode, train, dev, device, batch_size, lr, seed, warmup_steps)
            record.update(benchmark)
            record["status"] = "PASS"
            if mode == "eager": eager_reference = record
        except Exception as exc:
            record["status"] = "FAIL"
            record["error"] = f"{type(exc).__name__}: {exc}"
        results.append(record)
        if device.type == "cuda":
            torch.cuda.empty_cache()
    if eager_reference is None or eager_reference.get("status") != "PASS":
        raise RuntimeError("eager full-epoch benchmark failed")
    eager_dev = float(eager_reference["development"]["composite"])
    eager_pps = float(eager_reference["workflow_positions_per_second"])
    valid_compiled = []
    for record in results:
        if record["mode"] == "eager" or record.get("status") != "PASS":
            continue
        dev_delta = abs(float(record["development"]["composite"]) - eager_dev)
        speedup = float(record["workflow_positions_per_second"]) / eager_pps
        record["development_composite_abs_delta_vs_eager"] = dev_delta
        record["speedup_vs_eager"] = speedup
        record["full_epoch_semantic_parity"] = dev_delta <= 1e-3
        if dev_delta <= 1e-3 and speedup >= 1.02:
            valid_compiled.append(record)
    selected = max(valid_compiled, key=lambda x: float(x["workflow_positions_per_second"]))["mode"] if valid_compiled else "eager"
    return {"status": "PASS", "candidate": spec.to_dict(), "results": results, "selected_mode": selected, "policy": "compile only when same-weight forward/loss/gradient parity, <=1e-3 full-epoch dev composite delta, and >=1.02x train+development workflow speedup"}


def checkpoint_signature(parent_sha: str, source_sha: str, spec: CandidateSpec, seed: int, mode: str, config: dict[str, object]) -> str:
    payload = json.dumps({"parent": parent_sha, "source": source_sha, "spec": spec.to_dict(), "seed": seed, "mode": mode, "config": config}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_saved_compile_selection(saved: dict[str, object], expected_signature: str) -> tuple[list[dict[str, object]], dict[str, str]]:
    if saved.get("status") != "PASS" or saved.get("signature") != expected_signature:
        raise RuntimeError("persisted compile-selection signature/status mismatch")
    reports = saved.get("reports")
    selected = saved.get("selected_modes")
    if not isinstance(reports, list) or not isinstance(selected, dict):
        raise RuntimeError("persisted compile selection has invalid structure")
    if len(reports) != len(CANDIDATE_SPECS):
        raise RuntimeError("persisted compile selection has wrong report count")
    derived: dict[str, str] = {}
    expected_names = {spec.name for spec in CANDIDATE_SPECS}
    for report in reports:
        if not isinstance(report, dict) or report.get("status") != "PASS":
            raise RuntimeError("persisted compile selection contains a non-PASS report")
        candidate = report.get("candidate")
        name = candidate.get("name") if isinstance(candidate, dict) else None
        mode = report.get("selected_mode")
        if name not in expected_names or mode not in MODES or name in derived:
            raise RuntimeError("persisted compile selection report identity/mode is malformed")
        derived[str(name)] = str(mode)
    normalized = {str(k): str(v) for k, v in selected.items()}
    if set(normalized) != expected_names or normalized != derived:
        raise RuntimeError("persisted selected_modes disagrees with its compile reports")
    return [dict(report) for report in reports], normalized


def validate_completed_run_payload(completed: dict[str, object], signature: str, spec: CandidateSpec, seed: int, mode: str, run_dir: Path) -> dict[str, object]:
    if completed.get("status") != "PASS" or completed.get("signature") != signature:
        raise RuntimeError(f"completed-run signature/status mismatch for {run_dir}")
    run = completed.get("run")
    if not isinstance(run, dict):
        raise RuntimeError(f"completed-run summary missing for {run_dir}")
    if run.get("status") != "PASS" or run.get("candidate") != spec.to_dict() or int(run.get("seed", -1)) != seed or run.get("mode") != mode:
        raise RuntimeError(f"completed-run identity mismatch for {run_dir}")
    completed_best = Path(str(completed.get("best_checkpoint", "")))
    if not completed_best.is_file():
        raise RuntimeError(f"completed-run best checkpoint missing for {run_dir}")
    checkpoint_sha = sha256_file(completed_best)
    if checkpoint_sha != completed.get("best_checkpoint_sha256") or checkpoint_sha != run.get("best_checkpoint_sha256"):
        raise RuntimeError(f"completed-run best checkpoint hash mismatch for {run_dir}")
    if Path(str(run.get("best_checkpoint", ""))) != completed_best:
        raise RuntimeError(f"completed-run checkpoint path mismatch for {run_dir}")
    best = torch.load(completed_best, map_location="cpu", weights_only=False)
    if best.get("signature") != signature or best.get("spec") != spec.to_dict() or int(best.get("seed", -1)) != seed or best.get("mode") != mode:
        raise RuntimeError(f"completed-run checkpoint metadata mismatch for {run_dir}")
    if int(run.get("best_epoch", -1)) != int(best.get("epoch", -2)) or run.get("best_development") != best.get("development"):
        raise RuntimeError(f"completed-run summary disagrees with checkpoint for {run_dir}")
    return dict(run)


def train_run(spec: CandidateSpec, seed: int, mode: str, train: PreparedDataset, dev: PreparedDataset, device: torch.device, root: Path, config: dict[str, object], parent_sha: str, source_sha: str) -> dict[str, object]:
    run_dir = root / spec.name / f"seed-{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    resume_path = run_dir / "resume.pt"
    best_path = run_dir / "best.pt"
    signature = checkpoint_signature(parent_sha, source_sha, spec, seed, mode, config)
    complete_path = run_dir / "run-complete.json"
    if complete_path.is_file():
        completed = json.loads(complete_path.read_text(encoding="utf-8"))
        run = validate_completed_run_payload(completed, signature, spec, seed, mode, run_dir)
        print(f"Reusing completed training run: {spec.name} seed={seed}", flush=True)
        return run
    set_seed(seed)
    base = Te1Nnue(spec).to(device)
    optimizer = optimizer_for(base, float(config["learning_rate"]))
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    start_epoch = 0; best_metric = float("inf"); patience_used = 0; history = []
    if resume_path.is_file():
        checkpoint = torch.load(resume_path, map_location="cpu", weights_only=False)
        if checkpoint.get("signature") != signature:
            raise RuntimeError(f"resume signature mismatch for {run_dir}")
        base.load_state_dict(checkpoint["model"]); optimizer.load_state_dict(checkpoint["optimizer"]); optimizer_state_to_device(optimizer, device); scaler.load_state_dict(checkpoint["scaler"])
        start_epoch = int(checkpoint["epoch"]) + 1
        best_metric = float(checkpoint["best_metric"]); patience_used = int(checkpoint["patience_used"]); history = list(checkpoint["history"])
    run_model = compile_model(base, mode)
    max_epochs = int(config["epochs"]); patience = int(config["patience"]); batch_size = int(config["batch_size"])
    for epoch in range(start_epoch, max_epochs):
        train_stats = train_epoch(run_model, base, optimizer, scaler, train, device, batch_size, seed + epoch * 10_007)
        dev_metrics = evaluate(run_model, dev, device, batch_size)
        metric = float(dev_metrics["composite"])
        improved = metric < best_metric - float(config["min_delta"])
        if improved:
            best_metric = metric; patience_used = 0
            torch.save({"signature": signature, "epoch": epoch, "model": {k: v.detach().cpu() for k, v in base.state_dict().items()}, "spec": spec.to_dict(), "seed": seed, "mode": mode, "development": dev_metrics}, best_path)
        else:
            patience_used += 1
        history.append({"epoch": epoch, "train": train_stats, "development": dev_metrics, "improved": improved})
        checkpoint = {"signature": signature, "epoch": epoch, "model": {k: v.detach().cpu() for k, v in base.state_dict().items()}, "optimizer": optimizer.state_dict(), "scaler": scaler.state_dict(), "best_metric": best_metric, "patience_used": patience_used, "history": history}
        temp = resume_path.with_suffix(".tmp.pt"); torch.save(checkpoint, temp); os.replace(temp, resume_path)
        print(f"{spec.name} seed={seed} epoch={epoch+1}/{max_epochs} dev={metric:.6f} best={best_metric:.6f} patience={patience_used}/{patience}", flush=True)
        if patience_used >= patience:
            break
    if not best_path.is_file():
        raise RuntimeError("training produced no best checkpoint")
    best = torch.load(best_path, map_location="cpu", weights_only=False)
    run_result = {"status": "PASS", "candidate": spec.to_dict(), "seed": seed, "mode": mode, "best_epoch": int(best["epoch"]), "best_development": best["development"], "best_checkpoint": str(best_path), "best_checkpoint_sha256": sha256_file(best_path), "epochs_completed": len(history), "parameter_count": parameter_count(base)}
    strict_dump({"status": "PASS", "signature": signature, "best_checkpoint": str(best_path), "best_checkpoint_sha256": run_result["best_checkpoint_sha256"], "run": run_result}, complete_path)
    # Optimizer/scaler state is only needed while a run is incomplete; remove it once the best checkpoint is sealed.
    resume_path.unlink(missing_ok=True)
    return run_result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--parent-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--learning-rate", type=float, default=0.0015)
    parser.add_argument("--min-delta", type=float, default=1e-4)
    parser.add_argument("--seeds", default="20260816,20260817")
    parser.add_argument("--warmup-steps", type=int, default=8)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("B.1 production training requires CUDA; select a GPU runtime")
    device = torch.device("cuda")
    train = PreparedDataset(args.prepared_root / "train.npz")
    dev = PreparedDataset(args.prepared_root / "development.npz")
    hardware = {
        "device_name": torch.cuda.get_device_name(0),
        "compute_capability": list(torch.cuda.get_device_capability(0)),
    }
    config = {
        "epochs": args.epochs,
        "patience": args.patience,
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "min_delta": args.min_delta,
        "warmup_steps": args.warmup_steps,
        "torch_version": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        # Hardware identity is part of every resume/selection signature. This prevents
        # reusing a T4/L4/A100 performance decision or continuing an optimizer state
        # after an unrecorded GPU-class change on Colab reconnect.
        "hardware": hardware,
    }
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    if len(set(seeds)) < 2:
        raise RuntimeError("B.1 requires at least two independent seeds")
    args.state_root.mkdir(parents=True, exist_ok=True)
    selection_path = args.state_root / "compile-selection.json"
    selection_payload = {
        "parent": args.parent_sha256,
        "source": args.source_sha256,
        "config": config,
        "candidates": [spec.to_dict() for spec in CANDIDATE_SPECS],
    }
    selection_signature = hashlib.sha256(json.dumps(selection_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    mode_reports = []
    selected_modes = {}
    if selection_path.is_file():
        saved_selection = json.loads(selection_path.read_text(encoding="utf-8"))
        mode_reports, selected_modes = validate_saved_compile_selection(saved_selection, selection_signature)
        print("Reusing sealed compile-mode selection", flush=True)
    else:
        for spec in CANDIDATE_SPECS:
            report = benchmark_modes(spec, train, dev, device, args.batch_size, args.learning_rate, seeds[0] + 700_000, args.warmup_steps)
            mode_reports.append(report); selected_modes[spec.name] = report["selected_mode"]
            print(f"compile selection {spec.name}: {report['selected_mode']}", flush=True)
        strict_dump({
            "status": "PASS",
            "signature": selection_signature,
            "benchmark_hardware": hardware,
            "reports": mode_reports,
            "selected_modes": selected_modes,
        }, selection_path)
    runs = []
    for spec in CANDIDATE_SPECS:
        for seed in seeds:
            runs.append(train_run(spec, seed, str(selected_modes[spec.name]), train, dev, device, args.state_root, config, args.parent_sha256, args.source_sha256))
    family_winners = []
    for spec in CANDIDATE_SPECS:
        family = [run for run in runs if run["candidate"]["name"] == spec.name]
        winner = min(family, key=lambda x: float(x["best_development"]["composite"]))
        family_winners.append(winner)
    provisional = min(family_winners, key=lambda x: float(x["best_development"]["composite"]))
    result = {
        "status": "PASS",
        "parent_sha256": args.parent_sha256,
        "source_sha256": args.source_sha256,
        "device": hardware["device_name"],
        "compute_capability": hardware["compute_capability"],
        "torch": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "config": config,
        "seeds": seeds,
        "compile_mode_reports": mode_reports,
        "selected_modes": selected_modes,
        "runs": runs,
        "family_winners": family_winners,
        "provisional_winner": {
            "candidate": provisional["candidate"]["name"],
            "seed": provisional["seed"],
            "criterion": "lowest development composite only; reserve may veto and D.3 paired-match/SPRT remains authoritative",
        },
    }
    strict_dump(result, args.report)
    print(json.dumps(result["provisional_winner"], indent=2))


if __name__ == "__main__":
    main()
