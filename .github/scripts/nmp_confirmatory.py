import hashlib
import io
import json
import os
import urllib.request
import zipfile
from pathlib import Path

from scripts import r3_attribution_campaign as r3

BIN = Path("./target/release/te1")
SHARD = int(os.environ["SHARD"])
SHARDS = 8
PAIRS_TOTAL = 128
PAIRS_PER_SHARD = PAIRS_TOTAL // SHARDS
BATCH_OFFSET = 128
NODES = 100_000
OPENING_DEPTH_PLIES = 28
TOTAL_PLY_CAP = 200
ADDITIONAL_PLIES = TOTAL_PLY_CAP - OPENING_DEPTH_PLIES
HASH_MB = 16

BOOK_COMMIT = "65815ccdbc7727cd4f6aee252ba8f67fb740e92f"
BOOK_BLOB_SHA1 = "b851fc8c484b9e36b178131a7f47269bfdfacd39"
BOOK_SHA256 = "c20483ecca07676c10ad3fb5acad6370fc75a5e6bf3935a7255bb2a73fe8deac"
BOOK_NAME = "Drawkiller_balanced_big.epd.zip"
BOOK_URL = (
    "https://raw.githubusercontent.com/official-stockfish/books/"
    + BOOK_COMMIT
    + "/"
    + BOOK_NAME
)
PRIOR_SELECTION_SHA256 = "832435a9505b4b11abc4c1103f111add09348a80e83b63d313d7bb1409f080fe"
SORT_NAMESPACE = "TE1-NMP-R1-STRENGTH-v1\0"


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


with urllib.request.urlopen(BOOK_URL, timeout=30) as response:
    archive = response.read()
if git_blob_sha1(archive) != BOOK_BLOB_SHA1:
    raise RuntimeError("pinned opening-book Git blob mismatch")
book_sha256 = hashlib.sha256(archive).hexdigest()
if book_sha256 != BOOK_SHA256:
    raise RuntimeError("pinned opening-book SHA-256 mismatch")

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
    key = hashlib.sha256((SORT_NAMESPACE + fen).encode("ascii")).hexdigest()
    candidates.append((key, fen))
candidates.sort()


def set_fen_position(engine, start_fen: str, moves: list[str]) -> str:
    suffix = "" if not moves else " moves " + " ".join(moves)
    engine.send("position fen " + start_fen + suffix)
    engine.send("isready")
    engine._position_ready_barrier()
    engine.send("d")
    fen = engine.wait_for(lambda line: line.count("/") == 7)
    if len(fen.split()) != 6:
        raise RuntimeError(f"malformed FEN response: {fen}")
    return fen


def new_engine(enabled: bool):
    engine = r3.UciEngine(BIN, "CLASSICAL")
    engine.setoption("Hash", str(HASH_MB))
    engine.setoption("UseNullMovePruning", "true" if enabled else "false")
    if engine.evaluator_identity() != "classical":
        raise RuntimeError("confirmatory evaluator drifted from classical")
    return engine


validator = new_engine(False)
valid: list[str] = []
try:
    for _, fen in candidates:
        try:
            normalized = set_fen_position(validator, fen, [])
        except Exception:
            continue
        if normalized.split()[:4] != fen.split()[:4]:
            continue
        valid.append(fen)
        if len(valid) == BATCH_OFFSET + PAIRS_TOTAL:
            break
finally:
    validator.close()

if len(valid) != BATCH_OFFSET + PAIRS_TOTAL:
    raise RuntimeError(f"only {len(valid)} valid openings available; need 256")

prior = valid[:BATCH_OFFSET]
prior_hash = hashlib.sha256("\n".join(prior).encode("ascii")).hexdigest()
if prior_hash != PRIOR_SELECTION_SHA256:
    raise RuntimeError(
        f"prior opening selection drift: {prior_hash} != {PRIOR_SELECTION_SHA256}"
    )

selected = valid[BATCH_OFFSET : BATCH_OFFSET + PAIRS_TOTAL]
if set(prior) & set(selected):
    raise RuntimeError("confirmatory opening batch overlaps prior batch")
confirm_selection_sha256 = hashlib.sha256(
    "\n".join(selected).encode("ascii")
).hexdigest()

