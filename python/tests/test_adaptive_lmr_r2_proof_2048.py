from __future__ import annotations

import copy
import importlib.util
import os
import statistics
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".github" / "scripts" / "adaptive_lmr_r2_proof_2048_entry.py"
SPEC = importlib.util.spec_from_file_location("te1_adaptive_lmr_r2_proof_entry", ENTRY)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load Adaptive LMR R2 proof entry")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)
proof = entry.proof


class AdaptiveLmrProofContractTests(unittest.TestCase):
    def test_frozen_contract_is_exact(self) -> None:
        contract, digest = entry.load_contract()
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            proof.git("hash-object", str(entry.CONTRACT_PATH)),
            entry.CONTRACT_GIT_BLOB_SHA1,
        )
        self.assertEqual(contract["campaign_id"], "alpha26-adaptive-lmr-r2-proof-2048g-v1")
        self.assertEqual(contract["candidate"]["identity_commit"], entry.CANDIDATE_ID)
        self.assertEqual(contract["candidate"]["identity_tree"], entry.CANDIDATE_TREE)
        self.assertEqual(contract["feature"]["uci_option"], "UseAdaptiveLMR")
        self.assertFalse(contract["feature"]["control_value"])
        self.assertTrue(contract["feature"]["treatment_value"])
        self.assertEqual(contract["arms"]["TIME"]["pairs"], 512)
        self.assertEqual(contract["arms"]["NODES"]["pairs"], 512)
        self.assertEqual(
            contract["opening_selection"]["time_arm"],
            {"pairs": 512, "valid_rank_start": 128, "valid_rank_stop_exclusive": 640},
        )
        self.assertEqual(
            contract["opening_selection"]["nodes_arm"],
            {"pairs": 512, "valid_rank_start": 640, "valid_rank_stop_exclusive": 1152},
        )
        self.assertFalse(contract["opening_selection"]["prior_overlap_allowed"])
        self.assertFalse(contract["opening_selection"]["cross_arm_overlap_allowed"])
        self.assertEqual(contract["rerun_policy"]["admissible_run_attempt"], 1)
        self.assertTrue(
            contract["promotion"]["explicit_promotion_authorization_required"]
        )

    def test_copied_core_blob_is_exact_audited_nmp_core(self) -> None:
        observed = proof.git("hash-object", str(entry.CORE_PATH))
        self.assertEqual(observed, "76e635126242d270f772ebae6f291f995e4b8050")

    def test_candidate_identity_globals_are_frozen(self) -> None:
        self.assertEqual(proof.BASELINE_COMMIT, entry.CANDIDATE_ID)
        self.assertEqual(proof.BASELINE_TREE, entry.CANDIDATE_TREE)
        self.assertEqual(proof.EXPECTED_BLOBS, entry.EXPECTED_BLOBS)
        self.assertEqual(proof.SCHEMA_FINAL, entry.SCHEMA_FINAL)

    def test_first_attempt_only(self) -> None:
        with mock.patch.dict(
            os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}, clear=False
        ):
            self.assertEqual(proof.require_first_attempt(), ("123", 1))
        with mock.patch.dict(
            os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}, clear=False
        ):
            with self.assertRaises(proof.ProofError):
                proof.require_first_attempt()


class AdaptiveLmrProofEngineConfigTests(unittest.TestCase):
    class FakeEngine:
        instances: list["AdaptiveLmrProofEngineConfigTests.FakeEngine"] = []

        def __init__(self, binary: Path, mode: str):
            self.binary = binary
            self.mode = mode
            self.options: list[tuple[str, str | None]] = []
            self.closed = False
            self.__class__.instances.append(self)

        def setoption(self, name: str, value: str | None = None) -> None:
            self.options.append((name, value))

        def evaluator_identity(self) -> str:
            return "classical"

        def close(self) -> None:
            self.closed = True

    def setUp(self) -> None:
        self.FakeEngine.instances.clear()

    def test_only_adaptive_lmr_differs_between_control_and_treatment(self) -> None:
        contract, _ = entry.load_contract()
        with mock.patch.object(proof.r3, "UciEngine", self.FakeEngine):
            control = entry.new_engine(Path("te1"), False, contract)
            treatment = entry.new_engine(Path("te1"), True, contract)
        expected_common = [
            ("Hash", "16"),
            ("Threads", "1"),
            ("Deterministic", "true"),
            ("MoveOverhead", "0"),
            ("UseLMR", "true"),
            ("UseSEEPruning", "true"),
            ("UseNullMovePruning", "true"),
        ]
        self.assertEqual(control.mode, "CLASSICAL")
        self.assertEqual(treatment.mode, "CLASSICAL")
        self.assertEqual(control.options[:-1], expected_common)
        self.assertEqual(treatment.options[:-1], expected_common)
        self.assertEqual(control.options[-1], ("UseAdaptiveLMR", "false"))
        self.assertEqual(treatment.options[-1], ("UseAdaptiveLMR", "true"))


