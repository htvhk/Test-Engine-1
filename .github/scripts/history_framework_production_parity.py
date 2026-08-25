#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

DEPTH = 8

SUITE = [
    ("startpos", "position startpos"),
    (
        "ruy_lopez",
        "position startpos moves e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7",
    ),
    (
        "nimzo_indian",
        "position startpos moves d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 e8g8 f1d3 d7d5",
    ),
    (
        "sicilian_classical",
        "position startpos moves e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 b8c6",
    ),
    (
        "queens_gambit",
        "position startpos moves d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8",
    ),
    (
        "caro_kann_advance",
        "position startpos moves e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6 f1e2",
    ),
    (
        "english_four_knights",
        "position startpos moves c2c4 e7e5 b1c3 g8f6 g1f3 b8c6 g2g3 f8b4 f1g2 e8g8",
    ),
    (
        "kings_indian",
        "position startpos moves d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 g1f3 e8g8",
    ),
    (
        "kiwipete_family",
        "position fen r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1",
    ),
    (
        "quiet_middlegame",
        "position fen 2r2rk1/pp1nbppp/2p1pn2/q2p4/3P4/2N1PN2/PPQ1BPPP/2RR2K1 w - - 0 1",
    ),
]

EXACT_FIELDS = [
    "bestmove",
    "depth",
    "seldepth",
    "score_cp",
    "nodes",
    "hashfull_per_mille",
    "pv",
    "threads",
    "tt_hits",
    "beta_cutoffs",
    "qnodes",
]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class UciEngine:
    def __init__(self, path: str) -> None:
        self.path = path
        self.proc = subprocess.Popen(
            [path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("failed to create UCI pipes")
        self.stdin = self.proc.stdin
        self.stdout = self.proc.stdout
        self.send("uci")
        self.read_until(lambda line: line == "uciok", "uciok")
        for command in (
            "setoption name Hash value 16",
            "setoption name Threads value 1",
            "setoption name Deterministic value true",
            "setoption name UseLMR value true",
            "setoption name UseSEEPruning value true",
            "setoption name UseNullMovePruning value true",
            "setoption name UseNNUE value true",
            "setoption name UseHybridEval value false",
            "setoption name EvalFile value <embedded>",
        ):
            self.send(command)
        self.send("isready")
        self.read_until(lambda line: line == "readyok", "readyok")

    def send(self, command: str) -> None:
        self.stdin.write(command + "\n")
        self.stdin.flush()

    def read_until(self, predicate, label: str) -> list[str]:
        lines: list[str] = []
        while True:
            line = self.stdout.readline()
            if line == "":
                raise RuntimeError(
                    f"{self.path}: EOF while waiting for {label}; output={lines[-20:]}"
                )
            line = line.rstrip("\r\n")
            lines.append(line)
            if predicate(line):
                return lines

    def search(self, position_command: str) -> dict[str, object]:
        self.send("setoption name Clear Hash")
        self.send("isready")
        self.read_until(lambda line: line == "readyok", "readyok after Clear Hash")
        self.send(position_command)
        self.send(f"go depth {DEPTH}")
        lines = self.read_until(lambda line: line.startswith("bestmove "), "bestmove")
        info = [line for line in lines if line.startswith("info depth ")]
        stats = [line for line in lines if line.startswith("info string threads ")]
        best = [line for line in lines if line.startswith("bestmove ")]
        if len(info) != 1 or len(stats) != 1 or len(best) != 1:
            raise RuntimeError(
                f"unexpected UCI result cardinality: info={len(info)} stats={len(stats)} best={len(best)}"
            )
        return parse_result(info[0], stats[0], best[0])

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.send("quit")
            except BrokenPipeError:
                pass
            self.proc.wait(timeout=10)
        if self.proc.returncode != 0:
            raise RuntimeError(f"{self.path}: exited with {self.proc.returncode}")


def token_value(tokens: list[str], key: str) -> str:
    try:
        index = tokens.index(key)
    except ValueError as error:
        raise RuntimeError(f"missing {key} in {' '.join(tokens)}") from error
    if index + 1 >= len(tokens):
        raise RuntimeError(f"missing value after {key}")
    return tokens[index + 1]


def parse_result(info: str, stats: str, best: str) -> dict[str, object]:
    info_tokens = info.split()
    stats_tokens = stats.split()
    best_tokens = best.split()
    try:
        pv_index = info_tokens.index("pv")
    except ValueError as error:
        raise RuntimeError(f"missing PV in {info}") from error
    if token_value(info_tokens, "score") != "cp":
        raise RuntimeError(f"non-centipawn score in {info}")
    return {
        "bestmove": best_tokens[1],
        "depth": int(token_value(info_tokens, "depth")),
        "seldepth": int(token_value(info_tokens, "seldepth")),
        "score_cp": int(info_tokens[info_tokens.index("score") + 2]),
        "nodes": int(token_value(info_tokens, "nodes")),
        "hashfull_per_mille": int(token_value(info_tokens, "hashfull")),
        "pv": info_tokens[pv_index + 1 :],
        "threads": int(token_value(stats_tokens, "threads")),
        "tt_hits": int(token_value(stats_tokens, "tthits")),
        "beta_cutoffs": int(token_value(stats_tokens, "cutoffs")),
        "qnodes": int(token_value(stats_tokens, "qnodes")),
    }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: history_framework_production_parity.py CONTROL CANDIDATE OUTPUT_JSON"
        )
    control_path, candidate_path, output_path = sys.argv[1:]
    for path in (control_path, candidate_path):
        if not Path(path).is_file():
            raise SystemExit(f"missing engine binary: {path}")

    control = UciEngine(control_path)
    candidate = UciEngine(candidate_path)
    rows = []
    try:
        for identity, position_command in SUITE:
            control_result = control.search(position_command)
            candidate_result = candidate.search(position_command)
            if control_result != candidate_result:
                differences = {
                    key: {
                        "production": control_result.get(key),
                        "instrumented_off": candidate_result.get(key),
                    }
                    for key in EXACT_FIELDS
                    if control_result.get(key) != candidate_result.get(key)
                }
                raise SystemExit(
                    f"production/instrumented-OFF decision drift at {identity}: "
                    + json.dumps(differences, sort_keys=True)
                )
            rows.append(
                {
                    "id": identity,
                    "position_command": position_command,
                    "result": control_result,
                }
            )
    finally:
        control.close()
        candidate.close()

    obj = {
        "schema": "TE1-ALPHA26-HISTORY-FRAMEWORK-PRODUCTION-PARITY-v1",
        "status": "PASS",
        "depth": DEPTH,
        "positions": len(rows),
        "exact_fields": EXACT_FIELDS,
        "production_binary_sha256": sha256(control_path),
        "instrumented_off_binary_sha256": sha256(candidate_path),
        "elapsed_time_and_nps_excluded": True,
        "rows": rows,
    }
    Path(output_path).write_text(
        json.dumps(obj, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        "TE1_HISTORY_FRAMEWORK_PRODUCTION_PARITY_PASS",
        obj["production_binary_sha256"],
        obj["instrumented_off_binary_sha256"],
    )


if __name__ == "__main__":
    main()
