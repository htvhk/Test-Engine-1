from __future__ import annotations

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
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


class ContractTests(unittest.TestCase):
    def test_contract_identity_options_and_replay_policy(self) -> None:
        contract, digest = hardened.load_contract()
        self.assertEqual(len(digest), 64)
        self.assertEqual(proof.git("hash-object", str(entry.CONTRACT_PATH)), hardened.CONTRACT_BLOB)
        self.assertEqual(hardened.CONTRACT_BLOB, "8ec9d6260c0b54df1c6bb0d7c4d4fda59451e50a")
        self.assertEqual(contract["candidate"]["identity_commit"], entry.CANDIDATE_ID)
        self.assertEqual(contract["candidate"]["source_commit"], entry.SOURCE_COMMIT)
        self.assertEqual(
            contract["candidate"]["search_sha256"],
            "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e",
        )
        self.assertEqual(
            contract["opening_selection"]["time_arm"],
            {"pairs": 512, "valid_rank_start": 128, "valid_rank_stop_exclusive": 640},
        )
        self.assertEqual(
            contract["opening_selection"]["nodes_arm"],
            {"pairs": 512, "valid_rank_start": 640, "valid_rank_stop_exclusive": 1152},
        )
        control_sha, treatment_sha = evidence.validate_option_fingerprints(contract)
        self.assertEqual(control_sha, "eac46a801a886fd145b4cfd69351aa53eba4470a3c465249575cb2883b05018f")
        self.assertEqual(treatment_sha, "f91ace5aff85f01df76ce7ea29854a475172e03040bb9ae383e8959c5b96c0b1")
        control = contract["option_fingerprints"]["control"]["options"]
        treatment = contract["option_fingerprints"]["treatment"]["options"]
        differences = {key for key in control if control[key] != treatment[key]}
        self.assertEqual(differences, {"UseAdaptiveLMR"})
        self.assertTrue(control["UseNullMovePruning"])
        self.assertTrue(control["UseLMR"])
        self.assertTrue(control["UseSEEPruning"])
        self.assertFalse(control["UseNNUE"])
        self.assertFalse(control["UseHybridEval"])
        self.assertEqual(control["Evaluator"], "classical")
        replay = contract["independent_replay"]
        self.assertTrue(replay["required"])
        self.assertTrue(replay["aggregate_requires_all_receipts"])
        self.assertEqual(replay["scope"], "all 64 shards and all 2048 games")
        self.assertFalse(contract["promotion"]["proof_result_does_not_merge_or_flip_production_default"] is False)

    def test_core_blob_and_first_attempt_are_frozen(self) -> None:
        observed = proof.git("hash-object", str(entry.CORE_PATH))
        self.assertEqual(observed, "76e635126242d270f772ebae6f291f995e4b8050")
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}, clear=False):
            self.assertEqual(proof.require_first_attempt(), ("123", 1))
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}, clear=False):
            with self.assertRaises(proof.ProofError):
                proof.require_first_attempt()


class EngineConfigTests(unittest.TestCase):
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

    def test_control_and_treatment_only_differ_at_adaptive_lmr(self) -> None:
        contract, _ = hardened.load_contract()
        with mock.patch.object(proof.r3, "UciEngine", self.FakeEngine):
            control = hardened.new_engine(Path("te1"), False, contract)
            treatment = hardened.new_engine(Path("te1"), True, contract)
        expected_names = [
            "Hash", "Threads", "Deterministic", "MoveOverhead", "UseLMR",
            "UseSEEPruning", "UseNullMovePruning", "UseNNUE", "UseHybridEval",
            "UseAdaptiveLMR",
        ]
        self.assertEqual([name for name, _ in control.options], expected_names)
        self.assertEqual([name for name, _ in treatment.options], expected_names)
        differences = [(a, b) for a, b in zip(control.options, treatment.options, strict=True) if a != b]
        self.assertEqual(differences, [(('UseAdaptiveLMR', 'false'), ('UseAdaptiveLMR', 'true'))])
        self.assertIn(("UseNullMovePruning", "true"), control.options)
        self.assertIn(("UseNNUE", "false"), control.options)
        self.assertIn(("UseHybridEval", "false"), control.options)


class OpeningTests(unittest.TestCase):
    def test_fresh_ranges_are_disjoint_from_prior_and_each_other(self) -> None:
        valid = [f"fen-{index}" for index in range(1152)]
        contract, _ = hardened.load_contract()
        contract = copy.deepcopy(contract)
        contract["opening_selection"]["prior_256"]["sha256"] = proof.opening_hash(valid[:128])
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


