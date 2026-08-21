from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

CORE_PATH = Path(__file__).with_name("adaptive_lmr_r2_proof_2048_core.py")
SPEC = importlib.util.spec_from_file_location("te1_adaptive_lmr_r2_proof_core", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load frozen Adaptive LMR R2 proof core")
proof = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proof)

CONTRACT_PATH = Path(
    "diagnostics/adaptive_lmr_r2_proof_2048/ADAPTIVE_LMR_R2_PROOF_CONTRACT.json"
)
CONTRACT_SHA256 = "6a09d53e4a7cd5e35b2df48a1700f5bddfae315b952d421e9177267ca05a3e0a"
CANDIDATE_ID = "8f38a15919bb65c60c774ea96fd4e7e68d80d36b"
CANDIDATE_TREE = "0e8fc08be1642e89b8cdb791e54ea7c7d11a5de9"
SOURCE_COMMIT = "320bb584a4b9a0643aece496f5df4f4b779798cb"
SOURCE_TREE = "23125e2a1ed3b9fd0fe48572665f252790c85ce1"
PRIOR_SELECTION_SHA256 = "638f4fbb7d17d501241fc5443a5a81be58f118560094cef8a0acd8f7ceb5f8c0"
BOOK_SHA256 = "c20483ecca07676c10ad3fb5acad6370fc75a5e6bf3935a7255bb2a73fe8deac"

EXPECTED_BLOBS = {
    "Cargo.lock": "aa658abb8878317a99308209955c3406599aa8b2",
    "EXPERIMENTAL_SOURCE_AUTHORIZATION.json": "41e38146e6bdcfb8d8e1994bf6912ceaa9964580",
    "scripts/r3_attribution_campaign.py": "b0827a49ac6cd718353cc9536db1d0d7b7a1e59b",
    "crates/te1-chess/src/lib.rs": "aaca8a00442fa4de91e9368feb3d96718030ccd0",
    "crates/te1-engine/src/main.rs": "a7b6e91ae2404917e0874e2a4356e078a9ed3f24",
    "crates/te1-eval/src/lib.rs": "5626d3e620285cb4e966f41f50ed4886f2735274",
    "crates/te1-search/src/lib.rs": "e3eb612a68f9d0abfb78e6b6e5f0df220526fb27",
}

SCHEMA_OPENINGS = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-OPENINGS-v1"
SCHEMA_PREFLIGHT = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-PREFLIGHT-v1"
SCHEMA_SHARD = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-SHARD-v1"
SCHEMA_FINAL = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-FINAL-v1"


def _install_frozen_identity() -> None:
    proof.CONTRACT_PATH = CONTRACT_PATH
    proof.BASELINE_COMMIT = CANDIDATE_ID
    proof.BASELINE_TREE = CANDIDATE_TREE
    proof.EXPECTED_BLOBS = dict(EXPECTED_BLOBS)
    proof.SCHEMA_OPENINGS = SCHEMA_OPENINGS
    proof.SCHEMA_PREFLIGHT = SCHEMA_PREFLIGHT
    proof.SCHEMA_SHARD = SCHEMA_SHARD
    proof.SCHEMA_FINAL = SCHEMA_FINAL


