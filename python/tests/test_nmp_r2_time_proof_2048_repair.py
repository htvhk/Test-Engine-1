from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "nmp_r2_time_proof_2048_repair_entry.py"
SPEC = importlib.util.spec_from_file_location("te1_nmp_r2_shape_repair", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load R2 shape repair")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)

R1_NODES = {
    "games": 1024,
    "mode": "NODES",
    "on_score": 556.0,
    "on_score_pct": 54.296875,
    "on_wdl": {"draw": 292, "loss": 322, "win": 410},
    "paired_statistics": {
        "ci95_lower": 0.5183795622080772,
        "ci95_upper": 0.5675579377919228,
        "pairs": 512,
        "sample_sd": 0.28387756621955834,
        "score": 0.54296875,
        "score_pct": 54.296875,
        "standard_error": 0.01254573450628643,
    },
    "penta": {"0.0": 39, "0.5": 96, "1.0": 195, "1.5": 102, "2.0": 80},
}


class DecisionShapeRepairTests(unittest.TestCase):
    def test_reproduces_frozen_r2_shape_bug(self):
        with self.assertRaises(KeyError):
            repair._ORIGINAL_DECISION(
                {"ci95_lower": 0.501, "ci95_upper": 0.55},
                R1_NODES,
            )

    def test_adapter_accepts_exact_r1_nodes_arm_shape(self):
        self.assertEqual(
            repair.decision_with_r1_arm(
                {"ci95_lower": 0.501, "ci95_upper": 0.55},
                R1_NODES,
            ),
            "PASS_DEFAULT_ON",
        )
        self.assertAlmostEqual(
            R1_NODES["paired_statistics"]["ci95_lower"],
            0.5183795622080772,
        )

    def test_patched_frozen_module_uses_adapter(self):
        self.assertIs(repair.M.decision, repair.decision_with_r1_arm)
        self.assertEqual(
            repair.M.decision(
                {"ci95_lower": 0.49, "ci95_upper": 0.55},
                R1_NODES,
            ),
            "INCONCLUSIVE",
        )

    def test_fail_decision_preserved(self):
        self.assertEqual(
            repair.decision_with_r1_arm(
                {"ci95_lower": 0.44, "ci95_upper": 0.499},
                R1_NODES,
            ),
            "FAIL_NMP",
        )

    def test_flat_prior_shape_is_rejected(self):
        with self.assertRaises(repair.M.ProofError):
            repair.decision_with_r1_arm(
                {"ci95_lower": 0.51, "ci95_upper": 0.55},
                {"ci95_lower": 0.5183795622080772},
            )

    def test_invalid_r1_nodes_gate_still_fails_closed(self):
        bad = {
            **R1_NODES,
            "paired_statistics": {
                **R1_NODES["paired_statistics"],
                "ci95_lower": 0.5,
            },
        }
        with self.assertRaises(repair.M.ProofError):
            repair.decision_with_r1_arm(
                {"ci95_lower": 0.51, "ci95_upper": 0.55},
                bad,
            )


if __name__ == "__main__":
    unittest.main()
