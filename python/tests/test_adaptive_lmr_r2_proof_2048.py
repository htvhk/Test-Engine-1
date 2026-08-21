from __future__ import annotations

import copy
import importlib.util
import json
import os
import statistics
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
HARDENED = ROOT / ".github" / "scripts" / "adaptive_lmr_r2_proof_hardened_entry.py"
SPEC = importlib.util.spec_from_file_location("te1_adaptive_lmr_r2_hardened_test", HARDENED)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load hardened Adaptive LMR R2 proof entry")
hardened = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hardened)
entry = hardened.entry
evidence = hardened.evidence
proof = hardened.proof


class AdaptiveLmrProofContractTests(unittest.TestCase):
    def test_frozen_contract_and_option_fingerprints_are_exact(self) -> None:
        contract, digest = hardened.load_contract()
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            proof.git("hash-object", str(entry.CONTRACT_PATH)),
            "3ce56bcdb284771dcba3a5f5f3ec7a791a026db4",
        )
        self.assertEqual(contract["campaign_id"], "alpha26-adaptive-lmr-r2-proof-2048g-v1")
        self.assertEqual(contract["candidate"]["identity_commit"], entry.CANDIDATE_ID)
        self.assertEqual(contract["candidate"]["identity_tree"], entry.CANDIDATE_TREE)
        self.assertEqual(contract["feature"]["uci_option"], "UseAdaptiveLMR")
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
        control, treatment = evidence.validate_option_fingerprints(contract)
        self.assertEqual(control, "7103df8d2707dc432fe765d19b89890d76b6010f935af6aa178e284f400ccd9c")
        self.assertEqual(treatment, "ff1fd19bfe9e7ccd98bf0beec50d9955adaf25ad6b466f681335209984fb5ce2")
        self.assertEqual(
            contract["option_fingerprints"]["only_allowed_difference"],
            "UseAdaptiveLMR",
        )
        self.assertFalse(contract["opening_selection"]["prior_overlap_allowed"])
        self.assertFalse(contract["opening_selection"]["cross_arm_overlap_allowed"])
        self.assertEqual(contract["rerun_policy"]["admissible_run_attempt"], 1)
        self.assertTrue(contract["promotion"]["explicit_promotion_authorization_required"])

    def test_copied_core_blob_is_exact_audited_nmp_core(self) -> None:
        observed = proof.git("hash-object", str(entry.CORE_PATH))
        self.assertEqual(observed, "76e635126242d270f772ebae6f291f995e4b8050")

    def test_hardened_entry_installs_transactional_shard_runner(self) -> None:
        self.assertIs(proof.run_shard, hardened.run_shard)
        self.assertIs(proof.command_aggregate, hardened.command_aggregate)
        self.assertIs(proof.reconcile_game, entry.reconcile_game_terminal_first)

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
        def __init__(self, binary: Path, mode: str):
            self.binary = binary
            self.mode = mode
            self.options: list[tuple[str, str | None]] = []
            self.closed = False

        def setoption(self, name: str, value: str | None = None) -> None:
            self.options.append((name, value))

        def evaluator_identity(self) -> str:
            return "classical"

        def close(self) -> None:
            self.closed = True

    def test_only_adaptive_lmr_differs_between_control_and_treatment(self) -> None:
        contract, _ = hardened.load_contract()
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
        contract, _ = hardened.load_contract()
        contract = copy.deepcopy(contract)
        valid = [f"fen-{index}" for index in range(1152)]
        contract["opening_selection"]["prior_256"]["sha256"] = proof.opening_hash(valid[:128])
        return contract

    def test_opening_freeze_is_fresh_and_disjoint(self) -> None:
        valid = [f"fen-{index}" for index in range(1152)]
        contract = self.synthetic_contract()
        freeze = entry.make_opening_freeze(valid, contract, "contract-sha")
        self.assertEqual(freeze["prior_256"]["valid_rank_range"], [0, 128])
        self.assertEqual(freeze["arms"]["TIME"]["valid_rank_range"], [128, 640])
        self.assertEqual(freeze["arms"]["NODES"]["valid_rank_range"], [640, 1152])
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


class AdaptiveLmrProofPgnTests(unittest.TestCase):
    def record(self) -> dict:
        return {
            "schema": evidence.GAME_SCHEMA,
            "game_id": "TIME-S00-R0128-G1",
            "pair_id": "TIME-S00-R0128",
            "identity": {
                "campaign_id": "c",
                "contract_sha256": "1" * 64,
                "run_id": "42",
                "run_attempt": 1,
                "source_head": "a" * 40,
                "source_tree": "b" * 40,
                "binary_sha256": "2" * 64,
                "openings_file_sha256": "3" * 64,
                "mode": "TIME",
                "shard": 0,
                "control_options_sha256": "4" * 64,
                "treatment_options_sha256": "5" * 64,
                "candidate_identity_commit": "6" * 40,
                "candidate_source_commit": "7" * 40,
                "candidate_search_sha256": "8" * 64,
            },
            "opening_valid_rank": 128,
            "opening_fen_sha256": "9" * 64,
            "start_fen": "8/8/8/8/8/8/4K3/7k w - - 0 1",
            "white": "AdaptiveLMR-OFF",
            "black": "AdaptiveLMR-ON",
            "off_color": "white",
            "adaptive_color": "black",
            "adaptive_result": "D",
            "result": "1/2-1/2",
            "termination": "max-ply",
            "terminal_score": None,
            "moves_after_epd": 2,
            "moves": ["e2e4", "e7e5"],
        }

    def test_canonical_pgn_round_trip_is_ascii_lf_only(self) -> None:
        record = self.record()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.pgn"
            evidence.append_pgn(path, record)
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertEqual(raw, evidence.canonical_pgn_block(record))
            parsed = evidence.parse_pgn(path)
            evidence.verify_pgn_record(parsed[record["game_id"]], record)

    def test_pgn_parser_rejects_crlf_partial_and_duplicate_bytes(self) -> None:
        record = self.record()
        block = evidence.canonical_pgn_block(record)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.pgn"
            path.write_bytes(block.replace(b"\n", b"\r\n"))
            with self.assertRaises(RuntimeError):
                evidence.parse_pgn(path)
            path.write_bytes(block[:-3])
            with self.assertRaises(RuntimeError):
                evidence.parse_pgn(path)
            path.write_bytes(block + block)
            with self.assertRaises(RuntimeError):
                evidence.parse_pgn(path)


