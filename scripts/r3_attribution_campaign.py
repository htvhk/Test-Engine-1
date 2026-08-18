#!/usr/bin/env python3
"""Fail-closed, resumable harness for the TE1 R3 attribution campaign."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PRODUCTION_BASE = "f0ad93ad1940b964c513fc56175c7f907b114be9"
ENGINE_MAIN_BLOB = "7e2e5816b860be9ed7914f61bd636ecacfa01564"
ENGINE_EVAL_BLOB = "5626d3e620285cb4e966f41f50ed4886f2735274"
ENGINE_ARCHITECTURE = "k32-w128-h32-crelu"
SUPPORTED_KERNELS = frozenset(("scalar", "avx2-fma"))
PREFLIGHT_SCHEMA = "TE1-R3-ATTRIBUTION-PREFLIGHT-v2"
ACTIVE_R3_WITNESS_AVAILABLE = False
OPENING_SCHEMA = "TE1-R3-ATTRIBUTION-OPENINGS-v1"
STATE_SCHEMA = "TE1-R3-ATTRIBUTION-STATE-v2"
CONTRACT_SCHEMA = "TE1-R3-ATTRIBUTION-R1"
R3_SHA256 = "822c59d9adccaecc52a5f91991d3c5e85bb4f569e165b9c215c56d79c8bbc65c"
R3_SIZE = 5_784_602
EXPECTED_RELEASE_BINARY_SHA256 = "91abf1f8b094596835e86de92c675e64e6e34674b74d7349c5ead4602a85d725"
EXPECTED_OPENING_SHA256 = "018d1cad476c6d1afcbd611ed6d69eb36f28f8fa88523e57fad5861a0ff46873"
EXPECTED_CAMPAIGN_FINGERPRINT = "2cf5ac07270975a0597c3242d4da5d107daff382b20af792759652addd8966bb"
MODES = {
    "CLASSICAL": {"UseNNUE": "false"},
    "RAW": {"UseNNUE": "true", "UseHybridEval": "false"},
    "HYBRID": {"UseNNUE": "true", "UseHybridEval": "true"},
}
OPENING_MOVES = [
    ("O01", "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6"),
    ("O02", "e2e4 e7e5 g1f3 b8c6 f1c4 g8f6 d2d3 f8c5"),
    ("O03", "e2e4 e7e5 g1f3 b8c6 d2d4 e5d4 f3d4 g8f6"),
    ("O04", "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6"),
    ("O05", "e2e4 c7c5 g1f3 b8c6 d2d4 c5d4 f3d4 g7g6"),
    ("O06", "e2e4 e7e6 d2d4 d7d5 b1c3 g8f6 e4e5 f6d7"),
    ("O07", "e2e4 c7c6 d2d4 d7d5 b1c3 d5e4 c3e4 c8f5"),
    ("O08", "e2e4 d7d6 d2d4 g8f6 b1c3 g7g6 f2f4 f8g7"),
    ("O09", "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7"),
    ("O10", "d2d4 d7d5 c2c4 c7c6 g1f3 g8f6 b1c3 d5c4"),
    ("O11", "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6"),
    ("O12", "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 d7d5"),
    ("O13", "d2d4 g8f6 c2c4 e7e6 g1f3 b7b6 g2g3 c8b7"),
    ("O14", "c2c4 e7e5 b1c3 g8f6 g2g3 d7d5 c4d5 f6d5"),
    ("O15", "g1f3 d7d5 g2g3 g8f6 f1g2 g7g6 e1g1 f8g7"),
    ("O16", "d2d4 f7f5 g2g3 g8f6 f1g2 g7g6 g1f3 f8g7"),
]

class HarnessError(RuntimeError):
    pass

class ProtocolError(HarnessError):
    pass

class IllegalMoveError(HarnessError):
    pass

class EngineFailure(HarnessError):
    pass

class WrongEvaluatorError(HarnessError):
    pass

class WrongNetworkError(HarnessError):
    pass

class SourceAuthenticationError(HarnessError):
    pass

class PreflightReceiptError(HarnessError):
    pass

class WitnessUnavailable(HarnessError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_network(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise WrongNetworkError(f"R3 network is unavailable: {path}") from error
    if size != R3_SIZE:
        raise WrongNetworkError("wrong R3 network size")
    digest = sha256_file(path)
    if digest != R3_SHA256:
        raise WrongNetworkError("wrong R3 network SHA-256")
    return digest


def validate_evaluator(mode: str, identity: str) -> str | None:
    if mode == "CLASSICAL":
        if identity != "classical":
            raise WrongEvaluatorError(f"wrong evaluator for {mode}: {identity}")
        return None
    match = re.fullmatch(r"(nnue|hybrid):k32-w128-h32-crelu:(scalar|avx2-fma)", identity)
    expected_mode = {"RAW": "nnue", "HYBRID": "hybrid"}.get(mode)
    if match is None or match.group(1) != expected_mode or match.group(2) not in SUPPORTED_KERNELS:
        raise WrongEvaluatorError(f"wrong evaluator for {mode}: {identity}")
    return match.group(2)


def _git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments], text=True, capture_output=True, check=False,
    )
    if check and result.returncode != 0:
        raise SourceAuthenticationError(result.stderr.strip() or "Git identity command failed")
    return result.stdout.strip()


def measure_source_identity(repo: Path) -> dict[str, str]:
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    if subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", PRODUCTION_BASE, head],
        capture_output=True, check=False,
    ).returncode != 0:
        raise SourceAuthenticationError("canonical production base is not an ancestor")
    changed_crates = _git(repo, "diff", "--name-only", PRODUCTION_BASE, head, "--", "crates")
    expected_crates = {
        "crates/te1-engine/src/main.rs", "crates/te1-eval/src/lib.rs",
    }
    if set(changed_crates.splitlines()) != expected_crates:
        raise SourceAuthenticationError("unexpected production crate drift")
    main_blob = _git(repo, "hash-object", "crates/te1-engine/src/main.rs")
    eval_blob = _git(repo, "hash-object", "crates/te1-eval/src/lib.rs")
    if main_blob != ENGINE_MAIN_BLOB or eval_blob != ENGINE_EVAL_BLOB:
        raise SourceAuthenticationError("production engine blob drift")
    protected = [
        "crates",
        "scripts/r3_attribution_campaign.py", "diagnostics/r3_attribution_r1/openings.json",
        "diagnostics/r3_attribution_r1/OPENING_FREEZE.json",
        "diagnostics/r3_attribution_r1/CAMPAIGN_CONTRACT.json",
    ]
    dirty = _git(repo, "status", "--porcelain=v1", "--", *protected)
    if dirty:
        raise SourceAuthenticationError(f"relevant experiment path is dirty: {dirty}")
    return {
        "source_head": head, "source_tree": tree, "production_anchor": PRODUCTION_BASE,
        "production_main_blob": main_blob, "production_eval_blob": eval_blob,
    }


def require_matching_kernels(raw_identity: str, hybrid_identity: str) -> str:
    raw_kernel = validate_evaluator("RAW", raw_identity)
    hybrid_kernel = validate_evaluator("HYBRID", hybrid_identity)
    if raw_kernel != hybrid_kernel:
        raise WrongEvaluatorError("raw and Hybrid evaluator kernels differ")
    return str(raw_kernel)


class UciEngine:
    def __init__(self, binary: Path, mode: str, network: Path | None = None):
        self.mode = mode
        self.process = subprocess.Popen(
            [str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self.lines: queue.Queue[str] = queue.Queue()
        assert self.process.stdout is not None
        threading.Thread(target=self._reader, args=(self.process.stdout,), daemon=True).start()
        self.send("uci")
        self.wait_for(lambda line: line == "uciok")
        self.setoption("Threads", "1")
        self.setoption("Deterministic", "true")
        for name, value in MODES[mode].items():
            self.setoption(name, value)
        if mode != "CLASSICAL":
            if network is None:
                raise WrongNetworkError("neural mode requires an R3 network")
            verify_network(network)
            self.setoption("EvalFile", str(network))
        self.ready()
        self.send("position startpos")
        self.identity = self.evaluator_identity()
        validate_evaluator(mode, self.identity)

    def _reader(self, stream: Any) -> None:
        for line in stream:
            self.lines.put(line.rstrip("\r\n"))

    def send(self, command: str) -> None:
        if self.process.poll() is not None:
            raise EngineFailure("engine exited")
        assert self.process.stdin is not None
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def wait_for(self, predicate: Any, timeout: float = 15.0) -> str:
        while True:
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty as error:
                raise EngineFailure("UCI response timeout") from error
            if line.startswith("info string position error"):
                raise IllegalMoveError(line)
            if line.startswith("info string setoption error"):
                raise ProtocolError(line)
            if predicate(line):
                return line

    def ready(self) -> None:
        self.send("isready")
        self.wait_for(lambda line: line == "readyok")

    def setoption(self, name: str, value: str | None = None) -> None:
        suffix = "" if value is None else f" value {value}"
        self.send(f"setoption name {name}{suffix}")
        self.ready()

    def set_position(self, moves: list[str]) -> str:
        command = "position startpos" + (" moves " + " ".join(moves) if moves else "")
        self.send(command)
        self.send("d")
        fen = self.wait_for(lambda line: line.count("/") == 7)
        if len(fen.split()) != 6:
            raise ProtocolError(f"malformed FEN response: {fen}")
        return fen

    def evaluator_identity(self) -> str:
        self.send("eval")
        line = self.wait_for(lambda item: item.startswith("info string eval "))
        match = re.fullmatch(r"info string eval (\S+) cp -?\d+", line)
        if not match:
            raise ProtocolError(f"malformed eval response: {line}")
        return match.group(1)

    def bestmove(self, nodes: int) -> tuple[str, str | None]:
        self.send(f"go nodes {nodes}")
        final_score = None
        while True:
            line = self.wait_for(lambda _: True)
            if line.startswith("info depth "):
                score = re.search(r" score (cp -?\d+|mate -?\d+) ", line)
                if score:
                    final_score = score.group(1)
            if line.startswith("bestmove "):
                match = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000)", line)
                if not match:
                    raise ProtocolError(f"malformed bestmove: {line}")
                return match.group(1), final_score

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.send("quit")
                self.process.wait(timeout=5)
            except (HarnessError, subprocess.TimeoutExpired):
                self.process.kill()


def validate_openings(binary: Path) -> list[dict[str, Any]]:
    engine = UciEngine(binary, "CLASSICAL")
    records = []
    try:
        for opening_id, text in OPENING_MOVES:
            moves: list[str] = []
            for number, move in enumerate(text.split(), 1):
                before = engine.set_position(moves)
                try:
                    engine.set_position(moves + [move])
                except IllegalMoveError as error:
                    raise IllegalMoveError(
                        f"{opening_id} move {number} {move} illegal; before: {before}"
                    ) from error
                moves.append(move)
            fen = engine.set_position(moves)
            records.append({"id": opening_id, "moves": moves, "legal": True,
                            "fen": fen, "side_to_move": fen.split()[1]})
    finally:
        engine.close()
    return records


def freeze_openings(binary: Path, directory: Path) -> tuple[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    openings = {"schema": OPENING_SCHEMA, "source": "new-diagnostic-suite",
                "openings": validate_openings(binary)}
    opening_path = directory / "openings.json"
    opening_path.write_bytes(canonical_bytes(openings))
    opening_sha = sha256_file(opening_path)
    freeze = {
        "schema": "TE1-R3-ATTRIBUTION-OPENING-FREEZE-v1",
        "opening_count": 16, "opening_sha256": opening_sha,
        "ordered_ids": [item["id"] for item in openings["openings"]],
        "ordered_fens": [item["fen"] for item in openings["openings"]],
        "creation_source": "new-diagnostic-suite",
        "validation_method": "TE1 UCI position parser, sequential move validation",
    }
    (directory / "OPENING_FREEZE.json").write_bytes(canonical_bytes(freeze))
    contract = {
        "schema": CONTRACT_SCHEMA,
        "comparisons": ["classical_vs_raw", "raw_vs_hybrid", "classical_vs_hybrid"],
        "games_per_comparison": 32, "pairs_per_comparison": 16, "total_games": 96,
        "nodes_per_move": 100_000, "threads": 1, "deterministic": True,
        "max_plies": 200, "max_ply_result": "draw", "clear_hash_each_game": True,
        "resign_adjudication": False, "score_adjudication": False,
        "required_R3_SHA256": R3_SHA256, "required_R3_size": R3_SIZE,
        "opening_suite_sha256": opening_sha,
    }
    fingerprint = hashlib.sha256(canonical_bytes(contract)).hexdigest()
    contract["configuration_fingerprint"] = fingerprint
    (directory / "CAMPAIGN_CONTRACT.json").write_bytes(canonical_bytes(contract))
    return opening_sha, fingerprint


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text())
    fingerprint = contract.pop("configuration_fingerprint", None)
    computed = hashlib.sha256(canonical_bytes(contract)).hexdigest()
    required = {
        "schema": CONTRACT_SCHEMA, "games_per_comparison": 32,
        "comparisons": ["classical_vs_raw", "raw_vs_hybrid", "classical_vs_hybrid"],
        "pairs_per_comparison": 16, "total_games": 96, "nodes_per_move": 100_000,
        "threads": 1, "deterministic": True, "max_plies": 200,
        "max_ply_result": "draw", "clear_hash_each_game": True,
        "resign_adjudication": False, "score_adjudication": False,
        "required_R3_SHA256": R3_SHA256, "required_R3_size": R3_SIZE,
    }
    if (fingerprint != computed or fingerprint != EXPECTED_CAMPAIGN_FINGERPRINT
            or any(contract.get(key) != value for key, value in required.items())):
        raise HarnessError("campaign contract fingerprint or frozen field drift")
    opening_path = path.parent / "openings.json"
    freeze_path = path.parent / "OPENING_FREEZE.json"
    freeze = json.loads(freeze_path.read_text())
    if (contract.get("opening_suite_sha256") != EXPECTED_OPENING_SHA256
            or sha256_file(opening_path) != EXPECTED_OPENING_SHA256
            or freeze.get("opening_sha256") != EXPECTED_OPENING_SHA256):
        raise HarnessError("campaign opening freeze drift")
    contract["configuration_fingerprint"] = fingerprint
    return contract


def receipt_digest(receipt: dict[str, Any]) -> str:
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    return hashlib.sha256(canonical_bytes(unsigned)).hexdigest()


def validate_preflight_receipt(
    path: Path, identity: dict[str, str], binary_sha: str, network_sha: str,
    opening_sha: str, config_fingerprint: str,
) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PreflightReceiptError("missing or corrupt preflight receipt") from error
    required = {
        "schema": PREFLIGHT_SCHEMA, **identity, "binary_sha": binary_sha,
        "network_sha": network_sha, "network_size": R3_SIZE,
        "opening_sha": opening_sha, "config_fingerprint": config_fingerprint,
        "witness_result": "PASS",
    }
    if any(receipt.get(key) != value for key, value in required.items()):
        raise PreflightReceiptError("preflight receipt identity drift")
    try:
        kernel = require_matching_kernels(receipt["raw_evaluator"], receipt["hybrid_evaluator"])
    except (KeyError, WrongEvaluatorError) as error:
        raise PreflightReceiptError("preflight evaluator identity invalid") from error
    if receipt.get("kernel") != kernel or not receipt.get("witness_vector_sha256"):
        raise PreflightReceiptError("preflight witness binding invalid")
    if receipt.get("receipt_sha256") != receipt_digest(receipt):
        raise PreflightReceiptError("preflight receipt digest mismatch")
    verify_witness_receipt(receipt)
    return receipt


def run_real_r3_preflight(*_args: Any, **_kwargs: Any) -> None:
    raise WitnessUnavailable(
        "R3_ACTIVE_NETWORK_WITNESS_UNAVAILABLE: no existing independent TE1NN reference "
        "evaluator can consume an arbitrary R3 transport file"
    )


def require_active_witness_capability() -> None:
    if not ACTIVE_R3_WITNESS_AVAILABLE:
        raise WitnessUnavailable("R3_ACTIVE_NETWORK_WITNESS_UNAVAILABLE")


def verify_witness_receipt(_receipt: dict[str, Any]) -> None:
    # No existing independent repository reference can evaluate an arbitrary TE1NN file.
    # Until such a verifier is integrated, no caller-authored receipt can authorize games.
    raise WitnessUnavailable("R3_ACTIVE_NETWORK_WITNESS_UNAVAILABLE")


def new_state(**identity: str) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA, "phase": identity.get("phase", "campaign"),
        "source_head": identity.get("source_head", ""), "source_tree": identity.get("source_tree", ""),
        "production_anchor": identity.get("production_anchor", ""),
        "production_main_blob": identity.get("production_main_blob", ""),
        "production_eval_blob": identity.get("production_eval_blob", ""),
        "preflight_receipt_sha256": identity.get("preflight_receipt_sha256", ""),
        "binary_sha": identity["binary_sha"],
        "network_sha": identity.get("network_sha", ""), "opening_sha": identity["opening_sha"],
        "config_fingerprint": identity["config_fingerprint"],
        "comparison": identity.get("comparison", ""), "completed_games": [],
        "completed_pairs": [], "pending_game": None, "wdl": {"win": 0, "draw": 0, "loss": 0},
        "color_splits": {"white": {"win": 0, "draw": 0, "loss": 0},
                         "black": {"win": 0, "draw": 0, "loss": 0}},
        "illegal_moves": 0, "protocol_errors": 0, "engine_failures": 0,
        "wrong_evaluator_events": 0, "wrong_network_events": 0,
        "resume_eligible": True,
    }


def validate_state(state: dict[str, Any], identity: dict[str, str]) -> None:
    if state.get("schema") != STATE_SCHEMA:
        raise HarnessError("wrong state schema")
    for field in (
        "source_head", "source_tree", "production_anchor", "production_main_blob",
        "production_eval_blob", "preflight_receipt_sha256", "binary_sha", "opening_sha",
        "config_fingerprint",
    ):
        if state.get(field) != identity[field]:
            raise HarnessError(f"state {field} drift")
    if identity.get("network_sha", "") != state.get("network_sha", ""):
        raise HarnessError("state network_sha drift")
    for field in ("phase", "comparison"):
        if field in identity and state.get(field) != identity[field]:
            raise HarnessError(f"state {field} drift")
    if not state.get("resume_eligible"):
        raise HarnessError("state is not resume eligible")
    games = state.get("completed_games", [])
    pairs = state.get("completed_pairs", [])
    if len(games) != len(set(games)) or len(pairs) != len(set(pairs)):
        raise HarnessError("duplicate completed identity")
    if sum(state.get("wdl", {}).values()) != len(games):
        raise HarnessError("state result accounting drift")
    if len(pairs) * 2 > len(games):
        raise HarnessError("state pair accounting drift")
    if state.get("pending_game") in games:
        raise HarnessError("completed game cannot remain pending")
    namespace = "smoke" if state.get("phase") == "non-strength-classical-smoke" else state.get("comparison")
    if not isinstance(namespace, str) or not namespace:
        raise HarnessError("state identity namespace missing")
    game_pattern = re.compile(rf"{re.escape(namespace)}-O(0[1-9]|1[0-6])-G[12]")
    if any(not isinstance(item, str) or not game_pattern.fullmatch(item) for item in games):
        raise HarnessError("invalid completed game identity")
    pending = state.get("pending_game")
    if pending is not None and (not isinstance(pending, str) or not game_pattern.fullmatch(pending)):
        raise HarnessError("invalid pending game identity")
    expected_pairs = {
        game.rsplit("-G", 1)[0] for game in games
        if f"{game.rsplit('-G', 1)[0]}-G1" in games and f"{game.rsplit('-G', 1)[0]}-G2" in games
    }
    if set(pairs) != expected_pairs:
        raise HarnessError("completed pair is missing a game")
    splits = state.get("color_splits", {})
    for result in ("win", "draw", "loss"):
        if sum(splits.get(color, {}).get(result, -1) for color in ("white", "black")) != state["wdl"][result]:
            raise HarnessError("state color accounting drift")


def atomic_write_state(path: Path, state: dict[str, Any]) -> None:
    validate_state(state, state)
    temporary = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("wb") as stream:
        stream.write(canonical_bytes(state)); stream.flush(); os.fsync(stream.fileno())
    json.loads(temporary.read_text())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)


def load_state(path: Path, identity: dict[str, str]) -> dict[str, Any]:
    try: state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error: raise HarnessError("corrupt state") from error
    validate_state(state, identity)
    return state


def game_schedule(openings: list[dict[str, Any]], left: str, right: str,
                  namespace: str = "") -> list[dict[str, Any]]:
    games = []
    for pair, opening in enumerate(openings):
        prefix = f"{namespace}-" if namespace else ""
        pair_id = f"{prefix}{opening['id']}"
        games.extend([
            {"id": f"{prefix}{opening['id']}-G1", "pair": pair_id, "opening": opening,
             "white": left, "black": right},
            {"id": f"{prefix}{opening['id']}-G2", "pair": pair_id, "opening": opening,
             "white": right, "black": left},
        ])
    return games


def record_result(state: dict[str, Any], game: dict[str, Any], result: str, left: str) -> None:
    if game["id"] in state["completed_games"]: return
    perspective = "draw" if result == "1/2-1/2" else (
        "win" if (result == "1-0") == (game["white"] == left) else "loss")
    color = "white" if game["white"] == left else "black"
    state["wdl"][perspective] += 1; state["color_splits"][color][perspective] += 1
    state["completed_games"].append(game["id"]); state["pending_game"] = None
    base = game["id"].rsplit("-G", 1)[0]
    pair_ids = [f"{base}-G1", f"{base}-G2"]
    if all(item in state["completed_games"] for item in pair_ids) and game["pair"] not in state["completed_pairs"]:
        state["completed_pairs"].append(game["pair"])


def write_pgn(path: Path, game: dict[str, Any], moves: list[str], result: str) -> None:
    text = (f'[Event "TE1 R3 attribution diagnostic"]\n[GameId "{game["id"]}"]\n[White "{game["white"]}"]\n'
            f'[Black "{game["black"]}"]\n[Result "{result}"]\n\n'
            f'{{ UCI moves: {" ".join(moves)} }} {result}\n\n')
    if path.exists() and f'[GameId "{game["id"]}"]' in path.read_text(encoding="ascii"):
        return
    with path.open("a", encoding="ascii") as stream: stream.write(text); stream.flush(); os.fsync(stream.fileno())


def persist_game_result(directory: Path, game: dict[str, Any], moves: list[str], result: str,
                        identity: dict[str, str]) -> None:
    record = {"schema": "TE1-R3-ATTRIBUTION-GAME-v1", "game_id": game["id"],
              "moves": moves, "result": result,
              "identity": {key: identity[key] for key in
                           ("source_head", "source_tree", "production_anchor", "production_main_blob", "production_eval_blob", "preflight_receipt_sha256", "binary_sha", "network_sha", "opening_sha", "config_fingerprint")},
              "white": game["white"], "black": game["black"]}
    path = directory / "results" / f"{game['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical_bytes(record)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)


def load_persisted_game(directory: Path, game: dict[str, Any],
                        identity: dict[str, str]) -> tuple[list[str], str] | None:
    path = directory / "results" / f"{game['id']}.json"
    if not path.exists():
        return None
    try: record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error: raise ProtocolError(f"corrupt game record: {path}") from error
    expected_identity = {key: identity[key] for key in
                         ("source_head", "source_tree", "production_anchor", "production_main_blob", "production_eval_blob", "preflight_receipt_sha256", "binary_sha", "network_sha", "opening_sha", "config_fingerprint")}
    if (record.get("schema") != "TE1-R3-ATTRIBUTION-GAME-v1"
            or record.get("game_id") != game["id"] or record.get("identity") != expected_identity
            or record.get("white") != game["white"] or record.get("black") != game["black"]
            or record.get("result") not in ("1-0", "0-1", "1/2-1/2")
            or not isinstance(record.get("moves"), list)
            or any(not isinstance(move, str) or not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", move)
                   for move in record.get("moves", []))):
        raise ProtocolError(f"invalid persisted game record: {path}")
    opening_moves = game["opening"]["moves"]
    maximum = len(opening_moves) + 8 if identity.get("phase") == "non-strength-classical-smoke" else 200
    if record["moves"][:len(opening_moves)] != opening_moves or len(record["moves"]) > maximum:
        raise ProtocolError(f"persisted game opening or ply limit drift: {path}")
    return record["moves"], record["result"]


def validate_recovered_moves(binary: Path, moves: list[str]) -> None:
    engine = UciEngine(binary, "CLASSICAL")
    try:
        engine.set_position(moves)
    finally:
        engine.close()


def play_game(binary: Path, game: dict[str, Any], modes: dict[str, str], nodes: int,
              additional_plies: int, network: Path | None,
              expected_identities: dict[str, str] | None = None) -> tuple[list[str], str]:
    white = black = None
    try:
        white = UciEngine(binary, modes[game["white"]], network)
        black = UciEngine(binary, modes[game["black"]], network)
        if expected_identities is not None:
            for engine in (white, black):
                expected = expected_identities[engine.mode]
                if engine.identity != expected:
                    raise WrongEvaluatorError(
                        f"runtime evaluator differs from preflight receipt: {engine.identity}"
                    )
        white.setoption("Clear Hash"); black.setoption("Clear Hash")
        moves = list(game["opening"]["moves"])
        result = "1/2-1/2"
        for _ in range(additional_plies):
            actor = white if (len(moves) % 2 == 0) else black
            actor.set_position(moves)
            validate_evaluator(actor.mode, actor.evaluator_identity())
            move, score = actor.bestmove(nodes)
            if move == "0000":
                if score is not None and score.startswith("mate "):
                    result = "0-1" if len(moves) % 2 == 0 else "1-0"
                elif score != "cp 0":
                    raise ProtocolError("terminal bestmove lacks a mate or draw score")
                break
            moves.append(move)
            white.set_position(moves); black.set_position(moves)
        return moves, result
    finally:
        if white: white.close()
        if black: black.close()


def run_smoke(binary: Path, directory: Path) -> tuple[dict[str, Any], int]:
    openings_doc = json.loads((directory / "openings.json").read_text())
    contract = load_contract(directory / "CAMPAIGN_CONTRACT.json")
    binary_sha = sha256_file(binary); opening_sha = sha256_file(directory / "openings.json")
    source_identity = measure_source_identity(Path(__file__).resolve().parents[1])
    identity = {**source_identity, "preflight_receipt_sha256": "",
                "binary_sha": binary_sha, "network_sha": "",
                "opening_sha": opening_sha, "config_fingerprint": contract["configuration_fingerprint"],
                "phase": "non-strength-classical-smoke", "comparison": "classical_vs_classical"}
    smoke_dir = directory / "smoke"; state_path = smoke_dir / "state.json"
    if state_path.exists(): state = load_state(state_path, identity)
    else: state = new_state(**identity)
    schedule = game_schedule(openings_doc["openings"][:2], "CLASSICAL-A", "CLASSICAL-B", "smoke")
    played = 0
    for game in schedule:
        if game["id"] in state["completed_games"]: continue
        state["pending_game"] = game["id"]; atomic_write_state(state_path, state)
        try:
            persisted = load_persisted_game(smoke_dir, game, identity)
            if persisted is None:
                moves, result = play_game(
                    binary, game, {"CLASSICAL-A": "CLASSICAL", "CLASSICAL-B": "CLASSICAL"},
                    500, 8, None,
                )
                persist_game_result(smoke_dir, game, moves, result, identity)
                played += 1
            else:
                moves, result = persisted
                validate_recovered_moves(binary, moves)
            write_pgn(smoke_dir / "games.pgn", game, moves, result)
            record_result(state, game, result, "CLASSICAL-A")
            atomic_write_state(state_path, state)
        except IllegalMoveError:
            state["illegal_moves"] += 1
            atomic_write_state(state_path, state)
            raise
        except WrongEvaluatorError:
            state["wrong_evaluator_events"] += 1
            atomic_write_state(state_path, state)
            raise
        except WrongNetworkError:
            state["wrong_network_events"] += 1
            atomic_write_state(state_path, state)
            raise
        except EngineFailure:
            state["engine_failures"] += 1
            atomic_write_state(state_path, state)
            raise
        except ProtocolError:
            state["protocol_errors"] += 1
            atomic_write_state(state_path, state)
            raise
    summary = {"schema": "TE1-R3-ATTRIBUTION-SMOKE-v1", "non_strength_evidence": True,
               "nodes_per_move": 500, "additional_ply_limit": 8, "games_played_this_run": played,
               "state": state}
    (smoke_dir / "summary.json").write_bytes(canonical_bytes(summary))
    return state, played


def run_campaign(binary: Path, network: Path, directory: Path, receipt_path: Path) -> int:
    repo = Path(__file__).resolve().parents[1]
    source_identity = measure_source_identity(repo)
    network_sha = verify_network(network)
    binary_sha = sha256_file(binary)
    if binary_sha != EXPECTED_RELEASE_BINARY_SHA256:
        raise PreflightReceiptError("release binary SHA-256 drift")
    require_active_witness_capability()
    openings = json.loads((directory / "openings.json").read_text())["openings"]
    contract = load_contract(directory / "CAMPAIGN_CONTRACT.json")
    opening_sha = sha256_file(directory / "openings.json")
    if opening_sha != contract["opening_suite_sha256"]:
        raise HarnessError("campaign opening SHA drift")
    receipt = validate_preflight_receipt(
        receipt_path, source_identity, binary_sha, network_sha, opening_sha,
        contract["configuration_fingerprint"],
    )
    receipt_sha = receipt["receipt_sha256"]
    mode_pairs = [
        ("classical_vs_raw", "CLASSICAL", "RAW"),
        ("raw_vs_hybrid", "RAW", "HYBRID"),
        ("classical_vs_hybrid", "CLASSICAL", "HYBRID"),
    ]
    played = 0
    for comparison, left, right in mode_pairs:
        comparison_dir = directory / "campaign" / comparison
        state_path = comparison_dir / "state.json"
        identity = {
            **source_identity, "preflight_receipt_sha256": receipt_sha,
            "binary_sha": binary_sha,
            "network_sha": network_sha, "opening_sha": opening_sha,
            "config_fingerprint": contract["configuration_fingerprint"],
            "phase": "future-100k-attribution", "comparison": comparison,
        }
        state = load_state(state_path, identity) if state_path.exists() else new_state(
            **identity,
        )
        for game in game_schedule(openings, left, right, comparison):
            if game["id"] in state["completed_games"]:
                continue
            state["pending_game"] = game["id"]
            atomic_write_state(state_path, state)
            try:
                persisted = load_persisted_game(comparison_dir, game, identity)
                if persisted is None:
                    moves, result = play_game(
                        binary, game, {left: left, right: right},
                        contract["nodes_per_move"],
                        contract["max_plies"] - len(game["opening"]["moves"]), network,
                        {"CLASSICAL": "classical", "RAW": receipt["raw_evaluator"],
                         "HYBRID": receipt["hybrid_evaluator"]},
                    )
                    persist_game_result(comparison_dir, game, moves, result, identity)
                    played += 1
                else:
                    moves, result = persisted
                    validate_recovered_moves(binary, moves)
                write_pgn(comparison_dir / "games.pgn", game, moves, result)
                record_result(state, game, result, left)
                atomic_write_state(state_path, state)
            except IllegalMoveError:
                state["illegal_moves"] += 1; atomic_write_state(state_path, state); raise
            except WrongEvaluatorError:
                state["wrong_evaluator_events"] += 1; atomic_write_state(state_path, state); raise
            except WrongNetworkError:
                state["wrong_network_events"] += 1; atomic_write_state(state_path, state); raise
            except EngineFailure:
                state["engine_failures"] += 1; atomic_write_state(state_path, state); raise
            except ProtocolError:
                state["protocol_errors"] += 1; atomic_write_state(state_path, state); raise
    return played


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("freeze", "smoke", "campaign", "preflight"))
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--directory", type=Path, default=Path("diagnostics/r3_attribution_r1"))
    parser.add_argument("--network", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        opening_sha, fingerprint = freeze_openings(args.binary, args.directory)
        print(f"opening SHA-256: {opening_sha}\ncampaign fingerprint: {fingerprint}")
    elif args.command == "smoke":
        state, played = run_smoke(args.binary, args.directory)
        print(f"completed games: {len(state['completed_games'])}\ncompleted pairs: {len(state['completed_pairs'])}\ngames played this run: {played}")
    elif args.command == "campaign":
        if args.network is None or args.receipt is None:
            parser.error("campaign requires --network and --receipt")
        print(f"games played this run: {run_campaign(args.binary, args.network, args.directory, args.receipt)}")
    else:
        if args.network is None:
            parser.error("preflight requires --network")
        repo = Path(__file__).resolve().parents[1]
        measure_source_identity(repo)
        verify_network(args.network)
        if sha256_file(args.binary) != EXPECTED_RELEASE_BINARY_SHA256:
            raise PreflightReceiptError("release binary SHA-256 drift")
        run_real_r3_preflight()

if __name__ == "__main__": main()
