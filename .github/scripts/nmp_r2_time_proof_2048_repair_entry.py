from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

P = Path(__file__).with_name("nmp_r2_time_proof_2048.py")
S = importlib.util.spec_from_file_location("te1_nmp_r2_frozen_for_shape_repair", P)
if S is None or S.loader is None:
    raise RuntimeError("cannot load frozen R2 proof")
M = importlib.util.module_from_spec(S)
S.loader.exec_module(M)

_ORIGINAL_DECISION = M.decision


def decision_with_r1_arm(t: dict[str, Any], n: dict[str, Any]) -> str:
    """Adapt the authenticated full R1 NODES arm to the frozen R2 decision API."""
    try:
        paired = n["paired_statistics"]
    except (KeyError, TypeError) as error:
        raise M.ProofError("R1 NODES paired_statistics shape invalid") from error
    if not isinstance(paired, dict):
        raise M.ProofError("R1 NODES paired_statistics shape invalid")
    return _ORIGINAL_DECISION(t, paired)


# The frozen aggregate calls its module-global decision symbol with old["NODES"].
# Install only this bounded data-shape adapter; all game/evidence/statistical logic
# remains in the authenticated frozen R2 module.
M.decision = decision_with_r1_arm


def main() -> int:
    return int(M.main())


if __name__ == "__main__":
    raise SystemExit(main())