class EvidenceTests(unittest.TestCase):
    @staticmethod
    def identity() -> dict:
        return {
            "campaign_id": "campaign", "contract_sha256": "a" * 64,
            "run_id": "1", "run_attempt": 1, "source_head": "b" * 40,
            "source_tree": "c" * 40, "binary_sha256": "d" * 64,
            "openings_file_sha256": "e" * 64, "mode": "NODES", "shard": 0,
            "control_options_sha256": "f" * 64, "treatment_options_sha256": "0" * 64,
            "candidate_identity_commit": "1" * 40, "candidate_source_commit": "2" * 40,
            "candidate_search_sha256": "3" * 64,
        }

    @classmethod
    def record(cls, game_id: str = "NODES-S00-R0640-G1") -> dict:
        return {
            "schema": evidence.GAME_SCHEMA, "game_id": game_id,
            "pair_id": "NODES-S00-R0640", "identity": cls.identity(),
            "opening_valid_rank": 640, "opening_fen_sha256": "4" * 64,
            "start_fen": "8/8/8/8/8/8/4K3/7k w - - 0 15",
            "white": "AdaptiveLMR-OFF", "black": "AdaptiveLMR-ON",
            "off_color": "white", "adaptive_color": "black",
            "adaptive_result": "D", "result": "1/2-1/2", "termination": "max-ply",
            "terminal_score": None, "moves_after_epd": 2, "moves": ["e2e3", "h1g1"],
        }

    def test_canonical_pgn_roundtrip_and_rejections(self) -> None:
        record = self.record()
        block = evidence.canonical_pgn_block(record)
        self.assertNotIn(b"\r", block)
        block.decode("ascii")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "games.pgn"
            evidence.append_pgn(path, record)
            first = path.read_bytes()
            evidence.append_pgn(path, record)
            self.assertEqual(path.read_bytes(), first)
            parsed = evidence.parse_pgn(path)
            evidence.verify_pgn_record(parsed[record["game_id"]], record)
            path.write_bytes(block[:-1])
            with self.assertRaises(RuntimeError):
                evidence.parse_pgn(path)
            path.write_bytes(block + block)
            with self.assertRaises(RuntimeError):
                evidence.parse_pgn(path)
            path.write_bytes(block.replace(b"\n", b"\r\n"))
            with self.assertRaises(RuntimeError):
                evidence.parse_pgn(path)

    def test_state_requires_ordered_prefix_and_causal_pending(self) -> None:
        identity = self.identity()
        scheduled = ["g1", "g2", "g3"]
        state = evidence.new_state(identity, scheduled)
        state["pending_game"] = "g1"
        evidence.validate_state(state, identity, scheduled)
        bad = copy.deepcopy(state)
        bad["pending_game"] = "g2"
        with self.assertRaises(RuntimeError):
            evidence.validate_state(bad, identity, scheduled)
        bad = evidence.new_state(identity, scheduled)
        bad["completed_games"] = ["g2"]
        bad["wdl"]["draw"] = 1
        with self.assertRaises(RuntimeError):
            evidence.validate_state(bad, identity, scheduled)

    def test_manifest_detects_tampering(self) -> None:
        record = self.record("g1")
        identity = record["identity"]
        schedule = [{
            "id": "g1", "pair_id": record["pair_id"],
            "opening": {"valid_rank": 640, "fen_sha256": record["opening_fen_sha256"], "fen": record["start_fen"]},
            "off_color": "white", "adaptive_color": "black", "off_is_white": True,
            "white": record["white"], "black": record["black"],
        }]
        state = evidence.new_state(identity, ["g1"])
        state["completed_games"] = ["g1"]
        state["wdl"]["draw"] = 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence.persist_result(root, record)
            evidence.append_pgn(root / "games.pgn", record)
            evidence.write_state(root / "state.json", state)
            evidence.write_manifest(root, state)
            evidence.verify_manifest(root, state)
            (root / "games.pgn").write_bytes((root / "games.pgn").read_bytes() + b"x")
            with self.assertRaises(RuntimeError):
                evidence.verify_manifest(root, state)

    def test_linear_commit_rechecks_transaction_structurally(self) -> None:
        record = self.record("g1")
        identity = record["identity"]
        schedule = [{
            "id": "g1", "pair_id": record["pair_id"],
            "opening": {"valid_rank": 640, "fen_sha256": record["opening_fen_sha256"], "fen": record["start_fen"]},
            "off_color": "white", "adaptive_color": "black", "off_is_white": True,
            "white": record["white"], "black": record["black"],
        }]
        state = evidence.new_state(identity, ["g1"])
        state["pending_game"] = "g1"
        fake_proof = mock.Mock()
        fake_proof.validate_game_record.return_value = None
        fake_proof.reconcile_game.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence.persist_result(root, record)
            evidence.write_state(root / "state.json", state)
            with mock.patch.object(evidence, "validate_transaction", return_value=[]) as validate:
                self.assertTrue(hardened.commit_pending_linear(fake_proof, object(), {}, root, schedule, state))
            self.assertIsNone(validate.call_args.args[1])
            self.assertEqual(state["completed_games"], ["g1"])


class ReplayAndFinalTests(unittest.TestCase):
    def test_replay_loader_requires_complete_exact_receipt_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeError):
                hardened._load_replays(root, {("TIME", 0): root / "summary.json"})

    def test_blocked_final_never_authorizes_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final.json"
            rc = hardened._blocked_final(path, RuntimeError("boom"))
            obj = json.loads(path.read_bytes())
            self.assertEqual(rc, 2)
            self.assertEqual(obj["decision"], "BLOCKED_CORRECTNESS")
            self.assertFalse(obj["promotion_authorized"])
            self.assertFalse(obj["production_default_changed"])
            self.assertFalse(obj["singular_extensions_started"])
            self.assertEqual(obj["independent_replay_audit"], "FAIL")


if __name__ == "__main__":
    unittest.main()
