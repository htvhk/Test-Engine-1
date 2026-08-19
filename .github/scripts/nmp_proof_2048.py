from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from scripts import r3_attribution_campaign as r3

CONTRACT_PATH = Path("diagnostics/nmp_r1_proof_2048/NMP_PROOF_CONTRACT.json")
DEFAULT_BIN = Path("./target/release/te1")
BASELINE_COMMIT = "7820b54d511afbf5dd2d38a3f686af97c14de639"
BASELINE_TREE = "465e442fb26f8ad5ee6a793f35edb22d7f66f8b0"
EXPECTED_BLOBS = {
    "Cargo.lock": "aa658abb8878317a99308209955c3406599aa8b2",
    "EXPERIMENTAL_SOURCE_AUTHORIZATION.json": "2f00adfecb3ca3e96bc66bf7195ca29f1c16cb0e",
    "scripts/r3_attribution_campaign.py": "b0827a49ac6cd718353cc9536db1d0d7b7a1e59b",
    "crates/te1-chess/src/lib.rs": "aaca8a00442fa4de91e9368feb3d96718030ccd0",
    "crates/te1-engine/src/main.rs": "1c8bd3ee321394455cb5aaea06a71440f5954cbf",
    "crates/te1-eval/src/lib.rs": "5626d3e620285cb4e966f41f50ed4886f2735274",
    "crates/te1-search/src/lib.rs": "cd393b65085cdfa1b327f00f23c69f61763fcb2e",
}
BOOK_URL_TEMPLATE = "https://raw.githubusercontent.com/{repository}/{commit}/{file}"
Z95 = 1.959963984540054
PAIR_POINTS = {"W": 1.0, "D": 0.5, "L": 0.0}
SCHEMA_OPENINGS = "TE1-ALPHA26-NMP-R1-PROOF-OPENINGS-v1"
SCHEMA_PREFLIGHT = "TE1-ALPHA26-NMP-R1-PROOF-PREFLIGHT-v1"
SCHEMA_SHARD = "TE1-ALPHA26-NMP-R1-PROOF-SHARD-v1"
SCHEMA_FINAL = "TE1-ALPHA26-NMP-R1-PROOF-FINAL-v1"


class ProofError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    data = canonical_bytes(value)
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise ProofError(f"missing or malformed JSON: {path}") from error


def git(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *arguments], text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        raise ProofError(result.stderr.strip() or f"git {' '.join(arguments)} failed")
    return result.stdout.strip()


def load_contract() -> tuple[dict[str, Any], str]:
    raw = CONTRACT_PATH.read_bytes()
    contract = json.loads(raw)
    if contract.get("schema") != "TE1-ALPHA26-NMP-R1-PROOF-v1":
        raise ProofError("wrong proof contract schema")
    if contract.get("baseline") != {"commit": BASELINE_COMMIT, "tree": BASELINE_TREE}:
        raise ProofError("proof contract baseline drift")
    if contract["feature"] != {
        "control_value": False,
        "default_remains_off_during_campaign": True,
        "treatment_value": True,
        "uci_option": "UseNullMovePruning",
    }:
        raise ProofError("proof feature contract drift")
    if contract["confounders"] != {
        "clear_hash_each_game": True,
        "deterministic": True,
        "evaluator": "classical",
        "hash_mb": 16,
        "opening_depth_plies": 28,
        "resign_adjudication": False,
        "same_executable_for_both_sides": True,
        "score_adjudication": False,
        "threads": 1,
        "total_ply_cap": 200,
    }:
        raise ProofError("proof confounder contract drift")
    if contract["arms"]["TIME"] != {
        "games": 1024,
        "go": "movetime",
        "move_overhead_ms": 0,
        "movetime_ms": 200,
        "pairs": 512,
    }:
        raise ProofError("TIME arm contract drift")
    if contract["arms"]["NODES"] != {
        "games": 1024,
        "go": "nodes",
        "nodes_per_move": 100000,
        "pairs": 512,
    }:
        raise ProofError("NODES arm contract drift")
    if contract["sharding"] != {
        "pairs_per_shard": 16,
        "shards_per_arm": 32,
        "total_shards": 64,
    }:
        raise ProofError("proof sharding contract drift")
    selections = contract["opening_selection"]
    if selections["time_arm"] != {
        "pairs": 512,
        "valid_rank_start": 256,
        "valid_rank_stop_exclusive": 768,
    } or selections["nodes_arm"] != {
        "pairs": 512,
        "valid_rank_start": 768,
        "valid_rank_stop_exclusive": 1280,
    }:
        raise ProofError("proof opening ranges drift")
    if contract["statistics"]["z_95_two_sided"] != Z95:
        raise ProofError("proof statistical constant drift")
    if contract["rerun_policy"]["admissible_run_attempt"] != 1:
        raise ProofError("proof rerun policy drift")
    return contract, sha256_bytes(raw)


