#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

ENGINE = Path("crates/te1-engine/src/main.rs")
EXPECTED_BLOB = "a7b6e91ae2404917e0874e2a4356e078a9ed3f24"
EXPECTED_SHA256 = "15d1c59fac7e3dc9c5b94e61a56f7a75c9da373ec00b57b5157be922fdbdd6c2"
CANDIDATE = "8f38a15919bb65c60c774ea96fd4e7e68d80d36b"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    head = git("rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", CANDIDATE, head], check=True)
    blob = git("hash-object", str(ENGINE))
    if blob != EXPECTED_BLOB:
        raise SystemExit(f"engine blob drift: {blob} != {EXPECTED_BLOB}")
    raw = ENGINE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"engine SHA256 drift: {digest} != {EXPECTED_SHA256}")
    text = raw.decode("utf-8")
    old = """            use_lmr: self.use_lmr,
            use_adaptive_lmr: self.use_adaptive_lmr,
            use_see_pruning: self.use_see_pruning,
"""
    new = """            use_lmr: self.use_lmr,
            use_adaptive_lmr: self.use_adaptive_lmr,
            profile_adaptive_lmr: false,
            use_see_pruning: self.use_see_pruning,
"""
    if text.count(old) != 1:
        raise SystemExit(f"SearchOptions engine plumbing match count={text.count(old)}")
    text = text.replace(old, new, 1)
    ENGINE.write_text(text, encoding="utf-8", newline="\n")
    print("ADAPTIVE_LMR_R2_PROFILE_ENGINE_PATCH_OK")


if __name__ == "__main__":
    main()
