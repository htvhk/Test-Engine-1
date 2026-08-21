#!/usr/bin/env python3
"""Fail-closed aggregation for the frozen Adaptive LMR R2 256-game paired match."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

SHARD_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-STRENGTH-256-SHARD-v1"
AGGREGATE_SCHEMA = "TE1-ALPHA26-ADAPTIVE-LMR-R2-STRENGTH-256-v1"
CANDIDATE_ID = "8f38a15919bb65c60c774ea96fd4e7e68d80d36b"
SOURCE_COMMIT = "320bb584a4b9a0643aece496f5df4f4b779798cb"
SEARCH_SHA256 = "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e"
SHARDS = 8
PAIRS_TOTAL = 128
GAMES_TOTAL = 256
NODES = 100_000
T_CRITICAL_95_DF127 = 1.979


def elo_from_score(score_fraction: float) -> float:
    if score_fraction <= 0.0:
        return float("-inf")
    if score_fraction >= 1.0:
        return float("inf")
    return 400.0 * math.log10(score_fraction / (1.0 - score_fraction))


def finite_elo(score_fraction: float) -> float:
    clipped = min(max(score_fraction, 1e-9), 1.0 - 1e-9)
    return elo_from_score(clipped)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    paths = sorted(input_dir.rglob("adaptive-lmr-r2-strength-shard-*.json"))
    if len(paths) != SHARDS:
        raise SystemExit(f"expected {SHARDS} shard files, found {len(paths)}: {paths}")

    shard_objects = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    summaries = [obj["summary"] for obj in shard_objects]
    games = [game for obj in shard_objects for game in obj["games"]]

    seen_shards: set[int] = set()
    seen_ranges: list[tuple[int, int]] = []
    selection_sha256: str | None = None
    book_sha256: str | None = None
    book_blob_sha1: str | None = None
    pair_scores: list[float] = []
    penta = {"0.0": 0, "0.5": 0, "1.0": 0, "1.5": 0, "2.0": 0}
    wins = draws = losses = max_ply_draws = 0

    for summary in summaries:
        if summary["schema"] != SHARD_SCHEMA:
            raise SystemExit(f"wrong shard schema: {summary['schema']}")
        if summary["candidate_identity_commit"] != CANDIDATE_ID:
            raise SystemExit("candidate identity drift")
        if summary["candidate_source_commit"] != SOURCE_COMMIT:
            raise SystemExit("candidate source drift")
        if summary["candidate_search_sha256"] != SEARCH_SHA256:
            raise SystemExit("candidate search hash drift")
        if summary["pairs_total"] != PAIRS_TOTAL or summary["shards"] != SHARDS:
            raise SystemExit("campaign geometry drift")
        if summary["pairs"] != PAIRS_TOTAL // SHARDS or summary["games"] != GAMES_TOTAL // SHARDS:
            raise SystemExit("shard geometry drift")
        if summary["nodes_per_move"] != NODES:
            raise SystemExit("node budget drift")
        if not (
            summary["use_lmr_both_arms"]
            and summary["use_nmp_both_arms"]
            and summary["classical_evaluator_both_arms"]
        ):
            raise SystemExit("engine configuration drift")
        if summary["strength_claim_authorized"] is not False:
            raise SystemExit("premature strength claim")

        shard = int(summary["shard"])
        if shard in seen_shards or not 0 <= shard < SHARDS:
            raise SystemExit(f"duplicate/invalid shard {shard}")
        seen_shards.add(shard)
        pair_range = tuple(summary["pair_range"])
        if len(pair_range) != 2:
            raise SystemExit("malformed pair range")
        seen_ranges.append((int(pair_range[0]), int(pair_range[1])))

        if selection_sha256 is None:
            selection_sha256 = summary["selection_sha256"]
            book_sha256 = summary["source_sha256"]
            book_blob_sha1 = summary["source_blob_sha1"]
        elif (
            summary["selection_sha256"] != selection_sha256
            or summary["source_sha256"] != book_sha256
            or summary["source_blob_sha1"] != book_blob_sha1
        ):
            raise SystemExit("opening-book or selection identity drift across shards")

        wdl = summary["adaptive_wdl"]
        if wdl["win"] + wdl["draw"] + wdl["loss"] != summary["games"]:
            raise SystemExit("invalid shard WDL accounting")
        wins += int(wdl["win"])
        draws += int(wdl["draw"])
        losses += int(wdl["loss"])
        max_ply_draws += int(summary["max_ply_draws"])

        shard_pair_scores = [float(value) for value in summary["pair_scores"]]
        if len(shard_pair_scores) != PAIRS_TOTAL // SHARDS:
            raise SystemExit("pair-score count drift")
        if any(value not in {0.0, 0.5, 1.0, 1.5, 2.0} for value in shard_pair_scores):
            raise SystemExit("invalid pair score")
        pair_scores.extend(shard_pair_scores)
        for key in penta:
            penta[key] += int(summary["penta"][key])

    if seen_shards != set(range(SHARDS)):
        raise SystemExit(f"missing shards: {sorted(set(range(SHARDS)) - seen_shards)}")
    if sorted(seen_ranges) != [(index * 16, (index + 1) * 16) for index in range(SHARDS)]:
        raise SystemExit(f"pair-range coverage drift: {sorted(seen_ranges)}")
    if len(games) != GAMES_TOTAL or wins + draws + losses != GAMES_TOTAL:
        raise SystemExit("aggregate game accounting drift")
    if len(pair_scores) != PAIRS_TOTAL or sum(penta.values()) != PAIRS_TOTAL:
        raise SystemExit("aggregate pair accounting drift")

    game_ids = [game["id"] for game in games]
    if len(game_ids) != len(set(game_ids)):
        raise SystemExit("duplicate game identity")
    opening_counts: dict[int, int] = {}
    color_counts: dict[tuple[int, str], int] = {}
    for game in games:
        opening_index = int(game["opening_index"])
        if not 0 <= opening_index < PAIRS_TOTAL:
            raise SystemExit(f"invalid opening index {opening_index}")
        opening_counts[opening_index] = opening_counts.get(opening_index, 0) + 1
        color_key = (opening_index, game["adaptive_color"])
        color_counts[color_key] = color_counts.get(color_key, 0) + 1
    if any(opening_counts.get(index) != 2 for index in range(PAIRS_TOTAL)):
        raise SystemExit("opening pair coverage drift")
    for index in range(PAIRS_TOTAL):
        if color_counts.get((index, "white")) != 1 or color_counts.get((index, "black")) != 1:
            raise SystemExit(f"color reversal drift at opening {index}")

    score_points = wins + 0.5 * draws
    score_fraction = score_points / GAMES_TOTAL
    paired_fractions = [score / 2.0 for score in pair_scores]
    paired_mean = statistics.mean(paired_fractions)
    if abs(paired_mean - score_fraction) > 1e-12:
        raise SystemExit("paired/game score reconciliation failed")
    paired_stdev = statistics.stdev(paired_fractions)
    standard_error = paired_stdev / math.sqrt(PAIRS_TOTAL)
    margin = T_CRITICAL_95_DF127 * standard_error
    ci_low = max(0.0, paired_mean - margin)
    ci_high = min(1.0, paired_mean + margin)

    elo = finite_elo(score_fraction)
    elo_low = finite_elo(ci_low)
    elo_high = finite_elo(ci_high)
    if ci_low > 0.5:
        decision = "DIAGNOSTIC_POSITIVE"
    elif ci_high < 0.5:
        decision = "DIAGNOSTIC_NEGATIVE"
    else:
        decision = "DIAGNOSTIC_INCONCLUSIVE"

    aggregate = {
        "schema": AGGREGATE_SCHEMA,
        "candidate_identity_commit": CANDIDATE_ID,
        "candidate_source_commit": SOURCE_COMMIT,
        "candidate_search_sha256": SEARCH_SHA256,
        "pairs": PAIRS_TOTAL,
        "games": GAMES_TOTAL,
        "nodes_per_move": NODES,
        "shards": SHARDS,
        "selection_sha256": selection_sha256,
        "source_sha256": book_sha256,
        "source_blob_sha1": book_blob_sha1,
        "adaptive_wdl": {"win": wins, "draw": draws, "loss": losses},
        "adaptive_score": score_points,
        "adaptive_score_pct": score_fraction * 100.0,
        "penta": penta,
        "pair_score_mean": statistics.mean(pair_scores),
        "paired_score_fraction_mean": paired_mean,
        "paired_score_fraction_stdev": paired_stdev,
        "paired_standard_error": standard_error,
        "paired_normal_95ci_score_fraction": [ci_low, ci_high],
        "elo_estimate": elo,
        "elo_95ci": [elo_low, elo_high],
        "max_ply_draws": max_ply_draws,
        "decision": decision,
        "promotion_authorized": False,
        "parameter_tuning_after_launch": False,
        "games_detail": sorted(games, key=lambda game: game["id"]),
    }
    Path(args.output).write_text(
        json.dumps(aggregate, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("ADAPTIVE_LMR_R2_STRENGTH_AGGREGATE", json.dumps({
        key: aggregate[key]
        for key in [
            "adaptive_wdl",
            "adaptive_score_pct",
            "penta",
            "paired_normal_95ci_score_fraction",
            "elo_estimate",
            "elo_95ci",
            "max_ply_draws",
            "decision",
        ]
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
