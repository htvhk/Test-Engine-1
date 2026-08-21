from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

STATE_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-SHARD-STATE-v1"
GAME_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-GAME-v1"
MANIFEST_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-EVIDENCE-MANIFEST-v1"
PGN_EVENT = "TE1 Adaptive LMR R2 2048-game confirmatory proof"
UCI_MOVE = re.compile(r"[a-h][1-8][a-h][1-8][qrbn]?")
RESULTS = {"1-0", "0-1", "1/2-1/2"}
TERMINATIONS = {
    "max-ply",
    "checkmate",
    "stalemate",
    "threefold repetition",
    "50-move rule",
    "insufficient material",
}


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    ).encode("ascii")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical_bytes(value))
    try:
        observed = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeError) as error:
        raise RuntimeError(f"durable JSON re-read failed: {path}") from error
    if canonical_bytes(observed) != canonical_bytes(value):
        raise RuntimeError(f"durable JSON re-read mismatch: {path}")


def _option_payload(contract: dict[str, Any], arm: str) -> dict[str, Any]:
    record = contract["option_fingerprints"][arm]
    payload = record["options"]
    observed = sha256_bytes(canonical_bytes(payload))
    if observed != record["sha256"]:
        raise RuntimeError(
            f"{arm} option fingerprint drift: {observed} != {record['sha256']}"
        )
    return payload


def validate_option_fingerprints(contract: dict[str, Any]) -> tuple[str, str]:
    control = _option_payload(contract, "control")
    treatment = _option_payload(contract, "treatment")
    differences = {
        key
        for key in set(control) | set(treatment)
        if control.get(key) != treatment.get(key)
    }
    if differences != {"UseAdaptiveLMR"}:
        raise RuntimeError(f"unexpected arm option differences: {sorted(differences)}")
    if control["UseAdaptiveLMR"] is not False or treatment["UseAdaptiveLMR"] is not True:
        raise RuntimeError("Adaptive LMR treatment/control polarity drift")
    return (
        contract["option_fingerprints"]["control"]["sha256"],
        contract["option_fingerprints"]["treatment"]["sha256"],
    )


def schedule_for(
    openings: list[dict[str, Any]], mode: str, shard: int
) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    for opening in openings:
        rank = int(opening["valid_rank"])
        pair_id = f"{mode}-S{shard:02d}-R{rank:04d}"
        schedule.extend(
            [
                {
                    "id": pair_id + "-G1",
                    "pair_id": pair_id,
                    "opening": opening,
                    "off_is_white": True,
                    "off_color": "white",
                    "adaptive_color": "black",
                    "white": "AdaptiveLMR-OFF",
                    "black": "AdaptiveLMR-ON",
                },
                {
                    "id": pair_id + "-G2",
                    "pair_id": pair_id,
                    "opening": opening,
                    "off_is_white": False,
                    "off_color": "black",
                    "adaptive_color": "white",
                    "white": "AdaptiveLMR-ON",
                    "black": "AdaptiveLMR-OFF",
                },
            ]
        )
    return schedule


def identity_for(
    contract: dict[str, Any],
    manifest: dict[str, Any],
    mode: str,
    shard: int,
) -> dict[str, Any]:
    control_sha, treatment_sha = validate_option_fingerprints(contract)
    return {
        "campaign_id": contract["campaign_id"],
        "contract_sha256": manifest["contract_sha256"],
        "run_id": manifest["run_id"],
        "run_attempt": manifest["run_attempt"],
        "source_head": manifest["source_head"],
        "source_tree": manifest["source_tree"],
        "binary_sha256": manifest["binary_sha256"],
        "openings_file_sha256": manifest["openings_file_sha256"],
        "mode": mode,
        "shard": shard,
        "control_options_sha256": control_sha,
        "treatment_options_sha256": treatment_sha,
        "candidate_identity_commit": contract["candidate"]["identity_commit"],
        "candidate_source_commit": contract["candidate"]["source_commit"],
        "candidate_search_sha256": contract["candidate"]["search_sha256"],
    }


def new_state(identity: dict[str, Any], scheduled_ids: list[str]) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "identity": identity,
        "scheduled_ids": scheduled_ids,
        "completed_games": [],
        "pending_game": None,
        "wdl": {"win": 0, "draw": 0, "loss": 0},
        "resume_eligible": True,
    }


