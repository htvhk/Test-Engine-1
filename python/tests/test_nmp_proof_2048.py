from __future__ import annotations

import importlib.util
import os
import statistics
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / ".github" / "scripts" / "nmp_proof_2048_entry.py"
SPEC = importlib.util.spec_from_file_location("te1_nmp_proof_2048_entry", ENTRY)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load NMP proof entry")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)
proof = entry.proof


class NmpProofContractTests(unittest.TestCase):
    def test_frozen_contract_is_exact(self) -> None:
        contract, digest = proof.load_contract()
        self.assertEqual(len(digest), 64)
        self.assertEqual(contract["campaign_id"], "alpha26-nmp-r1-proof-2048g-v1")
        self.assertEqual(
            contract["baseline"],
            {
                "commit": "7820b54d511afbf5dd2d38a3f686af97c14de639",
                "tree": "465e442fb26f8ad5ee6a793f35edb22d7f66f8b0",
            },
        )
        self.assertEqual(contract["arms"]["TIME"]["pairs"], 512)
        self.assertEqual(contract["arms"]["TIME"]["games"], 1024)
        self.assertEqual(contract["arms"]["NODES"]["pairs"], 512)
        self.assertEqual(contract["arms"]["NODES"]["games"], 1024)
        self.assertEqual(
            contract["opening_selection"]["time_arm"],
            {"pairs": 512, "valid_rank_start": 256, "valid_rank_stop_exclusive": 768},
        )
        self.assertEqual(
            contract["opening_selection"]["nodes_arm"],
            {"pairs": 512, "valid_rank_start": 768, "valid_rank_stop_exclusive": 1280},
        )
        self.assertFalse(contract["opening_selection"]["prior_overlap_allowed"])
        self.assertFalse(contract["opening_selection"]["cross_arm_overlap_allowed"])
        self.assertEqual(
            contract["statistics"]["prior_512_games_use"],
            "supportive prior evidence only; never pooled into this trial",
        )
        self.assertEqual(contract["rerun_policy"]["admissible_run_attempt"], 1)

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


class NmpProofStatisticsTests(unittest.TestCase):
    def test_confirmatory_penta_reproduces_known_pair_statistics(self) -> None:
        scores: list[float] = []
        for points, count in zip(
            (0.0, 0.5, 1.0, 1.5, 2.0), (9, 24, 52, 26, 17), strict=True
        ):
            scores.extend([points / 2.0] * count)
        result = proof.paired_ci(scores)
        self.assertEqual(result["pairs"], 128)
        self.assertAlmostEqual(result["score"], 0.53515625, places=12)
        self.assertAlmostEqual(
            result["standard_error"], 0.024132075982555963, places=12
        )
        self.assertAlmostEqual(result["ci95_lower"], 0.4878582502020063, places=12)
        self.assertAlmostEqual(result["ci95_upper"], 0.5824542497979938, places=12)

    def test_ci_uses_sample_standard_deviation(self) -> None:
        scores = [0.0, 0.25, 0.5, 0.75, 1.0]
        result = proof.paired_ci(scores)
        expected = statistics.stdev(scores) / (len(scores) ** 0.5)
        self.assertAlmostEqual(result["standard_error"], expected, places=15)


class NmpProofEvidenceTests(unittest.TestCase):
    @staticmethod
    def contract() -> dict:
        return {"confounders": {"total_ply_cap": 200, "opening_depth_plies": 28}}

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

    def test_game_result_must_match_nmp_color(self) -> None:
        pair = {"opening_valid_rank": 256, "opening_fen_sha256": "abc"}
        white_off = {
            "opening_valid_rank": 256,
            "opening_fen_sha256": "abc",
            "off_color": "white",
            "result": "1-0",
            "on_result": "L",
            "moves": ["e2e4"],
            "moves_after_epd": 1,
            "termination": "checkmate",
            "terminal_score": "cp -30000",
        }
        proof.validate_game_record(self.contract(), pair, white_off)
        corrupted = dict(white_off)
        corrupted["on_result"] = "W"
        with self.assertRaises(proof.ProofError):
            proof.validate_game_record(self.contract(), pair, corrupted)

    def test_max_ply_requires_exact_frozen_length(self) -> None:
        pair = {"opening_valid_rank": 256, "opening_fen_sha256": "abc"}
        moves = ["a2a3"] * 172
        game = {
            "opening_valid_rank": 256,
            "opening_fen_sha256": "abc",
            "off_color": "white",
            "result": "1/2-1/2",
            "on_result": "D",
            "moves": moves,
            "moves_after_epd": len(moves),
            "termination": "max-ply",
            "terminal_score": None,
        }
        proof.validate_game_record(self.contract(), pair, game)
        game["moves"] = moves[:-1]
        game["moves_after_epd"] = len(moves) - 1
        with self.assertRaises(proof.ProofError):
            proof.validate_game_record(self.contract(), pair, game)

    def test_opening_freeze_rejects_overlap_with_prior(self) -> None:
        contract, contract_sha = proof.load_contract()
        valid = [f"fen-{index}" for index in range(1280)]
        freeze = proof.make_opening_freeze(valid, contract, contract_sha)
        self.assertEqual(freeze["arms"]["TIME"]["pairs"], 512)
        self.assertEqual(freeze["arms"]["NODES"]["pairs"], 512)
        self.assertTrue(
            set(
                item["fen"] for item in freeze["arms"]["TIME"]["openings"]
            ).isdisjoint(
                item["fen"] for item in freeze["arms"]["NODES"]["openings"]
            )
        )
        valid[256] = valid[0]
        with self.assertRaises(proof.ProofError):
            proof.make_opening_freeze(valid, contract, contract_sha)


if __name__ == "__main__":
    unittest.main()
