#!/usr/bin/env python3
"""Hydrate compile-time NNUE assets from canonical tracked repository assets.

Generated files are intentionally not canonical source-of-truth files. They are
recreated deterministically from networks/ and fixtures/ before Rust builds.
"""
from pathlib import Path
import hashlib, shutil

ROOT = Path(__file__).resolve().parents[1]
NET128 = ROOT / "networks/k32-w128-h32-crelu.te1nn"
REF512 = ROOT / "fixtures/nnue-reference/k32-w128-h32-crelu.rust-reference-512.jsonl"
TARGETS = [
    ROOT / "crates/te1-nnue/fixtures/network.te1nn",
    ROOT / "crates/te1-eval/networks/default.te1nn",
]
REF_TARGET = ROOT / "crates/te1-nnue/fixtures/reference-vectors.jsonl"
EXPECTED = "9cba80ed00f31946b54179d2ed63b4639ef3ba62d1a96ba2bca4fca4fc846974"

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

if sha256(NET128) != EXPECTED:
    raise SystemExit("canonical NNUE-128 hash mismatch")
for target in TARGETS:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(NET128, target)
lines = REF512.read_text(encoding='utf-8').splitlines()
if len(lines) != 512:
    raise SystemExit(f"expected 512 reference vectors, got {len(lines)}")
REF_TARGET.parent.mkdir(parents=True, exist_ok=True)
REF_TARGET.write_text("\n".join(lines[:256]) + "\n", encoding='utf-8')
print("TE1 generated Rust NNUE assets: PASS")
print("NNUE-128:", EXPECTED)
print("fixture reference rows: 256")
