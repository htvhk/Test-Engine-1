#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

TARGET = Path("crates/te1-chess/src/lib.rs")

OLD_MAKE = '''        self.board.play_unchecked(mv);
        self.halfmove_clock = if moving_piece == Some(Piece::Pawn) || capture {
            0
        } else {
            self.halfmove_clock.saturating_add(1)
        };
        self.repetition_keys.push(fide_position_hash(&self.board));
        self.repetition_count = count_current_repetitions(
            &self.repetition_keys[self.repetition_start..],
            self.halfmove_clock,
        );
        self.history_context = compute_history_context(
            &self.repetition_keys,
            self.halfmove_clock,
            self.repetition_start,
        );
        undo
'''

NEW_MAKE = '''        let previous_halfmove_clock = self.halfmove_clock;
        let reversible_move = moving_piece != Some(Piece::Pawn) && !capture;
        self.board.play_unchecked(mv);
        self.halfmove_clock = if reversible_move {
            self.halfmove_clock.saturating_add(1)
        } else {
            0
        };
        let new_key = fide_position_hash(&self.board);
        self.repetition_keys.push(new_key);
        self.repetition_count = count_current_repetitions(
            &self.repetition_keys[self.repetition_start..],
            self.halfmove_clock,
        );
        self.history_context = if reversible_move && previous_halfmove_clock < u16::MAX {
            let len = self.repetition_keys.len();
            let reversible_span = usize::from(self.halfmove_clock).saturating_add(1);
            let rule50_start = len.saturating_sub(reversible_span);
            let start = self.repetition_start.max(rule50_start).min(len);
            let index = len.saturating_sub(start).saturating_sub(1);
            append_history_context(self.history_context, new_key, index)
        } else {
            compute_history_context(
                &self.repetition_keys,
                self.halfmove_clock,
                self.repetition_start,
            )
        };
        undo
'''

OLD_CONTEXT = '''fn compute_history_context(keys: &[u64], halfmove_clock: u16, repetition_start: usize) -> u64 {
    let len = keys.len();
    let reversible_span = usize::from(halfmove_clock).saturating_add(1);
    let rule50_start = len.saturating_sub(reversible_span);
    let start = repetition_start.max(rule50_start).min(len);
    let synthetic_null_domain = repetition_start > rule50_start;
    let mut context = if synthetic_null_domain {
        SYNTHETIC_NULL_HISTORY_CONTEXT_DOMAIN
    } else {
        LEGAL_HISTORY_CONTEXT_DOMAIN
    };
    let window = &keys[start..];
    context = history_context_mix(context, u64::try_from(window.len()).unwrap_or(u64::MAX));
    for (index, key) in window.iter().copied().enumerate() {
        context = history_context_mix(context, key);
        context = history_context_mix(context, u64::try_from(index).unwrap_or(u64::MAX));
    }
    context
}
'''

NEW_CONTEXT = '''fn append_history_context(context: u64, key: u64, index: usize) -> u64 {
    let context = history_context_mix(context, key);
    history_context_mix(context, u64::try_from(index).unwrap_or(u64::MAX))
}

fn compute_history_context(keys: &[u64], halfmove_clock: u16, repetition_start: usize) -> u64 {
    let len = keys.len();
    let reversible_span = usize::from(halfmove_clock).saturating_add(1);
    let rule50_start = len.saturating_sub(reversible_span);
    let start = repetition_start.max(rule50_start).min(len);
    let synthetic_null_domain = repetition_start > rule50_start;
    let mut context = if synthetic_null_domain {
        SYNTHETIC_NULL_HISTORY_CONTEXT_DOMAIN
    } else {
        LEGAL_HISTORY_CONTEXT_DOMAIN
    };
    for (index, key) in keys[start..].iter().copied().enumerate() {
        context = append_history_context(context, key, index);
    }
    context
}
'''

