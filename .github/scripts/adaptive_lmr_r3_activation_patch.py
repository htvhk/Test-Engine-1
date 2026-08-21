#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

SEARCH = Path("crates/te1-search/src/lib.rs")
CANDIDATE_ID = "0c71255b5047f325e10e61e5d6eda2aae018bb5c"
EXPECTED_BLOB = "29fa07d33d37ef66aa4d93f3c4a8f63e42926000"
EXPECTED_SHA256 = "40d1d501d85bd656977167c150cc3dcbf6b77d30e94c2c65f9c01be87752e174"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    subprocess.run(["git", "merge-base", "--is-ancestor", CANDIDATE_ID, "HEAD"], check=True)
    blob = git("hash-object", str(SEARCH))
    if blob != EXPECTED_BLOB:
        raise SystemExit(f"R3 search blob drift: {blob} != {EXPECTED_BLOB}")
    raw = SEARCH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"R3 search SHA256 drift: {digest} != {EXPECTED_SHA256}")
    text = raw.decode("utf-8")

    text = replace_once(
        text,
        """    pub threads: usize,
    pub hashfull_per_mille: u16,
}

#[derive(Debug)]
struct SharedControl {
""",
        """    pub threads: usize,
    pub hashfull_per_mille: u16,
    pub adaptive_lmr_profile: AdaptiveLmrProfile,
}

#[derive(Debug, Clone, Copy, Default, Serialize, PartialEq, Eq)]
pub struct AdaptiveLmrProfile {
    pub eligible: u64,
    pub pv_eligible: u64,
    pub non_pv_eligible: u64,
    pub bad_history_trigger: u64,
    pub very_bad_history_trigger: u64,
    pub good_history_trigger: u64,
    pub countermove_trigger: u64,
    pub good_counter_overlap: u64,
    pub extra_one: u64,
    pub extra_two: u64,
    pub protect_one: u64,
    pub neutral_delta: u64,
    pub pv_delta_violation: u64,
    pub fixed_hist: [u64; 5],
    pub adaptive_hist: [u64; 5],
    pub history_bins: [u64; 6],
    pub depth_bins: [u64; 5],
    pub move_bins: [u64; 5],
}

impl AdaptiveLmrProfile {
    fn merge_from(&mut self, other: Self) {
        self.eligible = self.eligible.saturating_add(other.eligible);
        self.pv_eligible = self.pv_eligible.saturating_add(other.pv_eligible);
        self.non_pv_eligible = self.non_pv_eligible.saturating_add(other.non_pv_eligible);
        self.bad_history_trigger = self.bad_history_trigger.saturating_add(other.bad_history_trigger);
        self.very_bad_history_trigger = self.very_bad_history_trigger.saturating_add(other.very_bad_history_trigger);
        self.good_history_trigger = self.good_history_trigger.saturating_add(other.good_history_trigger);
        self.countermove_trigger = self.countermove_trigger.saturating_add(other.countermove_trigger);
        self.good_counter_overlap = self.good_counter_overlap.saturating_add(other.good_counter_overlap);
        self.extra_one = self.extra_one.saturating_add(other.extra_one);
        self.extra_two = self.extra_two.saturating_add(other.extra_two);
        self.protect_one = self.protect_one.saturating_add(other.protect_one);
        self.neutral_delta = self.neutral_delta.saturating_add(other.neutral_delta);
        self.pv_delta_violation = self.pv_delta_violation.saturating_add(other.pv_delta_violation);
        for (left, right) in self.fixed_hist.iter_mut().zip(other.fixed_hist) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.adaptive_hist.iter_mut().zip(other.adaptive_hist) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.history_bins.iter_mut().zip(other.history_bins) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.depth_bins.iter_mut().zip(other.depth_bins) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.move_bins.iter_mut().zip(other.move_bins) {
            *left = left.saturating_add(right);
        }
    }
}

#[derive(Debug)]
struct SharedControl {
""",
        "SearchResult/profile definition",
    )

    text = replace_once(
        text,
        """    beta_cutoffs: u64,
    seldepth: u16,
}
""",
        """    beta_cutoffs: u64,
    seldepth: u16,
    adaptive_lmr_profile: AdaptiveLmrProfile,
}
""",
        "WorkerStats profile",
    )

    text = replace_once(
        text,
        """        threads: thread_count,
        hashfull_per_mille: table.hashfull_per_mille(),
    })
""",
        """        threads: thread_count,
        hashfull_per_mille: table.hashfull_per_mille(),
        adaptive_lmr_profile: aggregate.adaptive_lmr_profile,
    })
""",
        "SearchResult profile output",
    )

    text = replace_once(
        text,
        """            total.beta_cutoffs = total.beta_cutoffs.saturating_add(output.stats.beta_cutoffs);
            total.seldepth = total.seldepth.max(output.stats.seldepth);
            total
""",
        """            total.beta_cutoffs = total.beta_cutoffs.saturating_add(output.stats.beta_cutoffs);
            total.seldepth = total.seldepth.max(output.stats.seldepth);
            total.adaptive_lmr_profile.merge_from(output.stats.adaptive_lmr_profile);
            total
""",
        "aggregate profile",
    )

    old_lmr = """    fn late_move_reduction(
        &self,
        depth: i16,
        move_index: usize,
        scored: ScoredMove,
        in_check: bool,
        gives_check: bool,
        pv_node: bool,
    ) -> i16 {
        lmr_reduction(
            self.options,
            depth,
            move_index,
            scored.tactical,
            in_check,
            gives_check,
            pv_node,
            scored.lmr_history,
            scored.countermove,
        )
    }
"""
    new_lmr = """    fn late_move_reduction(
        &mut self,
        depth: i16,
        move_index: usize,
        scored: ScoredMove,
        in_check: bool,
        gives_check: bool,
        pv_node: bool,
    ) -> i16 {
        let reduction = lmr_reduction(
            self.options,
            depth,
            move_index,
            scored.tactical,
            in_check,
            gives_check,
            pv_node,
            scored.lmr_history,
            scored.countermove,
        );
        if self.options.use_adaptive_lmr
            && self.options.use_lmr
            && depth >= 3
            && move_index >= 3
            && !scored.tactical
            && !in_check
            && !gives_check
        {
            let fixed = fixed_lmr_reduction(depth, move_index, pv_node);
            let profile = &mut self.stats.adaptive_lmr_profile;
            profile.eligible = profile.eligible.saturating_add(1);
            if pv_node {
                profile.pv_eligible = profile.pv_eligible.saturating_add(1);
                if reduction != fixed {
                    profile.pv_delta_violation = profile.pv_delta_violation.saturating_add(1);
                }
            } else {
                profile.non_pv_eligible = profile.non_pv_eligible.saturating_add(1);
            }

            let bad = !pv_node && scored.lmr_history <= -(HISTORY_LIMIT / 8);
            let very_bad = !pv_node && scored.lmr_history <= -(HISTORY_LIMIT / 4);
            let good = !pv_node && fixed >= 2 && scored.lmr_history >= HISTORY_LIMIT / 4;
            let counter = !pv_node && fixed >= 2 && scored.countermove;
            if bad {
                profile.bad_history_trigger = profile.bad_history_trigger.saturating_add(1);
            }
            if very_bad {
                profile.very_bad_history_trigger = profile.very_bad_history_trigger.saturating_add(1);
            }
            if good {
                profile.good_history_trigger = profile.good_history_trigger.saturating_add(1);
            }
            if counter {
                profile.countermove_trigger = profile.countermove_trigger.saturating_add(1);
            }
            if good && counter {
                profile.good_counter_overlap = profile.good_counter_overlap.saturating_add(1);
            }

            match reduction - fixed {
                -1 => profile.protect_one = profile.protect_one.saturating_add(1),
                0 => profile.neutral_delta = profile.neutral_delta.saturating_add(1),
                1 => profile.extra_one = profile.extra_one.saturating_add(1),
                2 => profile.extra_two = profile.extra_two.saturating_add(1),
                delta => panic!("unexpected Adaptive LMR R3 delta: {delta}"),
            }

            let fixed_index = usize::try_from(fixed.clamp(0, 4)).unwrap_or(0);
            let adaptive_index = usize::try_from(reduction.clamp(0, 4)).unwrap_or(0);
            profile.fixed_hist[fixed_index] = profile.fixed_hist[fixed_index].saturating_add(1);
            profile.adaptive_hist[adaptive_index] = profile.adaptive_hist[adaptive_index].saturating_add(1);

            let history_bin = if scored.lmr_history <= -(HISTORY_LIMIT / 4) {
                0
            } else if scored.lmr_history <= -(HISTORY_LIMIT / 8) {
                1
            } else if scored.lmr_history < 0 {
                2
            } else if scored.lmr_history < HISTORY_LIMIT / 8 {
                3
            } else if scored.lmr_history < HISTORY_LIMIT / 4 {
                4
            } else {
                5
            };
            profile.history_bins[history_bin] = profile.history_bins[history_bin].saturating_add(1);

            let depth_bin = match depth {
                3..=5 => 0,
                6..=7 => 1,
                8..=9 => 2,
                10..=11 => 3,
                _ => 4,
            };
            profile.depth_bins[depth_bin] = profile.depth_bins[depth_bin].saturating_add(1);
            let move_bin = match move_index {
                3..=7 => 0,
                8..=11 => 1,
                12..=15 => 2,
                16..=23 => 3,
                _ => 4,
            };
            profile.move_bins[move_bin] = profile.move_bins[move_bin].saturating_add(1);
        }
        reduction
    }
"""
    text = replace_once(text, old_lmr, new_lmr, "late_move_reduction instrumentation")

    SEARCH.write_text(text, encoding="utf-8", newline="\n")
    print("ADAPTIVE_LMR_R3_ACTIVATION_PATCH_OK")


if __name__ == "__main__":
    main()
