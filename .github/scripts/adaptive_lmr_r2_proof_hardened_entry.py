from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

ENTRY_PATH = Path(__file__).with_name("adaptive_lmr_r2_proof_2048_entry.py")
EVIDENCE_PATH = Path(__file__).with_name("adaptive_lmr_r2_proof_evidence.py")


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = _load("te1_adaptive_lmr_r2_proof_entry", ENTRY_PATH)
evidence = _load("te1_adaptive_lmr_r2_proof_evidence", EVIDENCE_PATH)
proof = entry.proof

# The contract was deliberately extended after the initial wrapper was written.
# Keep the original parser/identity checks but bind them to the hardened contract blob.
entry.CONTRACT_GIT_BLOB_SHA1 = "3ce56bcdb284771dcba3a5f5f3ec7a791a026db4"
_base_load_contract = entry.load_contract
_core_aggregate = entry._original_aggregate


def load_contract() -> tuple[dict[str, Any], str]:
    contract, digest = _base_load_contract()
    control_sha, treatment_sha = evidence.validate_option_fingerprints(contract)
    if control_sha != "7103df8d2707dc432fe765d19b89890d76b6010f935af6aa178e284f400ccd9c":
        raise proof.ProofError("control option fingerprint identity drift")
    if treatment_sha != "ff1fd19bfe9e7ccd98bf0beec50d9955adaf25ad6b466f681335209984fb5ce2":
        raise proof.ProofError("treatment option fingerprint identity drift")
    expected_evidence = {
        "raw_game_record": "canonical JSON per game with full UCI move list and frozen identity",
        "canonical_pgn": "ASCII and LF-only append-only PGN evidence containing the same full UCI move list",
        "state": "atomic fsync-backed transaction state with ordered completed-prefix and next-game pending identity",
        "resume": "within the same admissible run, only causally valid pending states may be recovered; a failed GitHub run remains discarded whole",
        "closure": "completed state, JSON result set, PGN set, schedule prefix, option fingerprints, and re-adjudicated terminal semantics must reconcile exactly",
        "manifest": "SHA-256 manifest over state, PGN, and every committed per-game JSON record",
        "foreign_or_future_evidence": "BLOCKED_CORRECTNESS",
        "malformed_duplicate_contradictory_non_ascii_or_partial_pgn": "BLOCKED_CORRECTNESS",
    }
    if contract.get("evidence") != expected_evidence:
        raise proof.ProofError("hardened evidence contract drift")
    if contract["statistics"].get("prior_256_games_use") != (
        "supportive prior evidence only; never pooled into this trial"
    ):
        raise proof.ProofError("prior diagnostic pooling policy drift")
    return contract, digest


def run_shard(args: Any) -> int:
    return int(evidence.run_hardened_shard(proof, args))


def _blocked_final(path: Path, error: BaseException) -> dict[str, Any]:
    final = {
        "schema": entry.SCHEMA_FINAL,
        "decision": "BLOCKED_CORRECTNESS",
        "error_type": type(error).__name__,
        "error": str(error),
        "prior_256_games_pooled": False,
        "feature_option": "UseAdaptiveLMR",
        "control_value": False,
        "treatment_value": True,
        "promotion_authorized": False,
        "production_default_changed": False,
        "singular_extensions_started": False,
        "raw_move_evidence_complete": False,
        "transaction_closure": "FAIL",
        "independent_audit_required": True,
    }
    evidence.atomic_json(path, final)
    return final


def command_aggregate(args: Any) -> int:
    path = Path(args.output)
    try:
        core_code = int(_core_aggregate(args))
        if not path.is_file():
            raise proof.ProofError("core aggregate did not produce final evidence")
        core_final = proof.load_json(path)
        if core_final.get("decision") == "BLOCKED_CORRECTNESS":
            final = entry._rewrite_final_result(path)
            final["raw_move_evidence_complete"] = False
            final["transaction_closure"] = "FAIL"
            final["independent_audit_required"] = True
            evidence.atomic_json(path, final)
            return core_code

        contract, _ = load_contract()
        summaries = []
        for candidate in sorted(Path(args.shards).rglob("summary.json")):
            try:
                obj = json.loads(candidate.read_bytes())
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise proof.ProofError(f"malformed shard summary: {candidate}") from error
            if obj.get("schema") == entry.SCHEMA_SHARD:
                summaries.append(candidate)
        if len(summaries) != 64:
            raise proof.ProofError(
                f"hardened closure requires 64 shard summaries, found {len(summaries)}"
            )
        seen: set[tuple[str, int]] = set()
        for summary_path in summaries:
            summary = evidence.verify_shard_closure(
                proof,
                contract,
                Path(args.preflight),
                Path(args.binary),
                summary_path,
                replay=False,
            )
            key = (str(summary["mode"]), int(summary["shard"]))
            if key in seen:
                raise proof.ProofError(f"duplicate hardened shard identity: {key}")
            seen.add(key)
        expected = {(mode, shard) for mode in ("TIME", "NODES") for shard in range(32)}
        if seen != expected:
            raise proof.ProofError(
                f"hardened shard coverage drift: missing={sorted(expected-seen)} foreign={sorted(seen-expected)}"
            )
        final = entry._rewrite_final_result(path)
        final["raw_move_evidence_complete"] = True
        final["transaction_closure"] = "PASS"
        final["hardened_shards_verified"] = 64
        final["hardened_games_closed"] = 2048
        final["control_options_sha256"] = (
            contract["option_fingerprints"]["control"]["sha256"]
        )
        final["treatment_options_sha256"] = (
            contract["option_fingerprints"]["treatment"]["sha256"]
        )
        final["independent_audit_required"] = True
        final["independent_audit_status"] = "PENDING"
        evidence.atomic_json(path, final)
        return core_code
    except BaseException as error:
        _blocked_final(path, error)
        return 2


# Patch the already-audited core rather than duplicating its search/match logic.
entry.load_contract = load_contract
proof.load_contract = load_contract
proof.run_shard = run_shard
proof.command_aggregate = command_aggregate


def main() -> int:
    return int(proof.main())


if __name__ == "__main__":
    raise SystemExit(main())