TEST_ANCHOR = '''    #[test]
    fn history_context_domain_separates_null_and_legal_windows() {
        let keys = [11u64, 22u64];
        let legal = compute_history_context(&keys, 0, 0);
        let synthetic = compute_history_context(&keys, 1, 1);
        assert_ne!(legal, synthetic);
    }
'''

TESTS = TEST_ANCHOR + '''
    #[test]
    fn reversible_history_context_is_append_only() {
        fn target_append(context: u64, key: u64, index: usize) -> u64 {
            let context = history_context_mix(context, key);
            history_context_mix(context, u64::try_from(index).unwrap_or(u64::MAX))
        }

        let game = Te1Game::from_fen(START_FEN).unwrap();
        let mut position = SearchPosition::from_game(&game);
        let parent_context = position.history_context();
        let mv = parse_legal_uci_move(position.board(), "g1f3").unwrap();
        let undo = position.make_move(mv);
        let new_key = *position.repetition_keys.last().unwrap();
        let len = position.repetition_keys.len();
        let reversible_span = usize::from(position.halfmove_clock).saturating_add(1);
        let rule50_start = len.saturating_sub(reversible_span);
        let start = position.repetition_start.max(rule50_start).min(len);
        let index = len.saturating_sub(start).saturating_sub(1);
        assert_eq!(
            position.history_context(),
            target_append(parent_context, new_key, index)
        );
        position.unmake_move(undo);
    }

    #[test]
    fn incremental_history_context_matches_full_reference_across_transitions() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let mut position = SearchPosition::from_game(&game);
        let mut undos = Vec::new();
        for uci in [
            "g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "b1c3", "b8c6", "e2e4",
        ] {
            let mv = parse_legal_uci_move(position.board(), uci).unwrap();
            undos.push(position.make_move(mv));
            assert_eq!(
                position.history_context(),
                compute_history_context(
                    &position.repetition_keys,
                    position.halfmove_clock,
                    position.repetition_start,
                )
            );
        }
        while let Some(undo) = undos.pop() {
            position.unmake_move(undo);
            assert_eq!(
                position.history_context(),
                compute_history_context(
                    &position.repetition_keys,
                    position.halfmove_clock,
                    position.repetition_start,
                )
            );
        }

        let mut seeded = Te1Game::from_fen(START_FEN).unwrap();
        for uci in ["g1f3", "g8f6", "f3g1", "f6g8"] {
            seeded.play_uci(uci).unwrap();
        }
        let mut position = SearchPosition::from_game(&seeded);
        let null_undo = position.make_null_move().unwrap();
        assert_eq!(
            position.history_context(),
            compute_history_context(
                &position.repetition_keys,
                position.halfmove_clock,
                position.repetition_start,
            )
        );
        let mut move_undos = Vec::new();
        for uci in ["g8f6", "g1f3", "e7e5"] {
            let mv = parse_legal_uci_move(position.board(), uci).unwrap();
            move_undos.push(position.make_move(mv));
            assert_eq!(
                position.history_context(),
                compute_history_context(
                    &position.repetition_keys,
                    position.halfmove_clock,
                    position.repetition_start,
                )
            );
        }
        while let Some(undo) = move_undos.pop() {
            position.unmake_move(undo);
            assert_eq!(
                position.history_context(),
                compute_history_context(
                    &position.repetition_keys,
                    position.halfmove_clock,
                    position.repetition_start,
                )
            );
        }
        position.unmake_null_move(null_undo);
        assert_eq!(
            position.history_context(),
            compute_history_context(
                &position.repetition_keys,
                position.halfmove_clock,
                position.repetition_start,
            )
        );
    }
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("red", "green"))
    args = parser.parse_args()

    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(text, TEST_ANCHOR, TESTS, "test anchor")
    if args.mode == "green":
        text = replace_once(text, OLD_MAKE, NEW_MAKE, "make_move")
        text = replace_once(text, OLD_CONTEXT, NEW_CONTEXT, "history context")
    TARGET.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
