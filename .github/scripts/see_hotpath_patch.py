#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import hashlib

SEARCH = Path("crates/te1-search/src/lib.rs")
EXPECTED_SHA256 = "e9d361343a00cfa168b70463b7918ce05e809addff14f8e1f6f22fcce25a0f4c"

raw = SEARCH.read_bytes()
got = hashlib.sha256(raw).hexdigest()
if got != EXPECTED_SHA256:
    raise SystemExit(f"unexpected te1-search source sha256: {got}")
text = raw.decode("utf-8")

constant_anchor = "const MAX_SEARCH_TIME: Duration = Duration::from_secs(24 * 60 * 60);\n"
if text.count(constant_anchor) != 1:
    raise SystemExit("SEE profile constant anchor count != 1")
profile_code = r'''

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SeeHotpathProfile {
    pub calls: u64,
    pub reply_calls: u64,
    pub clones: u64,
    pub move_lists: u64,
    pub reply_moves_scanned: u64,
    pub target_captures: u64,
    pub max_depth: u64,
    pub nanos: u64,
    pub negative: u64,
    pub zero: u64,
    pub positive: u64,
}

static SEE_PROFILE_MODE: AtomicU64 = AtomicU64::new(0);
static SEE_CALLS: AtomicU64 = AtomicU64::new(0);
static SEE_REPLY_CALLS: AtomicU64 = AtomicU64::new(0);
static SEE_CLONES: AtomicU64 = AtomicU64::new(0);
static SEE_MOVE_LISTS: AtomicU64 = AtomicU64::new(0);
static SEE_REPLY_MOVES_SCANNED: AtomicU64 = AtomicU64::new(0);
static SEE_TARGET_CAPTURES: AtomicU64 = AtomicU64::new(0);
static SEE_MAX_DEPTH: AtomicU64 = AtomicU64::new(0);
static SEE_NANOS: AtomicU64 = AtomicU64::new(0);
static SEE_NEGATIVE: AtomicU64 = AtomicU64::new(0);
static SEE_ZERO: AtomicU64 = AtomicU64::new(0);
static SEE_POSITIVE: AtomicU64 = AtomicU64::new(0);

pub fn set_see_hotpath_profile_mode(mode: u8) {
    assert!(mode <= 2, "SEE profile mode must be 0, 1, or 2");
    SEE_PROFILE_MODE.store(0, Ordering::Relaxed);
    for counter in [
        &SEE_CALLS,
        &SEE_REPLY_CALLS,
        &SEE_CLONES,
        &SEE_MOVE_LISTS,
        &SEE_REPLY_MOVES_SCANNED,
        &SEE_TARGET_CAPTURES,
        &SEE_MAX_DEPTH,
        &SEE_NANOS,
        &SEE_NEGATIVE,
        &SEE_ZERO,
        &SEE_POSITIVE,
    ] {
        counter.store(0, Ordering::Relaxed);
    }
    SEE_PROFILE_MODE.store(u64::from(mode), Ordering::Relaxed);
}

#[must_use]
pub fn see_hotpath_profile() -> SeeHotpathProfile {
    SeeHotpathProfile {
        calls: SEE_CALLS.load(Ordering::Relaxed),
        reply_calls: SEE_REPLY_CALLS.load(Ordering::Relaxed),
        clones: SEE_CLONES.load(Ordering::Relaxed),
        move_lists: SEE_MOVE_LISTS.load(Ordering::Relaxed),
        reply_moves_scanned: SEE_REPLY_MOVES_SCANNED.load(Ordering::Relaxed),
        target_captures: SEE_TARGET_CAPTURES.load(Ordering::Relaxed),
        max_depth: SEE_MAX_DEPTH.load(Ordering::Relaxed),
        nanos: SEE_NANOS.load(Ordering::Relaxed),
        negative: SEE_NEGATIVE.load(Ordering::Relaxed),
        zero: SEE_ZERO.load(Ordering::Relaxed),
        positive: SEE_POSITIVE.load(Ordering::Relaxed),
    }
}
'''
text = text.replace(constant_anchor, constant_anchor + profile_code, 1)