def source_identity() -> dict[str, str]:
    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    baseline_tree = git("rev-parse", f"{BASELINE_COMMIT}^{{tree}}")
    if baseline_tree != BASELINE_TREE:
        raise ProofError("frozen NMP baseline tree drift")
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASELINE_COMMIT, head],
        capture_output=True,
        check=False,
    ).returncode != 0:
        raise ProofError("proof branch does not descend from corrected NMP baseline")
    protected_paths = [
        "crates",
        "Cargo.lock",
        "EXPERIMENTAL_SOURCE_AUTHORIZATION.json",
        "scripts/r3_attribution_campaign.py",
    ]
    changed = git("diff", "--name-only", BASELINE_COMMIT, "HEAD", "--", *protected_paths)
    if changed:
        raise ProofError(f"production/protected drift in proof branch: {changed}")
    dirty = git("status", "--porcelain=v1", "--", *protected_paths)
    if dirty:
        raise ProofError(f"dirty production/protected path: {dirty}")
    for path, expected in EXPECTED_BLOBS.items():
        actual = git("hash-object", path)
        if actual != expected:
            raise ProofError(f"protected blob drift: {path} {actual} != {expected}")
    return {
        "source_head": head,
        "source_tree": tree,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_tree": BASELINE_TREE,
    }