class AdaptiveLmrProofOpeningTests(unittest.TestCase):
    def synthetic_contract(self) -> dict:
        contract, _ = entry.load_contract()
        contract = copy.deepcopy(contract)
        valid = [f"fen-{index}" for index in range(1152)]
        contract["opening_selection"]["prior_256"]["sha256"] = proof.opening_hash(
            valid[:128]
        )
        return contract

    def test_opening_freeze_is_fresh_and_disjoint(self) -> None:
        valid = [f"fen-{index}" for index in range(1152)]
        contract = self.synthetic_contract()
        freeze = entry.make_opening_freeze(valid, contract, "contract-sha")
        self.assertEqual(freeze["prior_256"]["valid_rank_range"], [0, 128])
        self.assertEqual(freeze["arms"]["TIME"]["valid_rank_range"], [128, 640])
        self.assertEqual(freeze["arms"]["NODES"]["valid_rank_range"], [640, 1152])
        self.assertEqual(freeze["arms"]["TIME"]["pairs"], 512)
        self.assertEqual(freeze["arms"]["NODES"]["pairs"], 512)
        prior = set(valid[:128])
        time_fens = {item["fen"] for item in freeze["arms"]["TIME"]["openings"]}
        node_fens = {item["fen"] for item in freeze["arms"]["NODES"]["openings"]}
        self.assertTrue(prior.isdisjoint(time_fens))
        self.assertTrue(prior.isdisjoint(node_fens))
        self.assertTrue(time_fens.isdisjoint(node_fens))

    def test_opening_freeze_rejects_prior_overlap(self) -> None:
        valid = [f"fen-{index}" for index in range(1152)]
        contract = self.synthetic_contract()
        valid[128] = valid[0]
        with self.assertRaises(proof.ProofError):
            entry.make_opening_freeze(valid, contract, "contract-sha")


class AdaptiveLmrProofEvidenceTests(unittest.TestCase):
    @staticmethod
    def contract() -> dict:
        return {
            "confounders": {
                "total_ply_cap": 200,
                "opening_depth_plies": 28,
            }
        }

    def test_entry_installs_terminal_first_reconciliation(self) -> None:
        self.assertIs(proof.reconcile_game, entry.reconcile_game_terminal_first)

    def test_terminal_status_precedes_coincident_draw_reason(self) -> None:
        opening = {"fen": "7k/7Q/7K/8/8/8/8/8 b - - 100 50"}
        game = {"moves": [], "termination": "checkmate", "result": "1-0"}
        with (
            mock.patch.object(
                proof,
                "set_fen_position",
                return_value="7k/7Q/7K/8/8/8/8/8 b - - 100 50",
            ),
            mock.patch.object(proof, "has_legal_move", return_value=False),
            mock.patch.object(proof.r3, "draw_reason", return_value="50-move rule"),
            mock.patch.object(
                proof.r3, "terminal_side_is_in_check", return_value=True
            ),
        ):
            entry.reconcile_game_terminal_first(
                object(), self.contract(), opening, game
            )

    def test_game_result_must_match_treatment_color(self) -> None:
        pair = {"opening_valid_rank": 128, "opening_fen_sha256": "abc"}
        control_white = {
            "opening_valid_rank": 128,
            "opening_fen_sha256": "abc",
            "off_color": "white",
            "result": "1-0",
            "on_result": "L",
            "moves": ["e2e4"],
            "moves_after_epd": 1,
            "termination": "checkmate",
            "terminal_score": "cp -30000",
        }
        proof.validate_game_record(self.contract(), pair, control_white)
        corrupted = dict(control_white)
        corrupted["on_result"] = "W"
        with self.assertRaises(proof.ProofError):
            proof.validate_game_record(self.contract(), pair, corrupted)


class AdaptiveLmrProofStatisticsTests(unittest.TestCase):
    def test_ci_uses_sample_standard_deviation(self) -> None:
        scores = [0.0, 0.25, 0.5, 0.75, 1.0]
        result = proof.paired_ci(scores)
        expected = statistics.stdev(scores) / (len(scores) ** 0.5)
        self.assertAlmostEqual(result["standard_error"], expected, places=15)

    def test_final_rewrite_never_authorizes_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final.json"
            proof.write_json(
                path,
                {
                    "schema": entry.SCHEMA_FINAL,
                    "decision": "PASS_DEFAULT_ON",
                    "prior_512_games_pooled": False,
                    "default_on_authorized": True,
                    "total_pairs": 1024,
                    "total_games": 2048,
                },
            )
            final = entry._rewrite_final_result(path)
            self.assertEqual(final["decision"], "PASS_ADAPTIVE_LMR_R2_PROOF")
            self.assertFalse(final["prior_256_games_pooled"])
            self.assertFalse(final["promotion_authorized"])
            self.assertFalse(final["production_default_changed"])
            self.assertFalse(final["singular_extensions_started"])
            self.assertNotIn("default_on_authorized", final)
            self.assertNotIn("prior_512_games_pooled", final)

    def test_negative_decision_is_renamed_without_tuning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final.json"
            proof.write_json(
                path,
                {
                    "schema": entry.SCHEMA_FINAL,
                    "decision": "FAIL_NMP",
                    "prior_512_games_pooled": False,
                },
            )
            final = entry._rewrite_final_result(path)
            self.assertEqual(final["decision"], "FAIL_ADAPTIVE_LMR_R2")
            self.assertFalse(final["promotion_authorized"])


if __name__ == "__main__":
    unittest.main()
