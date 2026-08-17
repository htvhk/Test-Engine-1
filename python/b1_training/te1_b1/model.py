from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn

from .features import NUM_FEATURES, PAD_INDEX


@dataclass(frozen=True)
class CandidateSpec:
    name: str
    width: int
    hidden: int
    activation: str = "crelu"

    def validate(self) -> None:
        if self.activation != "crelu":
            raise ValueError("B.1 production candidates intentionally use CReLU for D.2 SIMD compatibility")
        if self.width <= 0 or self.hidden <= 0:
            raise ValueError("candidate dimensions must be positive")
        if self.width % 16 != 0:
            raise ValueError("candidate width must be a multiple of 16 for the D.2 AVX2 kernel")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)


CANDIDATE_SPECS = (
    CandidateSpec("k32-w128-h32-crelu", 128, 32),
    CandidateSpec("k32-w256-h32-crelu", 256, 32),
)


class Te1Nnue(nn.Module):
    def __init__(self, spec: CandidateSpec):
        super().__init__()
        spec.validate()
        self.spec = spec
        self.feature = nn.Embedding(NUM_FEATURES + 1, spec.width, padding_idx=PAD_INDEX)
        self.feature_bias = nn.Parameter(torch.zeros(spec.width))
        self.hidden = nn.Linear(2 * spec.width, spec.hidden)
        self.wdl_head = nn.Linear(spec.hidden, 3)
        self.cp_head = nn.Linear(spec.hidden, 1)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.normal_(self.feature.weight, mean=0.0, std=0.01)
        with torch.no_grad():
            self.feature.weight[PAD_INDEX].zero_()
        nn.init.zeros_(self.feature_bias)
        nn.init.xavier_uniform_(self.hidden.weight, gain=0.5)
        nn.init.zeros_(self.hidden.bias)
        nn.init.xavier_uniform_(self.wdl_head.weight, gain=0.5)
        nn.init.zeros_(self.wdl_head.bias)
        nn.init.xavier_uniform_(self.cp_head.weight, gain=0.5)
        nn.init.zeros_(self.cp_head.bias)

    @staticmethod
    def crelu(value: torch.Tensor) -> torch.Tensor:
        return torch.clamp(value, 0.0, 1.0)

    def forward(
        self,
        white_features: torch.Tensor,
        black_features: torch.Tensor,
        white_to_move: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        white = self.feature(white_features).sum(dim=1) + self.feature_bias
        black = self.feature(black_features).sum(dim=1) + self.feature_bias
        white = self.crelu(white)
        black = self.crelu(black)
        stm = white_to_move.to(dtype=torch.bool).unsqueeze(1)
        first = torch.where(stm, white, black)
        second = torch.where(stm, black, white)
        hidden = self.crelu(self.hidden(torch.cat((first, second), dim=1)))
        return self.wdl_head(hidden), torch.tanh(self.cp_head(hidden).squeeze(1))


def parameter_count(model: nn.Module) -> int:
    return sum(p.numel() for p in model.parameters())
