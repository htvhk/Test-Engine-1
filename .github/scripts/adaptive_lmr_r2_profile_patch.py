#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

SEARCH = Path("crates/te1-search/src/lib.rs")
EXPECTED_BLOB = "e3eb612a68f9d0abfb78e6b6e5f0df220526fb27"
EXPECTED_SHA256 = "f97f81735d2df28c70f8763cd876aea1dd008a141c3910ea277e4dc5318f2c4e"
CANDIDATE = "8f38a15919bb65c60c774ea96fd4e7e68d80d36b"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    head = git("rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", CANDIDATE, head], check=True)
    observed_blob = git("hash-object", str(SEARCH))
    if observed_blob != EXPECTED_BLOB:
        raise SystemExit(f"search blob drift: {observed_blob} != {EXPECTED_BLOB}")
    raw = SEARCH.read_bytes()
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if observed_sha256 != EXPECTED_SHA256:
        raise SystemExit(f"search SHA256 drift: {observed_sha256} != {EXPECTED_SHA256}")
    text = raw.decode("utf-8")

    text = replace_once(
        text,
        "    pub use_adaptive_lmr: bool,\n    pub use_see_pruning: bool,",
        "    pub use_adaptive_lmr: bool,\n    pub profile_adaptive_lmr: bool,\n    pub use_see_pruning: bool,",
        "SearchOptions field",
    )
    text = replace_once(
        text,
        "            use_adaptive_lmr: false,\n            use_see_pruning: true,",
        "            use_adaptive_lmr: false,\n            profile_adaptive_lmr: false,\n            use_see_pruning: true,",
        "SearchOptions default",
    )
    text = replace_once(
        text,
        "    pub hashfull_per_mille: u16,\n}\n\n#[derive(Debug)]\nstruct SharedControl {",
        """    pub hashfull_per_mille: u16,
    pub adaptive_lmr_profile: AdaptiveLmrProfile,
}

#[derive(Debug, Clone, Copy, Default, Serialize, PartialEq, Eq)]
pub struct AdaptiveLmrProfile {
    pub eligible_quiet_moves: u64,
    pub pv_eligible: u64,
    pub non_pv_eligible: u64,
    pub current_r2_late_trigger: u64,
    pub current_r2_poor_history_trigger: u64,
    pub current_r2_both_triggers: u64,
    pub current_r2_extra_reduction: u64,
    pub current_r2_saturated_reduction: u64,
    pub countermove_eligible: u64,
    pub countermove_fixed_ge2: u64,
    pub total_history_ge4096_fixed_ge2: u64,
    pub total_history_ge8192_fixed_ge2: u64,
    pub base_history_ge4096_fixed_ge2: u64,
    pub base_history_ge8192_fixed_ge2: u64,
    pub fixed_reduction_hist: [u64; 5],
    pub adaptive_reduction_hist: [u64; 5],
    pub total_history_bins: [u64; 6],
    pub base_history_bins: [u64; 6],
    pub depth_bins: [u64; 5],
    pub move_index_bins: [u64; 5],
    pub depth_move_matrix: [u64; 25],
}

impl AdaptiveLmrProfile {
    fn merge_from(&mut self, other: &Self) {
        self.eligible_quiet_moves = self.eligible_quiet_moves.saturating_add(other.eligible_quiet_moves);
        self.pv_eligible = self.pv_eligible.saturating_add(other.pv_eligible);
        self.non_pv_eligible = self.non_pv_eligible.saturating_add(other.non_pv_eligible);
        self.current_r2_late_trigger = self.current_r2_late_trigger.saturating_add(other.current_r2_late_trigger);
        self.current_r2_poor_history_trigger = self.current_r2_poor_history_trigger.saturating_add(other.current_r2_poor_history_trigger);
        self.current_r2_both_triggers = self.current_r2_both_triggers.saturating_add(other.current_r2_both_triggers);
        self.current_r2_extra_reduction = self.current_r2_extra_reduction.saturating_add(other.current_r2_extra_reduction);
        self.current_r2_saturated_reduction = self.current_r2_saturated_reduction.saturating_add(other.current_r2_saturated_reduction);
        self.countermove_eligible = self.countermove_eligible.saturating_add(other.countermove_eligible);
        self.countermove_fixed_ge2 = self.countermove_fixed_ge2.saturating_add(other.countermove_fixed_ge2);
        self.total_history_ge4096_fixed_ge2 = self.total_history_ge4096_fixed_ge2.saturating_add(other.total_history_ge4096_fixed_ge2);
        self.total_history_ge8192_fixed_ge2 = self.total_history_ge8192_fixed_ge2.saturating_add(other.total_history_ge8192_fixed_ge2);
        self.base_history_ge4096_fixed_ge2 = self.base_history_ge4096_fixed_ge2.saturating_add(other.base_history_ge4096_fixed_ge2);
        self.base_history_ge8192_fixed_ge2 = self.base_history_ge8192_fixed_ge2.saturating_add(other.base_history_ge8192_fixed_ge2);
        for (left, right) in self.fixed_reduction_hist.iter_mut().zip(other.fixed_reduction_hist) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.adaptive_reduction_hist.iter_mut().zip(other.adaptive_reduction_hist) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.total_history_bins.iter_mut().zip(other.total_history_bins) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.base_history_bins.iter_mut().zip(other.base_history_bins) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.depth_bins.iter_mut().zip(other.depth_bins) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.move_index_bins.iter_mut().zip(other.move_index_bins) {
            *left = left.saturating_add(right);
        }
        for (left, right) in self.depth_move_matrix.iter_mut().zip(other.depth_move_matrix) {
            *left = left.saturating_add(right);
        }
    }
}

#[derive(Debug)]
struct SharedControl {""",
        "AdaptiveLmrProfile definition",
    )
    text = replace_once(
        text,
        "    beta_cutoffs: u64,\n    seldepth: u16,\n}",
        "    beta_cutoffs: u64,\n    seldepth: u16,\n    adaptive_lmr_profile: AdaptiveLmrProfile,\n}",
        "WorkerStats profile",
    )
    text = replace_once(
        text,
        "    quiet_history: i32,\n    tactical: bool,\n    see: i32,",
        "    quiet_history: i32,\n    base_quiet_history: i32,\n    countermove: bool,\n    tactical: bool,\n    see: i32,",
        "ScoredMove profiling fields",
    )
    text = replace_once(
        text,
        "        threads: thread_count,\n        hashfull_per_mille: table.hashfull_per_mille(),\n    })",
        "        threads: thread_count,\n        hashfull_per_mille: table.hashfull_per_mille(),\n        adaptive_lmr_profile: aggregate.adaptive_lmr_profile,\n    })",
        "SearchResult profile output",
    )
    text = replace_once(
        text,
        "            total.seldepth = total.seldepth.max(output.stats.seldepth);\n            total",
        "            total.seldepth = total.seldepth.max(output.stats.seldepth);\n            total.adaptive_lmr_profile.merge_from(&output.stats.adaptive_lmr_profile);\n            total",
        "aggregate profile",
    )
    text = replace_once(
        text,
        """            let quiet_history = if !tactical && self.options.use_adaptive_lmr {
                self.histories.quiet_score(board, mv, previous)
            } else {
                0
            };
            let score = if packed == tt_move {""",
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
            let base_quiet_history = quiet_history - if countermove { 8_000 } else { 0 };
            let score = if packed == tt_move {""",
        "ordered move history components",
    )
    text = replace_once(
        text,
        "                quiet_history,\n                tactical,\n                see,",
        "                quiet_history,\n                base_quiet_history,\n                countermove,\n                tactical,\n                see,",
        "ScoredMove assignment",
    )
    text = replace_once(
        text,
        """    fn late_move_reduction(
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
            scored.quiet_history,
        )
    }
""",
        """    fn record_adaptive_lmr_profile(
        &mut self,
        depth: i16,
        move_index: usize,
        scored: ScoredMove,
        in_check: bool,
        gives_check: bool,
        pv_node: bool,
        adaptive_reduction: i16,
    ) {
        if !self.options.use_lmr
            || !self.options.use_adaptive_lmr
            || depth < 3
            || move_index < 3
            || scored.tactical
            || in_check
            || gives_check
        {
            return;
        }
        let fixed = fixed_lmr_reduction(depth, move_index, pv_node);
        let profile = &mut self.stats.adaptive_lmr_profile;
        profile.eligible_quiet_moves = profile.eligible_quiet_moves.saturating_add(1);
        if pv_node {
            profile.pv_eligible = profile.pv_eligible.saturating_add(1);
        } else {
            profile.non_pv_eligible = profile.non_pv_eligible.saturating_add(1);
        }
        let late_trigger = !pv_node && depth >= 6 && move_index >= 12;
        let poor_history = !pv_node && scored.quiet_history <= -(HISTORY_LIMIT / 4);
        if late_trigger {
            profile.current_r2_late_trigger = profile.current_r2_late_trigger.saturating_add(1);
        }
        if poor_history {
            profile.current_r2_poor_history_trigger = profile.current_r2_poor_history_trigger.saturating_add(1);
        }
        if late_trigger && poor_history {
            profile.current_r2_both_triggers = profile.current_r2_both_triggers.saturating_add(1);
        }
        if adaptive_reduction > fixed {
            profile.current_r2_extra_reduction = profile.current_r2_extra_reduction.saturating_add(1);
        }
        if adaptive_reduction == (depth - 2).min(4) {
            profile.current_r2_saturated_reduction = profile.current_r2_saturated_reduction.saturating_add(1);
        }
        if scored.countermove {
            profile.countermove_eligible = profile.countermove_eligible.saturating_add(1);
            if fixed >= 2 {
                profile.countermove_fixed_ge2 = profile.countermove_fixed_ge2.saturating_add(1);
            }
        }
        if !pv_node && fixed >= 2 {
            if scored.quiet_history >= HISTORY_LIMIT / 4 {
                profile.total_history_ge4096_fixed_ge2 = profile.total_history_ge4096_fixed_ge2.saturating_add(1);
            }
            if scored.quiet_history >= HISTORY_LIMIT / 2 {
                profile.total_history_ge8192_fixed_ge2 = profile.total_history_ge8192_fixed_ge2.saturating_add(1);
            }
            if scored.base_quiet_history >= HISTORY_LIMIT / 4 {
                profile.base_history_ge4096_fixed_ge2 = profile.base_history_ge4096_fixed_ge2.saturating_add(1);
            }
            if scored.base_quiet_history >= HISTORY_LIMIT / 2 {
                profile.base_history_ge8192_fixed_ge2 = profile.base_history_ge8192_fixed_ge2.saturating_add(1);
            }
        }
        let fixed_index = usize::try_from(fixed.clamp(0, 4)).unwrap_or(0);
        let adaptive_index = usize::try_from(adaptive_reduction.clamp(0, 4)).unwrap_or(0);
        profile.fixed_reduction_hist[fixed_index] = profile.fixed_reduction_hist[fixed_index].saturating_add(1);
        profile.adaptive_reduction_hist[adaptive_index] = profile.adaptive_reduction_hist[adaptive_index].saturating_add(1);
        let total_history_bin = history_profile_bin(scored.quiet_history);
        let base_history_bin = history_profile_bin(scored.base_quiet_history);
        profile.total_history_bins[total_history_bin] = profile.total_history_bins[total_history_bin].saturating_add(1);
        profile.base_history_bins[base_history_bin] = profile.base_history_bins[base_history_bin].saturating_add(1);
        let depth_bin = lmr_depth_profile_bin(depth);
        let move_bin = lmr_move_profile_bin(move_index);
        profile.depth_bins[depth_bin] = profile.depth_bins[depth_bin].saturating_add(1);
        profile.move_index_bins[move_bin] = profile.move_index_bins[move_bin].saturating_add(1);
        let matrix_index = depth_bin * 5 + move_bin;
        profile.depth_move_matrix[matrix_index] = profile.depth_move_matrix[matrix_index].saturating_add(1);
    }

    fn late_move_reduction(
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
            scored.quiet_history,
        );
        if self.options.profile_adaptive_lmr {
            self.record_adaptive_lmr_profile(
                depth,
                move_index,
                scored,
                in_check,
                gives_check,
                pv_node,
                reduction,
            );
        }
        reduction
    }
