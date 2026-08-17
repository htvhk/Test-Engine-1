from __future__ import annotations

import math

import numpy as np
import torch
import torch.nn.functional as F

from .dataset import CATEGORY_FIELDS, PreparedDataset
from .losses import CP_WEIGHT, RESULT_WEIGHT, WDL_WEIGHT


def cp_from_normalized(values: np.ndarray) -> np.ndarray:
    bounded = np.clip(values.astype(np.float64), -0.999999, 0.999999)
    return 600.0 * np.arctanh(bounded)


def _safe_pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2 or np.std(a) == 0.0 or np.std(b) == 0.0:
        return 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if math.isfinite(value) else 0.0


@torch.no_grad()
def evaluate(model, dataset: PreparedDataset, device: torch.device, batch_size: int) -> dict[str, object]:
    model.eval()
    all_probs: list[np.ndarray] = []
    all_cp_norm: list[np.ndarray] = []
    batches = dataset.batch_indices(batch_size, shuffle_seed=None)
    for batch_no, indices in enumerate(batches):
        valid = min(batch_size, len(dataset) - batch_no * batch_size)
        batch = dataset.get_batch(indices, valid)
        white = batch["white"].to(device, non_blocking=True)
        black = batch["black"].to(device, non_blocking=True)
        stm = batch["stm"].to(device, non_blocking=True)
        with torch.amp.autocast(device_type="cuda", dtype=torch.float16, enabled=device.type == "cuda"):
            logits, cp_norm = model(white, black, stm)
        all_probs.append(F.softmax(logits.float(), dim=1)[:valid].cpu().numpy())
        all_cp_norm.append(cp_norm.float()[:valid].cpu().numpy())
    probs = np.concatenate(all_probs)
    pred_cp_norm = np.concatenate(all_cp_norm)
    true_wdl = dataset.tensors["teacher_wdl"].numpy()
    true_cp_norm = dataset.tensors["teacher_cp_norm"].numpy()
    true_cp = dataset.tensors["teacher_cp"].numpy().astype(np.float64)
    result_class = dataset.tensors["result_class"].numpy()
    clipped_probs = np.clip(probs, 1e-9, 1.0)
    wdl_ce_rows = -(true_wdl * np.log(clipped_probs)).sum(axis=1)
    cp_huber_rows = F.smooth_l1_loss(
        torch.from_numpy(pred_cp_norm), torch.from_numpy(true_cp_norm), reduction="none", beta=0.10
    ).numpy()
    available = result_class >= 0
    if available.any():
        result_ce_rows = -np.log(clipped_probs[np.arange(len(probs))[available], result_class[available]])
        result_ce = float(result_ce_rows.mean())
        result_factor = RESULT_WEIGHT
    else:
        result_ce = 0.0
        result_factor = 0.0
    wdl_ce = float(wdl_ce_rows.mean())
    cp_huber = float(cp_huber_rows.mean())
    composite = WDL_WEIGHT * wdl_ce + CP_WEIGHT * cp_huber + result_factor * result_ce
    pred_cp = cp_from_normalized(pred_cp_norm)
    teacher_class = true_wdl.argmax(axis=1)
    metrics: dict[str, object] = {
        "rows": len(probs),
        "wdl_cross_entropy": wdl_ce,
        "wdl_brier": float(np.mean(np.sum((probs - true_wdl) ** 2, axis=1))),
        "wdl_top_class_accuracy": float(np.mean(probs.argmax(axis=1) == teacher_class)),
        "cp_huber": cp_huber,
        "cp_mae": float(np.mean(np.abs(pred_cp - true_cp))),
        "cp_pearson": _safe_pearson(pred_cp, true_cp),
        "cp_sign_accuracy_abs50": float(np.mean(np.sign(pred_cp[np.abs(true_cp) >= 50]) == np.sign(true_cp[np.abs(true_cp) >= 50]))) if np.any(np.abs(true_cp) >= 50) else 0.0,
        "result_cross_entropy": result_ce,
        "result_rows": int(available.sum()),
        "composite": float(composite),
    }
    subgroups: dict[str, dict[str, object]] = {}
    for field in CATEGORY_FIELDS:
        codes = dataset.tensors[field].numpy()
        groups: dict[str, object] = {}
        for code in sorted(set(int(x) for x in codes.tolist())):
            mask = codes == code
            groups[str(code)] = {
                "rows": int(mask.sum()),
                "wdl_cross_entropy": float(wdl_ce_rows[mask].mean()),
                "cp_mae": float(np.abs(pred_cp[mask] - true_cp[mask]).mean()),
            }
        subgroups[field] = groups
    metrics["subgroups"] = subgroups
    return metrics