local_start = SHARD * PAIRS_PER_SHARD
local_stop = local_start + PAIRS_PER_SHARD
global_start = BATCH_OFFSET + local_start
global_stop = BATCH_OFFSET + local_stop
shard_openings = selected[local_start:local_stop]


def has_legal_move_fen(engine, start_fen: str, moves: list[str], fen: str) -> bool:
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
                    sources.append(
                        (f"{chr(ord('a') + file_index)}{rank}", symbol.lower())
                    )
                file_index += 1
    destinations = [
        f"{file_name}{rank}" for file_name in "abcdefgh" for rank in range(1, 9)
    ]
    for source, piece in sources:
        for destination in destinations:
            promotions = "qrbn" if piece == "p" and destination[1] in "18" else ""
            trials = (
                [source + destination + promo for promo in promotions]
                if promotions
                else [source + destination]
            )
            for candidate in trials:
                try:
                    set_fen_position(engine, start_fen, moves + [candidate])
                except r3.IllegalMoveError:
                    continue
                finally:
                    set_fen_position(engine, start_fen, moves)
                return True
    return False


def play(global_index: int, start_fen: str, off_is_white: bool) -> dict:
    white = black = None
    try:
        white = new_engine(not off_is_white)
        black = new_engine(off_is_white)
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

        on_result = "D"
        if result != "1/2-1/2":
            white_won = result == "1-0"
            on_is_white = not off_is_white
            on_result = "W" if white_won == on_is_white else "L"
        return {
            "id": f"C{SHARD:02d}-O{global_index:03d}-" + ("G1" if off_is_white else "G2"),
            "opening_index": global_index,
            "off_color": "white" if off_is_white else "black",
            "on_result": on_result,
            "result": result,
            "moves_after_epd": len(moves),
            "termination": termination,
        }
    finally:
        if white is not None:
            white.close()
        if black is not None:
            black.close()


games: list[dict] = []
penta = {0.0: 0, 0.5: 0, 1.0: 0, 1.5: 0, 2.0: 0}
for local_index, fen in enumerate(shard_openings):
    global_index = global_start + local_index
    g1 = play(global_index, fen, True)
    g2 = play(global_index, fen, False)
    games.extend([g1, g2])
    score = sum(
        1.0 if g["on_result"] == "W" else 0.5 if g["on_result"] == "D" else 0.0
        for g in (g1, g2)
    )
    penta[score] += 1
    print(
        "NMP_CONFIRM_PAIR",
        json.dumps(
            {
                "shard": SHARD,
                "opening_index": global_index,
                "g1": g1,
                "g2": g2,
                "pair_on_score": score,
            },
            sort_keys=True,
        ),
    )

w = sum(g["on_result"] == "W" for g in games)
d = sum(g["on_result"] == "D" for g in games)
l = sum(g["on_result"] == "L" for g in games)
summary = {
    "schema": "TE1-NMP-R1-CONFIRMATORY-SHARD-v1",
    "source_book": BOOK_NAME,
    "source_commit": BOOK_COMMIT,
    "source_blob_sha1": BOOK_BLOB_SHA1,
    "source_sha256": book_sha256,
    "prior_selection_sha256": prior_hash,
    "confirm_selection_sha256": confirm_selection_sha256,
    "batch_offset_pairs": BATCH_OFFSET,
    "pairs_total": PAIRS_TOTAL,
    "shards": SHARDS,
    "shard": SHARD,
    "pair_range": [global_start, global_stop],
    "pairs": PAIRS_PER_SHARD,
    "games": len(games),
    "nodes_per_move": NODES,
    "opening_depth_plies": OPENING_DEPTH_PLIES,
    "total_ply_cap": TOTAL_PLY_CAP,
    "on_wdl": {"win": w, "draw": d, "loss": l},
    "on_score": w + 0.5 * d,
    "on_score_pct": round((w + 0.5 * d) * 100.0 / len(games), 4),
    "penta": {str(k): v for k, v in penta.items()},
    "max_ply_draws": sum(g["termination"] == "max-ply" for g in games),
}
if len(games) != 2 * PAIRS_PER_SHARD or sum(penta.values()) != PAIRS_PER_SHARD:
    raise RuntimeError(f"incomplete confirmatory shard: {summary}")
print("NMP_CONFIRM_SHARD", json.dumps(summary, sort_keys=True))
