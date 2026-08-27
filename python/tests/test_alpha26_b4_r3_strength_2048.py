from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "alpha26_b4_r3_strength_2048.py"
SPEC = importlib.util.spec_from_file_location("te1_b4_r3_strength", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load B4-R3 strength harness")
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


class B4R3StrengthHarnessTests(unittest.TestCase):
    def test_contract_is_frozen_to_exact_candidate_and_time_geometry(self) -> None:
        contract, digest = HARNESS.load_contract()
        self.assertEqual(contract["baseline"]["commit"], "a02bcbd98f864fd4e3ab0e5136363b334e4388d1")
        self.assertEqual(contract["candidate"]["commit"], "4e7d1de0ed648c492f31d15a4006e5ff19cff8e0")
        self.assertEqual(contract["candidate_change_surface"], ["crates/te1-search/src/lib.rs"])
        self.assertEqual(contract["time_control"]["movetime_ms"], 200)
        self.assertEqual(contract["time_control"]["opening_depth_plies"], 28)
        self.assertEqual(contract["time_control"]["additional_plies"], 172)
        self.assertEqual(contract["sharding"]["pairs"], 1024)
        self.assertEqual(contract["sharding"]["games"], 2048)
        self.assertEqual(len(digest), 64)

    def test_paired_statistics_pass_only_when_lower_bound_clears_half(self) -> None:
        result = HARNESS.paired_statistics([2.0] * 1024)
        self.assertEqual(result["decision"], "PASS_STRENGTH")
        self.assertGreater(result["paired_95ci_score_fraction"][0], 0.5)

    def test_paired_statistics_fail_only_when_upper_bound_is_below_half(self) -> None:
        result = HARNESS.paired_statistics([0.0] * 1024)
        self.assertEqual(result["decision"], "FAIL_STRENGTH")
        self.assertLess(result["paired_95ci_score_fraction"][1], 0.5)

    def test_exactly_balanced_pairs_are_inconclusive(self) -> None:
        result = HARNESS.paired_statistics([1.0] * 1024)
        self.assertEqual(result["decision"], "INCONCLUSIVE")
        self.assertEqual(result["paired_95ci_score_fraction"], [0.5, 0.5])

    def test_pair_domain_rejects_non_pentanomial_values(self) -> None:
        with self.assertRaises(HARNESS.ProofError):
            HARNESS.paired_statistics([1.0, 1.25])

    def test_finite_elo_is_symmetric_around_half(self) -> None:
        plus = HARNESS.finite_elo(0.6)
        minus = HARNESS.finite_elo(0.4)
        self.assertTrue(math.isfinite(plus) and math.isfinite(minus))
        self.assertAlmostEqual(plus, -minus, places=12)


if __name__ == "__main__":
    unittest.main()
