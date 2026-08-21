#!/usr/bin/env python3
from pathlib import Path

SEARCH = Path("crates/te1-search/src/lib.rs")
OLD = "    fn record_adaptive_lmr_profile(\n"
NEW = "    #[allow(clippy::too_many_arguments)]\n    fn record_adaptive_lmr_profile(\n"

text = SEARCH.read_text(encoding="utf-8")
count = text.count(OLD)
if count != 1:
    raise SystemExit(f"expected exactly one profiling function match, found {count}")
text = text.replace(OLD, NEW, 1)
SEARCH.write_text(text, encoding="utf-8", newline="\n")
print("ADAPTIVE_LMR_R2_PROFILE_LINT_PATCH_OK")
