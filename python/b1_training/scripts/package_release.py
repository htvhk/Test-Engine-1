from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
import sys


# Make each CLI entry point runnable directly, independent of notebook PYTHONPATH state.
SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from te1_b1.io_utils import sha256_file, strict_dump


def file_record(path: Path, root: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--exports-root", type=Path, required=True)
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--logs-root", type=Path, required=True)
    parser.add_argument("--parent-provenance", type=Path, required=True)
    parser.add_argument("--output-zip", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--parent-sha256", required=True)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    root_name = "TE1-v1.0.0-alpha.2.5B.1-Production-NNUE-Training-and-Challenger-Selection-Output"
    with tempfile.TemporaryDirectory(prefix="te1-b1-package-") as temporary:
        root = Path(temporary) / root_name
        root.mkdir(parents=True)
        (root / "VERSION").write_text("1.0.0-alpha.2.5B.1\n")
        shutil.copytree(args.source_root, root / "source", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"))
        shutil.copytree(args.exports_root, root / "exports")
        shutil.copytree(args.reports_root, root / "reports")
        shutil.copytree(args.logs_root, root / "logs")
        shutil.copytree(args.parent_provenance, root / "input-provenance")
        release = {"status": "PASS", "stage": "TE1-A25B1-PRODUCTION-NNUE-HARDENED-R2", "parent_200k_sha256": args.parent_sha256, "source_zip_sha256": args.source_sha256, "promotion_semantics": "offline provisional only; Rust parity and D.3 paired-match/SPRT gates remain required"}
        strict_dump(release, root / "RELEASE-SUMMARY.json")
        outer_manifest = root / "SHA256-MANIFEST.json"
        records = [file_record(path, root) for path in sorted(root.rglob("*")) if path.is_file() and path != outer_manifest]
        strict_dump({"algorithm": "sha256", "files": records}, root / "SHA256-MANIFEST.json")
        args.output_zip.parent.mkdir(parents=True, exist_ok=True)
        temp_zip = args.output_zip.with_suffix(".tmp.zip")
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file(): archive.write(path, f"{root_name}/{path.relative_to(root).as_posix()}")
        os.replace(temp_zip, args.output_zip)
    with zipfile.ZipFile(args.output_zip) as archive:
        bad = archive.testzip()
        if bad is not None: raise RuntimeError(f"final ZIP CRC failure: {bad}")
    result = {"status": "PASS", "path": str(args.output_zip), "bytes": args.output_zip.stat().st_size, "sha256": sha256_file(args.output_zip)}
    strict_dump(result, args.result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
