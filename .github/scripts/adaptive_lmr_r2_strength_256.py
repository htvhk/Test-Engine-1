#!/usr/bin/env python3
"""Frozen 256-game paired strength shard for Alpha 2.6 Adaptive LMR R2."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from scripts import r3_attribution_campaign as r3

CANDIDATE_ID = "8f38a15919bb65c60c774ea96fd4e7e68d80d36b"
SOURCE_COMMIT = "320bb584a4b9a0643aece496f5df4f4b779798cb"
SEARCH_SHA256 = "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e"
SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-STRENGTH-256-SHARD-v1"
SELECTION_DOMAIN = "TE1-ALPHA26-ADAPTIVE-LMR-R2-STRENGTH-256-v1\0"
SHARDS = 8
PAIRS_TOTAL = 128
PAIRS_PER_SHARD = PAIRS_TOTAL // SHARDS
NODES = 100_000
OPENING_DEPTH_PLIES = 28
TOTAL_PLY_CAP = 200
ADDITIONAL_PLIES = TOTAL_PLY_CAP - OPENING_DEPTH_PLIES
HASH_MB = 16

BOOK_COMMIT = "65815ccdbc7727cd4f6aee252ba8f67fb740e92f"
BOOK_BLOB_SHA1 = "b851fc8c484b9e36b178131a7f47269bfdfacd39"
BOOK_NAME = "Drawkiller_balanced_big.epd.zip"
BOOK_URL = (
    "https://raw.githubusercontent.com/official-stockfish/books/"
    + BOOK_COMMIT
    + "/"
    + BOOK_NAME
)


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def load_book() -> tuple[list[str], str]:
    with urllib.request.urlopen(BOOK_URL, timeout=30) as response:
        archive = response.read()
    observed_blob = git_blob_sha1(archive)
    if observed_blob != BOOK_BLOB_SHA1:
        raise RuntimeError(f"book Git blob mismatch: {observed_blob} != {BOOK_BLOB_SHA1}")
    book_sha256 = hashlib.sha256(archive).hexdigest()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        epd_names = [name for name in zf.namelist() if name.lower().endswith(".epd")]
        if len(epd_names) != 1:
            raise RuntimeError(f"expected one EPD member, found {epd_names}")
        text = zf.read(epd_names[0]).decode("utf-8")

    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
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
        key = hashlib.sha256((SELECTION_DOMAIN + fen).encode("ascii")).hexdigest()
        candidates.append((key, fen))
    candidates.sort()
    return [fen for _, fen in candidates], book_sha256


def set_fen_position(engine: r3.UciEngine, start_fen: str, moves: list[str]) -> str:
    suffix = "" if not moves else " moves " + " ".join(moves)
    engine.send("position fen " + start_fen + suffix)
    engine.send("isready")
    engine._position_ready_barrier()
    engine.send("d")
    fen = engine.wait_for(lambda line: line.count("/") == 7)
    if len(fen.split()) != 6:
        raise RuntimeError(f"malformed FEN response: {fen}")
    return fen


def new_engine(binary: Path, adaptive: bool) -> r3.UciEngine:
    engine = r3.UciEngine(binary, "CLASSICAL")
    engine.setoption("Hash", str(HASH_MB))
    engine.setoption("Threads", "1")
    engine.setoption("Deterministic", "true")
    engine.setoption("UseLMR", "true")
    engine.setoption("UseSEEPruning", "true")
    engine.setoption("UseNullMovePruning", "true")
    engine.setoption("UseAdaptiveLMR", "true" if adaptive else "false")
    if engine.evaluator_identity() != "classical":
        raise RuntimeError("formal strength evaluator drifted from classical")
    return engine


def select_openings(binary: Path, candidates: list[str]) -> list[str]:
    validator = new_engine(binary, False)
    selected: list[str] = []
    try:
        for fen in candidates:
            try:
                normalized = set_fen_position(validator, fen, [])
            except Exception:
                continue
            if normalized.split()[:4] != fen.split()[:4]:
                continue
            selected.append(fen)
            if len(selected) == PAIRS_TOTAL:
                break
    finally:
        validator.close()
    if len(selected) != PAIRS_TOTAL:
        raise RuntimeError(f"only {len(selected)} valid openings selected")
    return selected


def has_legal_move_fen(
    engine: r3.UciEngine,
    start_fen: str,
    moves: list[str],
    fen: str,
) -> bool:
    board = fen.split()[0]
    side_white = fen.split()[1] == "w"
    sources: list[tuple[str, str]] = []
    ranks = board.split("/")
    for rank, row in zip(range(8, 0, -1), ranks, strict=True):
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
            trials = (
                [source + destination + promotion for promotion in promotions]
                if promotions
                else [source + destination]
            )
            for candidate in trials:
                legal = False
                try:
                    set_fen_position(engine, start_fen, moves + [candidate])
                    legal = True
                except r3.IllegalMoveError:
                    pass
                finally:
                    set_fen_position(engine, start_fen, moves)
                if legal:
                    return True
    return False


def play(binary: Path, shard: int, opening_index: int, start_fen: str, adaptive_is_white: bool) -> dict:
    white = black = None
    try:
        white = new_engine(binary, adaptive_is_white)
        black = new_engine(binary, not adaptive_is_white)
        white.setoption("Clear Hash")
        black.setoption("Clear Hash")
        moves: list[str] = []
        first = set_fen_position(white, start_fen, moves)
        set_fen_position(black, start_fen, moves)
        history = [first]
        result = "1/2-1/2"
        termination = "max-ply"
        for _ in range(ADDITIONAL_PLIES):
            current = history[-1]
            actor = white if current.split()[1] == "w" else black
            fen = set_fen_position(actor, start_fen, moves)
            reason = r3.draw_reason(history)
            if reason and has_legal_move_fen(actor, start_fen, moves, fen):
                result = "1/2-1/2"
                termination = reason
                break
            move, score = actor.bestmove(NODES)
            if move == "0000":
                if has_legal_move_fen(actor, start_fen, moves, fen):
                    raise RuntimeError("bestmove 0000 with legal move available")
                result = r3.terminal_result(fen, fen.split()[1] == "w", score)
                termination = "checkmate" if result != "1/2-1/2" else "stalemate"
                break
            moves.append(move)
            new_fen = set_fen_position(white, start_fen, moves)
            set_fen_position(black, start_fen, moves)
            history.append(new_fen)

        adaptive_result = "D"
        if result != "1/2-1/2":
            white_won = result == "1-0"
            adaptive_result = "W" if white_won == adaptive_is_white else "L"
        return {
            "id": f"S{shard:02d}-O{opening_index:03d}-" + ("G1" if adaptive_is_white else "G2"),
            "opening_index": opening_index,
            "adaptive_color": "white" if adaptive_is_white else "black",
            "adaptive_result": adaptive_result,
            "result": result,
            "moves_after_epd": len(moves),
            "termination": termination,
        }
    finally:
        if white is not None:
            white.close()
        if black is not None:
            black.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if not 0 <= args.shard < SHARDS:
        raise SystemExit(f"invalid shard {args.shard}")
    binary = Path(args.engine)
    if not binary.is_file():
        raise SystemExit(f"engine missing: {binary}")

    candidates, book_sha256 = load_book()
    selected = select_openings(binary, candidates)
    selection_payload = "\n".join(selected).encode("ascii")
    selection_sha256 = hashlib.sha256(selection_payload).hexdigest()

    start = args.shard * PAIRS_PER_SHARD
    stop = start + PAIRS_PER_SHARD
    shard_openings = selected[start:stop]
    games: list[dict] = []
    pair_scores: list[float] = []
    penta = {0.0: 0, 0.5: 0, 1.0: 0, 1.5: 0, 2.0: 0}

    for local_index, fen in enumerate(shard_openings):
        opening_index = start + local_index
        g1 = play(binary, args.shard, opening_index, fen, True)
        g2 = play(binary, args.shard, opening_index, fen, False)
        games.extend([g1, g2])
        pair_score = sum(
            1.0 if game["adaptive_result"] == "W" else 0.5 if game["adaptive_result"] == "D" else 0.0
            for game in (g1, g2)
        )
        pair_scores.append(pair_score)
        penta[pair_score] += 1
        print(
            "ADAPTIVE_LMR_R2_STRENGTH_PAIR",
            json.dumps(
                {
                    "shard": args.shard,
                    "opening_index": opening_index,
                    "g1": g1,
                    "g2": g2,
                    "pair_adaptive_score": pair_score,
                },
                sort_keys=True,
            ),
            flush=True,
        )

    wins = sum(game["adaptive_result"] == "W" for game in games)
    draws = sum(game["adaptive_result"] == "D" for game in games)
    losses = sum(game["adaptive_result"] == "L" for game in games)
    summary = {
        "schema": SCHEMA,
        "candidate_identity_commit": CANDIDATE_ID,
        "candidate_source_commit": SOURCE_COMMIT,
        "candidate_search_sha256": SEARCH_SHA256,
        "source_book": BOOK_NAME,
        "source_commit": BOOK_COMMIT,
        "source_blob_sha1": BOOK_BLOB_SHA1,
        "source_sha256": book_sha256,
        "selection_sha256": selection_sha256,
        "pairs_total": PAIRS_TOTAL,
        "shards": SHARDS,
        "shard": args.shard,
        "pair_range": [start, stop],
        "pairs": PAIRS_PER_SHARD,
        "games": len(games),
        "nodes_per_move": NODES,
        "opening_depth_plies": OPENING_DEPTH_PLIES,
        "total_ply_cap": TOTAL_PLY_CAP,
        "use_lmr_both_arms": True,
        "use_nmp_both_arms": True,
        "classical_evaluator_both_arms": True,
        "adaptive_wdl": {"win": wins, "draw": draws, "loss": losses},
        "adaptive_score": wins + 0.5 * draws,
        "adaptive_score_pct": round((wins + 0.5 * draws) * 100.0 / len(games), 4),
        "penta": {str(key): value for key, value in penta.items()},
        "pair_scores": pair_scores,
        "max_ply_draws": sum(game["termination"] == "max-ply" for game in games),
        "strength_claim_authorized": False,
    }
    if len(games) != 2 * PAIRS_PER_SHARD or len(pair_scores) != PAIRS_PER_SHARD:
        raise RuntimeError(f"incomplete shard: {summary}")
    if sum(penta.values()) != PAIRS_PER_SHARD:
        raise RuntimeError(f"invalid pentanomial accounting: {summary}")
    if wins + draws + losses != len(games):
        raise RuntimeError(f"invalid WDL accounting: {summary}")

    Path(args.output).write_text(
        json.dumps({"summary": summary, "games": games}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("ADAPTIVE_LMR_R2_STRENGTH_SHARD", json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
