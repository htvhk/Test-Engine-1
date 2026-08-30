from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

CORE_PATH = Path(__file__).with_name("nmp_proof_2048.py")
SPEC = importlib.util.spec_from_file_location("te1_nmp_proof_2048_core", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load frozen NMP proof core")
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)


def reconcile_game_terminal_first(
    validator: Any,
    contract: dict[str, Any],
    opening: dict[str, Any],
    game: dict[str, Any],
) -> None:
    """Reconcile persisted evidence with the same terminal precedence as live play."""
    start_fen = opening["fen"]
    moves: list[str] = []
    first = proof.set_fen_position(validator, start_fen, moves)
    history = [first]
    additional_plies = (
        int(contract["confounders"]["total_ply_cap"])
        - int(contract["confounders"]["opening_depth_plies"])
    )
    if len(game["moves"]) > additional_plies:
        raise proof.ProofError("game evidence exceeds frozen ply cap")
    for index, move in enumerate(game["moves"]):
        reason = proof.r3.draw_reason(history)
        if reason and proof.has_legal_move(validator, start_fen, moves):
            raise proof.ProofError(
                f"game contains move after mandatory draw at ply {index}: {reason}"
            )
        try:
            new_fen = proof.set_fen_position(
                validator, start_fen, moves + [move]
            )
        except proof.r3.IllegalMoveError as error:
            raise proof.ProofError(
                f"illegal recorded move at ply {index}: {move}"
            ) from error
        moves.append(move)
        history.append(new_fen)

    termination = game["termination"]
    if termination == "max-ply":
        if (
            len(moves) != additional_plies
            or game["result"] != "1/2-1/2"
        ):
            raise proof.ProofError("invalid max-ply adjudication evidence")
        return

    current = history[-1]
    legal_move = proof.has_legal_move(validator, start_fen, moves)
    reason = proof.r3.draw_reason(history)

    # Live play gives no-legal-move terminal status precedence over draw rules.
    if not legal_move:
        if termination not in ("checkmate", "stalemate"):
            raise proof.ProofError(
                f"terminal position has non-terminal label: {termination}"
            )
        white_to_move = current.split()[1] == "w"
        in_check = proof.r3.terminal_side_is_in_check(
            current, white_to_move
        )
        expected_termination = "checkmate" if in_check else "stalemate"
        expected_result = (
            ("0-1" if white_to_move else "1-0")
            if in_check
            else "1/2-1/2"
        )
        if (
            termination != expected_termination
            or game["result"] != expected_result
        ):
            raise proof.ProofError(
                "terminal semantics mismatch: "
                f"{termination}/{game['result']} != "
                f"{expected_termination}/{expected_result}"
            )
        return

    if reason is not None:
        if termination != reason:
            raise proof.ProofError("invalid rule-draw adjudication evidence")
        if game["result"] != "1/2-1/2":
            raise proof.ProofError("rule draw recorded as decisive")
        return

    raise proof.ProofError(
        f"game ended before a frozen termination rule applied: {termination}"
    )


# Patch exactly the one confirmed core defect before command dispatch.
proof.reconcile_game = reconcile_game_terminal_first


def main() -> int:
    return int(proof.main())


if __name__ == "__main__":
    raise SystemExit(main())