def load_contract() -> tuple[dict[str, Any], str]:
    raw = CONTRACT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CONTRACT_SHA256:
        raise proof.ProofError(f"Adaptive LMR proof contract byte drift: {digest}")
    contract = json.loads(raw)
    if contract.get("schema") != "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-v1":
        raise proof.ProofError("wrong Adaptive LMR proof contract schema")
    if contract.get("campaign_id") != "alpha26-adaptive-lmr-r2-proof-2048g-v1":
        raise proof.ProofError("Adaptive LMR proof campaign identity drift")
    expected_candidate = {
        "identity_commit": CANDIDATE_ID,
        "identity_tree": CANDIDATE_TREE,
        "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE,
        "engine_blob": EXPECTED_BLOBS["crates/te1-engine/src/main.rs"],
        "engine_sha256": "15d1c59fac7e3dc9c5b94e61a56f7a75c9da373ec00b57b5157be922fdbdd6c2",
        "search_blob": EXPECTED_BLOBS["crates/te1-search/src/lib.rs"],
        "search_sha256": "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e",
    }
    if contract.get("candidate") != expected_candidate:
        raise proof.ProofError("Adaptive LMR candidate identity drift")
    if contract.get("feature") != {
        "uci_option": "UseAdaptiveLMR",
        "control_value": False,
        "treatment_value": True,
        "default_remains_off_during_campaign": True,
        "requires_use_lmr": True,
    }:
        raise proof.ProofError("Adaptive LMR feature contract drift")
    if contract.get("arms", {}).get("TIME") != {
        "pairs": 512,
        "games": 1024,
        "go": "movetime",
        "movetime_ms": 200,
        "move_overhead_ms": 0,
    }:
        raise proof.ProofError("Adaptive LMR TIME arm drift")
    if contract.get("arms", {}).get("NODES") != {
        "pairs": 512,
        "games": 1024,
        "go": "nodes",
        "nodes_per_move": 100000,
    }:
        raise proof.ProofError("Adaptive LMR NODES arm drift")
    if contract.get("confounders") != {
        "same_executable_for_both_sides": True,
        "classical_evaluator": True,
        "use_lmr_both_arms": True,
        "use_see_pruning_both_arms": True,
        "use_null_move_pruning_both_arms": True,
        "hash_mb": 16,
        "threads": 1,
        "deterministic": True,
        "clear_hash_each_game": True,
        "opening_depth_plies": 28,
        "total_ply_cap": 200,
        "resign_adjudication": False,
        "score_adjudication": False,
    }:
        raise proof.ProofError("Adaptive LMR confounder contract drift")
    source = contract.get("opening_source", {})
    if source != {
        "repository": "official-stockfish/books",
        "commit": "65815ccdbc7727cd4f6aee252ba8f67fb740e92f",
        "file": "Drawkiller_balanced_big.epd.zip",
        "git_blob_sha1": "b851fc8c484b9e36b178131a7f47269bfdfacd39",
        "sha256": BOOK_SHA256,
        "sort_namespace": "TE1-ALPHA26-ADAPTIVE-LMR-R2-STRENGTH-256-v1\0",
    }:
        raise proof.ProofError("Adaptive LMR opening source drift")
    selection = contract.get("opening_selection", {})
    if selection.get("prior_256") != {
        "pairs": 128,
        "valid_rank_start": 0,
        "valid_rank_stop_exclusive": 128,
        "sha256": PRIOR_SELECTION_SHA256,
    }:
        raise proof.ProofError("Adaptive LMR prior opening identity drift")
    if selection.get("time_arm") != {
        "pairs": 512,
        "valid_rank_start": 128,
        "valid_rank_stop_exclusive": 640,
    }:
        raise proof.ProofError("Adaptive LMR TIME opening range drift")
    if selection.get("nodes_arm") != {
        "pairs": 512,
        "valid_rank_start": 640,
        "valid_rank_stop_exclusive": 1152,
    }:
        raise proof.ProofError("Adaptive LMR NODES opening range drift")
    if selection.get("prior_overlap_allowed") is not False:
        raise proof.ProofError("Adaptive LMR prior-overlap policy drift")
    if selection.get("cross_arm_overlap_allowed") is not False:
        raise proof.ProofError("Adaptive LMR cross-arm overlap policy drift")
    if contract.get("sharding") != {
        "pairs_per_shard": 16,
        "shards_per_arm": 32,
        "total_shards": 64,
    }:
        raise proof.ProofError("Adaptive LMR sharding contract drift")
    if contract.get("statistics", {}).get("z_95_two_sided") != proof.Z95:
        raise proof.ProofError("Adaptive LMR statistical constant drift")
    if contract.get("rerun_policy", {}).get("admissible_run_attempt") != 1:
        raise proof.ProofError("Adaptive LMR rerun policy drift")
    promotion = contract.get("promotion", {})
    if promotion != {
        "proof_result_does_not_merge_or_flip_production_default": True,
        "explicit_promotion_authorization_required": True,
        "singular_extensions_remain_frozen_until_closure": True,
    }:
        raise proof.ProofError("Adaptive LMR promotion policy drift")
    return contract, digest