def result_points(symbol: str) -> float:
    if symbol == "W":
        return 1.0
    if symbol == "D":
        return 0.5
    if symbol == "L":
        return 0.0
    raise RuntimeError(f"invalid Adaptive LMR result symbol: {symbol!r}")


def _validate_game_shape(game: dict[str, Any]) -> None:
    if game.get("result") not in RESULTS:
        raise RuntimeError("invalid game result")
    if game.get("adaptive_result") not in {"W", "D", "L"}:
        raise RuntimeError("invalid adaptive result")
    if game.get("termination") not in TERMINATIONS:
        raise RuntimeError("invalid game termination")
    moves = game.get("moves")
    if (
        not isinstance(moves, list)
        or any(not isinstance(move, str) or UCI_MOVE.fullmatch(move) is None for move in moves)
    ):
        raise RuntimeError("invalid UCI move evidence")
    if game.get("moves_after_epd") != len(moves):
        raise RuntimeError("move evidence count drift")


def game_record(
    schedule_game: dict[str, Any],
    played: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    _validate_game_shape(
        {
            "result": played.get("result"),
            "adaptive_result": played.get("on_result"),
            "termination": played.get("termination"),
            "moves": played.get("moves"),
            "moves_after_epd": played.get("moves_after_epd"),
        }
    )
    if played.get("opening_valid_rank") != schedule_game["opening"]["valid_rank"]:
        raise RuntimeError("played opening rank drift")
    if played.get("opening_fen_sha256") != schedule_game["opening"]["fen_sha256"]:
        raise RuntimeError("played opening digest drift")
    if played.get("off_color") != schedule_game["off_color"]:
        raise RuntimeError("played control color drift")
    return {
        "schema": GAME_SCHEMA,
        "game_id": schedule_game["id"],
        "pair_id": schedule_game["pair_id"],
        "identity": identity,
        "opening_valid_rank": played["opening_valid_rank"],
        "opening_fen_sha256": played["opening_fen_sha256"],
        "start_fen": schedule_game["opening"]["fen"],
        "white": schedule_game["white"],
        "black": schedule_game["black"],
        "off_color": played["off_color"],
        "adaptive_color": schedule_game["adaptive_color"],
        "adaptive_result": played["on_result"],
        "result": played["result"],
        "termination": played["termination"],
        "terminal_score": played["terminal_score"],
        "moves_after_epd": played["moves_after_epd"],
        "moves": played["moves"],
    }


def result_path(evidence_dir: Path, game_id: str) -> Path:
    return evidence_dir / "results" / f"{game_id}.json"


def persist_result(evidence_dir: Path, record: dict[str, Any]) -> None:
    path = result_path(evidence_dir, record["game_id"])
    if path.exists():
        existing = load_result(path, record["identity"])
        if canonical_bytes(existing) != canonical_bytes(record):
            raise RuntimeError(f"contradictory result evidence: {record['game_id']}")
        return
    atomic_json(path, record)


def load_result(path: Path, identity: dict[str, Any]) -> dict[str, Any]:
    try:
        record = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"corrupt game result evidence: {path}") from error
    if record.get("schema") != GAME_SCHEMA or record.get("identity") != identity:
        raise RuntimeError(f"game result identity drift: {path}")
    if record.get("game_id") != path.stem:
        raise RuntimeError(f"game result filename identity drift: {path}")
    _validate_game_shape(
        {
            "result": record.get("result"),
            "adaptive_result": record.get("adaptive_result"),
            "termination": record.get("termination"),
            "moves": record.get("moves"),
            "moves_after_epd": record.get("moves_after_epd"),
        }
    )
    if record.get("off_color") not in {"white", "black"}:
        raise RuntimeError(f"game result control color drift: {path}")
    if record.get("adaptive_color") not in {"white", "black"}:
        raise RuntimeError(f"game result treatment color drift: {path}")
    if record["off_color"] == record["adaptive_color"]:
        raise RuntimeError(f"game result color collision: {path}")
    return record


