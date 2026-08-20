#!/usr/bin/env python3
import argparse
import json
import math
import re
import statistics
import subprocess
from pathlib import Path

DEPTH = 6
POSITIONS = [
    {"id": "startpos", "position": "position startpos"},
    {
        "id": "ruy_lopez",
        "position": "position startpos moves e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7",
    },
    {
        "id": "nimzo_indian",
        "position": "position startpos moves d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 e8g8 f1d3 d7d5",
    },
    {
        "id": "sicilian_classical",
        "position": "position startpos moves e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 b8c6",
    },
    {
        "id": "queens_gambit",
        "position": "position startpos moves d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8",
    },
    {
        "id": "caro_kann_advance",
        "position": "position startpos moves e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6 f1e2",
    },
    {
        "id": "english_four_knights",
        "position": "position startpos moves c2c4 e7e5 b1c3 g8f6 g1f3 b8c6 g2g3 f8b4 f1g2 e8g8",
    },
    {
        "id": "kings_indian",
        "position": "position startpos moves d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 g1f3 e8g8",
    },
    {
        "id": "kiwipete_family",
        "position": "position fen r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1",
    },
    {
        "id": "quiet_middlegame",
        "position": "position fen 2r2rk1/pp1nbppp/2p1pn2/q2p4/3P4/2N1PN2/PPQ1BPPP/2RR2K1 w - - 0 1",
    },
]

INFO_RE = re.compile(
    r"^info depth (?P<depth>\d+) seldepth (?P<seldepth>\d+) score cp (?P<score>-?\d+) "
    r"nodes (?P<nodes>\d+) nps (?P<nps>\d+) hashfull (?P<hashfull>\d+) time (?P<time>\d+)(?: pv (?P<pv>.*))?$"
)
STATS_RE = re.compile(r"^info string threads (?P<threads>\d+) tthits (?P<tthits>\d+) cutoffs (?P<cutoffs>\d+) qnodes (?P<qnodes>\d+)$")


def send(proc: subprocess.Popen[str], line: str) -> None:
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def read_until(proc: subprocess.Popen[str], sentinel: str) -> list[str]:
    assert proc.stdout is not None
    lines = []
    while True:
        line = proc.stdout.readline()
        if line == "":
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            raise RuntimeError(f"engine exited before {sentinel}: {stderr}")
        line = line.rstrip("\n")
        lines.append(line)
        if line == sentinel:
            return lines


def run_engine(engine: str, position: str, adaptive: bool | None) -> dict:
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
        uci_lines = read_until(proc, "uciok")
        adaptive_option = "option name UseAdaptiveLMR type check default false"
        if adaptive is None:
            if adaptive_option in uci_lines:
                raise AssertionError("baseline engine unexpectedly exposes UseAdaptiveLMR")
        elif adaptive_option not in uci_lines:
            raise AssertionError("candidate engine does not expose default-OFF UseAdaptiveLMR")

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

        info = None
        stats = None
        bestmove = None
        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if line == "":
                stderr = proc.stderr.read() if proc.stderr is not None else ""
                raise RuntimeError(f"engine exited during search: {stderr}")
            line = line.rstrip("\n")
            match = INFO_RE.match(line)
            if match:
                info = match.groupdict()
            stats_match = STATS_RE.match(line)
            if stats_match:
                stats = stats_match.groupdict()
            if line.startswith("bestmove "):
                bestmove = line.split()[1]
                break

        send(proc, "quit")
        proc.wait(timeout=10)
        if proc.returncode != 0:
            stderr = proc.stderr.read() if proc.stderr is not None else ""
            raise RuntimeError(f"engine exit {proc.returncode}: {stderr}")
        if info is None or stats is None or bestmove is None or bestmove == "0000":
            raise AssertionError(f"incomplete search result: info={info} stats={stats} bestmove={bestmove}")
        if int(info["depth"]) != DEPTH:
            raise AssertionError(f"search did not complete depth {DEPTH}: {info}")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-engine", required=True)
    parser.add_argument("--candidate-engine", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    rows = []
    baseline_drift = []
    candidate_failures = []
    ratios = []
    same_bestmove = 0
    same_score = 0

    for entry in POSITIONS:
        baseline = run_engine(args.baseline_engine, entry["position"], None)
        disabled = run_engine(args.candidate_engine, entry["position"], False)
        enabled = run_engine(args.candidate_engine, entry["position"], True)

        exact_fields = ["score_cp", "nodes", "qnodes", "bestmove", "pv"]
        drift_fields = [field for field in exact_fields if baseline[field] != disabled[field]]
        if drift_fields:
            baseline_drift.append({"id": entry["id"], "fields": drift_fields})

        if enabled["bestmove"] == "0000" or enabled["nodes"] <= 0:
            candidate_failures.append(entry["id"])

        ratio = enabled["nodes"] / disabled["nodes"]
        ratios.append(ratio)
        same_bestmove += int(enabled["bestmove"] == disabled["bestmove"])
        same_score += int(enabled["score_cp"] == disabled["score_cp"])
        rows.append(
            {
                "id": entry["id"],
                "position": entry["position"],
                "baseline_main": baseline,
                "candidate_disabled": disabled,
                "candidate_enabled": enabled,
                "enabled_over_disabled_node_ratio": ratio,
            }
        )

    geomean = math.exp(sum(math.log(value) for value in ratios) / len(ratios))
    median = statistics.median(ratios)
    if baseline_drift:
        decision = "BLOCKED_BASELINE_DRIFT"
    elif candidate_failures:
        decision = "BLOCKED_CORRECTNESS"
    elif geomean < 0.98:
        decision = "DIAGNOSTIC_NODE_EFFICIENT"
    elif geomean <= 1.02:
        decision = "DIAGNOSTIC_NODE_NEUTRAL"
    else:
        decision = "DIAGNOSTIC_NODE_REGRESSION"

    result = {
        "schema": "TE1-ALPHA26-ADAPTIVE-LMR-R1-NODE-DIAGNOSTIC-v1",
        "depth": DEPTH,
        "positions": len(POSITIONS),
        "baseline_main_commit": "1e750218f43fa5129cb82f19b107555a1343d878",
        "candidate_identity_commit": "fd9d8ae49ba269f21e53c5c9ad481d29cd67c70c",
        "candidate_source_commit": "da1dbe57c41fce0fcf332cd8e47a053d59cf38d5",
        "candidate_search_sha256": "2fdb7f5796146ebec74382e7cc799176f8e2697f09487f456cb1ffe785f69e11",
        "nmp_enabled_both_arms": True,
        "baseline_drift": baseline_drift,
        "candidate_failures": candidate_failures,
        "node_ratio_geometric_mean": geomean,
        "node_ratio_median": median,
        "same_bestmove_count": same_bestmove,
        "same_score_count": same_score,
        "decision": decision,
        "strength_claim_authorized": False,
        "rows": rows,
    }
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: result[k] for k in [
        "decision", "node_ratio_geometric_mean", "node_ratio_median",
        "same_bestmove_count", "same_score_count", "baseline_drift", "candidate_failures"
    ]}, indent=2))
    return 0 if decision not in {"BLOCKED_BASELINE_DRIFT", "BLOCKED_CORRECTNESS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
