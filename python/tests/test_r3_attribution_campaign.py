import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("campaign", ROOT / "scripts/r3_attribution_campaign.py")
C = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(C)
ARTIFACTS = ROOT / "diagnostics/r3_attribution_r1"
EXPECTED_OPENING_SHA = "018d1cad476c6d1afcbd611ed6d69eb36f28f8fa88523e57fad5861a0ff46873"
EXPECTED_FENS = ['r1bqkb1r/1ppp1ppp/p1n2n2/4p3/B3P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 2 5',
 'r1bqk2r/pppp1ppp/2n2n2/2b1p3/2B1P3/3P1N2/PPP2PPP/RNBQK2R w KQkq - 1 5',
 'r1bqkb1r/pppp1ppp/2n2n2/8/3NP3/8/PPP2PPP/RNBQKB1R w KQkq - 1 5',
 'rnbqkb1r/pp2pppp/3p1n2/8/3NP3/8/PPP2PPP/RNBQKB1R w KQkq - 1 5',
 'r1bqkbnr/pp1ppp1p/2n3p1/8/3NP3/8/PPP2PPP/RNBQKB1R w KQkq - 0 5',
 'rnbqkb1r/pppn1ppp/4p3/3pP3/3P4/2N5/PPP2PPP/R1BQKBNR w KQkq - 1 5',
 'rn1qkbnr/pp2pppp/2p5/5b2/3PN3/8/PPP2PPP/R1BQKBNR w KQkq - 1 5',
 'rnbqk2r/ppp1ppbp/3p1np1/8/3PPP2/2N5/PPP3PP/R1BQKBNR w KQkq - 1 5',
 'rnbqk2r/ppp1bppp/4pn2/3p2B1/2PP4/2N5/PP2PPPP/R2QKBNR w KQkq - 4 5',
 'rnbqkb1r/pp2pppp/2p2n2/8/2pP4/2N2N2/PP2PPPP/R1BQKB1R w KQkq - 0 5',
 'rnbqk2r/ppp1ppbp/3p1np1/8/2PPP3/2N5/PP3PPP/R1BQKBNR w KQkq - 0 5',
 'rnbqk2r/ppp2ppp/4pn2/3p4/1bPP4/2N1P3/PP3PPP/R1BQKBNR w KQkq d6 0 5',
 'rn1qkb1r/pbpp1ppp/1p2pn2/8/2PP4/5NP1/PP2PP1P/RNBQKB1R w KQkq - 1 5',
 'rnbqkb1r/ppp2ppp/8/3np3/8/2N3P1/PP1PPP1P/R1BQKBNR w KQkq - 0 5',
 'rnbqk2r/ppp1ppbp/5np1/3p4/8/5NP1/PPPPPPBP/RNBQ1RK1 w kq - 2 5',
 'rnbqk2r/ppppp1bp/5np1/5p2/3P4/5NP1/PPP1PPBP/RNBQK2R w KQkq - 2 5']