_PGN_BLOCK = re.compile(
    rb'\[Event "TE1 Adaptive LMR R2 2048-game confirmatory proof"\]\n'
    rb'\[GameId "([^"\n]+)"\]\n'
    rb'\[Mode "(TIME|NODES)"\]\n'
    rb'\[Shard "([0-9]+)"\]\n'
    rb'\[OpeningRank "([0-9]+)"\]\n'
    rb'\[OpeningSHA256 "([0-9a-f]{64})"\]\n'
    rb'\[White "(AdaptiveLMR-ON|AdaptiveLMR-OFF)"\]\n'
    rb'\[Black "(AdaptiveLMR-ON|AdaptiveLMR-OFF)"\]\n'
    rb'\[ControlColor "(white|black)"\]\n'
    rb'\[AdaptiveColor "(white|black)"\]\n'
    rb'\[Result "(1-0|0-1|1/2-1/2)"\]\n'
    rb'\[Termination "(max-ply|checkmate|stalemate|threefold repetition|50-move rule|insufficient material)"\]\n'
    rb'\[CandidateCommit "([0-9a-f]{40})"\]\n'
    rb'\[BinarySHA256 "([0-9a-f]{64})"\]\n'
    rb'\[ControlOptionsSHA256 "([0-9a-f]{64})"\]\n'
    rb'\[TreatmentOptionsSHA256 "([0-9a-f]{64})"\]\n\n'
    rb'\{ UCI moves: ((?:[a-h][1-8][a-h][1-8][qrbn]?'
    rb'(?: [a-h][1-8][a-h][1-8][qrbn]?)*)?) \} '
    rb'(1-0|0-1|1/2-1/2)\n\n'
)


def canonical_pgn_block(record: dict[str, Any]) -> bytes:
    moves = " ".join(record["moves"])
    identity = record["identity"]
    text = (
        f'[Event "{PGN_EVENT}"]\n'
        f'[GameId "{record["game_id"]}"]\n'
        f'[Mode "{identity["mode"]}"]\n'
        f'[Shard "{identity["shard"]}"]\n'
        f'[OpeningRank "{record["opening_valid_rank"]}"]\n'
        f'[OpeningSHA256 "{record["opening_fen_sha256"]}"]\n'
        f'[White "{record["white"]}"]\n'
        f'[Black "{record["black"]}"]\n'
        f'[ControlColor "{record["off_color"]}"]\n'
        f'[AdaptiveColor "{record["adaptive_color"]}"]\n'
        f'[Result "{record["result"]}"]\n'
        f'[Termination "{record["termination"]}"]\n'
        f'[CandidateCommit "{identity["candidate_identity_commit"]}"]\n'
        f'[BinarySHA256 "{identity["binary_sha256"]}"]\n'
        f'[ControlOptionsSHA256 "{identity["control_options_sha256"]}"]\n'
        f'[TreatmentOptionsSHA256 "{identity["treatment_options_sha256"]}"]\n\n'
        f'{{ UCI moves: {moves} }} {record["result"]}\n\n'
    )
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError as error:
        raise RuntimeError(f"non-ASCII PGN evidence: {record['game_id']}") from error
    if b"\r" in raw:
        raise RuntimeError("canonical PGN contains carriage return")
    return raw


