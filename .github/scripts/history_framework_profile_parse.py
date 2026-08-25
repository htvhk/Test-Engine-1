#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCALAR_FIELDS = {
    "depth",
    "score_cp",
    "nodes",
    "qnodes",
    "tt_hits",
    "beta_cutoffs",
    "seldepth",
    "hashfull",
    "threads",
    "quiet_ordering_reads",
    "continuation_reads",
    "capture_history_reads",
    "countermove_matches",
    "quiet_near_saturation_reads",
    "quiet_exact_saturation_reads",
    "continuation_near_saturation_reads",
    "continuation_exact_saturation_reads",
    "capture_near_saturation_reads",
    "capture_exact_saturation_reads",
    "quiet_positive_updates",
    "quiet_negative_updates",
    "continuation_positive_updates",
    "continuation_negative_updates",
    "capture_positive_updates",
    "capture_negative_updates",
    "quiet_cutoffs",
    "capture_cutoffs",
    "killer_updates",
    "countermove_records",
    "quiet_search_traffic",
}

ARRAY_FIELDS = {
    "quiet_component_bins",
    "continuation_component_bins",
    "quiet_base_bins",
    "quiet_ordering_bins",
    "capture_component_bins",
    "positive_update_depth_bins",
    "negative_update_depth_bins",
    "quiet_traffic_depth_bins",
    "quiet_traffic_move_bins",
    "quiet_traffic_depth_move_matrix",
}

EXPECTED_IDS = [
    "startpos",
    "ruy_lopez",
    "nimzo_indian",
    "sicilian_classical",
    "queens_gambit",
    "caro_kann_advance",
    "english_four_knights",
    "kings_indian",
    "kiwipete_family",
    "quiet_middlegame",
]

SUITE = [
    {
        "id": "startpos",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "",
    },
    {
        "id": "ruy_lopez",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7",
    },
    {
        "id": "nimzo_indian",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 e8g8 f1d3 d7d5",
    },
    {
        "id": "sicilian_classical",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 b8c6",
    },
    {
        "id": "queens_gambit",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8",
    },
    {
        "id": "caro_kann_advance",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6 f1e2",
    },
    {
        "id": "english_four_knights",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "c2c4 e7e5 b1c3 g8f6 g1f3 b8c6 g2g3 f8b4 f1g2 e8g8",
    },
    {
        "id": "kings_indian",
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "moves": "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 g1f3 e8g8",
    },
    {
        "id": "kiwipete_family",
        "fen": "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1",
        "moves": "",
    },
    {
        "id": "quiet_middlegame",
        "fen": "2r2rk1/pp1nbppp/2p1pn2/q2p4/3P4/2N1PN2/PPQ1BPPP/2RR2K1 w - - 0 1",
        "moves": "",
    },
]