""",
        "late move profiling",
    )
    text = replace_once(
        text,
        """fn history_bonus(depth: i16) -> i32 {
    let depth = i32::from(depth.max(1));
""",
        """fn history_profile_bin(value: i32) -> usize {
    if value <= -(HISTORY_LIMIT / 2) {
        0
    } else if value <= -(HISTORY_LIMIT / 4) {
        1
    } else if value < 0 {
        2
    } else if value < HISTORY_LIMIT / 4 {
        3
    } else if value < HISTORY_LIMIT / 2 {
        4
    } else {
        5
    }
}

fn lmr_depth_profile_bin(depth: i16) -> usize {
    match depth {
        3..=5 => 0,
        6..=7 => 1,
        8..=9 => 2,
        10..=11 => 3,
        _ => 4,
    }
}

fn lmr_move_profile_bin(move_index: usize) -> usize {
    match move_index {
        3..=7 => 0,
        8..=11 => 1,
        12..=15 => 2,
        16..=23 => 3,
        _ => 4,
    }
}

fn history_bonus(depth: i16) -> i32 {
    let depth = i32::from(depth.max(1));
""",
        "profile bin helpers",
    )
    text = replace_once(
        text,
        """    #[test]
    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {""",
        """    #[test]
    fn adaptive_lmr_profile_is_search_decision_neutral() {
        let fen = "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1";
        let game = Te1Game::from_fen(fen).unwrap();
        let run_profile = |profile_adaptive_lmr| {
            search(
                &game,
                SearchLimits {
                    depth: Some(8),
                    ..SearchLimits::default()
                },
                Arc::new(AtomicBool::new(false)),
                Arc::new(TranspositionTable::with_megabytes(4)),
                SearchOptions {
                    use_null_move_pruning: true,
                    use_adaptive_lmr: true,
                    profile_adaptive_lmr,
                    ..SearchOptions::default()
                },
            )
            .unwrap()
        };
        let off = run_profile(false);
        let on = run_profile(true);
        assert_eq!(off.best_move, on.best_move);
        assert_eq!(off.score_cp, on.score_cp);
        assert_eq!(off.depth, on.depth);
        assert_eq!(off.nodes, on.nodes);
        assert_eq!(off.qnodes, on.qnodes);
        assert_eq!(off.tt_hits, on.tt_hits);
        assert_eq!(off.beta_cutoffs, on.beta_cutoffs);
        assert_eq!(off.pv, on.pv);
        assert_eq!(off.adaptive_lmr_profile, AdaptiveLmrProfile::default());
        assert!(on.adaptive_lmr_profile.eligible_quiet_moves > 0);
        assert_eq!(
            on.adaptive_lmr_profile.eligible_quiet_moves,
            on.adaptive_lmr_profile.pv_eligible + on.adaptive_lmr_profile.non_pv_eligible
        );
        assert_eq!(
            on.adaptive_lmr_profile.eligible_quiet_moves,
            on.adaptive_lmr_profile.fixed_reduction_hist.iter().sum::<u64>()
        );
        assert_eq!(
            on.adaptive_lmr_profile.eligible_quiet_moves,
            on.adaptive_lmr_profile.adaptive_reduction_hist.iter().sum::<u64>()
        );
        assert_eq!(
            on.adaptive_lmr_profile.eligible_quiet_moves,
            on.adaptive_lmr_profile.total_history_bins.iter().sum::<u64>()
        );
        assert_eq!(
            on.adaptive_lmr_profile.eligible_quiet_moves,
            on.adaptive_lmr_profile.base_history_bins.iter().sum::<u64>()
        );
    }

    #[test]
    fn adaptive_lmr_profile_bins_have_frozen_boundaries() {
        assert_eq!(history_profile_bin(-8_193), 0);
        assert_eq!(history_profile_bin(-8_192), 0);
        assert_eq!(history_profile_bin(-4_096), 1);
        assert_eq!(history_profile_bin(-1), 2);
        assert_eq!(history_profile_bin(0), 3);
        assert_eq!(history_profile_bin(4_095), 3);
        assert_eq!(history_profile_bin(4_096), 4);
        assert_eq!(history_profile_bin(8_191), 4);
        assert_eq!(history_profile_bin(8_192), 5);
        assert_eq!(lmr_depth_profile_bin(3), 0);
        assert_eq!(lmr_depth_profile_bin(6), 1);
        assert_eq!(lmr_depth_profile_bin(8), 2);
        assert_eq!(lmr_depth_profile_bin(10), 3);
        assert_eq!(lmr_depth_profile_bin(12), 4);
        assert_eq!(lmr_move_profile_bin(3), 0);
        assert_eq!(lmr_move_profile_bin(8), 1);
        assert_eq!(lmr_move_profile_bin(12), 2);
        assert_eq!(lmr_move_profile_bin(16), 3);
        assert_eq!(lmr_move_profile_bin(24), 4);
    }

    #[test]
    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {""",
        "profiling regressions",
    )

    SEARCH.write_text(text, encoding="utf-8", newline="\n")
    if "profile_adaptive_lmr" not in text or "AdaptiveLmrProfile" not in text:
        raise SystemExit("profiling patch marker missing after write")
    print("ADAPTIVE_LMR_R2_PROFILE_PATCH_OK")


if __name__ == "__main__":
    main()
