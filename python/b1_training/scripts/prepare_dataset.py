from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


# Make each CLI entry point runnable directly, independent of notebook PYTHONPATH state.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from te1_b1.dataset import audit_raw_identity_isolation, prepare_split
from te1_b1.io_utils import strict_dump


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    reports = []
    names = {"train": "nnue-train.jsonl.gz", "development": "nnue-development.jsonl.gz", "reserve": "nnue-reserve.jsonl.gz"}
    expected = {"train": 160_000, "development": 20_000, "reserve": 20_000}
    input_paths = {split: args.input_root / filename for split, filename in names.items()}
    identity_audit = audit_raw_identity_isolation(input_paths)
    if identity_audit["rows"] != expected:
        raise RuntimeError(f"raw identity audit row-count mismatch: {identity_audit['rows']} != {expected}")
    for split, filename in names.items():
        report = prepare_split(args.input_root / filename, args.output_root / f"{split}.npz", split)
        if report["rows"] != expected[split]:
            raise RuntimeError(f"{split} row count mismatch: {report['rows']} != {expected[split]}")
        reports.append(report)
    value = {"status": "PASS", "splits": reports, "total_rows": sum(int(x["rows"]) for x in reports), "identity_isolation": identity_audit}
    strict_dump(value, args.report)
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