class CampaignTests(unittest.TestCase):
    def identity(self):
        return {"source_head": "h" * 40, "source_tree": "t" * 40,
                "production_anchor": C.PRODUCTION_BASE,
                "production_main_blob": C.ENGINE_MAIN_BLOB,
                "production_eval_blob": C.ENGINE_EVAL_BLOB,
                "preflight_receipt_sha256": "r" * 64, "binary_sha": "b" * 64,
                "network_sha": "", "opening_sha": EXPECTED_OPENING_SHA,
                "config_fingerprint": "c" * 64, "phase": "campaign", "comparison": "test"}

    def test_opening_freeze_exact(self):
        document = json.loads((ARTIFACTS / "openings.json").read_text())
        freeze = json.loads((ARTIFACTS / "OPENING_FREEZE.json").read_text())
        self.assertEqual(C.sha256_file(ARTIFACTS / "openings.json"), EXPECTED_OPENING_SHA)
        self.assertEqual(document["schema"], C.OPENING_SCHEMA)
        self.assertEqual(len(document["openings"]), 16)
        self.assertEqual([x["id"] for x in document["openings"]], [f"O{x:02}" for x in range(1,17)])
        self.assertEqual([x["fen"] for x in document["openings"]], EXPECTED_FENS)
        self.assertTrue(all(x["legal"] and x["side_to_move"] == "w" for x in document["openings"]))
        self.assertEqual(freeze["opening_sha256"], EXPECTED_OPENING_SHA)

    def test_fresh_atomic_reload_and_corruption(self):
        identity = self.identity(); state = C.new_state(**identity)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"; C.atomic_write_state(path, state)
            self.assertEqual(C.load_state(path, identity), state)
            path.write_text("{")
            with self.assertRaisesRegex(C.HarnessError, "corrupt"): C.load_state(path, identity)

    def test_state_rejects_schema_and_identity_drift(self):
        identity = self.identity()
        for field in ("source_head", "source_tree", "production_main_blob", "production_eval_blob",
                      "preflight_receipt_sha256", "binary_sha", "opening_sha", "config_fingerprint", "network_sha"):
            state = C.new_state(**identity); state[field] = "wrong"
            with self.assertRaises(C.HarnessError): C.validate_state(state, identity)
        state = C.new_state(**identity); state["schema"] = "wrong"
        with self.assertRaisesRegex(C.HarnessError, "schema"): C.validate_state(state, identity)

        bound = {**identity, "phase": "campaign", "comparison": "one"}
        state = C.new_state(**bound)
        for field in ("phase", "comparison"):
            drifted = dict(bound); drifted[field] = "two"
            with self.assertRaises(C.HarnessError): C.validate_state(state, drifted)

    def test_persisted_result_recovers_without_game_replay(self):
        opening = json.loads((ARTIFACTS / "openings.json").read_text())["openings"][0]
        game = C.game_schedule([opening], "A", "B", "recovery")[0]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            moves = opening["moves"] + ["b1c3"]
            C.persist_game_result(directory, game, moves, "1/2-1/2", self.identity())
            self.assertEqual(C.load_persisted_game(directory, game, self.identity()), (moves, "1/2-1/2"))
            C.write_pgn(directory / "games.pgn", game, moves, "1/2-1/2")
            C.write_pgn(directory / "games.pgn", game, moves, "1/2-1/2")
            self.assertEqual((directory / "games.pgn").read_text().count('[GameId "recovery-O01-G1"]'), 1)

    def test_partial_existing_pgn_fails_before_completion(self):
        opening = json.loads((ARTIFACTS / "openings.json").read_text())["openings"][0]
        game = C.game_schedule([opening], "A", "B", "crash")[0]
        identity = self.identity(); state = C.new_state(**identity)
        moves = opening["moves"] + ["b1c3"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            C.persist_game_result(directory, game, moves, "1/2-1/2", identity)
            pgn = directory / "games.pgn"
            pgn.write_text(
                '[Event "TE1 R3 attribution diagnostic"]\n'
                f'[GameId "{game["id"]}"]\n[White "A"]\n',
                encoding="ascii",
            )
            with self.assertRaisesRegex(C.ProtocolError, "PGN evidence"):
                C.write_pgn(pgn, game, moves, "1/2-1/2")
            self.assertEqual(state["completed_games"], [])

            pgn.unlink()
            C.write_pgn(pgn, game, moves, "1/2-1/2")
            C.write_pgn(pgn, game, moves, "1/2-1/2")
            self.assertEqual(pgn.read_text().count(f'[GameId "{game["id"]}"]'), 1)

            with self.assertRaisesRegex(C.ProtocolError, "mismatch"):
                C.write_pgn(pgn, game, moves, "1-0")
            with pgn.open("a", encoding="ascii") as stream:
                stream.write(pgn.read_text())
            with self.assertRaisesRegex(C.ProtocolError, "duplicate PGN evidence"):
                C.write_pgn(pgn, game, moves, "1/2-1/2")
            self.assertEqual(state["completed_games"], [])

    def test_write_pgn_validates_complete_existing_file(self):
        opening = json.loads((ARTIFACTS / "openings.json").read_text())["openings"][0]
        first, target = C.game_schedule([opening], "A", "B", "whole-file")
        moves = opening["moves"] + ["b1c3"]
        result = "1/2-1/2"

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "games.pgn"

            for corrupt in (b"[Gam", b"[GameId"):
                with self.subTest(corrupt=corrupt):
                    path.write_bytes(corrupt)
                    before = path.read_bytes()
                    with self.assertRaises(C.ProtocolError):
                        C.write_pgn(path, target, moves, result)
                    self.assertEqual(path.read_bytes(), before)

            path.unlink()
            C.write_pgn(path, first, moves, result)
            valid_first = path.read_bytes()
            partial = b'[Event "TE1 R3 attribution diagnostic"]\n[Gam'
            path.write_bytes(valid_first + partial)
            before = path.read_bytes()
            with self.assertRaises(C.ProtocolError):
                C.write_pgn(path, target, moves, result)
            self.assertEqual(path.read_bytes(), before)

            # Even valid target evidence cannot hide an unrelated malformed tail.
            path.write_bytes(valid_first)
            C.write_pgn(path, target, moves, result)
            valid_both = path.read_bytes()
            path.write_bytes(valid_both + b"[Gam")
            before = path.read_bytes()
            with self.assertRaises(C.ProtocolError):
                C.write_pgn(path, target, moves, result)
            self.assertEqual(path.read_bytes(), before)

            path.write_bytes(b"junk" + valid_first)
            before = path.read_bytes()
            with self.assertRaises(C.ProtocolError):
                C.write_pgn(path, target, moves, result)
            self.assertEqual(path.read_bytes(), before)

            # A clean file appends one block, validates as a whole, and is idempotent.
            path.write_bytes(valid_first)
            C.write_pgn(path, target, moves, result)
            appended = path.read_bytes()
            self.assertEqual(len(C._validate_pgn_file(path)), 2)
            self.assertEqual(appended.count(f'[GameId "{target["id"]}"]'.encode()), 1)
            C.write_pgn(path, target, moves, result)
            self.assertEqual(path.read_bytes(), appended)

    def test_evaluator_and_network_fail_closed(self):
        C.validate_evaluator("CLASSICAL", "classical")
        C.validate_evaluator("RAW", "nnue:k32-w128-h32-crelu:scalar")
        C.validate_evaluator("HYBRID", "hybrid:k32-w128-h32-crelu:scalar")
        rejected = [
            ("RAW", "nnue:wrong-architecture:scalar"), ("HYBRID", "hybrid:garbage:x"),
            ("RAW", "hybrid:k32-w128-h32-crelu:scalar"), ("RAW", "nnue:k32-w128-h32-crelu:"),
            ("RAW", "nnue:k32-w128-h32-crelu:scalar:extra"),
            ("RAW", " nnue:k32-w128-h32-crelu:scalar"),
        ]
        for mode, identity in rejected:
            with self.subTest(identity=identity), self.assertRaises(C.WrongEvaluatorError):
                C.validate_evaluator(mode, identity)
        with self.assertRaises(C.WrongEvaluatorError):
            C.require_matching_kernels(
                "nnue:k32-w128-h32-crelu:scalar",
                "hybrid:k32-w128-h32-crelu:avx2-fma",
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wrong.te1nn"; path.write_bytes(b"wrong")
            with self.assertRaisesRegex(C.HarnessError, "size"): C.verify_network(path)

    def test_nnue_option_and_restore_errors_fail_closed(self):
        engine = object.__new__(C.UciEngine)
        for message in ("info string NNUE option error: unreadable EvalFile",
                        "info string NNUE restore error: embedded reload failed"):
            engine.lines = C.queue.Queue()
            engine.lines.put(message)
            with self.subTest(message=message), self.assertRaisesRegex(C.WrongNetworkError, "NNUE"):
                engine.wait_for(lambda line: line == "readyok", timeout=0.01)

    def test_search_path_failures_preempt_bestmove_0000(self):
        engine = object.__new__(C.UciEngine)
        failures = (
            "info string search error: root search failed",
            "info string search start error: failed to spawn UCI search thread",
            "info string go error: invalid nodes",
            "info string search thread panicked",
        )
        for message in failures:
            engine.lines = C.queue.Queue()
            engine.send = mock.Mock()
            engine.lines.put(message)
            engine.lines.put("bestmove 0000")
            with self.subTest(message=message), self.assertRaisesRegex(C.EngineFailure, "info string"):
                engine.bestmove(1)

    def test_terminal_checkmate_sentinel_and_stalemate(self):
        # Black is checkmated by Qg7; black is stalemated in the second position.
        mate = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
        stalemate = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"
        self.assertEqual(C.terminal_result(mate, False, "cp -30000"), "1-0")
        self.assertEqual(C.terminal_result(mate, False, "cp 30000"), "1-0")
        self.assertEqual(C.terminal_result(stalemate, False, "cp 0"), "1/2-1/2")
        with self.assertRaisesRegex(C.ProtocolError, "checkmate"):
            C.terminal_result(mate, False, "cp 0")

    def test_draw_adjudication_modes(self):
        repetition = [
            "8/8/8/8/8/6k1/8/K6R w - - 0 1",
            "8/8/8/8/8/6k1/K7/7R b - - 1 1",
        ] * 2 + ["8/8/8/8/8/6k1/8/K6R w - - 4 3"]
        self.assertEqual(C.draw_reason(repetition), "threefold repetition")
        self.assertEqual(C.draw_reason(["7k/8/8/8/8/8/R7/K7 w - - 100 51"]),
                         "50-move rule")
        self.assertEqual(C.draw_reason(["7k/8/8/8/8/8/8/K7 w - - 0 1"]),
                         "insufficient material")
        self.assertIsNone(C.draw_reason(["7k/8/8/8/8/8/P7/K7 w - - 0 1"]))

    def test_repetition_ignores_irrelevant_en_passant(self):
        # Mirrors te1-chess's exact_repetition_ignores_irrelevant_en_passant:
        # e3 cannot be captured, so production counts all three occurrences.
        history = [
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 4 3",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 8 5",
        ]
        self.assertEqual(C.draw_reason(history), "threefold repetition")

        capturable = "rnbqkb1r/ppp1pppp/5n2/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3"
        without_ep = "rnbqkb1r/ppp1pppp/5n2/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq - 4 5"
        self.assertNotEqual(C._repetition_key(capturable), C._repetition_key(without_ep))

    def test_draw_adjudication_preserves_no_legal_move_precedence(self):
        class FakeEngine:
            def __init__(self, legal): self.legal = legal
            def set_position(self, moves):
                if len(moves) > 2 and moves[-1] == "a1a2" and self.legal:
                    return "ok"
                if len(moves) > 2:
                    raise C.IllegalMoveError("illegal")
                return "restored"
        fen = "7k/8/8/8/8/8/8/K7 w - - 100 51"
        self.assertTrue(C.has_legal_move(FakeEngine(True), ["a2a3", "h7h6"], fen))
        self.assertFalse(C.has_legal_move(FakeEngine(False), ["a2a3", "h7h6"], fen))

    def test_has_legal_move_preserves_non_pawn_rank_edge_candidate(self):
        class RankEdgeEngine:
            def __init__(self): self.candidates = []
            def set_position(self, moves):
                if moves and len(moves) == 1:
                    self.candidates.append(moves[-1])
                    if moves[-1] == "a2a1":
                        return "legal"
                    raise C.IllegalMoveError("illegal")
                return "restored"

        engine = RankEdgeEngine()
        fen = "7k/8/8/8/8/8/R7/K7 w - - 0 1"
        self.assertTrue(C.has_legal_move(engine, [], fen))
        self.assertIn("a2a1", engine.candidates)
        self.assertNotIn("a2a1q", engine.candidates)

    def test_recovered_result_is_independently_adjudicated(self):
        mate = "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"
        self.assertEqual(C.adjudicate_recovered_result([mate], False, 200), "1-0")
        repetition = [
            "8/8/8/8/8/6k1/8/K6R w - - 0 1",
            "8/8/8/8/8/6k1/K7/7R b - - 1 1",
        ] * 2 + ["8/8/8/8/8/6k1/8/K6R w - - 4 3"]
        self.assertEqual(C.adjudicate_recovered_result(repetition, True, 200), "1/2-1/2")
        with self.assertRaisesRegex(C.ProtocolError, "before a frozen termination"):
            C.adjudicate_recovered_result(["7k/8/8/8/8/8/P7/K7 w - - 0 1"], True, 200)

    def test_recovery_exact_final_ply_mate_is_max_ply_draw(self):
        # Model a reduced one-ply version of both bounded live loops: the only
        # permitted move delivers Qg7#, but play_game exits the for-loop with its
        # initialized max-ply draw without observing the new terminal position.
        before = "7k/8/5QK1/8/8/8/8/8 w - - 0 1"
        after_qg7_mate = "7k/6Q1/6K1/8/8/8/8/8 b - - 1 1"
        self.assertEqual(
            C.adjudicate_recovered_result([before, after_qg7_mate], False, 1),
            "1/2-1/2",
        )
        # The same mate before a larger configured limit retains ordinary mate
        # precedence, proving only the exact-limit boundary is special.
        self.assertEqual(
            C.adjudicate_recovered_result([before, after_qg7_mate], False, 2),
            "1-0",
        )

    def test_recovery_rejects_moves_after_earliest_threefold(self):
        moves = "g1f3 g8f6 f3g1 f6g8 g1f3 g8f6 f3g1 f6g8 f2f3 e7e5 g2g4 d8h4".split()
        # The exact move sequence returns to the start position at plies four and
        # eight.  Qh4 is mate at ply twelve, but recovery must freeze the draw at 8.
        history = [
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1",
            "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2",
            "rnbqkb1r/pppppppp/5n2/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 3 2",
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 4 3",
            "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 5 3",
            "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 6 4",
            "rnbqkb1r/pppppppp/5n2/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 7 4",
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 8 5",
            "rnbqkbnr/pppppppp/8/8/8/5P2/PPPPP1PP/RNBQKBNR b KQkq - 0 5",
            "rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq e6 0 6",
            "rnbqkbnr/pppp1ppp/8/4p3/6P1/5P2/PPPPP2P/RNBQKBNR b KQkq g3 0 6",
            "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 7",
        ]
        self.assertEqual(len(history), len(moves) + 1)
        class ReconstructedGame:
            def __init__(self, _binary, _mode): pass
            def set_position(self, prefix):
                self.assert_prefix(prefix)
                return history[len(prefix)]
            def assert_prefix(self, prefix):
                if prefix != moves[:len(prefix)]:
                    raise AssertionError("recovery did not reconstruct in ply order")
            def close(self): pass

        with self.assertRaisesRegex(C.ProtocolError, "terminal position at ply 8"):
            with mock.patch.object(C, "UciEngine", ReconstructedGame), \
                 mock.patch.object(C, "has_legal_move", return_value=False):
                C.re_adjudicate_recovered_game(
                    Path("te1"), {"opening": {"moves": []}}, moves, self.identity()
                )

    def test_recovery_rejects_moves_after_earliest_fifty_move_draw(self):
        history = [
            "7k/8/8/8/8/8/R7/K7 w - - 99 50",
            "7k/8/8/8/8/R7/8/K7 b - - 100 50",
            "7k/8/8/8/8/8/8/K7 w - - 0 51",
        ]
        with self.assertRaisesRegex(C.ProtocolError, "terminal position at ply 1"):
            C.adjudicate_recovered_result(history, True, 200)

    def test_completed_state_requires_matching_result_and_pgn_evidence(self):
        opening = json.loads((ARTIFACTS / "openings.json").read_text())["openings"][0]
        game = C.game_schedule([opening], "A", "B", "test")[0]
        identity = self.identity(); state = C.new_state(**identity)
        moves = opening["moves"] + ["b1c3"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            C.persist_game_result(directory, game, moves, "1/2-1/2", identity)
            C.write_pgn(directory / "games.pgn", game, moves, "1/2-1/2")
            C.record_result(state, game, "1/2-1/2", "A")
            C.reconcile_completed_games(directory, [game], identity, state, "A")
            result_path = directory / "results" / f"{game['id']}.json"
            original = result_path.read_text()
            coherent_wrong = json.loads(original)
            coherent_wrong["result"] = "1-0"
            result_path.write_bytes(C.canonical_bytes(coherent_wrong))
            (directory / "games.pgn").unlink()
            C.write_pgn(directory / "games.pgn", game, moves, "1-0")
            with mock.patch.object(C, "re_adjudicate_recovered_game", return_value="1/2-1/2"), \
                 self.assertRaisesRegex(C.ProtocolError, "contradicts game semantics"):
                C.reconcile_completed_games(directory, [game], identity, state, "A", Path("te1"))
            result_path.write_text(original)
            (directory / "games.pgn").unlink()
            C.write_pgn(directory / "games.pgn", game, moves, "1/2-1/2")
            result_path.unlink()
            with self.assertRaisesRegex(C.ProtocolError, "missing completed"):
                C.reconcile_completed_games(directory, [game], identity, state, "A")
            result_path.write_text("{")
            with self.assertRaisesRegex(C.ProtocolError, "corrupt"):
                C.reconcile_completed_games(directory, [game], identity, state, "A")
            result_path.write_text(original)
            (directory / "games.pgn").unlink()
            with self.assertRaisesRegex(C.ProtocolError, "missing PGN"):
                C.reconcile_completed_games(directory, [game], identity, state, "A")
            C.write_pgn(directory / "games.pgn", game, moves, "1-0")
            with self.assertRaisesRegex(C.ProtocolError, "mismatch"):
                C.reconcile_completed_games(directory, [game], identity, state, "A")

    def test_checked_in_smoke_artifacts_are_a_complete_v2_set(self):
        state = json.loads((ARTIFACTS / "smoke/state.json").read_text())
        self.assertEqual(state["schema"], C.STATE_SCHEMA)
        self.assertNotIn("source_commit", state)
        identity_fields = ("source_head", "source_tree", "production_anchor",
                           "production_main_blob", "production_eval_blob",
                           "preflight_receipt_sha256", "binary_sha", "network_sha",
                           "opening_sha", "config_fingerprint")
        expected = {key: state[key] for key in identity_fields}
        results = sorted((ARTIFACTS / "smoke/results").glob("*.json"))
        self.assertEqual(len(results), 4)
        for path in results:
            record = json.loads(path.read_text())
            self.assertEqual(record["identity"], expected)
            self.assertNotIn("source_commit", record["identity"])
        summary = json.loads((ARTIFACTS / "smoke/summary.json").read_text())
        self.assertEqual(summary["state"], state)

    def test_source_identity_is_measured_and_fail_closed(self):
        values = {
            ("rev-parse", "HEAD"): "1" * 40,
            ("rev-parse", "HEAD^{tree}"): "2" * 40,
            ("diff", "--name-only", C.PRODUCTION_BASE, "1" * 40, "--", "crates"): "crates/te1-engine/src/main.rs\ncrates/te1-eval/src/lib.rs",
            ("hash-object", "crates/te1-engine/src/main.rs"): C.ENGINE_MAIN_BLOB,
            ("hash-object", "crates/te1-eval/src/lib.rs"): C.ENGINE_EVAL_BLOB,
            ("status", "--porcelain=v1", "--",
             "crates", "scripts/r3_attribution_campaign.py",
             "diagnostics/r3_attribution_r1/openings.json",
             "diagnostics/r3_attribution_r1/OPENING_FREEZE.json",
             "diagnostics/r3_attribution_r1/CAMPAIGN_CONTRACT.json"): "",
        }
        with mock.patch.object(C, "_git", side_effect=lambda _repo, *args, **_kw: values[args]), \
             mock.patch.object(C.subprocess, "run", return_value=mock.Mock(returncode=0)):
            measured = C.measure_source_identity(Path("/repo"))
        self.assertEqual(measured["source_head"], "1" * 40)
        self.assertEqual(measured["source_tree"], "2" * 40)
        self.assertNotIn("source_commit", measured)

        dirty_values = dict(values)
        status_key = next(key for key in dirty_values if key[0] == "status")
        dirty_values[status_key] = " M scripts/r3_attribution_campaign.py"
        with mock.patch.object(C, "_git", side_effect=lambda _repo, *args, **_kw: dirty_values[args]), \
             mock.patch.object(C.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             self.assertRaises(C.SourceAuthenticationError):
            C.measure_source_identity(Path("/repo"))

        with mock.patch.object(C, "_git", side_effect=lambda _repo, *args, **_kw: values[args]), \
             mock.patch.object(C.subprocess, "run", return_value=mock.Mock(returncode=1)), \
             self.assertRaisesRegex(C.SourceAuthenticationError, "base"):
            C.measure_source_identity(Path("/repo"))
        wrong_blob = dict(values)
        wrong_blob[("hash-object", "crates/te1-engine/src/main.rs")] = "bad"
        with mock.patch.object(C, "_git", side_effect=lambda _repo, *args, **_kw: wrong_blob[args]), \
             mock.patch.object(C.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             self.assertRaisesRegex(C.SourceAuthenticationError, "blob"):
            C.measure_source_identity(Path("/repo"))
        wrong_crates = dict(values)
        diff_key = next(key for key in wrong_crates if key[0] == "diff")
        wrong_crates[diff_key] += "\ncrates/te1-search/src/lib.rs"
        with mock.patch.object(C, "_git", side_effect=lambda _repo, *args, **_kw: wrong_crates[args]), \
             mock.patch.object(C.subprocess, "run", return_value=mock.Mock(returncode=0)), \
             self.assertRaisesRegex(C.SourceAuthenticationError, "crate"):
            C.measure_source_identity(Path("/repo"))

    def test_receipt_and_witness_fail_closed(self):
        identity = {key: self.identity()[key] for key in (
            "source_head", "source_tree", "production_anchor", "production_main_blob",
            "production_eval_blob")}
        receipt = {"schema": C.PREFLIGHT_SCHEMA, **identity, "binary_sha": "b" * 64,
                   "network_sha": C.R3_SHA256, "network_size": C.R3_SIZE,
                   "opening_sha": EXPECTED_OPENING_SHA, "config_fingerprint": "c" * 64,
                   "raw_evaluator": "nnue:k32-w128-h32-crelu:scalar",
                   "hybrid_evaluator": "hybrid:k32-w128-h32-crelu:scalar", "kernel": "scalar",
                   "witness_result": "PASS", "witness_vector_sha256": "v" * 64}
        receipt["receipt_sha256"] = C.receipt_digest(receipt)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            with self.assertRaises(C.PreflightReceiptError):
                C.validate_preflight_receipt(path, identity, "b" * 64, C.R3_SHA256,
                                             EXPECTED_OPENING_SHA, "c" * 64)
            path.write_bytes(C.canonical_bytes(receipt))
            with self.assertRaisesRegex(C.WitnessUnavailable, "R3_ACTIVE_NETWORK_WITNESS_UNAVAILABLE"):
                C.validate_preflight_receipt(path, identity, "b" * 64, C.R3_SHA256,
                                             EXPECTED_OPENING_SHA, "c" * 64)
            for field in ("source_head", "binary_sha", "network_sha", "opening_sha", "config_fingerprint"):
                altered = dict(receipt); altered[field] = "x" * 64
                altered["receipt_sha256"] = C.receipt_digest(altered)
                path.write_bytes(C.canonical_bytes(altered))
                with self.subTest(field=field), self.assertRaises(C.PreflightReceiptError):
                    C.validate_preflight_receipt(path, identity, "b" * 64, C.R3_SHA256,
                                                 EXPECTED_OPENING_SHA, "c" * 64)
        with self.assertRaisesRegex(C.WitnessUnavailable, "R3_ACTIVE_NETWORK_WITNESS_UNAVAILABLE"):
            C.run_real_r3_preflight()
        with self.assertRaisesRegex(C.WitnessUnavailable, "R3_ACTIVE_NETWORK_WITNESS_UNAVAILABLE"):
            C.require_active_witness_capability()

    def test_schedule_reversal_and_resume_never_replays(self):
        openings = json.loads((ARTIFACTS / "openings.json").read_text())["openings"][:2]
        schedule = C.game_schedule(openings, "A", "B", "test")
        self.assertEqual([(x["white"], x["black"]) for x in schedule],
                         [("A","B"),("B","A"),("A","B"),("B","A")])
        state = C.new_state(**self.identity())
        C.record_result(state, schedule[0], "1/2-1/2", "A")
        self.assertNotIn(0, state["completed_pairs"])
        remaining = [x["id"] for x in schedule if x["id"] not in state["completed_games"]]
        self.assertEqual(remaining, ["test-O01-G2", "test-O02-G1", "test-O02-G2"])
        C.record_result(state, schedule[0], "1-0", "A")
        self.assertEqual(len(state["completed_games"]), 1)
        C.record_result(state, schedule[1], "1/2-1/2", "A")
        self.assertEqual(state["completed_pairs"], ["test-O01"])

    def test_campaign_accounting_32_16_and_96(self):
        openings = json.loads((ARTIFACTS / "openings.json").read_text())["openings"]
        schedule = C.game_schedule(openings, "A", "B", "comparison")
        self.assertEqual(len(schedule), 32)
        state = C.new_state(**self.identity())
        for game in schedule: C.record_result(state, game, "1/2-1/2", "A")
        self.assertEqual(len(state["completed_games"]), 32)
        self.assertEqual(len(state["completed_pairs"]), 16)
        self.assertEqual(state["wdl"], {"win": 0, "draw": 32, "loss": 0})
        matrix = [C.game_schedule(openings, "A", "B", f"comparison-{index}") for index in range(3)]
        self.assertEqual(sum(map(len, matrix)), 96)
        self.assertEqual(len({game["id"] for games in matrix for game in games}), 96)

    def test_contract_is_fingerprinted_without_self_reference(self):
        contract = json.loads((ARTIFACTS / "CAMPAIGN_CONTRACT.json").read_text())
        fingerprint = contract.pop("configuration_fingerprint")
        self.assertEqual(fingerprint, C.hashlib.sha256(C.canonical_bytes(contract)).hexdigest())
        self.assertEqual((contract["games_per_comparison"], contract["pairs_per_comparison"], contract["total_games"]), (32,16,96))
        self.assertEqual((contract["required_R3_SHA256"], contract["required_R3_size"]), (C.R3_SHA256,C.R3_SIZE))

    def test_self_consistent_substituted_openings_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            openings = json.loads((ARTIFACTS / "openings.json").read_text())
            openings["openings"][0]["moves"][0] = "a2a3"
            (directory / "openings.json").write_bytes(C.canonical_bytes(openings))
            altered_sha = C.sha256_file(directory / "openings.json")
            freeze = json.loads((ARTIFACTS / "OPENING_FREEZE.json").read_text())
            freeze["opening_sha256"] = altered_sha
            (directory / "OPENING_FREEZE.json").write_bytes(C.canonical_bytes(freeze))
            contract = json.loads((ARTIFACTS / "CAMPAIGN_CONTRACT.json").read_text())
            contract["opening_suite_sha256"] = altered_sha
            contract.pop("configuration_fingerprint")
            contract["configuration_fingerprint"] = C.hashlib.sha256(C.canonical_bytes(contract)).hexdigest()
            (directory / "CAMPAIGN_CONTRACT.json").write_bytes(C.canonical_bytes(contract))
            with self.assertRaises(C.HarnessError):
                C.load_contract(directory / "CAMPAIGN_CONTRACT.json")

if __name__ == "__main__": unittest.main()
