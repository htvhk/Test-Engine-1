from __future__ import annotations

from dataclasses import dataclass

NUM_FEATURES = 22_528
PAD_INDEX = NUM_FEATURES
MAX_ACTIVE_FEATURES = 31
RELATIVE_PIECE_CLASSES = 11
BOARD_SQUARES = 64
PIECE_CODE = {"p": 0, "n": 1, "b": 2, "r": 3, "q": 4, "k": 5}


@dataclass(frozen=True)
class PieceOnSquare:
    color: str  # "w" | "b"
    piece: str  # lowercase p/n/b/r/q/k
    square: int  # a1 == 0


def parse_fen_board(fen: str) -> tuple[list[PieceOnSquare], str]:
    fields = fen.split()
    if len(fields) < 2:
        raise ValueError("FEN must contain board and side-to-move fields")
    board_field, stm = fields[0], fields[1]
    if stm not in {"w", "b"}:
        raise ValueError(f"invalid side to move: {stm!r}")
    ranks = board_field.split("/")
    if len(ranks) != 8:
        raise ValueError("FEN board must contain eight ranks")
    pieces: list[PieceOnSquare] = []
    for fen_rank, text in enumerate(ranks):
        rank = 7 - fen_rank
        file = 0
        for char in text:
            if char.isdigit():
                value = int(char)
                if not 1 <= value <= 8:
                    raise ValueError("invalid FEN digit")
                file += value
                continue
            lower = char.lower()
            if lower not in PIECE_CODE or file >= 8:
                raise ValueError(f"invalid FEN piece/rank content: {char!r}")
            pieces.append(PieceOnSquare("w" if char.isupper() else "b", lower, rank * 8 + file))
            file += 1
        if file != 8:
            raise ValueError("FEN rank does not contain eight files")
    for color in ("w", "b"):
        if sum(1 for p in pieces if p.color == color and p.piece == "k") != 1:
            raise ValueError(f"position must contain exactly one {color} king")
    return pieces, stm


def _king_frame(pieces: list[PieceOnSquare], perspective: str) -> tuple[int, bool]:
    king = next(p for p in pieces if p.color == perspective and p.piece == "k")
    file = king.square & 7
    rank = king.square >> 3
    relative_rank = rank if perspective == "w" else 7 - rank
    mirror = file >= 4
    canonical_file = 7 - file if mirror else file
    return relative_rank * 4 + canonical_file, mirror


def encode_perspective(pieces: list[PieceOnSquare], perspective: str) -> list[int]:
    king_bucket, mirror = _king_frame(pieces, perspective)
    active: list[int] = []
    for p in pieces:
        own = p.color == perspective
        if own and p.piece == "k":
            continue
        piece_kind = PIECE_CODE[p.piece]
        if own:
            relative_piece = piece_kind
        elif p.piece == "k":
            relative_piece = 10
        else:
            relative_piece = 5 + piece_kind
        file = p.square & 7
        rank = p.square >> 3
        transformed_rank = rank if perspective == "w" else 7 - rank
        transformed_file = 7 - file if mirror else file
        transformed_square = transformed_rank * 8 + transformed_file
        feature = (king_bucket * RELATIVE_PIECE_CLASSES + relative_piece) * BOARD_SQUARES + transformed_square
        if not 0 <= feature < NUM_FEATURES:
            raise ValueError(f"feature index out of range: {feature}")
        active.append(feature)
    active.sort()
    if len(active) > MAX_ACTIVE_FEATURES:
        raise ValueError("too many active features")
    if len(active) != len(set(active)):
        raise ValueError("duplicate active feature")
    return active + [PAD_INDEX] * (MAX_ACTIVE_FEATURES - len(active))


def encode_fen(fen: str) -> tuple[list[int], list[int], bool]:
    pieces, stm = parse_fen_board(fen)
    return encode_perspective(pieces, "w"), encode_perspective(pieces, "b"), stm == "w"