def new_engine(binary: Path, enabled: bool, contract: dict[str, Any]) -> Any:
    engine = proof.r3.UciEngine(binary, "CLASSICAL")
    try:
        conf = contract["confounders"]
        engine.setoption("Hash", str(conf["hash_mb"]))
        engine.setoption("Threads", "1")
        engine.setoption("Deterministic", "true")
        engine.setoption("MoveOverhead", "0")
        engine.setoption("UseLMR", "true")
        engine.setoption("UseSEEPruning", "true")
        engine.setoption("UseNullMovePruning", "true")
        engine.setoption("UseAdaptiveLMR", "true" if enabled else "false")
        if engine.evaluator_identity() != "classical":
            raise proof.ProofError("Adaptive LMR proof evaluator drifted from classical")
        return engine
    except BaseException:
        engine.close()
        raise


def validate_openings(
    binary: Path, contract: dict[str, Any], candidates: list[tuple[str, str]]
) -> list[str]:
    stop = int(contract["opening_selection"]["nodes_arm"]["valid_rank_stop_exclusive"])
    validator = new_engine(binary, False, contract)
    valid: list[str] = []
    try:
        for _, fen in candidates:
            try:
                normalized = proof.set_fen_position(validator, fen, [])
            except proof.r3.IllegalMoveError:
                continue
            if normalized.split()[:4] != fen.split()[:4]:
                continue
            valid.append(fen)
            if len(valid) == stop:
                break
    finally:
        validator.close()
    if len(valid) != stop:
        raise proof.ProofError(f"only {len(valid)} valid openings available; need {stop}")
    prior = contract["opening_selection"]["prior_256"]
    start = int(prior["valid_rank_start"])
    end = int(prior["valid_rank_stop_exclusive"])
    observed = proof.opening_hash(valid[start:end])
    if observed != prior["sha256"]:
        raise proof.ProofError(
            f"prior 256-game opening selection drift: {observed} != {prior['sha256']}"
        )
    return valid


def make_opening_freeze(
    valid: list[str], contract: dict[str, Any], contract_sha: str
) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for mode, key in (("TIME", "time_arm"), ("NODES", "nodes_arm")):
        spec = contract["opening_selection"][key]
        start = int(spec["valid_rank_start"])
        stop = int(spec["valid_rank_stop_exclusive"])
        fens = valid[start:stop]
        if len(fens) != int(spec["pairs"]):
            raise proof.ProofError(f"{mode} opening count mismatch")
        records = [
            {
                "valid_rank": rank,
                "fen": fen,
                "fen_sha256": proof.sha256_bytes(fen.encode("ascii")),
            }
            for rank, fen in zip(range(start, stop), fens, strict=True)
        ]
        arms[mode] = {
            "valid_rank_range": [start, stop],
            "pairs": len(records),
            "selection_sha256": proof.opening_hash(fens),
            "openings": records,
        }
    time_fens = {item["fen"] for item in arms["TIME"]["openings"]}
    node_fens = {item["fen"] for item in arms["NODES"]["openings"]}
    if time_fens & node_fens:
        raise proof.ProofError("TIME and NODES Adaptive LMR proof opening sets overlap")
    prior = contract["opening_selection"]["prior_256"]
    prior_start = int(prior["valid_rank_start"])
    prior_stop = int(prior["valid_rank_stop_exclusive"])
    prior_fens = set(valid[prior_start:prior_stop])
    if prior_fens & (time_fens | node_fens):
        raise proof.ProofError("Adaptive LMR proof openings overlap the prior 256-game gate")
    prior_hash = proof.opening_hash(valid[prior_start:prior_stop])
    if prior_hash != prior["sha256"]:
        raise proof.ProofError("Adaptive LMR prior selection digest drift during freeze")
    return {
        "schema": SCHEMA_OPENINGS,
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "prior_256": {
            "valid_rank_range": [prior_start, prior_stop],
            "pairs": prior_stop - prior_start,
            "selection_sha256": prior_hash,
        },
        "arms": arms,
    }


