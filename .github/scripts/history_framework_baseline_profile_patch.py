#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

BASE_COMMIT = "1e750218f43fa5129cb82f19b107555a1343d878"
SEARCH = Path("crates/te1-search/src/lib.rs")
ENGINE = Path("crates/te1-engine/src/main.rs")
SEARCH_BLOB = "cd393b65085cdfa1b327f00f23c69f61763fcb2e"
SEARCH_SHA256 = "943cd3320e0538b5e30763ebc1858cbe0fd53d94b6f9c31a1fc0b6364e397a26"
ENGINE_BLOB = "b2facdb682c7841e1b3c77db43dffdcc7fb59fcd"
ENGINE_SHA256 = "61c0b03d3bd274f281a5c375cd0f3b3dd37f48adca4c2fcc42e477471e867f7d"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def authenticate(path: Path, expected_blob: str, expected_sha256: str, label: str) -> None:
    blob = git("hash-object", str(path))
    if blob != expected_blob:
        raise SystemExit(f"{label} blob drift: {blob} != {expected_blob}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise SystemExit(f"{label} SHA256 drift: {digest} != {expected_sha256}")


def main() -> None:
    subprocess.run(["git", "merge-base", "--is-ancestor", BASE_COMMIT, "HEAD"], check=True)
    authenticate(SEARCH, SEARCH_BLOB, SEARCH_SHA256, "search")
    authenticate(ENGINE, ENGINE_BLOB, ENGINE_SHA256, "engine")

    text = SEARCH.read_text(encoding="utf-8")

    text = replace_once(
        text,
        """pub struct SearchOptions {
    pub threads: usize,
    pub deterministic: bool,
    pub use_lmr: bool,
    pub use_see_pruning: bool,
    pub use_null_move_pruning: bool,
}
""",
        """pub struct SearchOptions {
    pub threads: usize,
    pub deterministic: bool,
    pub use_lmr: bool,
    pub use_see_pruning: bool,
    pub use_null_move_pruning: bool,
    pub profile_history_framework: bool,
}
""",
        "SearchOptions profile flag",
    )

    text = replace_once(
        text,
        """            use_lmr: true,
            use_see_pruning: true,
            use_null_move_pruning: false,
        }
""",
        """            use_lmr: true,
            use_see_pruning: true,
            use_null_move_pruning: false,
            profile_history_framework: false,
        }
""",
        "SearchOptions default profile flag",
    )

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
    pub history_profile: HistoryProfile,
}

#[derive(Debug, Clone, Copy, Default, Serialize, PartialEq, Eq)]
pub struct HistoryProfile {
    pub quiet_ordering_reads: u64,
    pub continuation_reads: u64,
    pub capture_history_reads: u64,
    pub countermove_matches: u64,
    pub quiet_near_saturation_reads: u64,
    pub quiet_exact_saturation_reads: u64,
    pub continuation_near_saturation_reads: u64,
    pub continuation_exact_saturation_reads: u64,
    pub capture_near_saturation_reads: u64,
    pub capture_exact_saturation_reads: u64,
    pub quiet_positive_updates: u64,
    pub quiet_negative_updates: u64,
    pub continuation_positive_updates: u64,
    pub continuation_negative_updates: u64,
    pub capture_positive_updates: u64,
    pub capture_negative_updates: u64,
    pub quiet_cutoffs: u64,
    pub capture_cutoffs: u64,
    pub killer_updates: u64,
    pub countermove_records: u64,
    pub quiet_search_traffic: u64,
    pub quiet_component_bins: [u64; 8],
    pub continuation_component_bins: [u64; 8],
    pub quiet_base_bins: [u64; 8],
    pub quiet_ordering_bins: [u64; 8],
    pub capture_component_bins: [u64; 8],
    pub positive_update_depth_bins: [u64; 8],
    pub negative_update_depth_bins: [u64; 8],
    pub quiet_traffic_depth_bins: [u64; 5],
    pub quiet_traffic_move_bins: [u64; 5],
    pub quiet_traffic_depth_move_matrix: [u64; 25],
}

