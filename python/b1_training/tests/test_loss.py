from __future__ import annotations
import torch
from te1_b1.losses import composite_loss


def test_loss_is_finite_and_masked():
    logits = torch.tensor([[2.0, 0.0, -1.0], [9.0, -3.0, -5.0]], requires_grad=True)
    cp = torch.tensor([0.1, 0.9], requires_grad=True)
    wdl = torch.tensor([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    cp_target = torch.tensor([0.0, -0.9])
    result = torch.tensor([0, 2])
    mask = torch.tensor([True, False])
    loss, parts = composite_loss(logits, cp, wdl, cp_target, result, mask)
    assert torch.isfinite(loss)
    loss.backward()
    assert all(torch.isfinite(v) for v in parts.values())
