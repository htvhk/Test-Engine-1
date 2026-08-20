#!/usr/bin/env python3
import argparse
import json
import math
import re
import statistics
import subprocess
from pathlib import Path

DEPTH = 8
POSITIONS = [
    {"id": "startpos", "position": "position startpos"},
    {"id": "ruy_lopez", "position": "position startpos moves e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7"},
    {"id": "nimzo_indian", "position": "position startpos moves d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 e8g8 f1d3 d7d5"},
    {"id": "sicilian_classical", "position": "position startpos moves e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 b8c6"},
    {"id": "queens_gambit", "position": "position startpos moves d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8"},
    {"id": "caro_kann_advance", "position": "position startpos moves e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6 f1e2"},
    {"id": "english_four_knights", "position": "position startpos moves c2c4 e7e5 b1c3 g8f6 g1f3 b8c6 g2g3 f8b4 f1g2 e8g8"},
    {"id": "kings_indian", "position": "position startpos moves d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 g1f3 e8g8"},
    {"id": "kiwipete_family", "position": "position fen r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1"},
    {"id": "quiet_middlegame", "position": "position fen 2r2rk1/pp1nbppp/2p1pn2/q2p4/3P4/2N1PN2/PPQ1BPPP/2RR2K1 w - - 0 1"},
]

INFO_RE = re.compile(
    r"^info depth (?P<depth>\d+) seldepth (?P<seldepth>\d+) score cp (?P<score>-?\d+) "
    r"nodes (?P<nodes>\d+) nps (?P<nps>\d+) hashfull (?P<hashfull>\d+) time (?P<time>\d+)"
    r"(?: pv (?P<pv>.*))?$"
)
STATS_RE = re.compile(
    r"^info string threads (?P<threads>\d+) tthits (?P<tthits>\d+) cutoffs (?P<cutoffs>\d+) qnodes (?P<qnodes>\d+)$"
)


def send(proc, line):
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def read_until(proc, sentinel):
    lines = []
    while True:
        line = proc.stdout.readline()
        if line == "":
            raise RuntimeError(f"engine exited before {sentinel}: {proc.stderr.read()}")
        line = line.rstrip("\n")
        lines.append(line)
        if line == sentinel:
            return lines