def parse_pgn(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"cannot read PGN evidence: {path}") from error
    if b"\r" in data:
        raise RuntimeError(f"non-LF PGN evidence: {path}")
    records: dict[str, dict[str, Any]] = {}
    offset = 0
    while offset < len(data):
        match = _PGN_BLOCK.match(data, offset)
        if match is None:
            raise RuntimeError(f"malformed or partial PGN evidence at byte {offset}: {path}")
        (
            game_id_b,
            mode_b,
            shard_b,
            rank_b,
            opening_sha_b,
            white_b,
            black_b,
            off_b,
            adaptive_b,
            result_b,
            termination_b,
            candidate_b,
            binary_b,
            control_options_b,
            treatment_options_b,
            move_text_b,
            body_result_b,
        ) = match.groups()
        try:
            decoded = [
                item.decode("ascii")
                for item in (
                    game_id_b,
                    mode_b,
                    shard_b,
                    rank_b,
                    opening_sha_b,
                    white_b,
                    black_b,
                    off_b,
                    adaptive_b,
                    result_b,
                    termination_b,
                    candidate_b,
                    binary_b,
                    control_options_b,
                    treatment_options_b,
                    move_text_b,
                    body_result_b,
                )
            ]
        except UnicodeDecodeError as error:
            raise RuntimeError(f"non-ASCII PGN evidence at byte {offset}: {path}") from error
        (
            game_id,
            mode,
            shard,
            rank,
            opening_sha,
            white,
            black,
            off_color,
            adaptive_color,
            result,
            termination,
            candidate,
            binary_sha,
            control_options,
            treatment_options,
            move_text,
            body_result,
        ) = decoded
        if result != body_result:
            raise RuntimeError(f"contradictory PGN result: {game_id}")
        if game_id in records:
            raise RuntimeError(f"duplicate PGN game identity: {game_id}")
        records[game_id] = {
            "game_id": game_id,
            "mode": mode,
            "shard": int(shard),
            "opening_valid_rank": int(rank),
            "opening_fen_sha256": opening_sha,
            "white": white,
            "black": black,
            "off_color": off_color,
            "adaptive_color": adaptive_color,
            "result": result,
            "termination": termination,
            "candidate_identity_commit": candidate,
            "binary_sha256": binary_sha,
            "control_options_sha256": control_options,
            "treatment_options_sha256": treatment_options,
            "moves": move_text.split() if move_text else [],
        }
        offset = match.end()
    return records


def append_pgn(path: Path, record: dict[str, Any]) -> None:
    block = canonical_pgn_block(record)
    before = parse_pgn(path)
    game_id = record["game_id"]
    if game_id in before:
        verify_pgn_record(before[game_id], record)
        return
    existing = path.read_bytes() if path.exists() else b""
    atomic_write(path, existing + block)
    after = parse_pgn(path)
    if game_id not in after:
        raise RuntimeError(f"PGN transaction not recoverable: {game_id}")
    verify_pgn_record(after[game_id], record)


def verify_pgn_record(pgn: dict[str, Any], record: dict[str, Any]) -> None:
    identity = record["identity"]
    expected = {
        "game_id": record["game_id"],
        "mode": identity["mode"],
        "shard": identity["shard"],
        "opening_valid_rank": record["opening_valid_rank"],
        "opening_fen_sha256": record["opening_fen_sha256"],
        "white": record["white"],
        "black": record["black"],
        "off_color": record["off_color"],
        "adaptive_color": record["adaptive_color"],
        "result": record["result"],
        "termination": record["termination"],
        "candidate_identity_commit": identity["candidate_identity_commit"],
        "binary_sha256": identity["binary_sha256"],
        "control_options_sha256": identity["control_options_sha256"],
        "treatment_options_sha256": identity["treatment_options_sha256"],
        "moves": record["moves"],
    }
    if pgn != expected:
        raise RuntimeError(f"PGN/JSON evidence mismatch: {record['game_id']}")


def validate_state(
    state: dict[str, Any], identity: dict[str, Any], scheduled_ids: list[str]
) -> None:
    if state.get("schema") != STATE_SCHEMA:
        raise RuntimeError("wrong proof state schema")
    if state.get("identity") != identity:
        raise RuntimeError("proof state identity drift")
    if state.get("scheduled_ids") != scheduled_ids:
        raise RuntimeError("proof state schedule drift")
    if state.get("resume_eligible") is not True:
        raise RuntimeError("proof state is not resume eligible")
    completed = state.get("completed_games")
    if not isinstance(completed, list) or len(completed) != len(set(completed)):
        raise RuntimeError("proof state completed-game identity drift")
    if completed != scheduled_ids[: len(completed)]:
        raise RuntimeError("completed games are not the exact ordered schedule prefix")
    pending = state.get("pending_game")
    next_id = scheduled_ids[len(completed)] if len(completed) < len(scheduled_ids) else None
    if pending is not None and pending != next_id:
        raise RuntimeError("pending game is not the next scheduled game")
    wdl = state.get("wdl")
    if not isinstance(wdl, dict) or set(wdl) != {"win", "draw", "loss"}:
        raise RuntimeError("proof state WDL shape drift")
    if any(not isinstance(wdl[key], int) or wdl[key] < 0 for key in wdl):
        raise RuntimeError("proof state WDL value drift")
    if sum(wdl.values()) != len(completed):
        raise RuntimeError("proof state WDL cardinality drift")


