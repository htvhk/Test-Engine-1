#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

SEARCH = Path("crates/te1-search/src/lib.rs")
R2_ID = "8f38a15919bb65c60c774ea96fd4e7e68d80d36b"
R2_SEARCH_BLOB = "e3eb612a68f9d0abfb78e6b6e5f0df220526fb27"
R2_SEARCH_SHA256 = "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_test(text: str, name: str, replacement: str) -> str:
    marker = f"    #[test]\n    fn {name}() {{"
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"missing Rust test {name}")
    brace = text.find("{", start)
    depth = 0
    end = None
    for index in range(brace, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise SystemExit(f"unterminated Rust test {name}")
    # Preserve exactly one following newline so sequential test replacement stays stable.
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


def main() -> None:
    head = git("rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", R2_ID, head], check=True)
    blob = git("hash-object", str(SEARCH))
    if blob != R2_SEARCH_BLOB:
        raise SystemExit(f"R2 search blob drift: {blob} != {R2_SEARCH_BLOB}")
    raw = SEARCH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != R2_SEARCH_SHA256:
        raise SystemExit(f"R2 search SHA256 drift: {digest} != {R2_SEARCH_SHA256}")
    text = raw.decode("utf-8")

    text = replace_once(
        text,
        """struct ScoredMove {
    mv: Move,
    score: i32,
    quiet_history: i32,
    tactical: bool,
    see: i32,
}
""",
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
        "ScoredMove R3 fields",
    )

    text = replace_once(
        text,
        """            let quiet_history = if !tactical && self.options.use_adaptive_lmr {
                self.histories.quiet_score(board, mv, previous)
            } else {
                0
            };
            let score = if packed == tt_move {
""",
        """            let countermove = !tactical
                && self.options.use_adaptive_lmr
                && previous.is_some_and(|previous| {
                    self.histories.countermove[counter_index(previous.packed)] == packed
                });
            let quiet_history = if !tactical && self.options.use_adaptive_lmr {
                self.histories.quiet_score(board, mv, previous)
            } else {
                0
            };
            // Move ordering deliberately keeps the established +8000 countermove
            // bonus. LMR consumes the underlying signed history separately so a
            // countermove is not accidentally treated as extremely good history.
            let lmr_history = quiet_history - if countermove { 8_000 } else { 0 };
            let score = if packed == tt_move {
""",
        "ordered-move R3 history decomposition",
    )

    text = replace_once(
        text,
        """            scored.push(ScoredMove {
                mv,
                score,
                quiet_history,
                tactical,
                see,
            });
""",
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
        "ScoredMove R3 assignment",
    )

    text = replace_once(
        text,
        """            pv_node,
            scored.quiet_history,
        )
""",
        """            pv_node,
            scored.lmr_history,
            scored.countermove,
        )
""",
        "late_move_reduction R3 arguments",
    )

    adaptive_pattern = re.compile(
        r"fn adaptive_lmr_reduction\(.*?\n}\n\n#\[allow\(clippy::too_many_arguments\)\]\nfn lmr_reduction\(",
        re.DOTALL,
    )
    match = adaptive_pattern.search(text)
    if match is None:
        raise SystemExit("adaptive_lmr_reduction block not found")
    if len(adaptive_pattern.findall(text)) != 1:
        raise SystemExit("adaptive_lmr_reduction block is ambiguous")
    replacement = """fn adaptive_lmr_reduction(
    depth: i16,
    move_index: usize,
    pv_node: bool,
    lmr_history: i32,
    countermove: bool,
) -> i16 {
    // R3 deliberately preserves the proven fixed-LMR base so the new experiment
    // attributes its effect to *actual signed history adaptation*, not to a second
    // simultaneous rewrite of the depth x move-number schedule. The thresholds are
    // fractions of TE1's own bounded HISTORY_LIMIT rather than constants copied from
    // another engine. R2 profiling proved its -HISTORY_LIMIT/4 trigger never fired.
    const BAD_HISTORY_THRESHOLD: i32 = HISTORY_LIMIT / 8;
    const VERY_BAD_HISTORY_THRESHOLD: i32 = HISTORY_LIMIT / 4;
    const GOOD_HISTORY_THRESHOLD: i32 = HISTORY_LIMIT / 4;
    const MAX_REDUCTION: i16 = 4;

    let fixed = fixed_lmr_reduction(depth, move_index, pv_node);
    if pv_node {
        // R1 showed that blanket PV reduction protection can explode the tree.
        // Keep PV behavior exactly on the fixed schedule in this bounded R3 card.
        return fixed;
    }

    let maximum = MAX_REDUCTION.min(depth - 2);
    let mut reduction = fixed;

    if lmr_history <= -BAD_HISTORY_THRESHOLD {
        reduction += 1;
    }
    if lmr_history <= -VERY_BAD_HISTORY_THRESHOLD {
        reduction += 1;
    }

    // Good-history/countermove protection is intentionally restricted to moves
    // that the fixed schedule already reduces by at least two plies. This gives
    // R3 a genuinely signed response without reviving R1's blanket protection.
    if fixed >= 2 && (lmr_history >= GOOD_HISTORY_THRESHOLD || countermove) {
        reduction -= 1;
    }

    reduction.clamp(1, maximum)
}

#[allow(clippy::too_many_arguments)]
fn lmr_reduction("""
    text = text[: match.start()] + replacement + text[match.end() :]

    text = replace_once(
        text,
        """    pv_node: bool,
    quiet_history: i32,
) -> i16 {
""",
        """    pv_node: bool,
    lmr_history: i32,
    countermove: bool,
) -> i16 {
""",
        "lmr_reduction R3 signature",
    )
    text = replace_once(
        text,
        """    if options.use_adaptive_lmr {
        adaptive_lmr_reduction(depth, move_index, pv_node, quiet_history)
    } else {
""",
        """    if options.use_adaptive_lmr {
        adaptive_lmr_reduction(depth, move_index, pv_node, lmr_history, countermove)
    } else {
""",
        "lmr_reduction R3 dispatch",
    )

    text = replace_test(
        text,
        "adaptive_lmr_r2_defaults_off_and_preserves_fixed_schedule",
        """    #[test]
    fn adaptive_lmr_r3_defaults_off_and_preserves_fixed_schedule() {
        let options = SearchOptions::default();
        assert!(!options.use_adaptive_lmr);
        for (depth, index, pv) in [(3, 3, false), (6, 8, false), (8, 12, true), (10, 16, false)] {
            assert_eq!(
                lmr_reduction(
                    options,
                    depth,
                    index,
                    false,
                    false,
                    false,
                    pv,
                    -HISTORY_LIMIT,
                    false,
                ),
                fixed_lmr_reduction(depth, index, pv)
            );
        }
    }""",
    )

    text = replace_test(
        text,
        "adaptive_lmr_r2_never_reduces_less_than_fixed_lmr",
        """    #[test]
    fn adaptive_lmr_r3_is_bounded_and_pv_nodes_keep_fixed_schedule() {
        let options = SearchOptions {
            use_adaptive_lmr: true,
            ..SearchOptions::default()
        };
        for depth in 3..=20 {
            for move_index in 3..=32 {
                for history in [
                    -HISTORY_LIMIT,
                    -HISTORY_LIMIT / 4,
                    -HISTORY_LIMIT / 8,
                    0,
                    HISTORY_LIMIT / 4,
                    HISTORY_LIMIT,
                ] {
                    let non_pv = lmr_reduction(
                        options,
                        depth,
                        move_index,
                        false,
                        false,
                        false,
                        false,
                        history,
                        false,
                    );
                    assert!(non_pv >= 1);
                    assert!(non_pv <= (depth - 2).min(4));

                    let pv = lmr_reduction(
                        options,
                        depth,
                        move_index,
                        false,
                        false,
                        false,
                        true,
                        history,
                        true,
                    );
                    assert_eq!(pv, fixed_lmr_reduction(depth, move_index, true));
                }
            }
        }
    }""",
    )

    text = replace_test(
        text,
        "adaptive_lmr_r2_targets_only_late_non_pv_or_bad_history",
        """    #[test]
    fn adaptive_lmr_r3_uses_signed_history_without_r2_late_only_bias() {
        let options = SearchOptions {
            use_adaptive_lmr: true,
            ..SearchOptions::default()
        };
        let fixed = fixed_lmr_reduction(6, 12, false);
        assert_eq!(fixed, 2);

        let neutral = lmr_reduction(
            options, 6, 12, false, false, false, false, 0, false,
        );
        let bad = lmr_reduction(
            options,
            6,
            12,
            false,
            false,
            false,
            false,
            -(HISTORY_LIMIT / 8),
            false,
        );
        let very_bad = lmr_reduction(
            options,
            6,
            12,
            false,
            false,
            false,
            false,
            -(HISTORY_LIMIT / 4),
            false,
        );
        let good = lmr_reduction(
            options,
            6,
            12,
            false,
            false,
            false,
            false,
            HISTORY_LIMIT / 4,
            false,
        );
        let counter = lmr_reduction(
            options, 6, 12, false, false, false, false, 0, true,
        );

        assert_eq!(neutral, fixed, "R2's unconditional late +1 must be gone");
        assert_eq!(bad, 3);
        assert_eq!(very_bad, 4);
        assert_eq!(good, 1);
        assert_eq!(counter, 1);
    }""",
    )

    text = replace_test(
        text,
        "adaptive_lmr_r2_preserves_tactical_and_check_exclusions",
        """    #[test]
    fn adaptive_lmr_r3_preserves_tactical_and_check_exclusions() {
        let options = SearchOptions {
            use_adaptive_lmr: true,
            ..SearchOptions::default()
        };
        assert_eq!(
            lmr_reduction(
                options,
                10,
                20,
                true,
                false,
                false,
                false,
                -HISTORY_LIMIT,
                false,
            ),
            0
        );
        assert_eq!(
            lmr_reduction(
                options,
                10,
                20,
                false,
                true,
                false,
                false,
                -HISTORY_LIMIT,
                false,
            ),
            0
        );
        assert_eq!(
            lmr_reduction(
                options,
                10,
                20,
                false,
                false,
                true,
                false,
                -HISTORY_LIMIT,
                false,
            ),
            0
        );
    }""",
    )

    text = text.replace(
        "fn adaptive_lmr_r2_is_deterministic_with_production_nmp_enabled()",
        "fn adaptive_lmr_r3_is_deterministic_with_production_nmp_enabled()",
        1,
    )

    # The renamed deterministic test does not call lmr_reduction directly; all other
    # direct test calls are R3-shaped after the replacements above.
    if "adaptive_lmr_r2_" in text:
        raise SystemExit("stale adaptive_lmr_r2 test name remains after R3 patch")
    if "adaptive_lmr_reduction(depth, move_index, pv_node, quiet_history)" in text:
        raise SystemExit("stale R2 adaptive dispatch remains")

    SEARCH.write_text(text, encoding="utf-8", newline="\n")
    print("ADAPTIVE_LMR_R3_APPLY_OK")


if __name__ == "__main__":
    main()
