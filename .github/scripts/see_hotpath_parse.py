#!/usr/bin/env python3
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

IDS = [
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
REPS = range(3)
MODES = range(3)
COUNTERS = [
    "calls",
    "reply_calls",
    "clones",
    "move_lists",
    "reply_moves_scanned",
    "target_captures",
    "max_depth",
    "negative",
    "zero",
    "positive",
]


def parse_rows(path: Path, prefix: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.startswith(prefix + "\t"):
            continue
        row: dict[str, str] = {}
        for field in raw.split("\t")[1:]:
            key, sep, value = field.partition("=")
            if not sep or not key:
                raise SystemExit(f"malformed field in {path}: {field!r}")
            row[key] = value
        rows.append(row)
    return rows


def as_int(row: dict[str, str], key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, ValueError) as exc:
        raise SystemExit(f"invalid integer field {key}: {row}") from exc


def median(values: list[float]) -> float:
    if not values:
        raise SystemExit("cannot take median of empty values")
    return float(statistics.median(values))


def keyed(rows: list[dict[str, str]], include_mode: bool) -> dict[tuple, dict[str, str]]:
    out: dict[tuple, dict[str, str]] = {}
    for row in rows:
        ident = row.get("id")
        rep = as_int(row, "rep")
        key = (ident, as_int(row, "mode"), rep) if include_mode else (ident, rep)
        if key in out:
            raise SystemExit(f"duplicate row: {key}")
        out[key] = row
    return out


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: see_hotpath_parse.py CONTROL PROFILE OUTPUT_JSON")
    control_path, profile_path, output_path = map(Path, sys.argv[1:])
    control_rows = parse_rows(control_path, "TE1_SEE_CONTROL")
    profile_rows = parse_rows(profile_path, "TE1_SEE_PROFILE")
    if len(control_rows) != len(IDS) * 3:
        raise SystemExit(f"expected 30 control rows, got {len(control_rows)}")
    if len(profile_rows) != len(IDS) * 3 * 3:
        raise SystemExit(f"expected 90 profile rows, got {len(profile_rows)}")

    control = keyed(control_rows, False)
    profile = keyed(profile_rows, True)
    expected_control_keys = {(ident, rep) for ident in IDS for rep in REPS}
    expected_profile_keys = {(ident, mode, rep) for ident in IDS for mode in MODES for rep in REPS}
    if set(control) != expected_control_keys:
        raise SystemExit("control key set mismatch")
    if set(profile) != expected_profile_keys:
        raise SystemExit("profile key set mismatch")

    representative_counts: dict[str, dict[str, int]] = {}
    for ident in IDS:
        semantics = {control[(ident, rep)]["semantic"] for rep in REPS}
        if len(semantics) != 1:
            raise SystemExit(f"control semantic repeat drift: {ident}")
        expected_semantic = next(iter(semantics))
        for mode in MODES:
            for rep in REPS:
                row = profile[(ident, mode, rep)]
                if row.get("semantic") != expected_semantic:
                    raise SystemExit(f"semantic drift: {ident} mode={mode} rep={rep}")

        for rep in REPS:
            row0 = profile[(ident, 0, rep)]
            if any(as_int(row0, counter) != 0 for counter in COUNTERS) or as_int(row0, "nanos") != 0:
                raise SystemExit(f"mode 0 counters not dormant: {ident} rep={rep}")

        reference = profile[(ident, 1, 0)]
        counts = {counter: as_int(reference, counter) for counter in COUNTERS}
        representative_counts[ident] = counts
        if as_int(reference, "nanos") != 0:
            raise SystemExit(f"counts-only mode has timing data: {ident}")
        for rep in REPS:
            count_row = profile[(ident, 1, rep)]
            timed_row = profile[(ident, 2, rep)]
            for counter, expected in counts.items():
                if as_int(count_row, counter) != expected:
                    raise SystemExit(f"counts repeat drift: {ident} {counter} rep={rep}")
                if as_int(timed_row, counter) != expected:
                    raise SystemExit(f"timed/count counter drift: {ident} {counter} rep={rep}")
            if counts["calls"] > 0 and as_int(timed_row, "nanos") <= 0:
                raise SystemExit(f"timed mode missing SEE timing: {ident} rep={rep}")

        calls = counts["calls"]
        if counts["reply_calls"] < calls:
            raise SystemExit(f"reply calls below top-level calls: {ident}")
        if counts["clones"] < calls:
            raise SystemExit(f"clone count below top-level calls: {ident}")
        if counts["move_lists"] > counts["reply_calls"]:
            raise SystemExit(f"move-list count exceeds reply calls: {ident}")
        if counts["target_captures"] > counts["reply_moves_scanned"]:
            raise SystemExit(f"target captures exceed scanned replies: {ident}")
        if counts["max_depth"] > 16:
            raise SystemExit(f"SEE depth cap violated: {ident}")
        if counts["negative"] + counts["zero"] + counts["positive"] != calls:
            raise SystemExit(f"SEE sign accounting mismatch: {ident}")

    total_counts = {counter: sum(values[counter] for values in representative_counts.values()) for counter in COUNTERS}
    calls = total_counts["calls"]
    if calls <= 0:
        raise SystemExit("SEE profile is dormant across the full 10-position suite")
    total_nodes = sum(as_int(control[(ident, 0)], "nodes") for ident in IDS)
    total_qnodes = sum(as_int(control[(ident, 0)], "qnodes") for ident in IDS)

    control_elapsed_by_rep = [sum(as_int(control[(ident, rep)], "elapsed_ms") for ident in IDS) for rep in REPS]
    profile_elapsed_by_mode: dict[int, list[int]] = {
        mode: [sum(as_int(profile[(ident, mode, rep)], "elapsed_ms") for ident in IDS) for rep in REPS]
        for mode in MODES
    }
    timed_nanos_by_rep = [sum(as_int(profile[(ident, 2, rep)], "nanos") for ident in IDS) for rep in REPS]
    timed_share = [
        100.0 * nanos / max(1, elapsed_ms * 1_000_000)
        for nanos, elapsed_ms in zip(timed_nanos_by_rep, profile_elapsed_by_mode[2], strict=True)
    ]

    summary = {
        "schema": "TE1-ALPHA26-SEE-HOTPATH-PROFILE-v1",
        "production_base": "fc3be2f9dc922db64db20aab78e6ae5d93cbde58",
        "search_blob": "536a9bb287a2aaacdad4cfe795382552d971260a",
        "search_sha256": "e9d361343a00cfa168b70463b7918ce05e809addff14f8e1f6f22fcce25a0f4c",
        "reference_engines": {
            "stockfish": "2edd935bbb3ea6e484a1700f582a95e0ee773ec2",
            "berserk": "b0c05b0f0138aeb694a84ac74e1d750d8f0d76d2",
            "stormphrax": "2402cae156a11eb5433e07dd8ec8ed7a9d67b750",
        },
        "positions": len(IDS),
        "depth": 8,
        "repetitions": 3,
        "semantic_parity": True,
        "profile_valid": True,
        "nodes": total_nodes,
        "qnodes": total_qnodes,
        "totals": total_counts,
        "derived": {
            "see_calls_per_1000_nodes": 1000.0 * calls / max(1, total_nodes),
            "clones_per_see_call": total_counts["clones"] / calls,
            "reply_calls_per_see_call": total_counts["reply_calls"] / calls,
            "reply_moves_scanned_per_see_call": total_counts["reply_moves_scanned"] / calls,
            "target_captures_per_see_call": total_counts["target_captures"] / calls,
            "negative_fraction": total_counts["negative"] / calls,
            "zero_fraction": total_counts["zero"] / calls,
            "positive_fraction": total_counts["positive"] / calls,
        },
        "timing": {
            "control_total_ms_by_rep": control_elapsed_by_rep,
            "instrumented_off_total_ms_by_rep": profile_elapsed_by_mode[0],
            "counts_total_ms_by_rep": profile_elapsed_by_mode[1],
            "timed_total_ms_by_rep": profile_elapsed_by_mode[2],
            "timed_see_nanos_by_rep": timed_nanos_by_rep,
            "timed_see_share_percent_by_rep": timed_share,
            "control_total_ms_median": median([float(v) for v in control_elapsed_by_rep]),
            "instrumented_off_total_ms_median": median([float(v) for v in profile_elapsed_by_mode[0]]),
            "counts_total_ms_median": median([float(v) for v in profile_elapsed_by_mode[1]]),
            "timed_total_ms_median": median([float(v) for v in profile_elapsed_by_mode[2]]),
            "timed_see_share_percent_median": median(timed_share),
        },
    }
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
