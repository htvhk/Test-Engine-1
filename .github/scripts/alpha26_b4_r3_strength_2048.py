#!/usr/bin/env python3
"""Fail-closed 2048-game clocked strength gate for Alpha 2.6 B4-R3 fast exact SEE."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from scripts import r3_attribution_campaign as r3

CONTRACT_PATH = Path("diagnostics/whole_engine_audit/B4_R3_STRENGTH_2048_CONTRACT.json")
CONTRACT_SCHEMA = "TE1-ALPHA26-B4-R3-STRENGTH-2048-CONTRACT-v1"
OPENINGS_SCHEMA = "TE1-ALPHA26-B4-R3-STRENGTH-2048-OPENINGS-v1"
PREFLIGHT_SCHEMA = "TE1-ALPHA26-B4-R3-STRENGTH-2048-PREFLIGHT-v1"
SHARD_SCHEMA = "TE1-ALPHA26-B4-R3-STRENGTH-2048-SHARD-v1"
FINAL_SCHEMA = "TE1-ALPHA26-B4-R3-STRENGTH-2048-FINAL-v1"
BOOK_URL_PREFIX = "https://raw.githubusercontent.com/official-stockfish/books/"


class ProofError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True).encode("ascii") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def require_first_attempt() -> tuple[str, int]:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    if attempt != 1:
        raise ProofError(f"only run attempt 1 is admissible, observed {attempt}")
    return run_id, attempt


def load_contract() -> tuple[dict[str, Any], str]:
    raw = CONTRACT_PATH.read_bytes()
    contract = json.loads(raw)
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise ProofError("contract schema drift")
    if contract.get("campaign_id") != "alpha26-b4-r3-strength-2048-v1":
        raise ProofError("campaign identity drift")
    if contract.get("baseline") != {
        "commit": "a02bcbd98f864fd4e3ab0e5136363b334e4388d1",
        "tree": "0f801f99e45562c173921832982aff72a32e5ae8",
        "search_blob": "f12bd20a4aca03fe6501d6db25e4e21d1eb3b5cb",
    }:
        raise ProofError("baseline identity drift")
    if contract.get("candidate") != {
        "commit": "4e7d1de0ed648c492f31d15a4006e5ff19cff8e0",
        "tree": "fcd74438a52369ba6697f70fbdf9a545a58cff95",
        "search_blob": "94a099411923ab690a80ebfef1c277a1d44ad934",
        "search_sha256": "b577f71bd09319c5c62fcace480bddc3828953f4302668e8414506cd4ebf7cc4",
    }:
        raise ProofError("candidate identity drift")
    if contract.get("candidate_change_surface") != ["crates/te1-search/src/lib.rs"]:
        raise ProofError("candidate surface drift")
    if contract.get("engine_profile") != {
        "threads": 1,
        "deterministic": True,
        "hash_mb": 16,
        "move_overhead_ms": 0,
        "use_nnue": False,
        "use_hybrid_eval": False,
        "use_lmr": True,
        "use_see_pruning": True,
        "use_null_move_pruning": True,
    }:
        raise ProofError("engine profile drift")
    if contract.get("time_control") != {
        "command": "go movetime",
        "movetime_ms": 200,
        "opening_depth_plies": 28,
        "additional_plies": 172,
        "max_total_plies": 200,
        "resign_adjudication": False,
        "score_adjudication": False,
        "max_ply_result": "draw",
    }:
        raise ProofError("time-control drift")
    if contract.get("sharding") != {
        "pairs": 1024,
        "games": 2048,
        "shards": 64,
        "pairs_per_shard": 16,
        "color_reversal": True,
        "max_parallel": 16,
    }:
        raise ProofError("sharding drift")
    statistics_contract = contract.get("statistics", {})
    if statistics_contract.get("t_critical_df1023") != 1.962341:
        raise ProofError("statistical threshold drift")
    if contract.get("rerun_policy", {}).get("admissible_run_attempt") != 1:
        raise ProofError("rerun policy drift")
    return contract, sha256_bytes(raw)


def set_fen_position(engine: r3.UciEngine, start_fen: str, moves: list[str]) -> str:
    suffix = "" if not moves else " moves " + " ".join(moves)
    engine.send("position fen " + start_fen + suffix)
    engine.send("isready")
    engine._position_ready_barrier()
    engine.send("d")
    fen = engine.wait_for(lambda line: line.count("/") == 7)
    if len(fen.split()) != 6:
        raise ProofError(f"malformed FEN response: {fen}")
    return fen


def new_engine(binary: Path, contract: dict[str, Any]) -> r3.UciEngine:
    profile = contract["engine_profile"]
    engine = r3.UciEngine(binary, "CLASSICAL")
    engine.setoption("Hash", str(profile["hash_mb"]))
    engine.setoption("Threads", str(profile["threads"]))
    engine.setoption("Deterministic", "true" if profile["deterministic"] else "false")
    engine.setoption("MoveOverhead", str(profile["move_overhead_ms"]))
    engine.setoption("UseNNUE", "true" if profile["use_nnue"] else "false")
    engine.setoption("UseHybridEval", "true" if profile["use_hybrid_eval"] else "false")
    engine.setoption("UseLMR", "true" if profile["use_lmr"] else "false")
    engine.setoption("UseSEEPruning", "true" if profile["use_see_pruning"] else "false")
    engine.setoption("UseNullMovePruning", "true" if profile["use_null_move_pruning"] else "false")
    if engine.evaluator_identity() != "classical":
        raise ProofError("strength gate evaluator is not classical")
    return engine


def load_book(contract: dict[str, Any]) -> tuple[list[str], str]:
    source = contract["opening_source"]
    url = BOOK_URL_PREFIX + source["commit"] + "/" + source["filename"]
    with urllib.request.urlopen(url, timeout=30) as response:
        archive = response.read()
    observed_blob = git_blob_sha1(archive)
    if observed_blob != source["git_blob_sha1"]:
        raise ProofError(f"opening-book Git blob drift: {observed_blob}")
    archive_sha256 = sha256_bytes(archive)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        epd_names = [name for name in zf.namelist() if name.lower().endswith(".epd")]
        if len(epd_names) != 1:
            raise ProofError(f"expected one EPD member, found {epd_names}")
        text = zf.read(epd_names[0]).decode("utf-8")

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    domain = source["selection_domain"]
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.split()
        if len(parts) < 4:
            continue
        fen4 = " ".join(parts[:4])
        if fen4 in seen:
            continue
        seen.add(fen4)
        fen = fen4 + " 0 15"
        key = hashlib.sha256((domain + fen).encode("ascii")).hexdigest()
        candidates.append((key, fen))
    candidates.sort()
    return [fen for _, fen in candidates], archive_sha256


def freeze_openings(base_binary: Path, candidate_binary: Path, contract: dict[str, Any]) -> dict[str, Any]:
    candidates, book_sha256 = load_book(contract)
    required = int(contract["opening_source"]["unique_pairs"])
    base = new_engine(base_binary, contract)
    candidate = new_engine(candidate_binary, contract)
    selected: list[str] = []
    try:
        for fen in candidates:
            try:
                base_fen = set_fen_position(base, fen, [])
                candidate_fen = set_fen_position(candidate, fen, [])
            except r3.IllegalMoveError:
                continue
            if base_fen.split()[:4] != fen.split()[:4]:
                continue
            if candidate_fen.split()[:4] != fen.split()[:4] or candidate_fen != base_fen:
                raise ProofError("candidate/base opening normalization drift")
            selected.append(fen)
            if len(selected) == required:
                break
    finally:
        base.close()
        candidate.close()
    if len(selected) != required or len(set(selected)) != required:
        raise ProofError(f"only {len(selected)} unique valid openings selected")
    selection_sha256 = sha256_bytes("\n".join(selected).encode("ascii"))
    return {
        "schema": OPENINGS_SCHEMA,
        "pairs": required,
        "source_sha256": book_sha256,
        "selection_sha256": selection_sha256,
        "openings": [
            {"index": index, "fen": fen, "fen_sha256": sha256_bytes(fen.encode("ascii"))}
            for index, fen in enumerate(selected)
        ],
    }


def bestmove_time(engine: r3.UciEngine, movetime_ms: int) -> tuple[str, str | None, int, int, int, float]:
    engine.send(f"go movetime {movetime_ms}")
    final_score: str | None = None
    final_nodes = 0
    final_depth = 0
    final_seldepth = 0
    started = time.monotonic_ns()
    while True:
        line = engine.wait_for(lambda _: True)
        if line.startswith("info depth "):
            score = re.search(r" score (cp -?\d+|mate -?\d+) ", line)
            nodes = re.search(r" nodes (\d+) ", line)
            depth = re.match(r"info depth (\d+)", line)
            seldepth = re.search(r" seldepth (\d+) ", line)
            if score:
                final_score = score.group(1)
            if nodes:
                final_nodes = int(nodes.group(1))
            if depth:
                final_depth = int(depth.group(1))
            if seldepth:
                final_seldepth = int(seldepth.group(1))
        if line.startswith("bestmove "):
            match = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000)", line)
            if not match:
                raise ProofError(f"malformed bestmove: {line}")
            elapsed_ms = (time.monotonic_ns() - started) / 1_000_000.0
            if elapsed_ms > 5_000:
                raise ProofError(f"movetime search exceeded 5s: {elapsed_ms:.3f}ms")
            return match.group(1), final_score, final_nodes, final_depth, final_seldepth, elapsed_ms


def has_legal_move_fen(engine: r3.UciEngine, start_fen: str, moves: list[str], fen: str) -> bool:
    board = fen.split()[0]
    side_white = fen.split()[1] == "w"
    sources: list[tuple[str, str]] = []
    for rank, row in zip(range(8, 0, -1), board.split("/"), strict=True):
        file_index = 0
        for symbol in row:
            if symbol.isdigit():
                file_index += int(symbol)
            else:
                if symbol.isupper() == side_white:
                    sources.append((f"{chr(ord('a') + file_index)}{rank}", symbol.lower()))
                file_index += 1
    destinations = [f"{file_name}{rank}" for file_name in "abcdefgh" for rank in range(1, 9)]
    for source, piece in sources:
        for destination in destinations:
            promotions = "qrbn" if piece == "p" and destination[1] in "18" else ""
            trials = [source + destination + p for p in promotions] if promotions else [source + destination]
            for trial in trials:
                legal = False
                try:
                    set_fen_position(engine, start_fen, moves + [trial])
                    legal = True
                except r3.IllegalMoveError:
                    pass
                finally:
                    set_fen_position(engine, start_fen, moves)
                if legal:
                    return True
    return False


def play_game(
    base_binary: Path,
    candidate_binary: Path,
    contract: dict[str, Any],
    shard: int,
    opening: dict[str, Any],
    candidate_is_white: bool,
) -> dict[str, Any]:
    white = black = None
    try:
        white_binary = candidate_binary if candidate_is_white else base_binary
        black_binary = base_binary if candidate_is_white else candidate_binary
        white = new_engine(white_binary, contract)
        black = new_engine(black_binary, contract)
        white.setoption("Clear Hash")
        black.setoption("Clear Hash")
        start_fen = opening["fen"]
        moves: list[str] = []
        first = set_fen_position(white, start_fen, moves)
        second = set_fen_position(black, start_fen, moves)
        if first != second:
            raise ProofError("base/candidate initial FEN drift")
        history = [first]
        result = "1/2-1/2"
        termination = "max-ply"
        candidate_nodes = base_nodes = 0
        candidate_searches = base_searches = 0
        candidate_elapsed_ms = base_elapsed_ms = 0.0
        candidate_max_depth = base_max_depth = 0

        for _ in range(int(contract["time_control"]["additional_plies"])):
            current = history[-1]
            side_white = current.split()[1] == "w"
            actor = white if side_white else black
            actor_is_candidate = side_white == candidate_is_white
            fen = set_fen_position(actor, start_fen, moves)
            reason = r3.draw_reason(history)
            if reason and has_legal_move_fen(actor, start_fen, moves, fen):
                result = "1/2-1/2"
                termination = reason
                break
            move, score, nodes, depth, _seldepth, elapsed_ms = bestmove_time(
                actor, int(contract["time_control"]["movetime_ms"])
            )
            if actor_is_candidate:
                candidate_nodes += nodes
                candidate_searches += 1
                candidate_elapsed_ms += elapsed_ms
                candidate_max_depth = max(candidate_max_depth, depth)
            else:
                base_nodes += nodes
                base_searches += 1
                base_elapsed_ms += elapsed_ms
                base_max_depth = max(base_max_depth, depth)
            if move == "0000":
                if has_legal_move_fen(actor, start_fen, moves, fen):
                    raise ProofError("bestmove 0000 with legal move available")
                result = r3.terminal_result(fen, side_white, score)
                termination = "checkmate" if result != "1/2-1/2" else "stalemate"
                break
            moves.append(move)
            new_white = set_fen_position(white, start_fen, moves)
            new_black = set_fen_position(black, start_fen, moves)
            if new_white != new_black:
                raise ProofError("base/candidate position-state drift after move")
            history.append(new_white)

        candidate_result = "D"
        if result != "1/2-1/2":
            white_won = result == "1-0"
            candidate_result = "W" if white_won == candidate_is_white else "L"
        return {
            "id": f"S{shard:02d}-O{int(opening['index']):04d}-" + ("G1" if candidate_is_white else "G2"),
            "opening_index": int(opening["index"]),
            "opening_fen_sha256": opening["fen_sha256"],
            "candidate_color": "white" if candidate_is_white else "black",
            "candidate_result": candidate_result,
            "result": result,
            "moves_after_epd": len(moves),
            "termination": termination,
            "candidate_nodes": candidate_nodes,
            "base_nodes": base_nodes,
            "candidate_searches": candidate_searches,
            "base_searches": base_searches,
            "candidate_elapsed_ms": round(candidate_elapsed_ms, 3),
            "base_elapsed_ms": round(base_elapsed_ms, 3),
            "candidate_max_depth": candidate_max_depth,
            "base_max_depth": base_max_depth,
        }
    finally:
        if white is not None:
            white.close()
        if black is not None:
            black.close()


def paired_statistics(pair_scores: list[float], t_critical: float = 1.962341) -> dict[str, float | list[float] | str]:
    if len(pair_scores) < 2 or any(score not in {0.0, 0.5, 1.0, 1.5, 2.0} for score in pair_scores):
        raise ProofError("invalid paired score sample")
    fractions = [score / 2.0 for score in pair_scores]
    mean = statistics.mean(fractions)
    stdev = statistics.stdev(fractions)
    standard_error = stdev / math.sqrt(len(fractions))
    margin = t_critical * standard_error
    low = max(0.0, mean - margin)
    high = min(1.0, mean + margin)
    if low > 0.5:
        decision = "PASS_STRENGTH"
    elif high < 0.5:
        decision = "FAIL_STRENGTH"
    else:
        decision = "INCONCLUSIVE"
    return {
        "paired_score_fraction_mean": mean,
        "paired_score_fraction_stdev": stdev,
        "paired_standard_error": standard_error,
        "paired_95ci_score_fraction": [low, high],
        "decision": decision,
    }


def finite_elo(score_fraction: float) -> float:
    clipped = min(max(score_fraction, 1e-9), 1.0 - 1e-9)
    return 400.0 * math.log10(clipped / (1.0 - clipped))


def command_preflight(args: argparse.Namespace) -> int:
    contract, contract_sha = load_contract()
    run_id, attempt = require_first_attempt()
    base_binary = Path(args.base_binary)
    candidate_binary = Path(args.candidate_binary)
    if not base_binary.is_file() or not candidate_binary.is_file():
        raise ProofError("preflight binary missing")
    openings = freeze_openings(base_binary, candidate_binary, contract)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "openings.json", openings)
    base = new_engine(base_binary, contract)
    candidate = new_engine(candidate_binary, contract)
    try:
        base_identity = base.evaluator_identity()
        candidate_identity = candidate.evaluator_identity()
    finally:
        base.close()
        candidate.close()
    report = {
        "schema": PREFLIGHT_SCHEMA,
        "status": "PASS",
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "run_id": run_id,
        "run_attempt": attempt,
        "source_head": os.environ.get("GITHUB_SHA", "local"),
        "base_commit": contract["baseline"]["commit"],
        "candidate_commit": contract["candidate"]["commit"],
        "base_binary_sha256": sha256_file(base_binary),
        "candidate_binary_sha256": sha256_file(candidate_binary),
        "base_evaluator": base_identity,
        "candidate_evaluator": candidate_identity,
        "openings_sha256": sha256_file(out / "openings.json"),
        "selection_sha256": openings["selection_sha256"],
        "pairs": openings["pairs"],
    }
    write_json(out / "preflight.json", report)
    print("B4_R3_STRENGTH_PREFLIGHT", json.dumps(report, sort_keys=True), flush=True)
    return 0


def load_preflight(directory: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    contract, contract_sha = load_contract()
    run_id, attempt = require_first_attempt()
    preflight = json.loads((directory / "preflight.json").read_text(encoding="utf-8"))
    openings = json.loads((directory / "openings.json").read_text(encoding="utf-8"))
    base_binary = directory / "base" / "te1"
    candidate_binary = directory / "candidate" / "te1"
    required = {
        "schema": PREFLIGHT_SCHEMA,
        "status": "PASS",
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "run_id": run_id,
        "run_attempt": attempt,
        "base_commit": contract["baseline"]["commit"],
        "candidate_commit": contract["candidate"]["commit"],
        "base_binary_sha256": sha256_file(base_binary),
        "candidate_binary_sha256": sha256_file(candidate_binary),
        "base_evaluator": "classical",
        "candidate_evaluator": "classical",
        "openings_sha256": sha256_file(directory / "openings.json"),
        "selection_sha256": openings["selection_sha256"],
        "pairs": contract["sharding"]["pairs"],
    }
    for key, value in required.items():
        if preflight.get(key) != value:
            raise ProofError(f"preflight identity drift: {key}")
    if openings.get("schema") != OPENINGS_SCHEMA or len(openings.get("openings", [])) != contract["sharding"]["pairs"]:
        raise ProofError("opening freeze drift")
    return contract, preflight, openings, contract_sha


def command_shard(args: argparse.Namespace) -> int:
    shard = int(args.shard)
    output = Path(args.output)
    try:
        root = Path(args.preflight)
        contract, preflight, openings, contract_sha = load_preflight(root)
        count = int(contract["sharding"]["shards"])
        pairs_per_shard = int(contract["sharding"]["pairs_per_shard"])
        if not 0 <= shard < count:
            raise ProofError(f"invalid shard {shard}")
        start = shard * pairs_per_shard
        stop = start + pairs_per_shard
        selected = openings["openings"][start:stop]
        if len(selected) != pairs_per_shard:
            raise ProofError("incomplete opening shard")
        base_binary = root / "base" / "te1"
        candidate_binary = root / "candidate" / "te1"
        games: list[dict[str, Any]] = []
        pair_scores: list[float] = []
        penta = {"0.0": 0, "0.5": 0, "1.0": 0, "1.5": 0, "2.0": 0}
        for opening in selected:
            g1 = play_game(base_binary, candidate_binary, contract, shard, opening, True)
            g2 = play_game(base_binary, candidate_binary, contract, shard, opening, False)
            games.extend([g1, g2])
            points = sum(
                1.0 if game["candidate_result"] == "W" else 0.5 if game["candidate_result"] == "D" else 0.0
                for game in (g1, g2)
            )
            pair_scores.append(points)
            penta[str(points)] += 1
            print(
                "B4_R3_STRENGTH_PAIR",
                json.dumps({"shard": shard, "opening_index": opening["index"], "g1": g1, "g2": g2, "pair_candidate_points": points}, sort_keys=True),
                flush=True,
            )
        wins = sum(game["candidate_result"] == "W" for game in games)
        draws = sum(game["candidate_result"] == "D" for game in games)
        losses = len(games) - wins - draws
        report = {
            "schema": SHARD_SCHEMA,
            "status": "PASS",
            "campaign_id": contract["campaign_id"],
            "contract_sha256": contract_sha,
            "run_id": preflight["run_id"],
            "run_attempt": preflight["run_attempt"],
            "source_head": preflight["source_head"],
            "base_binary_sha256": preflight["base_binary_sha256"],
            "candidate_binary_sha256": preflight["candidate_binary_sha256"],
            "selection_sha256": preflight["selection_sha256"],
            "shard": shard,
            "pair_range": [start, stop],
            "pairs": len(pair_scores),
            "games": len(games),
            "candidate_wdl": {"win": wins, "draw": draws, "loss": losses},
            "pair_scores": pair_scores,
            "penta": penta,
            "max_ply_draws": sum(game["termination"] == "max-ply" for game in games),
            "games_detail": games,
            "operational_failures": 0,
        }
        if len(games) != 2 * pairs_per_shard or sum(penta.values()) != pairs_per_shard:
            raise ProofError("shard accounting drift")
        write_json(output, report)
        print("B4_R3_STRENGTH_SHARD", json.dumps({k: v for k, v in report.items() if k != "games_detail"}, sort_keys=True), flush=True)
        return 0
    except BaseException as error:
        blocked = {
            "schema": SHARD_SCHEMA,
            "status": "BLOCKED_CORRECTNESS",
            "shard": shard,
            "error_type": type(error).__name__,
            "error": str(error),
            "operational_failures": 1,
        }
        write_json(output, blocked)
        print("B4_R3_STRENGTH_BLOCKED", json.dumps(blocked, sort_keys=True), file=sys.stderr, flush=True)
        return 2


def command_aggregate(args: argparse.Namespace) -> int:
    output = Path(args.output)
    try:
        contract, preflight, openings, contract_sha = load_preflight(Path(args.preflight))
        files = sorted(Path(args.shards).rglob("b4-r3-strength-shard-*.json")) if Path(args.shards).exists() else []
        expected_shards = int(contract["sharding"]["shards"])
        reports: dict[int, dict[str, Any]] = {}
        blocked: list[dict[str, Any]] = []
        for path in files:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("schema") != SHARD_SCHEMA:
                raise ProofError(f"foreign shard JSON: {path}")
            shard = int(report.get("shard", -1))
            if shard in reports:
                raise ProofError(f"duplicate shard {shard}")
            reports[shard] = report
            if report.get("status") != "PASS":
                blocked.append({"shard": shard, "status": report.get("status"), "error": report.get("error")})
        missing = sorted(set(range(expected_shards)) - set(reports))
        foreign = sorted(set(reports) - set(range(expected_shards)))
        if missing or foreign or blocked:
            final = {
                "schema": FINAL_SCHEMA,
                "campaign_id": contract["campaign_id"],
                "contract_sha256": contract_sha,
                "decision": "BLOCKED_CORRECTNESS",
                "missing_shards": missing,
                "foreign_shards": foreign,
                "blocked_shards": blocked,
                "strength_gate_passed": False,
            }
            write_json(output, final)
            print("B4_R3_STRENGTH_FINAL", json.dumps(final, sort_keys=True), flush=True)
            return 2

        pair_scores: list[float] = []
        penta = {"0.0": 0, "0.5": 0, "1.0": 0, "1.5": 0, "2.0": 0}
        games: list[dict[str, Any]] = []
        wins = draws = losses = max_ply_draws = 0
        seen_ranges: list[tuple[int, int]] = []
        for shard in range(expected_shards):
            report = reports[shard]
            required = {
                "campaign_id": contract["campaign_id"],
                "contract_sha256": contract_sha,
                "run_id": preflight["run_id"],
                "run_attempt": preflight["run_attempt"],
                "source_head": preflight["source_head"],
                "base_binary_sha256": preflight["base_binary_sha256"],
                "candidate_binary_sha256": preflight["candidate_binary_sha256"],
                "selection_sha256": preflight["selection_sha256"],
                "shard": shard,
                "pairs": contract["sharding"]["pairs_per_shard"],
                "games": 2 * contract["sharding"]["pairs_per_shard"],
                "operational_failures": 0,
            }
            for key, value in required.items():
                if report.get(key) != value:
                    raise ProofError(f"shard {shard} identity drift: {key}")
            seen_ranges.append(tuple(int(v) for v in report["pair_range"]))
            shard_scores = [float(value) for value in report["pair_scores"]]
            pair_scores.extend(shard_scores)
            for key in penta:
                penta[key] += int(report["penta"][key])
            wdl = report["candidate_wdl"]
            wins += int(wdl["win"])
            draws += int(wdl["draw"])
            losses += int(wdl["loss"])
            max_ply_draws += int(report["max_ply_draws"])
            games.extend(report["games_detail"])

        pairs_total = int(contract["sharding"]["pairs"])
        games_total = int(contract["sharding"]["games"])
        expected_ranges = [
            (index * int(contract["sharding"]["pairs_per_shard"]), (index + 1) * int(contract["sharding"]["pairs_per_shard"]))
            for index in range(expected_shards)
        ]
        if sorted(seen_ranges) != expected_ranges:
            raise ProofError("pair-range coverage drift")
        if len(pair_scores) != pairs_total or len(games) != games_total or wins + draws + losses != games_total:
            raise ProofError("aggregate cardinality drift")
        if sum(penta.values()) != pairs_total:
            raise ProofError("aggregate pentanomial drift")

        game_ids = [game["id"] for game in games]
        if len(game_ids) != len(set(game_ids)):
            raise ProofError("duplicate game identity")
        opening_counts: dict[int, int] = {}
        color_counts: dict[tuple[int, str], int] = {}
        for game in games:
            index = int(game["opening_index"])
            if not 0 <= index < pairs_total:
                raise ProofError(f"invalid opening index {index}")
            if game["opening_fen_sha256"] != openings["openings"][index]["fen_sha256"]:
                raise ProofError(f"opening digest drift at {index}")
            opening_counts[index] = opening_counts.get(index, 0) + 1
            key = (index, game["candidate_color"])
            color_counts[key] = color_counts.get(key, 0) + 1
        for index in range(pairs_total):
            if opening_counts.get(index) != 2:
                raise ProofError(f"opening pair coverage drift at {index}")
            if color_counts.get((index, "white")) != 1 or color_counts.get((index, "black")) != 1:
                raise ProofError(f"color reversal drift at {index}")

        score_points = wins + 0.5 * draws
        score_fraction = score_points / games_total
        stats = paired_statistics(pair_scores, float(contract["statistics"]["t_critical_df1023"]))
        if abs(float(stats["paired_score_fraction_mean"]) - score_fraction) > 1e-12:
            raise ProofError("paired/game score reconciliation failed")
        ci_low, ci_high = [float(v) for v in stats["paired_95ci_score_fraction"]]
        candidate_nodes = sum(int(game["candidate_nodes"]) for game in games)
        base_nodes = sum(int(game["base_nodes"]) for game in games)
        candidate_searches = sum(int(game["candidate_searches"]) for game in games)
        base_searches = sum(int(game["base_searches"]) for game in games)
        candidate_elapsed = sum(float(game["candidate_elapsed_ms"]) for game in games)
        base_elapsed = sum(float(game["base_elapsed_ms"]) for game in games)
        final = {
            "schema": FINAL_SCHEMA,
            "campaign_id": contract["campaign_id"],
            "contract_sha256": contract_sha,
            "run_id": preflight["run_id"],
            "run_attempt": preflight["run_attempt"],
            "source_head": preflight["source_head"],
            "baseline_commit": contract["baseline"]["commit"],
            "candidate_commit": contract["candidate"]["commit"],
            "base_binary_sha256": preflight["base_binary_sha256"],
            "candidate_binary_sha256": preflight["candidate_binary_sha256"],
            "selection_sha256": preflight["selection_sha256"],
            "pairs": pairs_total,
            "games": games_total,
            "movetime_ms": contract["time_control"]["movetime_ms"],
            "candidate_wdl": {"win": wins, "draw": draws, "loss": losses},
            "candidate_score": score_points,
            "candidate_score_pct": score_fraction * 100.0,
            "penta": penta,
            **stats,
            "elo_estimate": finite_elo(score_fraction),
            "elo_95ci": [finite_elo(ci_low), finite_elo(ci_high)],
            "candidate_nodes_total": candidate_nodes,
            "base_nodes_total": base_nodes,
            "candidate_nodes_per_search": candidate_nodes / max(candidate_searches, 1),
            "base_nodes_per_search": base_nodes / max(base_searches, 1),
            "candidate_elapsed_ms_total": candidate_elapsed,
            "base_elapsed_ms_total": base_elapsed,
            "max_ply_draws": max_ply_draws,
            "operational_failures": 0,
            "strength_gate_passed": stats["decision"] == "PASS_STRENGTH",
            "benchmark_speed_alone_used_for_promotion": False,
            "parameter_tuning_after_launch": False,
        }
        write_json(output, final)
        print("B4_R3_STRENGTH_FINAL", json.dumps(final, sort_keys=True), flush=True)
        return 0
    except BaseException as error:
        blocked = {
            "schema": FINAL_SCHEMA,
            "decision": "BLOCKED_CORRECTNESS",
            "error_type": type(error).__name__,
            "error": str(error),
            "strength_gate_passed": False,
        }
        write_json(output, blocked)
        print("B4_R3_STRENGTH_FINAL_BLOCKED", json.dumps(blocked, sort_keys=True), file=sys.stderr, flush=True)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--base-binary", required=True)
    preflight.add_argument("--candidate-binary", required=True)
    preflight.add_argument("--out", required=True)
    shard = subparsers.add_parser("shard")
    shard.add_argument("--preflight", required=True)
    shard.add_argument("--shard", type=int, required=True)
    shard.add_argument("--output", required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--preflight", required=True)
    aggregate.add_argument("--shards", required=True)
    aggregate.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "preflight":
        return command_preflight(args)
    if args.command == "shard":
        return command_shard(args)
    return command_aggregate(args)


if __name__ == "__main__":
    raise SystemExit(main())
