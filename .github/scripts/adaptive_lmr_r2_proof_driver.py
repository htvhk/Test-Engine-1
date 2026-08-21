from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ENTRY_PATH = HERE / "adaptive_lmr_r2_proof_2048_entry.py"
EVIDENCE_PATH = HERE / "adaptive_lmr_r2_proof_evidence.py"
CONTRACT_BLOB = "8ec9d6260c0b54df1c6bb0d7c4d4fda59451e50a"
REPLAY_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-REPLAY-v1"
FINAL_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-PROOF-FINAL-v1"
EXPECTED_SHARDS = 64
EXPECTED_GAMES = 2048


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load proof module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = _load("te1_adaptive_lmr_r2_proof_entry", ENTRY_PATH)
evidence = _load("te1_adaptive_lmr_r2_proof_evidence", EVIDENCE_PATH)
entry.CONTRACT_GIT_BLOB_SHA1 = CONTRACT_BLOB
proof = entry.proof


def _uci_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def new_engine_strict(binary: Path, enabled: bool, contract: dict[str, Any]) -> Any:
    engine = proof.r3.UciEngine(binary, "CLASSICAL")
    arm = "treatment" if enabled else "control"
    options = contract["option_fingerprints"][arm]["options"]
    try:
        for name in (
            "Hash",
            "Threads",
            "Deterministic",
            "MoveOverhead",
            "UseLMR",
            "UseSEEPruning",
            "UseNullMovePruning",
            "UseNNUE",
            "UseHybridEval",
            "UseAdaptiveLMR",
        ):
            engine.setoption(name, _uci_value(options[name]))
        observed = engine.evaluator_identity()
        if observed != options["Evaluator"] or observed != "classical":
            raise proof.ProofError(
                f"Adaptive LMR proof evaluator drift: {observed!r} != 'classical'"
            )
        return engine
    except BaseException:
        engine.close()
        raise