old_see = '''#[must_use]
pub fn static_exchange_eval(board: &Board, mv: Move) -> i32 {
    debug_assert!(board.is_legal(mv));
    let gain = captured_piece(board, mv).map_or(0, piece_value) + promotion_gain(mv);
    let mut next = board.clone();
    next.play_unchecked(mv);
    gain - see_reply(&next, mv.to as usize, 0)
}

fn see_reply(board: &Board, target: usize, depth: usize) -> i32 {
    if depth >= 16 {
        return 0;
    }
    let mut best = 0i32;
    for mv in legal_moves_unsorted(board) {
        if mv.to as usize != target || !is_capture(board, mv) {
            continue;
        }
        let gain = captured_piece(board, mv).map_or(0, piece_value) + promotion_gain(mv);
        let mut next = board.clone();
        next.play_unchecked(mv);
        let score = gain - see_reply(&next, target, depth + 1);
        best = best.max(score);
    }
    best
}
'''
if text.count(old_see) != 1:
    raise SystemExit("SEE implementation anchor count != 1")
new_see = '''#[must_use]
pub fn static_exchange_eval(board: &Board, mv: Move) -> i32 {
    debug_assert!(board.is_legal(mv));
    let mode = SEE_PROFILE_MODE.load(Ordering::Relaxed);
    let started = (mode >= 2).then(Instant::now);
    if mode != 0 {
        SEE_CALLS.fetch_add(1, Ordering::Relaxed);
        SEE_CLONES.fetch_add(1, Ordering::Relaxed);
    }
    let gain = captured_piece(board, mv).map_or(0, piece_value) + promotion_gain(mv);
    let mut next = board.clone();
    next.play_unchecked(mv);
    let result = gain - see_reply(&next, mv.to as usize, 0);
    if mode != 0 {
        match result.cmp(&0) {
            CmpOrdering::Less => {
                SEE_NEGATIVE.fetch_add(1, Ordering::Relaxed);
            }
            CmpOrdering::Equal => {
                SEE_ZERO.fetch_add(1, Ordering::Relaxed);
            }
            CmpOrdering::Greater => {
                SEE_POSITIVE.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
    if let Some(started) = started {
        let nanos = u64::try_from(started.elapsed().as_nanos()).unwrap_or(u64::MAX);
        SEE_NANOS.fetch_add(nanos, Ordering::Relaxed);
    }
    result
}

fn see_reply(board: &Board, target: usize, depth: usize) -> i32 {
    let mode = SEE_PROFILE_MODE.load(Ordering::Relaxed);
    if mode != 0 {
        SEE_REPLY_CALLS.fetch_add(1, Ordering::Relaxed);
        SEE_MAX_DEPTH.fetch_max(u64::try_from(depth).unwrap_or(u64::MAX), Ordering::Relaxed);
    }
    if depth >= 16 {
        return 0;
    }
    if mode != 0 {
        SEE_MOVE_LISTS.fetch_add(1, Ordering::Relaxed);
    }
    let mut best = 0i32;
    for mv in legal_moves_unsorted(board) {
        if mode != 0 {
            SEE_REPLY_MOVES_SCANNED.fetch_add(1, Ordering::Relaxed);
        }
        if mv.to as usize != target || !is_capture(board, mv) {
            continue;
        }
        if mode != 0 {
            SEE_TARGET_CAPTURES.fetch_add(1, Ordering::Relaxed);
            SEE_CLONES.fetch_add(1, Ordering::Relaxed);
        }
        let gain = captured_piece(board, mv).map_or(0, piece_value) + promotion_gain(mv);
        let mut next = board.clone();
        next.play_unchecked(mv);
        let score = gain - see_reply(&next, target, depth + 1);
        best = best.max(score);
    }
    best
}
'''
text = text.replace(old_see, new_see, 1)
SEARCH.write_text(text, encoding="utf-8")
print("SEE hotpath temporary instrumentation applied")