def reconcile_game_terminal_first(
    validator: Any,
    contract: dict[str, Any],
    opening: dict[str, Any],
    game: dict[str, Any],
) -> None:
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
            new_fen = proof.set_fen_position(validator, start_fen, moves + [move])
        except proof.r3.IllegalMoveError as error:
            raise proof.ProofError(
                f"illegal recorded move at ply {index}: {move}"
            ) from error
        moves.append(move)
        history.append(new_fen)

    termination = game["termination"]
    if termination == "max-ply":
        if len(moves) != additional_plies or game["result"] != "1/2-1/2":
            raise proof.ProofError("invalid max-ply adjudication evidence")
        return

    current = history[-1]
    legal_move = proof.has_legal_move(validator, start_fen, moves)
    reason = proof.r3.draw_reason(history)

    if not legal_move:
        if termination not in ("checkmate", "stalemate"):
            raise proof.ProofError(
                f"terminal position has non-terminal label: {termination}"
            )
        white_to_move = current.split()[1] == "w"
        in_check = proof.r3.terminal_side_is_in_check(current, white_to_move)
        expected_termination = "checkmate" if in_check else "stalemate"
        expected_result = (
            ("0-1" if white_to_move else "1-0") if in_check else "1/2-1/2"
        )
        if termination != expected_termination or game["result"] != expected_result:
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


def command_contract_check(_: argparse.Namespace) -> int:
    contract, digest = load_contract()
    identity = proof.source_identity()
    print(
        "ADAPTIVE_LMR_R2_PROOF_CONTRACT_OK",
        json.dumps(
            {
                "campaign_id": contract["campaign_id"],
                "contract_sha256": digest,
                **identity,
            },
            sort_keys=True,
        ),
    )
    return 0


def _rewrite_final_result(path: Path) -> dict[str, Any]:
    final = proof.load_json(path)
    decision_map = {
        "PASS_DEFAULT_ON": "PASS_ADAPTIVE_LMR_R2_PROOF",
        "FAIL_NMP": "FAIL_ADAPTIVE_LMR_R2",
        "INCONCLUSIVE": "INCONCLUSIVE",
        "BLOCKED_CORRECTNESS": "BLOCKED_CORRECTNESS",
    }
    original = final.get("decision")
    if original not in decision_map:
        raise proof.ProofError(f"unexpected core proof decision: {original!r}")
    final["decision"] = decision_map[original]
    final.pop("prior_512_games_pooled", None)
    final.pop("default_on_authorized", None)
    final["prior_256_games_pooled"] = False
    final["feature_option"] = "UseAdaptiveLMR"
    final["control_value"] = False
    final["treatment_value"] = True
    final["promotion_authorized"] = False
    final["production_default_changed"] = False
    final["singular_extensions_started"] = False
    proof.write_json(path, final)
    return final


_original_aggregate = proof.command_aggregate


def command_aggregate(args: argparse.Namespace) -> int:
    return_code = int(_original_aggregate(args))
    path = Path(args.output)
    if not path.is_file():
        raise proof.ProofError("core aggregate did not produce final evidence")
    final = _rewrite_final_result(path)
    print(
        "ADAPTIVE_LMR_R2_PROOF_FINAL",
        json.dumps(
            {
                "decision": final["decision"],
                "total_pairs": final.get("total_pairs"),
                "total_games": final.get("total_games"),
                "promotion_authorized": final["promotion_authorized"],
            },
            sort_keys=True,
        ),
    )
    return return_code


_install_frozen_identity()
proof.load_contract = load_contract
proof.new_engine = new_engine
proof.validate_openings = validate_openings
proof.make_opening_freeze = make_opening_freeze
proof.reconcile_game = reconcile_game_terminal_first
proof.command_contract_check = command_contract_check
proof.command_aggregate = command_aggregate


def main() -> int:
    return int(proof.main())


if __name__ == "__main__":
    raise SystemExit(main())