def load_state(
    path: Path, identity: dict[str, Any], scheduled_ids: list[str]
) -> dict[str, Any]:
    try:
        state = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"corrupt proof state: {path}") from error
    validate_state(state, identity, scheduled_ids)
    return state


def write_state(path: Path, state: dict[str, Any]) -> None:
    validate_state(state, state["identity"], state["scheduled_ids"])
    atomic_json(path, state)


def _perspective_symbol(record: dict[str, Any]) -> str:
    symbol = record["adaptive_result"]
    result = record["result"]
    expected = "D"
    if result != "1/2-1/2":
        white_won = result == "1-0"
        adaptive_is_white = record["adaptive_color"] == "white"
        expected = "W" if white_won == adaptive_is_white else "L"
    if symbol != expected:
        raise RuntimeError(
            f"adaptive result contradicts result/color evidence: {record['game_id']}"
        )
    return symbol


def recompute_wdl(records: list[dict[str, Any]]) -> dict[str, int]:
    wdl = {"win": 0, "draw": 0, "loss": 0}
    for record in records:
        symbol = _perspective_symbol(record)
        wdl[{"W": "win", "D": "draw", "L": "loss"}[symbol]] += 1
    return wdl


def proof_game_for_core(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "opening_valid_rank": record["opening_valid_rank"],
        "opening_fen_sha256": record["opening_fen_sha256"],
        "off_color": record["off_color"],
        "on_result": record["adaptive_result"],
        "result": record["result"],
        "termination": record["termination"],
        "terminal_score": record["terminal_score"],
        "moves_after_epd": record["moves_after_epd"],
        "moves": record["moves"],
    }


def reconcile_record(
    proof: Any,
    validator: Any,
    contract: dict[str, Any],
    schedule_game: dict[str, Any],
    record: dict[str, Any],
) -> None:
    if record["game_id"] != schedule_game["id"]:
        raise RuntimeError("result/schedule game identity drift")
    if record["pair_id"] != schedule_game["pair_id"]:
        raise RuntimeError("result/schedule pair identity drift")
    if record["white"] != schedule_game["white"] or record["black"] != schedule_game["black"]:
        raise RuntimeError("result/schedule engine-color drift")
    game = proof_game_for_core(record)
    pair = {
        "opening_valid_rank": schedule_game["opening"]["valid_rank"],
        "opening_fen_sha256": schedule_game["opening"]["fen_sha256"],
    }
    proof.validate_game_record(contract, pair, game)
    proof.reconcile_game(validator, contract, schedule_game["opening"], game)


def result_ids(evidence_dir: Path) -> set[str]:
    root = evidence_dir / "results"
    if not root.exists():
        return set()
    ids: set[str] = set()
    for path in root.iterdir():
        if path.is_dir():
            raise RuntimeError(f"foreign result evidence directory: {path}")
        if path.suffix != ".json":
            raise RuntimeError(f"foreign result evidence file: {path}")
        if path.stem in ids:
            raise RuntimeError(f"duplicate result evidence identity: {path.stem}")
        ids.add(path.stem)
    return ids