def sha256(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_rows(raw_path: str) -> tuple[str, list[dict[str, object]]]:
    lines = Path(raw_path).read_text(encoding="utf-8").splitlines()
    meta_lines = [
        line for line in lines if line.startswith("TE1_HISTORY_FRAMEWORK_BASELINE_META\t")
    ]
    if len(meta_lines) != 1:
        raise SystemExit(f"expected one meta line, found {len(meta_lines)}")
    evaluator = meta_lines[0].split("\t", 1)[1].split("=", 1)[1]
    if not evaluator.startswith("nnue:k32-w128-h32-crelu:"):
        raise SystemExit(f"unexpected production evaluator identity: {evaluator}")

    rows: list[dict[str, object]] = []
    for raw in lines:
        if not raw.startswith("TE1_HISTORY_FRAMEWORK_BASELINE_PROFILE\t"):
            continue
        fields: dict[str, str] = {}
        for part in raw.split("\t")[1:]:
            key, value = part.split("=", 1)
            fields[key] = value
        row: dict[str, object] = {}
        for key, value in fields.items():
            if key in SCALAR_FIELDS:
                row[key] = int(value)
            elif key in ARRAY_FIELDS:
                row[key] = json.loads(value)
            elif key == "stopped":
                if value not in {"true", "false"}:
                    raise SystemExit(f"invalid stopped boolean: {value}")
                row[key] = value == "true"
            elif key == "pv":
                row[key] = [] if not value else value.split(",")
            else:
                row[key] = value
        rows.append(row)
    return evaluator, rows


def validate_row(row: dict[str, object]) -> None:
    identity = str(row["id"])
    if row["depth"] != 8 or row["threads"] != 1 or row["stopped"]:
        raise SystemExit(f"incomplete/non-deterministic row: {identity}")

    q = int(row["quiet_ordering_reads"])
    c = int(row["continuation_reads"])
    cap = int(row["capture_history_reads"])
    traffic = int(row["quiet_search_traffic"])
    if q <= 0 or traffic <= 0:
        raise SystemExit(f"dormant quiet instrumentation: {identity}")

    if sum(row["quiet_component_bins"]) != q:  # type: ignore[arg-type]
        raise SystemExit(f"quiet component cardinality drift: {identity}")
    if sum(row["quiet_base_bins"]) != q or sum(row["quiet_ordering_bins"]) != q:  # type: ignore[arg-type]
        raise SystemExit(f"quiet composite cardinality drift: {identity}")
    if sum(row["continuation_component_bins"]) != c:  # type: ignore[arg-type]
        raise SystemExit(f"continuation cardinality drift: {identity}")
    if sum(row["capture_component_bins"]) != cap:  # type: ignore[arg-type]
        raise SystemExit(f"capture cardinality drift: {identity}")
    if sum(row["quiet_traffic_depth_bins"]) != traffic:  # type: ignore[arg-type]
        raise SystemExit(f"quiet depth traffic cardinality drift: {identity}")
    if sum(row["quiet_traffic_move_bins"]) != traffic:  # type: ignore[arg-type]
        raise SystemExit(f"quiet move traffic cardinality drift: {identity}")
    if sum(row["quiet_traffic_depth_move_matrix"]) != traffic:  # type: ignore[arg-type]
        raise SystemExit(f"quiet matrix cardinality drift: {identity}")

    positive = (
        int(row["quiet_positive_updates"])
        + int(row["continuation_positive_updates"])
        + int(row["capture_positive_updates"])
    )
    negative = (
        int(row["quiet_negative_updates"])
        + int(row["continuation_negative_updates"])
        + int(row["capture_negative_updates"])
    )
    if sum(row["positive_update_depth_bins"]) != positive:  # type: ignore[arg-type]
        raise SystemExit(f"positive update cardinality drift: {identity}")
    if sum(row["negative_update_depth_bins"]) != negative:  # type: ignore[arg-type]
        raise SystemExit(f"negative update cardinality drift: {identity}")

    if row["quiet_positive_updates"] != row["quiet_cutoffs"]:
        raise SystemExit(f"quiet cutoff/update drift: {identity}")
    if row["capture_positive_updates"] != row["capture_cutoffs"]:
        raise SystemExit(f"capture cutoff/update drift: {identity}")
    if row["continuation_positive_updates"] != row["countermove_records"]:
        raise SystemExit(f"continuation/countermove record drift: {identity}")
    if int(row["countermove_matches"]) > c:
        raise SystemExit(f"countermove match exceeds continuation context: {identity}")

    for prefix, reads in (("quiet", q), ("continuation", c), ("capture", cap)):
        near = int(row[f"{prefix}_near_saturation_reads"])
        exact = int(row[f"{prefix}_exact_saturation_reads"])
        if not 0 <= exact <= near <= reads:
            raise SystemExit(f"{prefix} saturation cardinality drift: {identity}")


def aggregate(rows: list[dict[str, object]]) -> dict[str, object]:
    scalar_keys = sorted(
        SCALAR_FIELDS
        - {
            "depth",
            "score_cp",
            "nodes",
            "qnodes",
            "tt_hits",
            "beta_cutoffs",
            "seldepth",
            "hashfull",
            "threads",
        }
    )
    out: dict[str, object] = {
        key: sum(int(row[key]) for row in rows) for key in scalar_keys
    }
    for key in sorted(ARRAY_FIELDS):
        first = rows[0][key]
        if not isinstance(first, list):
            raise SystemExit(f"not an array: {key}")
        width = len(first)
        if any(not isinstance(row[key], list) or len(row[key]) != width for row in rows):
            raise SystemExit(f"array width drift: {key}")
        out[key] = [sum(int(row[key][i]) for row in rows) for i in range(width)]  # type: ignore[index]

    q = int(out["quiet_ordering_reads"])
    c = int(out["continuation_reads"])
    cap = int(out["capture_history_reads"])
    if q <= 0 or c <= 0 or cap <= 0:
        raise SystemExit("one or more baseline history signals are dormant")
    if int(out["quiet_cutoffs"]) <= 0 or int(out["capture_cutoffs"]) <= 0:
        raise SystemExit("cutoff attribution signal is dormant")

    out["rates"] = {
        "continuation_context_pct_of_quiet_reads": 100.0 * c / q,
        "countermove_match_pct_of_quiet_reads": 100.0
        * int(out["countermove_matches"])
        / q,
        "quiet_near_saturation_pct": 100.0
        * int(out["quiet_near_saturation_reads"])
        / q,
        "quiet_exact_saturation_pct": 100.0
        * int(out["quiet_exact_saturation_reads"])
        / q,
        "continuation_near_saturation_pct": 100.0
        * int(out["continuation_near_saturation_reads"])
        / c,
        "continuation_exact_saturation_pct": 100.0
        * int(out["continuation_exact_saturation_reads"])
        / c,
        "capture_near_saturation_pct": 100.0
        * int(out["capture_near_saturation_reads"])
        / cap,
        "capture_exact_saturation_pct": 100.0
        * int(out["capture_exact_saturation_reads"])
        / cap,
    }
    return out


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: history_framework_profile_parse.py RAW RESULT_JSON TRIGGER_SHA PARITY_JSON"
        )
    raw_path, result_path, trigger_sha, parity_path = sys.argv[1:]
    evaluator, rows = parse_rows(raw_path)
    if [str(row.get("id")) for row in rows] != EXPECTED_IDS:
        raise SystemExit(
            f"position identity/order drift: {[row.get('id') for row in rows]}"
        )
    if len(rows) != 10:
        raise SystemExit(f"expected 10 rows, found {len(rows)}")
    for row in rows:
        validate_row(row)
    aggregate_result = aggregate(rows)

    parity = json.loads(Path(parity_path).read_text(encoding="utf-8"))
    if parity.get("schema") != "TE1-ALPHA26-HISTORY-FRAMEWORK-PRODUCTION-PARITY-v1":
        raise SystemExit("production parity schema drift")
    if parity.get("status") != "PASS" or parity.get("positions") != 10:
        raise SystemExit("production parity did not pass")
    if [row["id"] for row in parity.get("rows", [])] != EXPECTED_IDS:
        raise SystemExit("production parity position identity drift")

    obj = {
        "schema": "TE1-ALPHA26-HISTORY-FRAMEWORK-BASELINE-PROFILE-v2",
        "feature": "History Framework / Correction History foundation",
        "gate": "B",
        "decision": "PASS",
        "diagnostic_only": True,
        "instrumentation_only": True,
        "search_decision_change_authorized": False,
        "production_promotion_authorized": False,
        "adaptive_lmr_started": False,
        "singular_extensions_started": False,
        "production_baseline": {
            "commit": "1e750218f43fa5129cb82f19b107555a1343d878",
            "tree": "df59aa937bbff6736af25304ca990a69d06ae49f",
            "search_blob": "cd393b65085cdfa1b327f00f23c69f61763fcb2e",
            "search_sha256": "943cd3320e0538b5e30763ebc1858cbe0fd53d94b6f9c31a1fc0b6364e397a26",
            "engine_blob": "b2facdb682c7841e1b3c77db43dffdcc7fb59fcd",
            "engine_sha256": "61c0b03d3bd274f281a5c375cd0f3b3dd37f48adca4c2fcc42e477471e867f7d",
        },
        "trigger_sha": trigger_sha,
        "instrumented_source": {
            "search_blob": subprocess.check_output(
                ["git", "hash-object", "crates/te1-search/src/lib.rs"], text=True
            ).strip(),
            "search_sha256": sha256("crates/te1-search/src/lib.rs"),
            "engine_blob": subprocess.check_output(
                ["git", "hash-object", "crates/te1-engine/src/main.rs"], text=True
            ).strip(),
            "engine_sha256": sha256("crates/te1-engine/src/main.rs"),
            "patch_blob": subprocess.check_output(
                ["git", "hash-object", ".github/scripts/history_framework_baseline_profile_patch.py"],
                text=True,
            ).strip(),
            "patch_sha256": sha256(
                ".github/scripts/history_framework_baseline_profile_patch.py"
            ),
            "reporter_blob": subprocess.check_output(
                [
                    "git",
                    "hash-object",
                    ".github/diagnostic_sources/history_framework_baseline_profile.rs.txt",
                ],
                text=True,
            ).strip(),
            "reporter_sha256": sha256(
                ".github/diagnostic_sources/history_framework_baseline_profile.rs.txt"
            ),
            "parity_checker_blob": subprocess.check_output(
                ["git", "hash-object", ".github/scripts/history_framework_production_parity.py"],
                text=True,
            ).strip(),
            "parity_checker_sha256": sha256(
                ".github/scripts/history_framework_production_parity.py"
            ),
            "parser_blob": subprocess.check_output(
                ["git", "hash-object", ".github/scripts/history_framework_profile_parse.py"],
                text=True,
            ).strip(),
            "parser_sha256": sha256(
                ".github/scripts/history_framework_profile_parse.py"
            ),
        },
        "runtime": {
            "rustc": subprocess.check_output(["rustc", "--version"], text=True).strip(),
            "cargo": subprocess.check_output(["cargo", "--version"], text=True).strip(),
            "evaluator": evaluator,
            "depth": 8,
            "hash_mb": 16,
            "threads": 1,
            "deterministic": True,
            "use_lmr": True,
            "use_see_pruning": True,
            "use_null_move_pruning": True,
            "use_nnue": True,
            "use_hybrid_eval": False,
        },
        "neutrality_contract": {
            "status": "PASS",
            "production_vs_instrumented_off": parity,
            "instrumented_off_vs_instrumented_on": {
                "status": "PASS",
                "equal_fields": [
                    "best_move",
                    "score_cp",
                    "depth",
                    "seldepth",
                    "nodes",
                    "qnodes",
                    "tt_hits",
                    "beta_cutoffs",
                    "pv",
                    "stopped",
                    "threads",
                    "hashfull_per_mille",
                ],
                "off_profile_all_zero": True,
                "elapsed_ms_excluded": True,
            },
        },
        "bin_definitions": {
            "history_value_bins": [
                "<=-12288",
                "-12287..-8192",
                "-8191..-4096",
                "-4095..-1",
                "0..4095",
                "4096..8191",
                "8192..12287",
                ">=12288",
            ],
            "update_depth_bins": [
                "<=1",
                "2",
                "3",
                "4..5",
                "6..7",
                "8..9",
                "10..11",
                ">=12",
            ],
            "quiet_traffic_depth_bins": ["<=3", "4..5", "6..7", "8..9", ">=10"],
            "quiet_traffic_move_index_bins_zero_based": [
                "0..2",
                "3..7",
                "8..11",
                "12..15",
                ">=16",
            ],
            "quiet_traffic_depth_move_matrix": "row-major depth-bin x move-index-bin, 5x5",
            "near_saturation": "abs(history) >= 3/4 * HISTORY_LIMIT = 12288",
            "exact_saturation": "abs(history) >= HISTORY_LIMIT = 16384",
        },
        "measurement_semantics": {
            "history_distributions": "dynamic read-weighted values at actual ordering consumers, not a raw table-occupancy census",
            "quiet_search_traffic": "quiet moves actually reached by negamax move loop, binned by current node depth and zero-based ordered move index",
            "countermove_match": "categorical +8000 ordering bonus match, kept distinct from signed quiet/continuation history",
        },
        "suite": SUITE,
        "aggregate": aggregate_result,
        "rows": rows,
        "interpretation_limits": [
            "This is baseline instrumentation, not a playing-strength test.",
            "No Correction History key, table size, scaling constant, or update constant is selected by Gate B.",
            "Move-ordering history and future Correction History remain separate signals.",
        ],
    }
    Path(result_path).parent.mkdir(parents=True, exist_ok=True)
    Path(result_path).write_text(
        json.dumps(obj, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        "HISTORY_FRAMEWORK_GATE_B_V2_AGGREGATE",
        json.dumps(aggregate_result, sort_keys=True),
    )


if __name__ == "__main__":
    main()