def require_first_attempt() -> tuple[str, int]:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    attempt_text = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id or not attempt_text:
        raise ProofError("campaign execution requires GitHub run identity")
    attempt = int(attempt_text)
    if attempt != 1:
        raise ProofError(
            "only GitHub run attempt 1 is admissible; discard failed attempts rather than selectively rerunning"
        )
    return run_id, attempt


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def fetch_book(contract: dict[str, Any]) -> list[tuple[str, str]]:
    source = contract["opening_source"]
    url = BOOK_URL_TEMPLATE.format(**source)
    with urllib.request.urlopen(url, timeout=60) as response:
        archive = response.read()
    if git_blob_sha1(archive) != source["git_blob_sha1"]:
        raise ProofError("pinned opening-book Git blob mismatch")
    if sha256_bytes(archive) != source["sha256"]:
        raise ProofError("pinned opening-book SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        epd_names = [name for name in zf.namelist() if name.lower().endswith(".epd")]
        if len(epd_names) != 1:
            raise ProofError(f"expected exactly one EPD member, found {epd_names}")
        text = zf.read(epd_names[0]).decode("utf-8")
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    namespace = source["sort_namespace"]
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
        key = hashlib.sha256((namespace + fen).encode("ascii")).hexdigest()
        candidates.append((key, fen))
    candidates.sort()
    return candidates


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


def new_engine(binary: Path, enabled: bool, contract: dict[str, Any]) -> r3.UciEngine:
    engine = r3.UciEngine(binary, "CLASSICAL")
    engine.setoption("Hash", str(contract["confounders"]["hash_mb"]))
    engine.setoption("MoveOverhead", "0")
    engine.setoption("UseNullMovePruning", "true" if enabled else "false")
    if engine.evaluator_identity() != "classical":
        engine.close()
        raise ProofError("proof evaluator drifted from classical")
    return engine


def has_legal_move(engine: r3.UciEngine, start_fen: str, moves: list[str]) -> bool:
    set_fen_position(engine, start_fen, moves)
    engine.send("perft 1")
    line = engine.wait_for(lambda item: item.startswith("info string perft 1 nodes "))
    match = re.fullmatch(r"info string perft 1 nodes (\d+)", line)
    if match is None:
        raise ProofError(f"malformed perft response: {line}")
    return int(match.group(1)) > 0


def bestmove_time(engine: r3.UciEngine, movetime_ms: int) -> tuple[str, str | None]:
    engine.send(f"go movetime {movetime_ms}")
    final_score = None
    while True:
        line = engine.wait_for(lambda _: True)
        if line.startswith("info depth "):
            score = re.search(r" score (cp -?\d+|mate -?\d+) ", line)
            if score:
                final_score = score.group(1)
        if line.startswith("bestmove "):
            match = re.fullmatch(r"bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000)", line)
            if not match:
                raise ProofError(f"malformed bestmove: {line}")
            return match.group(1), final_score


def arm_bestmove(
    engine: r3.UciEngine, mode: str, contract: dict[str, Any]
) -> tuple[str, str | None]:
    if mode == "NODES":
        return engine.bestmove(int(contract["arms"]["NODES"]["nodes_per_move"]))
    if mode == "TIME":
        return bestmove_time(engine, int(contract["arms"]["TIME"]["movetime_ms"]))
    raise ProofError(f"unknown arm: {mode}")


def opening_hash(fens: list[str]) -> str:
    return hashlib.sha256("\n".join(fens).encode("ascii")).hexdigest()


def validate_openings(
    binary: Path, contract: dict[str, Any], candidates: list[tuple[str, str]]
) -> list[str]:
    required = contract["opening_selection"]["nodes_arm"]["valid_rank_stop_exclusive"]
    validator = new_engine(binary, False, contract)
    valid: list[str] = []
    try:
        for _, fen in candidates:
            try:
                normalized = set_fen_position(validator, fen, [])
            except r3.IllegalMoveError:
                continue
            if normalized.split()[:4] != fen.split()[:4]:
                continue
            valid.append(fen)
            if len(valid) == required:
                break
    finally:
        validator.close()
    if len(valid) != required:
        raise ProofError(f"only {len(valid)} valid openings available; need {required}")
    selection = contract["opening_selection"]
    p1 = selection["prior_batch_1"]
    p2 = selection["prior_batch_2"]
    p1_hash = opening_hash(valid[p1["valid_rank_start"] : p1["valid_rank_stop_exclusive"]])
    p2_hash = opening_hash(valid[p2["valid_rank_start"] : p2["valid_rank_stop_exclusive"]])
    if p1_hash != p1["sha256"]:
        raise ProofError(f"prior batch 1 opening drift: {p1_hash}")
    if p2_hash != p2["sha256"]:
        raise ProofError(f"prior batch 2 opening drift: {p2_hash}")
    return valid


def make_opening_freeze(valid: list[str], contract: dict[str, Any], contract_sha: str) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for mode, key in (("TIME", "time_arm"), ("NODES", "nodes_arm")):
        spec = contract["opening_selection"][key]
        start = int(spec["valid_rank_start"])
        stop = int(spec["valid_rank_stop_exclusive"])
        fens = valid[start:stop]
        if len(fens) != int(spec["pairs"]):
            raise ProofError(f"{mode} opening count mismatch")
        records = [
            {"valid_rank": rank, "fen": fen, "fen_sha256": sha256_bytes(fen.encode("ascii"))}
            for rank, fen in zip(range(start, stop), fens, strict=True)
        ]
        arms[mode] = {
            "valid_rank_range": [start, stop],
            "pairs": len(records),
            "selection_sha256": opening_hash(fens),
            "openings": records,
        }
    time_fens = {item["fen"] for item in arms["TIME"]["openings"]}
    node_fens = {item["fen"] for item in arms["NODES"]["openings"]}
    if time_fens & node_fens:
        raise ProofError("TIME and NODES opening sets overlap")
    prior = set(valid[:256])
    if prior & (time_fens | node_fens):
        raise ProofError("new proof openings overlap observed prior 512-game openings")
    return {
        "schema": SCHEMA_OPENINGS,
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "arms": arms,
    }


def command_contract_check(_: argparse.Namespace) -> int:
    contract, contract_sha = load_contract()
    identity = source_identity()
    print(
        "NMP_PROOF_CONTRACT_OK",
        json.dumps({"contract_sha256": contract_sha, **identity}, sort_keys=True),
    )
    return 0


def command_preflight(args: argparse.Namespace) -> int:
    contract, contract_sha = load_contract()
    identity = source_identity()
    run_id, run_attempt = require_first_attempt()
    binary = Path(args.binary)
    if not binary.is_file():
        raise ProofError(f"missing proof binary: {binary}")
    binary_sha = sha256_file(binary)
    candidates = fetch_book(contract)
    valid = validate_openings(binary, contract, candidates)
    opening_freeze = make_opening_freeze(valid, contract, contract_sha)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    openings_path = out / "nmp-proof-openings.json"
    write_json(openings_path, opening_freeze)
    openings_sha = sha256_file(openings_path)
    manifest = {
        "schema": SCHEMA_PREFLIGHT,
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        **identity,
        "binary_sha256": binary_sha,
        "openings_file_sha256": openings_sha,
        "time_selection_sha256": opening_freeze["arms"]["TIME"]["selection_sha256"],
        "nodes_selection_sha256": opening_freeze["arms"]["NODES"]["selection_sha256"],
        "book_git_blob_sha1": contract["opening_source"]["git_blob_sha1"],
        "book_sha256": contract["opening_source"]["sha256"],
        "status": "PASS",
    }
    write_json(out / "nmp-proof-preflight.json", manifest)
    print("NMP_PROOF_PREFLIGHT", json.dumps(manifest, sort_keys=True))
    return 0


def load_preflight(directory: Path, binary: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    contract, contract_sha = load_contract()
    identity = source_identity()
    run_id, run_attempt = require_first_attempt()
    manifest = load_json(directory / "nmp-proof-preflight.json")
    openings_path = directory / "nmp-proof-openings.json"
    openings = load_json(openings_path)
    required = {
        "schema": SCHEMA_PREFLIGHT,
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        **identity,
        "binary_sha256": sha256_file(binary),
        "openings_file_sha256": sha256_file(openings_path),
        "status": "PASS",
    }
    for key, value in required.items():
        if manifest.get(key) != value:
            raise ProofError(f"preflight identity drift for {key}: {manifest.get(key)!r} != {value!r}")
    if openings.get("schema") != SCHEMA_OPENINGS or openings.get("contract_sha256") != contract_sha:
        raise ProofError("opening-freeze identity drift")
    for mode in ("TIME", "NODES"):
        selection_sha = openings["arms"][mode]["selection_sha256"]
        if manifest.get(f"{mode.lower()}_selection_sha256") != selection_sha:
            raise ProofError(f"{mode} selection digest drift")
    return contract, manifest, openings, contract_sha


def play_game(
    binary: Path,
    contract: dict[str, Any],
    mode: str,
    opening: dict[str, Any],
    off_is_white: bool,
) -> dict[str, Any]:
    white: r3.UciEngine | None = None
    black: r3.UciEngine | None = None
    try:
        white = new_engine(binary, not off_is_white, contract)
        black = new_engine(binary, off_is_white, contract)
        white.setoption("Clear Hash")
        black.setoption("Clear Hash")
        moves: list[str] = []
        start_fen = opening["fen"]
        first = set_fen_position(white, start_fen, moves)
        set_fen_position(black, start_fen, moves)
        history = [first]
        result = "1/2-1/2"
        termination = "max-ply"
        terminal_score: str | None = None
        additional_plies = (
            int(contract["confounders"]["total_ply_cap"])
            - int(contract["confounders"]["opening_depth_plies"])
        )
        for _ in range(additional_plies):
            current = history[-1]
            actor = white if current.split()[1] == "w" else black
            fen = set_fen_position(actor, start_fen, moves)
            reason = r3.draw_reason(history)
            if reason and has_legal_move(actor, start_fen, moves):
                result = "1/2-1/2"
                termination = reason
                break
            move, score = arm_bestmove(actor, mode, contract)
            if move == "0000":
                if has_legal_move(actor, start_fen, moves):
                    raise ProofError("bestmove 0000 while a legal move exists")
                result = r3.terminal_result(fen, fen.split()[1] == "w", score)
                termination = "checkmate" if result != "1/2-1/2" else "stalemate"
                terminal_score = score
                break
            moves.append(move)
            new_fen = set_fen_position(white, start_fen, moves)
            set_fen_position(black, start_fen, moves)
            history.append(new_fen)
        on_result = "D"
        if result != "1/2-1/2":
            white_won = result == "1-0"
            on_is_white = not off_is_white
            on_result = "W" if white_won == on_is_white else "L"
        return {
            "opening_valid_rank": opening["valid_rank"],
            "opening_fen_sha256": opening["fen_sha256"],
            "off_color": "white" if off_is_white else "black",
            "on_result": on_result,
            "result": result,
            "termination": termination,
            "terminal_score": terminal_score,
            "moves_after_epd": len(moves),
            "moves": moves,
        }
    finally:
        if white is not None:
            white.close()
        if black is not None:
            black.close()


def reconcile_game(
    validator: r3.UciEngine,
    contract: dict[str, Any],
    opening: dict[str, Any],
    game: dict[str, Any],
) -> None:
    start_fen = opening["fen"]
    moves: list[str] = []
    first = set_fen_position(validator, start_fen, moves)
    history = [first]
    additional_plies = (
        int(contract["confounders"]["total_ply_cap"])
        - int(contract["confounders"]["opening_depth_plies"])
    )
    if len(game["moves"]) > additional_plies:
        raise ProofError("game evidence exceeds frozen ply cap")
    for index, move in enumerate(game["moves"]):
        reason = r3.draw_reason(history)
        if reason and has_legal_move(validator, start_fen, moves):
            raise ProofError(f"game contains move after mandatory draw at ply {index}: {reason}")
        try:
            new_fen = set_fen_position(validator, start_fen, moves + [move])
        except r3.IllegalMoveError as error:
            raise ProofError(f"illegal recorded move at ply {index}: {move}") from error
        moves.append(move)
        history.append(new_fen)
    termination = game["termination"]
    if termination == "max-ply":
        if len(moves) != additional_plies or game["result"] != "1/2-1/2":
            raise ProofError("invalid max-ply adjudication evidence")
        return
    current = history[-1]
    reason = r3.draw_reason(history)
    if reason is not None:
        if termination != reason or not has_legal_move(validator, start_fen, moves):
            raise ProofError("invalid rule-draw adjudication evidence")
        if game["result"] != "1/2-1/2":
            raise ProofError("rule draw recorded as decisive")
        return
    if termination not in ("checkmate", "stalemate"):
        raise ProofError(f"unknown termination: {termination}")
    if has_legal_move(validator, start_fen, moves):
        raise ProofError("terminal game still has a legal move")
    white_to_move = current.split()[1] == "w"
    in_check = r3.terminal_side_is_in_check(current, white_to_move)
    expected_termination = "checkmate" if in_check else "stalemate"
    expected_result = ("0-1" if white_to_move else "1-0") if in_check else "1/2-1/2"
    if termination != expected_termination or game["result"] != expected_result:
        raise ProofError(
            f"terminal semantics mismatch: {termination}/{game['result']} != "
            f"{expected_termination}/{expected_result}"
        )


def game_points(result: str) -> float:
    try:
        return PAIR_POINTS[result]
    except KeyError as error:
        raise ProofError(f"invalid ON result symbol: {result}") from error


def run_shard(args: argparse.Namespace) -> int:
    mode = args.mode.upper()
    shard = int(args.shard)
    output = Path(args.output)
    blocked: dict[str, Any] | None = None
    try:
        contract, manifest, openings, contract_sha = load_preflight(Path(args.preflight), Path(args.binary))
        binary = Path(args.binary)
        if mode not in ("TIME", "NODES"):
            raise ProofError(f"invalid mode: {mode}")
        shards_per_arm = int(contract["sharding"]["shards_per_arm"])
        pairs_per_shard = int(contract["sharding"]["pairs_per_shard"])
        if not 0 <= shard < shards_per_arm:
            raise ProofError(f"invalid shard index: {shard}")
        arm_openings = openings["arms"][mode]["openings"]
        start = shard * pairs_per_shard
        stop = start + pairs_per_shard
        selected = arm_openings[start:stop]
        if len(selected) != pairs_per_shard:
            raise ProofError("incomplete shard opening slice")
        validator = new_engine(binary, False, contract)
        pairs: list[dict[str, Any]] = []
        try:
            for opening in selected:
                g1 = play_game(binary, contract, mode, opening, True)
                g2 = play_game(binary, contract, mode, opening, False)
                reconcile_game(validator, contract, opening, g1)
                reconcile_game(validator, contract, opening, g2)
                pair_points = game_points(g1["on_result"]) + game_points(g2["on_result"])
                pair = {
                    "opening_valid_rank": opening["valid_rank"],
                    "opening_fen_sha256": opening["fen_sha256"],
                    "games": [g1, g2],
                    "pair_on_points": pair_points,
                    "pair_normalized_score": pair_points / 2.0,
                }
                pairs.append(pair)
                print("NMP_PROOF_PAIR", json.dumps({"mode": mode, "shard": shard, **pair}, sort_keys=True))
        finally:
            validator.close()
        games = [game for pair in pairs for game in pair["games"]]
        w = sum(game["on_result"] == "W" for game in games)
        d = sum(game["on_result"] == "D" for game in games)
        l = sum(game["on_result"] == "L" for game in games)
        penta = {str(units / 2.0): 0 for units in range(5)}
        for pair in pairs:
            penta[str(pair["pair_on_points"])] += 1
        report = {
            "schema": SCHEMA_SHARD,
            "status": "PASS",
            "campaign_id": contract["campaign_id"],
            "contract_sha256": contract_sha,
            "run_id": manifest["run_id"],
            "run_attempt": manifest["run_attempt"],
            "source_head": manifest["source_head"],
            "source_tree": manifest["source_tree"],
            "binary_sha256": manifest["binary_sha256"],
            "openings_file_sha256": manifest["openings_file_sha256"],
            "mode": mode,
            "shard": shard,
            "pairs": pairs,
            "games": len(games),
            "on_wdl": {"win": w, "draw": d, "loss": l},
            "on_score": w + 0.5 * d,
            "penta": penta,
            "operational_failures": 0,
        }
        write_json(output, report)
        print("NMP_PROOF_SHARD", json.dumps({k: v for k, v in report.items() if k != "pairs"}, sort_keys=True))
        return 0
    except BaseException as error:
        blocked = {
            "schema": SCHEMA_SHARD,
            "status": "BLOCKED_CORRECTNESS",
            "mode": mode,
            "shard": shard,
            "error_type": type(error).__name__,
            "error": str(error),
            "operational_failures": 1,
        }
        write_json(output, blocked)
        print("NMP_PROOF_BLOCKED", json.dumps(blocked, sort_keys=True), file=sys.stderr)
        return 2


def validate_game_record(
    contract: dict[str, Any],
    pair: dict[str, Any],
    game: dict[str, Any],
) -> None:
    if game.get("opening_valid_rank") != pair.get("opening_valid_rank"):
        raise ProofError("game/pair opening rank mismatch")
    if game.get("opening_fen_sha256") != pair.get("opening_fen_sha256"):
        raise ProofError("game/pair opening digest mismatch")
    off_color = game.get("off_color")
    if off_color not in ("white", "black"):
        raise ProofError("invalid OFF color evidence")
    result = game.get("result")
    if result not in ("1-0", "0-1", "1/2-1/2"):
        raise ProofError("invalid game result evidence")
    expected_on = "D"
    if result != "1/2-1/2":
        white_won = result == "1-0"
        on_is_white = off_color == "black"
        expected_on = "W" if white_won == on_is_white else "L"
    if game.get("on_result") != expected_on:
        raise ProofError("ON result symbol contradicts game result/color evidence")
    moves = game.get("moves")
    if (
        not isinstance(moves, list)
        or any(
            not isinstance(move, str)
            or re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", move) is None
            for move in moves
        )
    ):
        raise ProofError("malformed UCI move evidence")
    if game.get("moves_after_epd") != len(moves):
        raise ProofError("move-count evidence mismatch")
    maximum = (
        int(contract["confounders"]["total_ply_cap"])
        - int(contract["confounders"]["opening_depth_plies"])
    )
    if len(moves) > maximum:
        raise ProofError("game evidence exceeds frozen ply cap")
    termination = game.get("termination")
    allowed_terminations = {
        "max-ply",
        "checkmate",
        "stalemate",
        "threefold repetition",
        "50-move rule",
        "insufficient material",
    }
    if termination not in allowed_terminations:
        raise ProofError(f"unknown termination evidence: {termination!r}")
    if termination == "max-ply" and (
        len(moves) != maximum or result != "1/2-1/2"
    ):
        raise ProofError("max-ply evidence is inconsistent")
    if termination in {
        "threefold repetition",
        "50-move rule",
        "insufficient material",
        "stalemate",
    } and result != "1/2-1/2":
        raise ProofError("draw termination recorded as decisive")
    terminal_score = game.get("terminal_score")
    if termination in ("checkmate", "stalemate"):
        if terminal_score is not None and not isinstance(terminal_score, str):
            raise ProofError("malformed terminal score evidence")
    elif terminal_score is not None:
        raise ProofError("non-terminal game unexpectedly records terminal score")


def paired_ci(normalized_pair_scores: list[float]) -> dict[str, float | int]:
    n = len(normalized_pair_scores)
    if n < 2:
        raise ProofError("at least two pairs required for confidence interval")
    mean = statistics.fmean(normalized_pair_scores)
    sample_sd = statistics.stdev(normalized_pair_scores)
    se = sample_sd / math.sqrt(n)
    margin = Z95 * se
    return {
        "pairs": n,
        "score": mean,
        "score_pct": mean * 100.0,
        "sample_sd": sample_sd,
        "standard_error": se,
        "ci95_lower": max(0.0, mean - margin),
        "ci95_upper": min(1.0, mean + margin),
    }


def summarize_arm(mode: str, pairs: list[dict[str, Any]]) -> dict[str, Any]:
    games = [game for pair in pairs for game in pair["games"]]
    w = sum(game["on_result"] == "W" for game in games)
    d = sum(game["on_result"] == "D" for game in games)
    l = sum(game["on_result"] == "L" for game in games)
    penta = {str(units / 2.0): 0 for units in range(5)}
    scores: list[float] = []
    for pair in pairs:
        points = sum(game_points(game["on_result"]) for game in pair["games"])
        if abs(points - float(pair["pair_on_points"])) > 1e-12:
            raise ProofError("pair score evidence mismatch")
        penta[str(points)] += 1
        scores.append(points / 2.0)
    return {
        "mode": mode,
        "games": len(games),
        "on_wdl": {"win": w, "draw": d, "loss": l},
        "on_score": w + 0.5 * d,
        "on_score_pct": (w + 0.5 * d) * 100.0 / len(games),
        "penta": penta,
        "paired_statistics": paired_ci(scores),
    }


def command_aggregate(args: argparse.Namespace) -> int:
    output = Path(args.output)
    try:
        contract, manifest, openings, contract_sha = load_preflight(Path(args.preflight), Path(args.binary))
        root = Path(args.shards)
        files = sorted(root.rglob("*.json")) if root.exists() else []
        shards_per_arm = int(contract["sharding"]["shards_per_arm"])
        pairs_per_shard = int(contract["sharding"]["pairs_per_shard"])
        expected_keys = {
            (mode, shard)
            for mode in ("TIME", "NODES")
            for shard in range(shards_per_arm)
        }
        reports: dict[tuple[str, int], dict[str, Any]] = {}
        blocked: list[dict[str, Any]] = []
        for path in files:
            report = load_json(path)
            if report.get("schema") != SCHEMA_SHARD:
                continue
            key = (str(report.get("mode")), int(report.get("shard", -1)))
            if key in reports:
                raise ProofError(f"duplicate shard evidence: {key}")
            reports[key] = report
            if report.get("status") != "PASS":
                blocked.append({"key": key, "status": report.get("status"), "error": report.get("error")})
        missing = sorted(expected_keys - set(reports))
        foreign = sorted(set(reports) - expected_keys)
        if missing or foreign or blocked:
            final = {
                "schema": SCHEMA_FINAL,
                "campaign_id": contract["campaign_id"],
                "contract_sha256": contract_sha,
                "decision": "BLOCKED_CORRECTNESS",
                "missing_shards": missing,
                "foreign_shards": foreign,
                "blocked_shards": blocked,
                "prior_512_games_pooled": False,
            }
            write_json(output, final)
            print("NMP_PROOF_FINAL", json.dumps(final, sort_keys=True))
            return 2
        for report in reports.values():
            required = {
                "campaign_id": contract["campaign_id"],
                "contract_sha256": contract_sha,
                "run_id": manifest["run_id"],
                "run_attempt": manifest["run_attempt"],
                "source_head": manifest["source_head"],
                "source_tree": manifest["source_tree"],
                "binary_sha256": manifest["binary_sha256"],
                "openings_file_sha256": manifest["openings_file_sha256"],
                "operational_failures": 0,
            }
            for key, value in required.items():
                if report.get(key) != value:
                    raise ProofError(f"shard identity drift: {key}")
            if (
                len(report.get("pairs", [])) != pairs_per_shard
                or report.get("games") != 2 * pairs_per_shard
            ):
                raise ProofError("shard cardinality drift")
        all_pairs: dict[str, list[dict[str, Any]]] = {"TIME": [], "NODES": []}
        seen_ranks: set[int] = set()
        for mode in ("TIME", "NODES"):
            frozen_by_rank = {
                int(item["valid_rank"]): item for item in openings["arms"][mode]["openings"]
            }
            expected_ranks = set(frozen_by_rank)
            observed_ranks: set[int] = set()
            for shard in range(shards_per_arm):
                report = reports[(mode, shard)]
                for pair in report["pairs"]:
                    rank = int(pair["opening_valid_rank"])
                    if rank in observed_ranks or rank in seen_ranks:
                        raise ProofError(f"duplicate/cross-arm opening rank: {rank}")
                    frozen = frozen_by_rank.get(rank)
                    if frozen is None or pair.get("opening_fen_sha256") != frozen["fen_sha256"]:
                        raise ProofError(f"opening evidence drift at rank {rank}")
                    if len(pair.get("games", [])) != 2:
                        raise ProofError("pair does not contain exactly two games")
                    for game in pair["games"]:
                        validate_game_record(contract, pair, game)
                    colors = {game.get("off_color") for game in pair["games"]}
                    if colors != {"white", "black"}:
                        raise ProofError("pair color reversal evidence drift")
                    observed_ranks.add(rank)
                    seen_ranks.add(rank)
                    all_pairs[mode].append(pair)
            if observed_ranks != expected_ranks:
                raise ProofError(f"{mode} opening set is not exact")
            expected_pairs = int(contract["arms"][mode]["pairs"])
            if len(all_pairs[mode]) != expected_pairs:
                raise ProofError(
                    f"{mode} pair count is not {expected_pairs}: {len(all_pairs[mode])}"
                )
        time_summary = summarize_arm("TIME", all_pairs["TIME"])
        node_summary = summarize_arm("NODES", all_pairs["NODES"])
        combined_pairs = all_pairs["TIME"] + all_pairs["NODES"]
        combined_summary = summarize_arm("COMBINED", combined_pairs)
        time_ci = time_summary["paired_statistics"]
        node_ci = node_summary["paired_statistics"]
        if time_ci["ci95_lower"] > 0.5 and node_ci["ci95_lower"] > 0.5:
            decision = "PASS_DEFAULT_ON"
        elif time_ci["ci95_upper"] < 0.5 or node_ci["ci95_upper"] < 0.5:
            decision = "FAIL_NMP"
        else:
            decision = "INCONCLUSIVE"
        final = {
            "schema": SCHEMA_FINAL,
            "campaign_id": contract["campaign_id"],
            "contract_sha256": contract_sha,
            "run_id": manifest["run_id"],
            "run_attempt": manifest["run_attempt"],
            "source_head": manifest["source_head"],
            "source_tree": manifest["source_tree"],
            "binary_sha256": manifest["binary_sha256"],
            "openings_file_sha256": manifest["openings_file_sha256"],
            "time_selection_sha256": manifest["time_selection_sha256"],
            "nodes_selection_sha256": manifest["nodes_selection_sha256"],
            "total_pairs": 1024,
            "total_games": 2048,
            "operational_failures": 0,
            "TIME": time_summary,
            "NODES": node_summary,
            "COMBINED_SUPPORTIVE": combined_summary,
            "decision": decision,
            "prior_512_games_pooled": False,
            "default_on_authorized": decision == "PASS_DEFAULT_ON",
        }
        write_json(output, final)
        print("NMP_PROOF_FINAL", json.dumps(final, sort_keys=True))
        return 0
    except BaseException as error:
        final = {
            "schema": SCHEMA_FINAL,
            "decision": "BLOCKED_CORRECTNESS",
            "error_type": type(error).__name__,
            "error": str(error),
            "prior_512_games_pooled": False,
        }
        write_json(output, final)
        print("NMP_PROOF_FINAL_BLOCKED", json.dumps(final, sort_keys=True), file=sys.stderr)
        return 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="TE1 Alpha 2.6 NMP R1 predeclared 2048-game proof")
    sub = root.add_subparsers(dest="command", required=True)
    check = sub.add_parser("contract-check")
    check.set_defaults(func=command_contract_check)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--binary", default=str(DEFAULT_BIN))
    preflight.add_argument("--out", required=True)
    preflight.set_defaults(func=command_preflight)
    shard = sub.add_parser("shard")
    shard.add_argument("--binary", required=True)
    shard.add_argument("--preflight", required=True)
    shard.add_argument("--mode", required=True, choices=["TIME", "NODES"])
    shard.add_argument("--shard", required=True, type=int)
    shard.add_argument("--output", required=True)
    shard.set_defaults(func=run_shard)
    aggregate = sub.add_parser("aggregate")
    aggregate.add_argument("--binary", required=True)
    aggregate.add_argument("--preflight", required=True)
    aggregate.add_argument("--shards", required=True)
    aggregate.add_argument("--output", required=True)
    aggregate.set_defaults(func=command_aggregate)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