def validate_transaction(
    proof: Any,
    validator: Any,
    contract: dict[str, Any],
    evidence_dir: Path,
    schedule: list[dict[str, Any]],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    scheduled_ids = [game["id"] for game in schedule]
    identity = state["identity"]
    validate_state(state, identity, scheduled_ids)
    completed = state["completed_games"]
    pending = state["pending_game"]
    committed_results = result_ids(evidence_dir)
    pgn_records = parse_pgn(evidence_dir / "games.pgn")
    committed_pgn = set(pgn_records)
    completed_set = set(completed)
    allowed = completed_set | ({pending} if pending is not None else set())
    if not committed_results <= allowed or not committed_pgn <= allowed:
        raise RuntimeError("foreign, stale, or future committed evidence")
    if not completed_set <= committed_results or not completed_set <= committed_pgn:
        raise RuntimeError("completed transaction evidence is incomplete")
    if pending is None:
        if committed_results != completed_set or committed_pgn != completed_set:
            raise RuntimeError("evidence exists outside completed transaction")
    else:
        has_result = pending in committed_results
        has_pgn = pending in committed_pgn
        if has_pgn and not has_result:
            raise RuntimeError("pending PGN exists without durable result JSON")

    by_id = {game["id"]: game for game in schedule}
    records: list[dict[str, Any]] = []
    for game_id in completed:
        schedule_game = by_id[game_id]
        record = load_result(result_path(evidence_dir, game_id), identity)
        verify_pgn_record(pgn_records[game_id], record)
        reconcile_record(proof, validator, contract, schedule_game, record)
        records.append(record)
    if state["wdl"] != recompute_wdl(records):
        raise RuntimeError("state WDL does not reconcile with committed evidence")

    if pending is not None and pending in committed_results:
        schedule_game = by_id[pending]
        record = load_result(result_path(evidence_dir, pending), identity)
        reconcile_record(proof, validator, contract, schedule_game, record)
        if pending in committed_pgn:
            verify_pgn_record(pgn_records[pending], record)
    return records


def commit_pending(
    proof: Any,
    validator: Any,
    contract: dict[str, Any],
    evidence_dir: Path,
    schedule: list[dict[str, Any]],
    state: dict[str, Any],
) -> bool:
    pending = state["pending_game"]
    if pending is None:
        return False
    index = len(state["completed_games"])
    if index >= len(schedule) or schedule[index]["id"] != pending:
        raise RuntimeError("pending game does not match schedule cursor")
    path = result_path(evidence_dir, pending)
    if not path.exists():
        return False
    record = load_result(path, state["identity"])
    reconcile_record(proof, validator, contract, schedule[index], record)
    pgn_path = evidence_dir / "games.pgn"
    pgn = parse_pgn(pgn_path)
    if pending not in pgn:
        append_pgn(pgn_path, record)
        pgn = parse_pgn(pgn_path)
    verify_pgn_record(pgn[pending], record)
    state["completed_games"].append(pending)
    state["pending_game"] = None
    state["wdl"] = recompute_wdl(
        [
            load_result(result_path(evidence_dir, game_id), state["identity"])
            for game_id in state["completed_games"]
        ]
    )
    write_state(evidence_dir / "state.json", state)
    validate_transaction(proof, validator, contract, evidence_dir, schedule, state)
    return True


def pairs_from_records(
    schedule: list[dict[str, Any]], records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {record["game_id"]: record for record in records}
    pairs: list[dict[str, Any]] = []
    for index in range(0, len(schedule), 2):
        first = schedule[index]
        second = schedule[index + 1]
        if first["pair_id"] != second["pair_id"]:
            raise RuntimeError("schedule pair identity drift")
        r1 = by_id.get(first["id"])
        r2 = by_id.get(second["id"])
        if r1 is None or r2 is None:
            raise RuntimeError(f"incomplete pair evidence: {first['pair_id']}")
        if {r1["off_color"], r2["off_color"]} != {"white", "black"}:
            raise RuntimeError(f"pair color reversal drift: {first['pair_id']}")
        points = result_points(r1["adaptive_result"]) + result_points(r2["adaptive_result"])
        pairs.append(
            {
                "opening_valid_rank": first["opening"]["valid_rank"],
                "opening_fen_sha256": first["opening"]["fen_sha256"],
                "games": [proof_game_for_core(r1), proof_game_for_core(r2)],
                "pair_on_points": points,
                "pair_normalized_score": points / 2.0,
            }
        )
    return pairs


def evidence_manifest(
    evidence_dir: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    result_root = evidence_dir / "results"
    results: dict[str, str] = {}
    if result_root.exists():
        for path in sorted(result_root.glob("*.json")):
            results[path.name] = sha256_file(path)
    state_path = evidence_dir / "state.json"
    pgn_path = evidence_dir / "games.pgn"
    return {
        "schema": MANIFEST_SCHEMA,
        "identity": state["identity"],
        "completed_games": list(state["completed_games"]),
        "pending_game": state["pending_game"],
        "state_sha256": sha256_file(state_path),
        "pgn_sha256": sha256_file(pgn_path),
        "result_files_sha256": results,
    }


def write_manifest(evidence_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    manifest = evidence_manifest(evidence_dir, state)
    path = evidence_dir / "evidence-manifest.json"
    atomic_json(path, manifest)
    verify_manifest(evidence_dir, state, manifest)
    return manifest


def verify_manifest(
    evidence_dir: Path,
    state: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = evidence_dir / "evidence-manifest.json"
    if manifest is None:
        try:
            manifest = json.loads(path.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"missing or corrupt evidence manifest: {path}") from error
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("identity") != state["identity"]:
        raise RuntimeError("evidence manifest identity drift")
    observed = evidence_manifest(evidence_dir, state)
    if canonical_bytes(observed) != canonical_bytes(manifest):
        raise RuntimeError("evidence manifest hash closure drift")
    completed_files = {f"{game_id}.json" for game_id in state["completed_games"]}
    if set(manifest["result_files_sha256"]) != completed_files:
        raise RuntimeError("evidence manifest result-set drift")
    if manifest["pending_game"] is not None:
        raise RuntimeError("final evidence manifest cannot contain a pending game")
    return manifest


def verify_shard_closure(
    proof: Any,
    contract: dict[str, Any],
    preflight_dir: Path,
    binary: Path,
    summary_path: Path,
) -> dict[str, Any]:
    summary = json.loads(summary_path.read_bytes())
    if summary.get("schema") != proof.SCHEMA_SHARD:
        raise RuntimeError(f"wrong shard summary schema: {summary_path}")
    mode = str(summary["mode"])
    shard = int(summary["shard"])
    contract_loaded, manifest, openings, _ = proof.load_preflight(preflight_dir, binary)
    if contract_loaded != contract:
        raise RuntimeError("aggregate contract object drift")
    pps = int(contract["sharding"]["pairs_per_shard"])
    selected = openings["arms"][mode]["openings"][shard * pps : (shard + 1) * pps]
    schedule = schedule_for(selected, mode, shard)
    identity = identity_for(contract, manifest, mode, shard)
    evidence_dir = summary_path.parent / "evidence"
    state = load_state(
        evidence_dir / "state.json", identity, [game["id"] for game in schedule]
    )
    if len(state["completed_games"]) != len(schedule) or state["pending_game"] is not None:
        raise RuntimeError(f"shard transaction is not closed: {mode}/{shard}")
    validator = proof.new_engine(binary, False, contract)
    try:
        records = validate_transaction(
            proof, validator, contract, evidence_dir, schedule, state
        )
    finally:
        validator.close()
    if len(records) != 2 * pps:
        raise RuntimeError(f"shard game evidence cardinality drift: {mode}/{shard}")
    manifest_obj = verify_manifest(evidence_dir, state)
    if summary.get("evidence_manifest_sha256") != sha256_file(
        evidence_dir / "evidence-manifest.json"
    ):
        raise RuntimeError(f"shard evidence manifest digest drift: {mode}/{shard}")
    if summary.get("pgn_sha256") != manifest_obj["pgn_sha256"]:
        raise RuntimeError(f"shard PGN digest drift: {mode}/{shard}")
    if summary.get("state_sha256") != manifest_obj["state_sha256"]:
        raise RuntimeError(f"shard state digest drift: {mode}/{shard}")
    if summary.get("raw_move_evidence_complete") is not True:
        raise RuntimeError(f"raw move evidence flag missing: {mode}/{shard}")
    if summary.get("transaction_closure") != "PASS":
        raise RuntimeError(f"transaction closure flag missing: {mode}/{shard}")
    return summary


def discard_uncommitted_temporaries(evidence_dir: Path) -> None:
    candidates = [
        evidence_dir / "state.json.tmp",
        evidence_dir / "games.pgn.tmp",
        evidence_dir / "evidence-manifest.json.tmp",
    ]
    result_root = evidence_dir / "results"
    if result_root.exists():
        for path in result_root.iterdir():
            if path.is_file() and path.name.endswith(".json.tmp"):
                candidates.append(path)
            elif path.is_file() and path.suffix == ".tmp":
                raise RuntimeError(f"foreign temporary evidence file: {path}")
    touched: set[Path] = set()
    for path in candidates:
        if path.exists():
            path.unlink()
            touched.add(path.parent)
    for directory in touched:
        fsync_directory(directory)


def run_hardened_shard(proof: Any, args: Any) -> int:
    mode = str(args.mode).upper()
    shard = int(args.shard)
    output = Path(args.output)
    evidence_dir = output.parent / "evidence"
    try:
        contract, manifest, openings, contract_sha = proof.load_preflight(
            Path(args.preflight), Path(args.binary)
        )
        binary = Path(args.binary)
        if mode not in {"TIME", "NODES"}:
            raise RuntimeError(f"invalid proof mode: {mode}")
        shards_per_arm = int(contract["sharding"]["shards_per_arm"])
        pps = int(contract["sharding"]["pairs_per_shard"])
        if not 0 <= shard < shards_per_arm:
            raise RuntimeError(f"invalid proof shard: {shard}")
        selected = openings["arms"][mode]["openings"][shard * pps : (shard + 1) * pps]
        if len(selected) != pps:
            raise RuntimeError("incomplete shard opening slice")
        schedule = schedule_for(selected, mode, shard)
        scheduled_ids = [game["id"] for game in schedule]
        identity = identity_for(contract, manifest, mode, shard)
        state_path = evidence_dir / "state.json"
        pgn_path = evidence_dir / "games.pgn"
        results_root = evidence_dir / "results"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        discard_uncommitted_temporaries(evidence_dir)
        if state_path.exists():
            state = load_state(state_path, identity, scheduled_ids)
        else:
            if pgn_path.exists() or results_root.exists():
                raise RuntimeError("evidence exists without transaction state")
            state = new_state(identity, scheduled_ids)
            write_state(state_path, state)

        validator = proof.new_engine(binary, False, contract)
        try:
            validate_transaction(
                proof, validator, contract, evidence_dir, schedule, state
            )
            while len(state["completed_games"]) < len(schedule):
                if state["pending_game"] is not None:
                    if commit_pending(
                        proof, validator, contract, evidence_dir, schedule, state
                    ):
                        continue
                index = len(state["completed_games"])
                scheduled = schedule[index]
                if state["pending_game"] is None:
                    state["pending_game"] = scheduled["id"]
                    write_state(state_path, state)
                played = proof.play_game(
                    binary,
                    contract,
                    mode,
                    scheduled["opening"],
                    scheduled["off_is_white"],
                )
                record = game_record(scheduled, played, identity)
                reconcile_record(proof, validator, contract, scheduled, record)
                persist_result(evidence_dir, record)
                append_pgn(pgn_path, record)
                if not commit_pending(
                    proof, validator, contract, evidence_dir, schedule, state
                ):
                    raise RuntimeError("durable game transaction could not be committed")
            records = validate_transaction(
                proof, validator, contract, evidence_dir, schedule, state
            )
        finally:
            validator.close()

        if len(records) != len(schedule):
            raise RuntimeError("completed game evidence cardinality drift")
        pairs = pairs_from_records(schedule, records)
        if len(pairs) != pps:
            raise RuntimeError("completed pair evidence cardinality drift")
        games = [game for pair in pairs for game in pair["games"]]
        wins = sum(game["on_result"] == "W" for game in games)
        draws = sum(game["on_result"] == "D" for game in games)
        losses = sum(game["on_result"] == "L" for game in games)
        penta = {str(units / 2.0): 0 for units in range(5)}
        for pair in pairs:
            penta[str(pair["pair_on_points"])] += 1
        manifest_obj = write_manifest(evidence_dir, state)
        summary = {
            "schema": proof.SCHEMA_SHARD,
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
            "on_wdl": {"win": wins, "draw": draws, "loss": losses},
            "on_score": wins + 0.5 * draws,
            "penta": penta,
            "operational_failures": 0,
            "control_options_sha256": identity["control_options_sha256"],
            "treatment_options_sha256": identity["treatment_options_sha256"],
            "state_sha256": manifest_obj["state_sha256"],
            "pgn_sha256": manifest_obj["pgn_sha256"],
            "evidence_manifest_sha256": sha256_file(
                evidence_dir / "evidence-manifest.json"
            ),
            "raw_move_evidence_complete": True,
            "transaction_closure": "PASS",
        }
        atomic_json(output, summary)
        return 0
    except BaseException as error:
        blocked = {
            "schema": proof.SCHEMA_SHARD,
            "status": "BLOCKED_CORRECTNESS",
            "mode": mode,
            "shard": shard,
            "error_type": type(error).__name__,
            "error": str(error),
            "operational_failures": 1,
            "raw_move_evidence_complete": False,
            "transaction_closure": "FAIL",
        }
        atomic_json(output, blocked)
        return 2