class AdaptiveLmrProofTransactionTests(unittest.TestCase):
    class FakeProof:
        @staticmethod
        def validate_game_record(contract, pair, game) -> None:  # noqa: ANN001
            if game["opening_valid_rank"] != pair["opening_valid_rank"]:
                raise RuntimeError("rank")

        @staticmethod
        def reconcile_game(validator, contract, opening, game) -> None:  # noqa: ANN001
            return None

    def identity(self) -> dict:
        return {
            "campaign_id": "c",
            "contract_sha256": "1" * 64,
            "run_id": "42",
            "run_attempt": 1,
            "source_head": "a" * 40,
            "source_tree": "b" * 40,
            "binary_sha256": "2" * 64,
            "openings_file_sha256": "3" * 64,
            "mode": "NODES",
            "shard": 0,
            "control_options_sha256": "4" * 64,
            "treatment_options_sha256": "5" * 64,
            "candidate_identity_commit": "6" * 40,
            "candidate_source_commit": "7" * 40,
            "candidate_search_sha256": "8" * 64,
        }

    def schedule(self) -> list[dict]:
        opening = {
            "valid_rank": 640,
            "fen": "8/8/8/8/8/8/4K3/7k w - - 0 1",
            "fen_sha256": "9" * 64,
        }
        return evidence.schedule_for([opening], "NODES", 0)

    def record(self, scheduled: dict, identity: dict, result: str = "1/2-1/2") -> dict:
        played = {
            "opening_valid_rank": scheduled["opening"]["valid_rank"],
            "opening_fen_sha256": scheduled["opening"]["fen_sha256"],
            "off_color": scheduled["off_color"],
            "on_result": "D",
            "result": result,
            "termination": "max-ply",
            "terminal_score": None,
            "moves_after_epd": 2,
            "moves": ["e2e4", "e7e5"],
        }
        return evidence.game_record(scheduled, played, identity)

    def test_pending_json_without_pgn_is_recovered_and_committed(self) -> None:
        schedule = self.schedule()
        identity = self.identity()
        state = evidence.new_state(identity, [game["id"] for game in schedule])
        state["pending_game"] = schedule[0]["id"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence.write_state(root / "state.json", state)
            record = self.record(schedule[0], identity)
            evidence.persist_result(root, record)
            self.assertFalse((root / "games.pgn").exists())
            committed = evidence.commit_pending(
                self.FakeProof, object(), {}, root, schedule, state
            )
            self.assertTrue(committed)
            self.assertEqual(state["completed_games"], [schedule[0]["id"]])
            self.assertIsNone(state["pending_game"])
            parsed = evidence.parse_pgn(root / "games.pgn")
            self.assertIn(schedule[0]["id"], parsed)
            evidence.validate_transaction(
                self.FakeProof, object(), {}, root, schedule, state
            )

    def test_foreign_future_result_fails_closed(self) -> None:
        schedule = self.schedule()
        identity = self.identity()
        state = evidence.new_state(identity, [game["id"] for game in schedule])
        state["pending_game"] = schedule[0]["id"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence.write_state(root / "state.json", state)
            evidence.persist_result(root, self.record(schedule[1], identity))
            with self.assertRaises(RuntimeError):
                evidence.validate_transaction(
                    self.FakeProof, None, {}, root, schedule, state
                )

    def test_manifest_detects_tampering(self) -> None:
        schedule = self.schedule()[:1]
        identity = self.identity()
        state = evidence.new_state(identity, [schedule[0]["id"]])
        state["pending_game"] = schedule[0]["id"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence.write_state(root / "state.json", state)
            evidence.persist_result(root, self.record(schedule[0], identity))
            evidence.commit_pending(self.FakeProof, object(), {}, root, schedule, state)
            evidence.write_manifest(root, state)
            evidence.verify_manifest(root, state)
            (root / "games.pgn").write_bytes((root / "games.pgn").read_bytes() + b"x")
            with self.assertRaises(RuntimeError):
                evidence.verify_manifest(root, state)


class AdaptiveLmrProofEvidenceSemanticsTests(unittest.TestCase):
    @staticmethod
    def contract() -> dict:
        return {"confounders": {"total_ply_cap": 200, "opening_depth_plies": 28}}

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
            mock.patch.object(proof.r3, "terminal_side_is_in_check", return_value=True),
        ):
            entry.reconcile_game_terminal_first(object(), self.contract(), opening, game)

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
            self.assertFalse(final["promotion_authorized"])
            self.assertFalse(final["production_default_changed"])
            self.assertFalse(final["singular_extensions_started"])


if __name__ == "__main__":
    unittest.main()