def commit_pending_linear(
    proof_module: Any,
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
    path = evidence.result_path(evidence_dir, pending)
    if not path.exists():
        return False
    record = evidence.load_result(path, state["identity"])
    evidence.reconcile_record(proof_module, validator, contract, schedule[index], record)
    pgn_path = evidence_dir / "games.pgn"
    pgn = evidence.parse_pgn(pgn_path)
    if pending not in pgn:
        evidence.append_pgn(pgn_path, record)
        pgn = evidence.parse_pgn(pgn_path)
    evidence.verify_pgn_record(pgn[pending], record)
    state["completed_games"].append(pending)
    state["pending_game"] = None
    state["wdl"] = evidence.recompute_wdl(
        [
            evidence.load_result(
                evidence.result_path(evidence_dir, game_id), state["identity"]
            )
            for game_id in state["completed_games"]
        ]
    )
    evidence.write_state(evidence_dir / "state.json", state)
    # The just-committed game has already been independently reconciled above.
    # Revalidate the whole transaction structurally here; a full semantic replay
    # is performed once at shard closure and again in the post-match replay job.
    evidence.validate_transaction(
        proof_module, None, contract, evidence_dir, schedule, state
    )
    return True


entry.new_engine = new_engine_strict
proof.new_engine = new_engine_strict
evidence.commit_pending = commit_pending_linear


def contract_check() -> dict[str, Any]:
    contract, digest = entry.load_contract()
    control_sha, treatment_sha = evidence.validate_option_fingerprints(contract)
    identity = proof.source_identity()
    replay = contract.get("independent_replay", {})
    if replay != {
        "required": True,
        "scope": "all 64 shards and all 2048 games",
        "execution": "post-match read-only replay jobs using the exact frozen binary, preflight, schedule, JSON evidence, and PGN evidence",
        "receipt": "one authenticated replay receipt per shard bound to the shard summary and evidence hashes",
        "aggregate_requires_all_receipts": True,
        "failure": "BLOCKED_CORRECTNESS",
    }:
        raise proof.ProofError("independent replay contract drift")
    return {
        "campaign_id": contract["campaign_id"],
        "contract_sha256": digest,
        "contract_blob": CONTRACT_BLOB,
        "control_options_sha256": control_sha,
        "treatment_options_sha256": treatment_sha,
        **identity,
    }


def _selected_schedule(
    contract: dict[str, Any], openings: dict[str, Any], mode: str, shard: int
) -> list[dict[str, Any]]:
    pps = int(contract["sharding"]["pairs_per_shard"])
    selected = openings["arms"][mode]["openings"][shard * pps : (shard + 1) * pps]
    if len(selected) != pps:
        raise RuntimeError(f"incomplete opening slice: {mode}/{shard}")
    return evidence.schedule_for(selected, mode, shard)


def _summary_expected(
    schedule: list[dict[str, Any]], records: list[dict[str, Any]]
) -> dict[str, Any]:
    pairs = evidence.pairs_from_records(schedule, records)
    games = [game for pair in pairs for game in pair["games"]]
    wins = sum(game["on_result"] == "W" for game in games)
    draws = sum(game["on_result"] == "D" for game in games)
    losses = sum(game["on_result"] == "L" for game in games)
    penta = {str(units / 2.0): 0 for units in range(5)}
    for pair in pairs:
        penta[str(pair["pair_on_points"])] += 1
    return {
        "pairs": pairs,
        "games": len(games),
        "on_wdl": {"win": wins, "draw": draws, "loss": losses},
        "on_score": wins + 0.5 * draws,
        "penta": penta,
    }


def strict_verify_summary(
    preflight_dir: Path,
    binary: Path,
    summary_path: Path,
    *,
    replay: bool,
) -> dict[str, Any]:
    contract, manifest, openings, contract_sha = proof.load_preflight(
        preflight_dir, binary
    )
    summary = evidence.verify_shard_closure(
        proof, contract, preflight_dir, binary, summary_path, replay=replay
    )
    mode = str(summary["mode"])
    shard = int(summary["shard"])
    if mode not in {"TIME", "NODES"} or not 0 <= shard < 32:
        raise RuntimeError(f"invalid shard identity: {mode}/{shard}")
    schedule = _selected_schedule(contract, openings, mode, shard)
    identity = evidence.identity_for(contract, manifest, mode, shard)
    evidence_dir = summary_path.parent / "evidence"
    state = evidence.load_state(
        evidence_dir / "state.json",
        identity,
        [game["id"] for game in schedule],
    )
    records = evidence.validate_transaction(
        proof, None, contract, evidence_dir, schedule, state
    )
    expected = _summary_expected(schedule, records)
    for key, value in expected.items():
        if summary.get(key) != value:
            raise RuntimeError(f"shard summary/evidence drift for {mode}/{shard}: {key}")
    required = {
        "status": "PASS",
        "campaign_id": contract["campaign_id"],
        "contract_sha256": contract_sha,
        "run_id": manifest["run_id"],
        "run_attempt": manifest["run_attempt"],
        "source_head": manifest["source_head"],
        "source_tree": manifest["source_tree"],
        "binary_sha256": manifest["binary_sha256"],
        "openings_file_sha256": manifest["openings_file_sha256"],
        "operational_failures": 0,
        "control_options_sha256": identity["control_options_sha256"],
        "treatment_options_sha256": identity["treatment_options_sha256"],
        "raw_move_evidence_complete": True,
        "transaction_closure": "PASS",
    }
    for key, value in required.items():
        if summary.get(key) != value:
            raise RuntimeError(f"shard summary identity drift for {mode}/{shard}: {key}")
    if len(records) != 32 or len(expected["pairs"]) != 16:
        raise RuntimeError(f"shard cardinality drift: {mode}/{shard}")
    return summary


def write_replay_receipt(
    preflight_dir: Path, binary: Path, summary_path: Path, output: Path
) -> dict[str, Any]:
    summary = strict_verify_summary(
        preflight_dir, binary, summary_path, replay=True
    )
    receipt = {
        "schema": REPLAY_SCHEMA,
        "status": "PASS",
        "mode": summary["mode"],
        "shard": summary["shard"],
        "campaign_id": summary["campaign_id"],
        "contract_sha256": summary["contract_sha256"],
        "run_id": summary["run_id"],
        "run_attempt": summary["run_attempt"],
        "source_head": summary["source_head"],
        "source_tree": summary["source_tree"],
        "binary_sha256": summary["binary_sha256"],
        "openings_file_sha256": summary["openings_file_sha256"],
        "control_options_sha256": summary["control_options_sha256"],
        "treatment_options_sha256": summary["treatment_options_sha256"],
        "summary_sha256": evidence.sha256_file(summary_path),
        "evidence_manifest_sha256": summary["evidence_manifest_sha256"],
        "state_sha256": summary["state_sha256"],
        "pgn_sha256": summary["pgn_sha256"],
        "replayed_games": 32,
        "terminal_and_legality_replay": "PASS",
    }
    evidence.atomic_json(output, receipt)
    return receipt


def _summary_paths(root: Path) -> dict[tuple[str, int], Path]:
    found: dict[tuple[str, int], Path] = {}
    for path in sorted(root.rglob("summary.json")):
        try:
            obj = json.loads(path.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"malformed shard summary: {path}") from error
        key = (str(obj.get("mode")), int(obj.get("shard", -1)))
        if key in found:
            raise RuntimeError(f"duplicate shard summary: {key}")
        found[key] = path
    expected = {
        (mode, shard) for mode in ("TIME", "NODES") for shard in range(32)
    }
    if set(found) != expected:
        missing = sorted(expected - set(found))
        foreign = sorted(set(found) - expected)
        raise RuntimeError(f"shard summary set drift; missing={missing}, foreign={foreign}")
    return found


def _load_replays(
    root: Path,
    summaries: dict[tuple[str, int], Path],
) -> dict[tuple[str, int], dict[str, Any]]:
    receipts: dict[tuple[str, int], dict[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            obj = json.loads(path.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"malformed replay receipt: {path}") from error
        if obj.get("schema") != REPLAY_SCHEMA:
            raise RuntimeError(f"foreign replay evidence: {path}")
        key = (str(obj.get("mode")), int(obj.get("shard", -1)))
        if key in receipts:
            raise RuntimeError(f"duplicate replay receipt: {key}")
        if key not in summaries:
            raise RuntimeError(f"foreign replay shard: {key}")
        summary = json.loads(summaries[key].read_bytes())
        required = {
            "status": "PASS",
            "campaign_id": summary["campaign_id"],
            "contract_sha256": summary["contract_sha256"],
            "run_id": summary["run_id"],
            "run_attempt": summary["run_attempt"],
            "source_head": summary["source_head"],
            "source_tree": summary["source_tree"],
            "binary_sha256": summary["binary_sha256"],
            "openings_file_sha256": summary["openings_file_sha256"],
            "control_options_sha256": summary["control_options_sha256"],
            "treatment_options_sha256": summary["treatment_options_sha256"],
            "summary_sha256": evidence.sha256_file(summaries[key]),
            "evidence_manifest_sha256": summary["evidence_manifest_sha256"],
            "state_sha256": summary["state_sha256"],
            "pgn_sha256": summary["pgn_sha256"],
            "replayed_games": 32,
            "terminal_and_legality_replay": "PASS",
        }
        for field, value in required.items():
            if obj.get(field) != value:
                raise RuntimeError(f"replay receipt drift for {key}: {field}")
        receipts[key] = obj
    if set(receipts) != set(summaries):
        missing = sorted(set(summaries) - set(receipts))
        raise RuntimeError(f"missing replay receipts: {missing}")
    return receipts


def _blocked_final(output: Path, error: BaseException) -> int:
    final = {
        "schema": FINAL_SCHEMA,
        "decision": "BLOCKED_CORRECTNESS",
        "error_type": type(error).__name__,
        "error": str(error),
        "prior_256_games_pooled": False,
        "promotion_authorized": False,
        "production_default_changed": False,
        "singular_extensions_started": False,
        "evidence_audit": "FAIL",
        "independent_replay_audit": "FAIL",
    }
    evidence.atomic_json(output, final)
    return 2


def aggregate(
    binary: Path,
    preflight: Path,
    shards: Path,
    replays: Path,
    output: Path,
) -> int:
    try:
        summaries = _summary_paths(shards)
        for path in summaries.values():
            strict_verify_summary(preflight, binary, path, replay=False)
        receipts = _load_replays(replays, summaries)
        args = argparse.Namespace(
            binary=str(binary),
            preflight=str(preflight),
            shards=str(shards),
            output=str(output),
        )
        result = int(entry.command_aggregate(args))
        final = json.loads(output.read_bytes())
        if final.get("schema") != FINAL_SCHEMA:
            raise RuntimeError("aggregate final schema drift")
        if final.get("decision") == "BLOCKED_CORRECTNESS":
            final["evidence_audit"] = "FAIL"
            final["independent_replay_audit"] = "FAIL"
        else:
            final["evidence_audit"] = "PASS"
            final["independent_replay_audit"] = "PASS"
        final["replayed_shards"] = len(receipts)
        final["replayed_games"] = sum(
            int(receipt["replayed_games"]) for receipt in receipts.values()
        )
        final["replay_receipts_sha256"] = hashlib.sha256(
            b"".join(
                evidence.canonical_bytes(receipts[key])
                for key in sorted(receipts)
            )
        ).hexdigest()
        final["promotion_authorized"] = False
        final["production_default_changed"] = False
        final["singular_extensions_started"] = False
        evidence.atomic_json(output, final)
        if final["replayed_shards"] != EXPECTED_SHARDS:
            raise RuntimeError("final replay shard count drift")
        if final["replayed_games"] != EXPECTED_GAMES:
            raise RuntimeError("final replay game count drift")
        return result
    except BaseException as error:
        return _blocked_final(output, error)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="TE1 Adaptive LMR R2 hardened 2048-game proof driver"
    )
    sub = root.add_subparsers(dest="command", required=True)

    check = sub.add_parser("contract-check")
    check.set_defaults(command_func=lambda _: _print_contract_check())

    preflight = sub.add_parser("preflight")
    preflight.add_argument("--binary", required=True)
    preflight.add_argument("--out", required=True)
    preflight.set_defaults(
        command_func=lambda args: proof.command_preflight(
            argparse.Namespace(binary=args.binary, out=args.out)
        )
    )

    shard = sub.add_parser("shard")
    shard.add_argument("--binary", required=True)
    shard.add_argument("--preflight", required=True)
    shard.add_argument("--mode", choices=["TIME", "NODES"], required=True)
    shard.add_argument("--shard", required=True, type=int)
    shard.add_argument("--output", required=True)
    shard.set_defaults(command_func=lambda args: evidence.run_hardened_shard(proof, args))

    replay = sub.add_parser("verify-shard")
    replay.add_argument("--binary", required=True)
    replay.add_argument("--preflight", required=True)
    replay.add_argument("--summary", required=True)
    replay.add_argument("--output", required=True)
    replay.set_defaults(command_func=lambda args: _run_replay(args))

    final = sub.add_parser("aggregate")
    final.add_argument("--binary", required=True)
    final.add_argument("--preflight", required=True)
    final.add_argument("--shards", required=True)
    final.add_argument("--replays", required=True)
    final.add_argument("--output", required=True)
    final.set_defaults(command_func=lambda args: _run_aggregate(args))
    return root


def _print_contract_check() -> int:
    print("ADAPTIVE_LMR_R2_PROOF_CONTRACT_OK", json.dumps(contract_check(), sort_keys=True))
    return 0


def _run_replay(args: argparse.Namespace) -> int:
    try:
        receipt = write_replay_receipt(
            Path(args.preflight), Path(args.binary), Path(args.summary), Path(args.output)
        )
        print("ADAPTIVE_LMR_R2_REPLAY_OK", json.dumps(receipt, sort_keys=True))
        return 0
    except BaseException as error:
        blocked = {
            "schema": REPLAY_SCHEMA,
            "status": "BLOCKED_CORRECTNESS",
            "error_type": type(error).__name__,
            "error": str(error),
        }
        evidence.atomic_json(Path(args.output), blocked)
        print("ADAPTIVE_LMR_R2_REPLAY_BLOCKED", json.dumps(blocked, sort_keys=True))
        return 2


def _run_aggregate(args: argparse.Namespace) -> int:
    return aggregate(
        Path(args.binary),
        Path(args.preflight),
        Path(args.shards),
        Path(args.replays),
        Path(args.output),
    )


def main() -> int:
    args = parser().parse_args()
    return int(args.command_func(args))


if __name__ == "__main__":
    raise SystemExit(main())
