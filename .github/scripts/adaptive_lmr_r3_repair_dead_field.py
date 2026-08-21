#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

SEARCH = Path("crates/te1-search/src/lib.rs")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = SEARCH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """struct ScoredMove {
    mv: Move,
    score: i32,
    quiet_history: i32,
    lmr_history: i32,
    countermove: bool,
    tactical: bool,
    see: i32,
}
""",
        """struct ScoredMove {
    mv: Move,
    score: i32,
    lmr_history: i32,
    countermove: bool,
    tactical: bool,
    see: i32,
}
""",
        "remove redundant quiet_history field",
    )
    text = replace_once(
        text,
        """            scored.push(ScoredMove {
                mv,
                score,
                quiet_history,
                lmr_history,
                countermove,
                tactical,
                see,
            });
""",
        """            scored.push(ScoredMove {
                mv,
                score,
                lmr_history,
                countermove,
                tactical,
                see,
            });
""",
        "remove redundant quiet_history assignment",
    )
    SEARCH.write_text(text, encoding="utf-8", newline="\n")
    print("ADAPTIVE_LMR_R3_DEAD_FIELD_REPAIR_OK")


if __name__ == "__main__":
    main()
