from __future__ import annotations

import torch
import torch.nn.functional as F

WDL_WEIGHT = 0.55
CP_WEIGHT = 0.40
RESULT_WEIGHT = 0.05


def _masked_mean(values: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    mask_f = mask.to(values.dtype)
    denominator = mask_f.sum().clamp_min(1.0)
    return (values * mask_f).sum() / denominator


def composite_loss(
    wdl_logits: torch.Tensor,
    cp_normalized: torch.Tensor,
    teacher_wdl: torch.Tensor,
    teacher_cp_normalized: torch.Tensor,
    game_result_class: torch.Tensor,
    sample_mask: torch.Tensor,
) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    log_probs = F.log_softmax(wdl_logits.float(), dim=1)
    teacher_ce_rows = -(teacher_wdl.float() * log_probs).sum(dim=1)
    wdl_ce = _masked_mean(teacher_ce_rows, sample_mask)

    cp_rows = F.smooth_l1_loss(
        cp_normalized.float(), teacher_cp_normalized.float(), reduction="none", beta=0.10
    )
    cp_loss = _masked_mean(cp_rows, sample_mask)

    result_available = (game_result_class >= 0) & sample_mask
    safe_classes = game_result_class.clamp_min(0)
    result_rows = F.cross_entropy(wdl_logits.float(), safe_classes, reduction="none")
    if bool(result_available.any().item()):
        result_loss = _masked_mean(result_rows, result_available)
        result_factor = RESULT_WEIGHT
    else:
        result_loss = wdl_ce.new_zeros(())
        result_factor = 0.0

    total = WDL_WEIGHT * wdl_ce + CP_WEIGHT * cp_loss + result_factor * result_loss
    return total, {"wdl_ce": wdl_ce, "cp_huber": cp_loss, "result_ce": result_loss}