impl HistoryProfile {
    fn merge_from(&mut self, other: Self) {
        self.quiet_ordering_reads = self.quiet_ordering_reads.saturating_add(other.quiet_ordering_reads);
        self.continuation_reads = self.continuation_reads.saturating_add(other.continuation_reads);
        self.capture_history_reads = self.capture_history_reads.saturating_add(other.capture_history_reads);
        self.countermove_matches = self.countermove_matches.saturating_add(other.countermove_matches);
        self.quiet_near_saturation_reads = self.quiet_near_saturation_reads.saturating_add(other.quiet_near_saturation_reads);
        self.quiet_exact_saturation_reads = self.quiet_exact_saturation_reads.saturating_add(other.quiet_exact_saturation_reads);
        self.continuation_near_saturation_reads = self.continuation_near_saturation_reads.saturating_add(other.continuation_near_saturation_reads);
        self.continuation_exact_saturation_reads = self.continuation_exact_saturation_reads.saturating_add(other.continuation_exact_saturation_reads);
        self.capture_near_saturation_reads = self.capture_near_saturation_reads.saturating_add(other.capture_near_saturation_reads);
        self.capture_exact_saturation_reads = self.capture_exact_saturation_reads.saturating_add(other.capture_exact_saturation_reads);
        self.quiet_positive_updates = self.quiet_positive_updates.saturating_add(other.quiet_positive_updates);
        self.quiet_negative_updates = self.quiet_negative_updates.saturating_add(other.quiet_negative_updates);
        self.continuation_positive_updates = self.continuation_positive_updates.saturating_add(other.continuation_positive_updates);
        self.continuation_negative_updates = self.continuation_negative_updates.saturating_add(other.continuation_negative_updates);
        self.capture_positive_updates = self.capture_positive_updates.saturating_add(other.capture_positive_updates);
        self.capture_negative_updates = self.capture_negative_updates.saturating_add(other.capture_negative_updates);
        self.quiet_cutoffs = self.quiet_cutoffs.saturating_add(other.quiet_cutoffs);
        self.capture_cutoffs = self.capture_cutoffs.saturating_add(other.capture_cutoffs);
        self.killer_updates = self.killer_updates.saturating_add(other.killer_updates);
        self.countermove_records = self.countermove_records.saturating_add(other.countermove_records);
        self.quiet_search_traffic = self.quiet_search_traffic.saturating_add(other.quiet_search_traffic);
        for (left, right) in self.quiet_component_bins.iter_mut().zip(other.quiet_component_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.continuation_component_bins.iter_mut().zip(other.continuation_component_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.quiet_base_bins.iter_mut().zip(other.quiet_base_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.quiet_ordering_bins.iter_mut().zip(other.quiet_ordering_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.capture_component_bins.iter_mut().zip(other.capture_component_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.positive_update_depth_bins.iter_mut().zip(other.positive_update_depth_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.negative_update_depth_bins.iter_mut().zip(other.negative_update_depth_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.quiet_traffic_depth_bins.iter_mut().zip(other.quiet_traffic_depth_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.quiet_traffic_move_bins.iter_mut().zip(other.quiet_traffic_move_bins) { *left = left.saturating_add(right); }
        for (left, right) in self.quiet_traffic_depth_move_matrix.iter_mut().zip(other.quiet_traffic_depth_move_matrix) { *left = left.saturating_add(right); }
    }
}

#[derive(Debug)]
struct SharedControl {
""",
        "HistoryProfile definition",
    )

    text = replace_once(
        text,
        """    beta_cutoffs: u64,
    seldepth: u16,
}
""",
        """    beta_cutoffs: u64,
    seldepth: u16,
    history_profile: HistoryProfile,
}
""",
        "WorkerStats history profile",
    )

    text = replace_once(
        text,
        """        threads: thread_count,
        hashfull_per_mille: table.hashfull_per_mille(),
    })
""",
        """        threads: thread_count,
        hashfull_per_mille: table.hashfull_per_mille(),
        history_profile: aggregate.history_profile,
    })
""",
        "SearchResult history profile output",
    )

    text = replace_once(
        text,
        """            total.beta_cutoffs = total.beta_cutoffs.saturating_add(output.stats.beta_cutoffs);
            total.seldepth = total.seldepth.max(output.stats.seldepth);
            total
""",
        """            total.beta_cutoffs = total.beta_cutoffs.saturating_add(output.stats.beta_cutoffs);
            total.seldepth = total.seldepth.max(output.stats.seldepth);
            total.history_profile.merge_from(output.stats.history_profile);
            total
""",
        "aggregate history profile",
    )

    text = replace_once(
        text,
        """            let undo = position.make_move(scored.mv);
            let gives_check = !position.board().checkers().is_empty();
""",
        """            if self.options.profile_history_framework && !scored.tactical {
                let depth_bin = history_traffic_depth_bin(depth);
                let move_bin = history_traffic_move_bin(index);
                let profile = &mut self.stats.history_profile;
                profile.quiet_search_traffic = profile.quiet_search_traffic.saturating_add(1);
                profile.quiet_traffic_depth_bins[depth_bin] = profile.quiet_traffic_depth_bins[depth_bin].saturating_add(1);
                profile.quiet_traffic_move_bins[move_bin] = profile.quiet_traffic_move_bins[move_bin].saturating_add(1);
                let matrix = depth_bin * 5 + move_bin;
                profile.quiet_traffic_depth_move_matrix[matrix] = profile.quiet_traffic_depth_move_matrix[matrix].saturating_add(1);
            }
            let undo = position.make_move(scored.mv);
            let gives_check = !position.board().checkers().is_empty();
""",
        "quiet search traffic instrumentation",
    )

    text = replace_once(
        text,
        """            if alpha >= beta {
                self.stats.beta_cutoffs = self.stats.beta_cutoffs.saturating_add(1);
                if scored.tactical {
                    self.histories.record_capture_cutoff(
""",
        """            if alpha >= beta {
                self.stats.beta_cutoffs = self.stats.beta_cutoffs.saturating_add(1);
                if self.options.profile_history_framework {
                    self.record_history_cutoff_profile(
                        position.board(),
                        scored,
                        previous,
                        ply,
                        depth,
                        &searched_quiets,
                        &searched_captures,
                    );
                }
                if scored.tactical {
                    self.histories.record_capture_cutoff(
""",
        "cutoff profile call",
    )

    text = replace_once(
        text,
        """    fn ordered_moves(
        &self,
        board: &Board,
""",
        """    fn ordered_moves(
        &mut self,
        board: &Board,
""",
        "ordered_moves mutable diagnostic receiver",
    )

    text = replace_once(
        text,
        """            let score = if packed == tt_move {
                30_000_000
            } else if tactical && see >= 0 {
                20_000_000
                    + see.saturating_mul(16)
                    + self.histories.capture_score(board, mv)
                    + promotion_gain(mv).saturating_mul(8)
            } else if promotion {
                18_000_000 + promotion_gain(mv).saturating_mul(8)
            } else if ply < MAX_PLY && packed == self.histories.killers[ply][0] {
                15_000_000
            } else if ply < MAX_PLY && packed == self.histories.killers[ply][1] {
                14_000_000
            } else if tactical {
                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)
            } else {
                self.histories.quiet_score(board, mv, previous)
            };
            scored.push(ScoredMove {
""",
        """            let score = if packed == tt_move {
                30_000_000
            } else if tactical && see >= 0 {
                20_000_000
                    + see.saturating_mul(16)
                    + self.histories.capture_score(board, mv)
                    + promotion_gain(mv).saturating_mul(8)
            } else if promotion {
                18_000_000 + promotion_gain(mv).saturating_mul(8)
            } else if ply < MAX_PLY && packed == self.histories.killers[ply][0] {
                15_000_000
            } else if ply < MAX_PLY && packed == self.histories.killers[ply][1] {
                14_000_000
            } else if tactical {
                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)
            } else {
                self.histories.quiet_score(board, mv, previous)
            };
            if self.options.profile_history_framework {
                let killer = ply < MAX_PLY
                    && (packed == self.histories.killers[ply][0]
                        || packed == self.histories.killers[ply][1]);
                if !tactical && packed != tt_move && !killer {
                    self.record_quiet_history_read(board, mv, previous);
                } else if tactical && packed != tt_move && (see >= 0 || !promotion) {
                    self.record_capture_history_read(board, mv);
                }
            }
            scored.push(ScoredMove {
""",
        "ordered-move history read instrumentation",
    )

    marker = """    fn late_move_reduction(
        &self,
"""
    methods = """    fn record_quiet_history_read(
        &mut self,
        board: &Board,
        mv: Move,
        previous: Option<MoveContext>,
    ) {
        let side = color_index(board.side_to_move());
        let quiet = i32::from(self.histories.quiet[quiet_index(side, mv)]);
        let mut continuation = 0i32;
        let mut has_continuation = false;
        let mut countermove = false;
        if let (Some(previous), Some(piece)) = (previous, board.piece_on(mv.from)) {
            continuation = i32::from(
                self.histories.continuation[
                    continuation_index(previous.piece, previous.to, piece, mv.to as usize)
                ],
            );
            has_continuation = true;
            countermove = self.histories.countermove[counter_index(previous.packed)]
                == PackedMove::from_move(mv);
        }
        let base = quiet.saturating_add(continuation);
        let ordering = base.saturating_add(if countermove { 8_000 } else { 0 });
        let profile = &mut self.stats.history_profile;
        profile.quiet_ordering_reads = profile.quiet_ordering_reads.saturating_add(1);
        profile.quiet_component_bins[history_value_bin(quiet)] =
            profile.quiet_component_bins[history_value_bin(quiet)].saturating_add(1);
        profile.quiet_base_bins[history_value_bin(base)] =
            profile.quiet_base_bins[history_value_bin(base)].saturating_add(1);
        profile.quiet_ordering_bins[history_value_bin(ordering)] =
            profile.quiet_ordering_bins[history_value_bin(ordering)].saturating_add(1);
        if quiet.abs() >= HISTORY_LIMIT * 3 / 4 {
            profile.quiet_near_saturation_reads = profile.quiet_near_saturation_reads.saturating_add(1);
        }
        if quiet.abs() >= HISTORY_LIMIT {
            profile.quiet_exact_saturation_reads = profile.quiet_exact_saturation_reads.saturating_add(1);
        }
        if has_continuation {
            profile.continuation_reads = profile.continuation_reads.saturating_add(1);
            profile.continuation_component_bins[history_value_bin(continuation)] =
                profile.continuation_component_bins[history_value_bin(continuation)].saturating_add(1);
            if continuation.abs() >= HISTORY_LIMIT * 3 / 4 {
                profile.continuation_near_saturation_reads = profile.continuation_near_saturation_reads.saturating_add(1);
            }
            if continuation.abs() >= HISTORY_LIMIT {
                profile.continuation_exact_saturation_reads = profile.continuation_exact_saturation_reads.saturating_add(1);
            }
        }
        if countermove {
            profile.countermove_matches = profile.countermove_matches.saturating_add(1);
        }
    }

    fn record_capture_history_read(&mut self, board: &Board, mv: Move) {
        let Some(moving) = board.piece_on(mv.from) else {
            return;
        };
        let victim = captured_piece(board, mv).unwrap_or(Piece::Pawn);
        let value = i32::from(
            self.histories.capture[capture_index(
                color_index(board.side_to_move()),
                moving,
                mv.to as usize,
                victim,
            )],
        );
        let profile = &mut self.stats.history_profile;
        profile.capture_history_reads = profile.capture_history_reads.saturating_add(1);
        profile.capture_component_bins[history_value_bin(value)] =
            profile.capture_component_bins[history_value_bin(value)].saturating_add(1);
        if value.abs() >= HISTORY_LIMIT * 3 / 4 {
            profile.capture_near_saturation_reads = profile.capture_near_saturation_reads.saturating_add(1);
        }
        if value.abs() >= HISTORY_LIMIT {
            profile.capture_exact_saturation_reads = profile.capture_exact_saturation_reads.saturating_add(1);
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn record_history_cutoff_profile(
        &mut self,
        board: &Board,
        scored: ScoredMove,
        previous: Option<MoveContext>,
        ply: usize,
        depth: i16,
        searched_quiets: &[Move],
        searched_captures: &[Move],
    ) {
        let depth_bin = history_update_depth_bin(depth);
        let profile = &mut self.stats.history_profile;
        if scored.tactical {
            profile.capture_cutoffs = profile.capture_cutoffs.saturating_add(1);
            if board.piece_on(scored.mv.from).is_some() {
                profile.capture_positive_updates = profile.capture_positive_updates.saturating_add(1);
                profile.positive_update_depth_bins[depth_bin] =
                    profile.positive_update_depth_bins[depth_bin].saturating_add(1);
            }
            for searched in searched_captures {
                if *searched != scored.mv && board.piece_on(searched.from).is_some() {
                    profile.capture_negative_updates = profile.capture_negative_updates.saturating_add(1);
                    profile.negative_update_depth_bins[depth_bin] =
                        profile.negative_update_depth_bins[depth_bin].saturating_add(1);
                }
            }
            return;
        }

        profile.quiet_cutoffs = profile.quiet_cutoffs.saturating_add(1);
        profile.quiet_positive_updates = profile.quiet_positive_updates.saturating_add(1);
        profile.positive_update_depth_bins[depth_bin] =
            profile.positive_update_depth_bins[depth_bin].saturating_add(1);
        let packed = PackedMove::from_move(scored.mv);
        if ply < MAX_PLY && self.histories.killers[ply][0] != packed {
            profile.killer_updates = profile.killer_updates.saturating_add(1);
        }
        if let (Some(_), Some(_)) = (previous, board.piece_on(scored.mv.from)) {
            profile.continuation_positive_updates =
                profile.continuation_positive_updates.saturating_add(1);
            profile.countermove_records = profile.countermove_records.saturating_add(1);
            profile.positive_update_depth_bins[depth_bin] =
                profile.positive_update_depth_bins[depth_bin].saturating_add(1);
        }
        for searched in searched_quiets {
            if *searched == scored.mv {
                continue;
            }
            profile.quiet_negative_updates = profile.quiet_negative_updates.saturating_add(1);
            profile.negative_update_depth_bins[depth_bin] =
                profile.negative_update_depth_bins[depth_bin].saturating_add(1);
            if let (Some(_), Some(_)) = (previous, board.piece_on(searched.from)) {
                profile.continuation_negative_updates =
                    profile.continuation_negative_updates.saturating_add(1);
                profile.negative_update_depth_bins[depth_bin] =
                    profile.negative_update_depth_bins[depth_bin].saturating_add(1);
            }
        }
    }

"""
    text = replace_once(text, marker, methods + marker, "History profile worker methods")

    helper_marker = """fn color_index(color: cozy_chess::Color) -> usize {
"""
    helpers = """fn history_value_bin(value: i32) -> usize {
    match value {
        ..=-12_288 => 0,
        -12_287..=-8_192 => 1,
        -8_191..=-4_096 => 2,
        -4_095..=-1 => 3,
        0..=4_095 => 4,
        4_096..=8_191 => 5,
        8_192..=12_287 => 6,
        _ => 7,
    }
}

fn history_update_depth_bin(depth: i16) -> usize {
    match depth {
        ..=1 => 0,
        2 => 1,
        3 => 2,
        4..=5 => 3,
        6..=7 => 4,
        8..=9 => 5,
        10..=11 => 6,
        _ => 7,
    }
}

fn history_traffic_depth_bin(depth: i16) -> usize {
    match depth {
        ..=3 => 0,
        4..=5 => 1,
        6..=7 => 2,
        8..=9 => 3,
        _ => 4,
    }
}

fn history_traffic_move_bin(move_index: usize) -> usize {
    match move_index {
        0..=2 => 0,
        3..=7 => 1,
        8..=11 => 2,
        12..=15 => 3,
        _ => 4,
    }
}

"""
    text = replace_once(text, helper_marker, helpers + helper_marker, "History profile bin helpers")

    SEARCH.write_text(text, encoding="utf-8", newline="\n")

    engine = ENGINE.read_text(encoding="utf-8")
    engine = replace_once(
        engine,
        """            use_lmr: self.use_lmr,
            use_see_pruning: self.use_see_pruning,
            use_null_move_pruning: self.use_null_move_pruning,
        }
""",
        """            use_lmr: self.use_lmr,
            use_see_pruning: self.use_see_pruning,
            use_null_move_pruning: self.use_null_move_pruning,
            profile_history_framework: false,
        }
""",
        "Engine SearchOptions diagnostic plumbing",
    )
    ENGINE.write_text(engine, encoding="utf-8", newline="\n")

    print("TE1_HISTORY_FRAMEWORK_BASELINE_PROFILE_PATCH_OK")


if __name__ == "__main__":
    main()
