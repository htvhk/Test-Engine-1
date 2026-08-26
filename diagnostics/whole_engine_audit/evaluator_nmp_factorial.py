#!/usr/bin/env python3
"""Diagnostic-only factorial audit of TE1 evaluator mode x Null-Move Pruning.

This harness never modifies production Rust.  It authenticates an exact production
ancestor, exact canonical B1 network bytes, and a strict diagnostic write surface,
then runs one predeclared reversed-colour fixed-node comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = "1e750218f43fa5129cb82f19b107555a1343d878"
B1_SHA256 = "9cba80ed00f31946b54179d2ed63b4639ef3ba62d1a96ba2bca4fca4fc846974"
B1_SIZE = 5_784_602
NODES_PER_MOVE = 20_000
OPENING_COUNT = 4
MAX_ADDITIONAL_PLIES = 160
ALLOWED_PERSISTENT = {
    ".github/workflows/whole-engine-audit-evaluator-nmp.yml",
    "diagnostics/whole_engine_audit/evaluator_nmp_factorial.py",
}

MATCHES: dict[str, tuple[tuple[str, str, bool], tuple[str, str, bool]]] = {
    "classical_off_vs_raw_off": (("CLASSICAL-OFF", "CLASSICAL", False), ("RAW-OFF", "RAW", False)),
    "classical_on_vs_raw_on": (("CLASSICAL-ON", "CLASSICAL", True), ("RAW-ON", "RAW", True)),
    "raw_off_vs_raw_on": (("RAW-OFF", "RAW", False), ("RAW-ON", "RAW", True)),
    "classical_off_vs_classical_on": (("CLASSICAL-OFF", "CLASSICAL", False), ("CLASSICAL-ON", "CLASSICAL", True)),
    "raw_on_vs_hybrid_on": (("RAW-ON", "RAW", True), ("HYBRID-ON", "HYBRID", True)),
    "classical_on_vs_hybrid_on": (("CLASSICAL-ON", "CLASSICAL", True), ("HYBRID-ON", "HYBRID", True)),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def git(*args: str) -> str:
    proc = subprocess.run(["git", "-C", str(ROOT), *args], text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def authenticate_source() -> dict[str, Any]:
    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    ancestor = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", BASE, head],
        capture_output=True,
    )
    if ancestor.returncode:
        raise RuntimeError(f"HEAD {head} does not descend from exact production {BASE}")
    changed = [x for x in git("diff", "--name-only", BASE, head).splitlines() if x]
    foreign = sorted(set(changed) - ALLOWED_PERSISTENT)
    if foreign:
        raise RuntimeError(f"persistent write-surface violation: {foreign}")
    production_changed = [
        x for x in changed
        if x.startswith("crates/") or x in {"Cargo.toml", "Cargo.lock", "EXPERIMENTAL_SOURCE_AUTHORIZATION.json", "CANONICAL_BASELINE.json"}
    ]
    if production_changed:
        raise RuntimeError(f"production drift on diagnostic branch: {production_changed}")
    return {"base": BASE, "head": head, "tree": tree, "changed": changed}


def load_harness():
    path = ROOT / "scripts/r3_attribution_campaign.py"
    spec = importlib.util.spec_from_file_location("te1_r3_harness", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load existing TE1 attribution harness")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Reuse the battle-tested UCI/game-semantics helpers, but bind them to the
    # exact current B1 network identity rather than the historical R3 identity.
    module.R3_SHA256 = B1_SHA256
    module.R3_SIZE = B1_SIZE
    return module


@dataclass(frozen=True)
class EngineSpec:
    name: str
    mode: str
    nmp: bool


def make_engine(h: Any, binary: Path, network: Path, spec: EngineSpec):
    engine = h.UciEngine(binary, spec.mode, network if spec.mode != "CLASSICAL" else None)
    engine.setoption("Hash", "16")
    engine.setoption("UseNullMovePruning", "true" if spec.nmp else "false")
    engine.ready()
    return engine


def direct_eval(engine: Any, moves: list[str]) -> tuple[str, int]:
    engine.set_position(moves)
    engine.send("eval")
    line = engine.wait_for(lambda item: item.startswith("info string eval "))
    match = re.fullmatch(r"info string eval (\S+) cp (-?\d+)", line)
    if match is None:
        raise RuntimeError(f"malformed eval response: {line}")
    return match.group(1), int(match.group(2))


def validate_embedded_equivalence(h: Any, binary: Path, network: Path, openings: list[dict[str, Any]]) -> dict[str, Any]:
    embedded = external = None
    rows = []
    try:
        # Start in Classical only so UciEngine does not overwrite EvalFile, then
        # switch to NNUE.  This exercises the compiled-in embedded B1 bytes.
        embedded = h.UciEngine(binary, "CLASSICAL")
        embedded.setoption("UseNNUE", "true")
        embedded.setoption("UseHybridEval", "false")
        external = h.UciEngine(binary, "RAW", network)
        for opening in openings:
            a_id, a_cp = direct_eval(embedded, opening["moves"])
            b_id, b_cp = direct_eval(external, opening["moves"])
            if a_id != b_id or a_cp != b_cp:
                raise RuntimeError(
                    f"embedded/external B1 mismatch at {opening['id']}: "
                    f"{a_id}/{a_cp} != {b_id}/{b_cp}"
                )
            rows.append({"opening": opening["id"], "identity": a_id, "cp": a_cp})
    finally:
        if embedded is not None:
            embedded.close()
        if external is not None:
            external.close()
    return {"status": "PASS", "rows": rows}


def play_game(h: Any, binary: Path, network: Path, opening: dict[str, Any], white_spec: EngineSpec, black_spec: EngineSpec) -> dict[str, Any]:
    white = black = None
    move_times_ms: list[float] = []
    try:
        white = make_engine(h, binary, network, white_spec)
        black = make_engine(h, binary, network, black_spec)
        white.setoption("Clear Hash")
        black.setoption("Clear Hash")
        moves = list(opening["moves"])
        history = [white.set_position(moves[:ply]) for ply in range(len(moves) + 1)]
        result = "1/2-1/2"
        termination = "max-ply"
        for _ in range(MAX_ADDITIONAL_PLIES):
            actor = white if len(moves) % 2 == 0 else black
            terminal_fen = actor.set_position(moves)
            reason = h.draw_reason(history)
            if reason and h.has_legal_move(actor, moves, terminal_fen):
                result = "1/2-1/2"
                termination = reason
                break
            start = time.perf_counter()
            move, score = actor.bestmove(NODES_PER_MOVE)
            move_times_ms.append((time.perf_counter() - start) * 1000.0)
            if move == "0000":
                if h.has_legal_move(actor, moves, terminal_fen):
                    raise RuntimeError("bestmove 0000 returned with legal moves")
                result = h.terminal_result(terminal_fen, len(moves) % 2 == 0, score)
                termination = "terminal"
                break
            moves.append(move)
            history.append(white.set_position(moves))
            black.set_position(moves)
        return {
            "opening": opening["id"],
            "white": white_spec.name,
            "black": black_spec.name,
            "result": result,
            "termination": termination,
            "moves": moves,
            "searched_plies": len(move_times_ms),
            "search_wall_ms_sum": round(sum(move_times_ms), 3),
            "search_wall_ms_mean": round(sum(move_times_ms) / len(move_times_ms), 3) if move_times_ms else 0.0,
        }
    finally:
        if white is not None:
            white.close()
        if black is not None:
            black.close()


def result_from_left(game: dict[str, Any], left: EngineSpec) -> str:
    if game["result"] == "1/2-1/2":
        return "draw"
    left_is_white = game["white"] == left.name
    left_won = (game["result"] == "1-0") == left_is_white
    return "win" if left_won else "loss"


def run_match(match_name: str, binary: Path, network: Path, output: Path) -> dict[str, Any]:
    if match_name not in MATCHES:
        raise RuntimeError(f"unknown predeclared match: {match_name}")
    source = authenticate_source()
    if sha256_file(network) != B1_SHA256 or network.stat().st_size != B1_SIZE:
        raise RuntimeError("canonical B1 network byte identity mismatch")
    h = load_harness()
    openings = h.validate_openings(binary)[:OPENING_COUNT]
    opening_doc = [{"id": item["id"], "moves": item["moves"], "fen": item["fen"]} for item in openings]
    opening_sha = hashlib.sha256(canonical_bytes(opening_doc)).hexdigest()
    equivalence = validate_embedded_equivalence(h, binary, network, openings)

    l_raw, r_raw = MATCHES[match_name]
    left = EngineSpec(*l_raw)
    right = EngineSpec(*r_raw)
    games: list[dict[str, Any]] = []
    for opening in openings:
        games.append(play_game(h, binary, network, opening, left, right))
        games.append(play_game(h, binary, network, opening, right, left))

    # Prove exact four reversed-colour pairs and no foreign schedule entries.
    if len(games) != OPENING_COUNT * 2:
        raise RuntimeError("incomplete schedule")
    for idx, opening in enumerate(openings):
        g1, g2 = games[idx * 2 : idx * 2 + 2]
        if g1["opening"] != opening["id"] or g2["opening"] != opening["id"]:
            raise RuntimeError("opening schedule drift")
        if not (g1["white"] == left.name and g1["black"] == right.name and
                g2["white"] == right.name and g2["black"] == left.name):
            raise RuntimeError("color-reversal invariant failed")

    wdl = {"win": 0, "draw": 0, "loss": 0}
    for game in games:
        wdl[result_from_left(game, left)] += 1
    points = wdl["win"] + 0.5 * wdl["draw"]
    total_search_ms = sum(float(game["search_wall_ms_sum"]) for game in games)
    total_searched_plies = sum(int(game["searched_plies"]) for game in games)
    summary = {
        "schema": "TE1-WHOLE-ENGINE-AUDIT-EVAL-NMP-v1",
        "status": "PASS",
        "match": match_name,
        "source": source,
        "binary_sha256": sha256_file(binary),
        "network_sha256": B1_SHA256,
        "network_size": B1_SIZE,
        "embedded_external_equivalence": equivalence,
        "nodes_per_move": NODES_PER_MOVE,
        "max_additional_plies": MAX_ADDITIONAL_PLIES,
        "opening_count": OPENING_COUNT,
        "opening_sha256": opening_sha,
        "left": asdict(left),
        "right": asdict(right),
        "games": games,
        "wdl_left": wdl,
        "score_left": points / len(games),
        "total_searched_plies": total_searched_plies,
        "total_search_wall_ms": round(total_search_ms, 3),
        "mean_search_wall_ms_per_searched_ply": round(total_search_ms / total_searched_plies, 3) if total_searched_plies else 0.0,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_bytes(summary))
    print(json.dumps({
        "status": summary["status"],
        "match": match_name,
        "wdl_left": wdl,
        "score_left": summary["score_left"],
        "mean_search_wall_ms_per_searched_ply": summary["mean_search_wall_ms_per_searched_ply"],
        "artifact_sha256": sha256_file(output),
    }, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--match", required=True, choices=sorted(MATCHES))
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--network", type=Path, default=ROOT / "networks/k32-w128-h32-crelu.te1nn")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_match(args.match, args.binary, args.network, args.output)


if __name__ == "__main__":
    main()