def run_engine(engine, position, adaptive):
    proc = subprocess.Popen(
        [engine],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        send(proc, "uci")
        uci = read_until(proc, "uciok")
        option = "option name UseAdaptiveLMR type check default false"
        if adaptive is None:
            if option in uci:
                raise AssertionError("baseline unexpectedly exposes UseAdaptiveLMR")
        elif option not in uci:
            raise AssertionError("candidate missing default-OFF UseAdaptiveLMR")
        for command in [
            "setoption name Threads value 1",
            "setoption name Deterministic value true",
            "setoption name Hash value 16",
            "setoption name MoveOverhead value 0",
            "setoption name UseLMR value true",
            "setoption name UseSEEPruning value true",
            "setoption name UseNullMovePruning value true",
            "setoption name UseNNUE value true",
            "setoption name EvalFile value default",
        ]:
            send(proc, command)
        if adaptive is not None:
            send(proc, f"setoption name UseAdaptiveLMR value {'true' if adaptive else 'false'}")
        send(proc, "isready")
        read_until(proc, "readyok")
        send(proc, "ucinewgame")
        send(proc, position)
        send(proc, f"go depth {DEPTH}")
        info = stats = None
        bestmove = None
        while True:
            line = proc.stdout.readline()
            if line == "":
                raise RuntimeError(f"engine exited during search: {proc.stderr.read()}")
            line = line.rstrip("\n")
            match = INFO_RE.match(line)
            if match:
                info = match.groupdict()
            match = STATS_RE.match(line)
            if match:
                stats = match.groupdict()
            if line.startswith("bestmove "):
                bestmove = line.split()[1]
                break
        send(proc, "quit")
        proc.wait(timeout=10)
        if proc.returncode != 0 or info is None or stats is None or not bestmove or bestmove == "0000":
            raise RuntimeError(
                f"incomplete engine result rc={proc.returncode} info={info} stats={stats} bestmove={bestmove}"
            )
        if int(info["depth"]) != DEPTH:
            raise AssertionError(f"depth {info['depth']} != {DEPTH}")
        return {
            "depth": int(info["depth"]),
            "seldepth": int(info["seldepth"]),
            "score_cp": int(info["score"]),
            "nodes": int(info["nodes"]),
            "qnodes": int(stats["qnodes"]),
            "tt_hits": int(stats["tthits"]),
            "beta_cutoffs": int(stats["cutoffs"]),
            "bestmove": bestmove,
            "pv": (info["pv"] or "").split(),
        }
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-engine", required=True)
    parser.add_argument("--candidate-engine", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = []
    drift = []
    failures = []
    ratios = []
    same_bestmove = 0
    same_score = 0
    activation_count = 0

    for item in POSITIONS:
        baseline = run_engine(args.baseline_engine, item["position"], None)
        disabled = run_engine(args.candidate_engine, item["position"], False)
        enabled = run_engine(args.candidate_engine, item["position"], True)

        exact = ["score_cp", "nodes", "qnodes", "bestmove", "pv"]
        fields = [field for field in exact if baseline[field] != disabled[field]]
        if fields:
            drift.append({"id": item["id"], "fields": fields})
        if enabled["nodes"] <= 0 or enabled["bestmove"] == "0000":
            failures.append(item["id"])

        ratio = enabled["nodes"] / disabled["nodes"]
        ratios.append(ratio)
        same_bestmove += int(enabled["bestmove"] == disabled["bestmove"])
        same_score += int(enabled["score_cp"] == disabled["score_cp"])
        activated = any(enabled[field] != disabled[field] for field in exact)
        activation_count += int(activated)

        rows.append(
            {
                "id": item["id"],
                "position": item["position"],
                "baseline_main": baseline,
                "candidate_disabled": disabled,
                "candidate_enabled": enabled,
                "enabled_over_disabled_node_ratio": ratio,
                "adaptive_effect_observed": activated,
            }
        )

    geomean = math.exp(sum(math.log(x) for x in ratios) / len(ratios))
    median = statistics.median(ratios)

    if drift:
        decision = "BLOCKED_BASELINE_DRIFT"
    elif failures:
        decision = "BLOCKED_CORRECTNESS"
    elif activation_count == 0:
        decision = "BLOCKED_NO_ADAPTIVE_ACTIVATION"
    elif geomean < 0.98:
        decision = "DIAGNOSTIC_NODE_EFFICIENT"
    elif geomean <= 1.02:
        decision = "DIAGNOSTIC_NODE_NEUTRAL"
    else:
        decision = "DIAGNOSTIC_NODE_REGRESSION"

    out = {
        "schema": "TE1-ALPHA26-ADAPTIVE-LMR-R2-NODE-DEPTH8-v1",
        "depth": DEPTH,
        "positions": len(POSITIONS),
        "baseline_main_commit": "1e750218f43fa5129cb82f19b107555a1343d878",
        "candidate_identity_commit": "8f38a15919bb65c60c774ea96fd4e7e68d80d36b",
        "candidate_source_commit": "320bb584a4b9a0643aece496f5df4f4b779798cb",
        "candidate_search_sha256": "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e",
        "nmp_enabled_both_arms": True,
        "baseline_drift": drift,
        "candidate_failures": failures,
        "adaptive_activation_count": activation_count,
        "node_ratio_geometric_mean": geomean,
        "node_ratio_median": median,
        "same_bestmove_count": same_bestmove,
        "same_score_count": same_score,
        "decision": decision,
        "strength_claim_authorized": False,
        "rows": rows,
    }

    Path(args.output).write_text(
        json.dumps(out, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                key: out[key]
                for key in [
                    "decision",
                    "adaptive_activation_count",
                    "node_ratio_geometric_mean",
                    "node_ratio_median",
                    "same_bestmove_count",
                    "same_score_count",
                    "baseline_drift",
                    "candidate_failures",
                ]
            },
            indent=2,
        )
    )
    return 0 if decision not in {
        "BLOCKED_BASELINE_DRIFT",
        "BLOCKED_CORRECTNESS",
        "BLOCKED_NO_ADAPTIVE_ACTIVATION",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
