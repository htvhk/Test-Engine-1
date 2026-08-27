#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: s2_apply.py WORKTREE")
root = Path(sys.argv[1])


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1))


chess = root / "crates/te1-chess/src/lib.rs"
tt = root / "crates/te1-tt/src/lib.rs"
search = root / "crates/te1-search/src/lib.rs"

# ---- te1-chess: cached reversible-history authentication context ----
replace_once(
    chess,
    """    repetition_count: u8,\n    repetition_start: usize,\n}\n\n#[derive(Debug, Clone)]\npub struct SearchUndo {\n    board: Board,\n    halfmove_clock: u16,\n    repetition_count: u8,\n}\n\n/// Exact state needed to undo an artificial search-only pass.\n#[derive(Debug, Clone)]\npub struct NullMoveUndo {\n    board: Board,\n    repetition_count: u8,\n    repetition_start: usize,\n}\n""",
    """    repetition_count: u8,\n    repetition_start: usize,\n    history_context: u64,\n}\n\n#[derive(Debug, Clone)]\npub struct SearchUndo {\n    board: Board,\n    halfmove_clock: u16,\n    repetition_count: u8,\n    history_context: u64,\n}\n\n/// Exact state needed to undo an artificial search-only pass.\n#[derive(Debug, Clone)]\npub struct NullMoveUndo {\n    board: Board,\n    repetition_count: u8,\n    repetition_start: usize,\n    history_context: u64,\n}\n""",
    "chess state structs",
)

replace_once(
    chess,
    """        let repetition_count = count_current_repetitions(&repetition_keys, halfmove_clock);\n        Self {\n            board: game.board.clone(),\n            repetition_keys,\n            halfmove_clock,\n            repetition_count,\n            repetition_start: 0,\n        }\n""",
    """        let repetition_count = count_current_repetitions(&repetition_keys, halfmove_clock);\n        let repetition_start = 0;\n        let history_context =\n            compute_history_context(&repetition_keys, halfmove_clock, repetition_start);\n        Self {\n            board: game.board.clone(),\n            repetition_keys,\n            halfmove_clock,\n            repetition_count,\n            repetition_start,\n            history_context,\n        }\n""",
    "chess from_game context",
)

replace_once(
    chess,
    """    pub fn repetition_count(&self) -> usize {\n        usize::from(self.repetition_count)\n    }\n\n    #[must_use]\n    pub fn is_draw(&self) -> bool {\n""",
    """    pub fn repetition_count(&self) -> usize {\n        usize::from(self.repetition_count)\n    }\n\n    /// Order-sensitive fingerprint of the FIDE-relevant reversible history.\n    ///\n    /// The ordinary position/search key deliberately remains history-agnostic so\n    /// TT moves can still be reused for ordering across transpositions. Score and\n    /// bound consumers authenticate this separate context before trusting them.\n    #[must_use]\n    pub fn history_context(&self) -> u64 {\n        self.history_context\n    }\n\n    #[must_use]\n    pub fn is_draw(&self) -> bool {\n""",
    "chess history_context getter",
)

replace_once(
    chess,
    """        let undo = SearchUndo {\n            board: self.board.clone(),\n            halfmove_clock: self.halfmove_clock,\n            repetition_count: self.repetition_count,\n        };\n""",
    """        let undo = SearchUndo {\n            board: self.board.clone(),\n            halfmove_clock: self.halfmove_clock,\n            repetition_count: self.repetition_count,\n            history_context: self.history_context,\n        };\n""",
    "chess real undo save context",
)

replace_once(
    chess,
    """        self.repetition_count = count_current_repetitions(\n            &self.repetition_keys[self.repetition_start..],\n            self.halfmove_clock,\n        );\n        undo\n    }\n\n    pub fn unmake_move(&mut self, undo: SearchUndo) {\n""",
    """        self.repetition_count = count_current_repetitions(\n            &self.repetition_keys[self.repetition_start..],\n            self.halfmove_clock,\n        );\n        self.history_context = compute_history_context(\n            &self.repetition_keys,\n            self.halfmove_clock,\n            self.repetition_start,\n        );\n        undo\n    }\n\n    pub fn unmake_move(&mut self, undo: SearchUndo) {\n""",
    "chess real make recompute context",
)

replace_once(
    chess,
    """        self.board = undo.board;\n        self.halfmove_clock = undo.halfmove_clock;\n        self.repetition_count = undo.repetition_count;\n    }\n\n    /// Makes a synthetic null move without advancing legal-game rule-50 state.\n""",
    """        self.board = undo.board;\n        self.halfmove_clock = undo.halfmove_clock;\n        self.repetition_count = undo.repetition_count;\n        self.history_context = undo.history_context;\n    }\n\n    /// Makes a synthetic null move without advancing legal-game rule-50 state.\n""",
    "chess real unmake restore context",
)

replace_once(
    chess,
    """        let undo = NullMoveUndo {\n            board: self.board.clone(),\n            repetition_count: self.repetition_count,\n            repetition_start: self.repetition_start,\n        };\n""",
    """        let undo = NullMoveUndo {\n            board: self.board.clone(),\n            repetition_count: self.repetition_count,\n            repetition_start: self.repetition_start,\n            history_context: self.history_context,\n        };\n""",
    "chess null undo save context",
)

replace_once(
    chess,
    """        self.repetition_start = self.repetition_keys.len() - 1;\n        self.repetition_count = 1;\n        Some(undo)\n    }\n""",
    """        self.repetition_start = self.repetition_keys.len() - 1;\n        self.repetition_count = 1;\n        self.history_context = compute_history_context(\n            &self.repetition_keys,\n            self.halfmove_clock,\n            self.repetition_start,\n        );\n        Some(undo)\n    }\n""",
    "chess null make recompute context",
)

replace_once(
    chess,
    """        self.board = undo.board;\n        self.repetition_count = undo.repetition_count;\n        self.repetition_start = undo.repetition_start;\n    }\n}\n\nfn count_current_repetitions(keys: &[u64], halfmove_clock: u16) -> u8 {\n""",
    """        self.board = undo.board;\n        self.repetition_count = undo.repetition_count;\n        self.repetition_start = undo.repetition_start;\n        self.history_context = undo.history_context;\n    }\n}\n\nconst LEGAL_HISTORY_CONTEXT_DOMAIN: u64 = 0x243f_6a88_85a3_08d3;\nconst SYNTHETIC_NULL_HISTORY_CONTEXT_DOMAIN: u64 = 0x1319_8a2e_0370_7344;\nconst HISTORY_CONTEXT_GOLDEN: u64 = 0x9e37_79b9_7f4a_7c15;\n\nfn history_context_mix(state: u64, value: u64) -> u64 {\n    let mut mixed = state ^ value.wrapping_add(HISTORY_CONTEXT_GOLDEN).rotate_left(17);\n    mixed ^= mixed >> 30;\n    mixed = mixed.wrapping_mul(0xbf58_476d_1ce4_e5b9);\n    mixed ^= mixed >> 27;\n    mixed = mixed.wrapping_mul(0x94d0_49bb_1331_11eb);\n    mixed ^ (mixed >> 31)\n}\n\nfn compute_history_context(keys: &[u64], halfmove_clock: u16, repetition_start: usize) -> u64 {\n    let len = keys.len();\n    let reversible_span = usize::from(halfmove_clock).saturating_add(1);\n    let rule50_start = len.saturating_sub(reversible_span);\n    let start = repetition_start.max(rule50_start).min(len);\n    let synthetic_null_domain = repetition_start > rule50_start;\n    let mut context = if synthetic_null_domain {\n        SYNTHETIC_NULL_HISTORY_CONTEXT_DOMAIN\n    } else {\n        LEGAL_HISTORY_CONTEXT_DOMAIN\n    };\n    let window = &keys[start..];\n    context = history_context_mix(\n        context,\n        u64::try_from(window.len()).unwrap_or(u64::MAX),\n    );\n    for (index, key) in window.iter().copied().enumerate() {\n        context = history_context_mix(context, key);\n        context = history_context_mix(context, u64::try_from(index).unwrap_or(u64::MAX));\n    }\n    context\n}\n\nfn count_current_repetitions(keys: &[u64], halfmove_clock: u16) -> u8 {\n""",
    "chess context function",
)

replace_once(
    chess,
    """    #[test]\n    fn search_position_make_unmake_restores_state() {\n""",
    """    #[test]\n    fn history_context_distinguishes_the_proven_ghi_histories() {\n        let mut a = Te1Game::from_fen(START_FEN).unwrap();\n        for mv in [\n            \"g1f3\", \"g8f6\", \"f3g1\", \"f6g8\", \"g1f3\", \"g8f6\", \"b1c3\", \"b8c6\",\n        ] {\n            a.play_uci(mv).unwrap();\n        }\n\n        let mut seed = Te1Game::from_fen(START_FEN).unwrap();\n        for mv in [\"g1h3\", \"b8a6\", \"b1a3\", \"g8h6\"] {\n            seed.play_uci(mv).unwrap();\n        }\n        let mut fields: Vec<&str> = seed.fen().split_whitespace().collect();\n        fields[4] = \"0\";\n        let seed_fen = fields.join(\" \" );\n        let mut b = Te1Game::from_fen(&seed_fen).unwrap();\n        for mv in [\n            \"a3b1\", \"a6b8\", \"h3g1\", \"h6g8\", \"g1f3\", \"g8f6\", \"b1c3\", \"b8c6\",\n        ] {\n            b.play_uci(mv).unwrap();\n        }\n\n        let mut pa = SearchPosition::from_game(&a);\n        let mut pb = SearchPosition::from_game(&b);\n        assert!(pa.board().same_position(pb.board()));\n        assert_eq!(pa.halfmove_clock(), 8);\n        assert_eq!(pb.halfmove_clock(), 8);\n        assert_eq!(pa.repetition_count(), 1);\n        assert_eq!(pb.repetition_count(), 1);\n        assert_eq!(pa.search_key(), pb.search_key());\n        assert_ne!(pa.history_context(), pb.history_context());\n        assert_eq!(pa.history_context(), SearchPosition::from_game(&a).history_context());\n\n        let ma = parse_legal_uci_move(pa.board(), \"e2e4\").unwrap();\n        let mb = parse_legal_uci_move(pb.board(), \"e2e4\").unwrap();\n        let _ = pa.make_move(ma);\n        let _ = pb.make_move(mb);\n        assert_eq!(pa.halfmove_clock(), 0);\n        assert_eq!(pb.halfmove_clock(), 0);\n        assert_eq!(pa.history_context(), pb.history_context());\n    }\n\n    #[test]\n    fn history_context_domain_separates_null_and_legal_windows() {\n        let keys = [11u64, 22u64];\n        let legal = compute_history_context(&keys, 0, 0);\n        let synthetic = compute_history_context(&keys, 1, 1);\n        assert_ne!(legal, synthetic);\n    }\n\n    #[test]\n    fn search_position_make_unmake_restores_state() {\n""",
    "chess GHI tests",
)

replace_once(
    chess,
    """        let original_board = position.board().clone();\n        let original_key = position.search_key();\n""",
    """        let original_board = position.board().clone();\n        let original_key = position.search_key();\n        let original_context = position.history_context();\n""",
    "chess roundtrip capture context",
)

replace_once(
    chess,
    """        assert_eq!(position.board(), &original_board);\n        assert_eq!(position.search_key(), original_key);\n    }\n\n    #[test]\n    fn synthetic_null_transition_is_exact_and_clears_en_passant() {\n""",
    """        assert_eq!(position.board(), &original_board);\n        assert_eq!(position.search_key(), original_key);\n        assert_eq!(position.history_context(), original_context);\n    }\n\n    #[test]\n    fn synthetic_null_transition_is_exact_and_clears_en_passant() {\n""",
    "chess real roundtrip assert context",
)

replace_once(
    chess,
    """        let original_key = position.search_key();\n\n        let undo = position.make_null_move().unwrap();\n""",
    """        let original_key = position.search_key();\n        let original_context = position.history_context();\n\n        let undo = position.make_null_move().unwrap();\n""",
    "chess null capture context",
)

replace_once(
    chess,
    """        assert_eq!(position.repetition_keys, original.repetition_keys);\n    }\n""",
    """        assert_eq!(position.repetition_keys, original.repetition_keys);\n        assert_eq!(position.history_context(), original_context);\n    }\n""",
    "chess null roundtrip assert context",
)

# ---- te1-tt: position-key index with guarded history-context sidecar ----
replace_once(
    tt,
    """    pub best_move: PackedMove,\n    pub generation: u8,\n}\n\n#[derive(Debug)]\nstruct Slot {\n    guard: AtomicU8,\n    key_xor_data: AtomicU64,\n    data: AtomicU64,\n}\n""",
    """    pub best_move: PackedMove,\n    pub generation: u8,\n    pub history_context: u64,\n}\n\n#[derive(Debug)]\nstruct Slot {\n    guard: AtomicU8,\n    key_xor_data: AtomicU64,\n    data: AtomicU64,\n    history_context: AtomicU64,\n}\n""",
    "tt entry and slot context",
)

replace_once(
    tt,
    """            key_xor_data: AtomicU64::new(0),\n            data: AtomicU64::new(0),\n        }\n""",
    """            key_xor_data: AtomicU64::new(0),\n            data: AtomicU64::new(0),\n            history_context: AtomicU64::new(0),\n        }\n""",
    "tt empty context",
)

replace_once(
    tt,
    """        self.key_xor_data.store(0, Ordering::Relaxed);\n        self.data.store(0, Ordering::Relaxed);\n    }\n\n    fn read(&self) -> Option<(u64, Entry)> {\n        let _guard = self.lock();\n        let key_xor_data = self.key_xor_data.load(Ordering::Relaxed);\n        let data = self.data.load(Ordering::Relaxed);\n        let entry = unpack(data)?;\n        Some((key_xor_data ^ data, entry))\n    }\n""",
    """        self.key_xor_data.store(0, Ordering::Relaxed);\n        self.data.store(0, Ordering::Relaxed);\n        self.history_context.store(0, Ordering::Relaxed);\n    }\n\n    fn read(&self) -> Option<(u64, Entry)> {\n        let _guard = self.lock();\n        let key_xor_data = self.key_xor_data.load(Ordering::Relaxed);\n        let data = self.data.load(Ordering::Relaxed);\n        let mut entry = unpack(data)?;\n        entry.history_context = self.history_context.load(Ordering::Relaxed);\n        Some((key_xor_data ^ data, entry))\n    }\n""",
    "tt clear/read context",
)

replace_once(
    tt,
    """        if replace {\n            let data = pack(entry);\n            self.data.store(data, Ordering::Relaxed);\n            self.key_xor_data.store(key ^ data, Ordering::Relaxed);\n        }\n""",
    """        if replace {\n            let data = pack(entry);\n            self.history_context\n                .store(entry.history_context, Ordering::Relaxed);\n            self.data.store(data, Ordering::Relaxed);\n            self.key_xor_data.store(key ^ data, Ordering::Relaxed);\n        }\n""",
    "tt store context sidecar",
)

replace_once(
    tt,
    """    pub fn store(&self, key: u64, depth: i16, score: i32, bound: Bound, best_move: PackedMove) {\n        let entry = Entry {\n            depth,\n            score,\n            bound,\n            best_move,\n            generation: self.generation(),\n        };\n""",
    """    pub fn store(\n        &self,\n        key: u64,\n        history_context: u64,\n        depth: i16,\n        score: i32,\n        bound: Bound,\n        best_move: PackedMove,\n    ) {\n        let entry = Entry {\n            depth,\n            score,\n            bound,\n            best_move,\n            generation: self.generation(),\n            history_context,\n        };\n""",
    "tt public store signature",
)

replace_once(
    tt,
    """        best_move: PackedMove::from_raw(move_raw),\n        generation,\n    })\n}\n""",
    """        best_move: PackedMove::from_raw(move_raw),\n        generation,\n        history_context: 0,\n    })\n}\n""",
    "tt unpack placeholder context",
)

# Update the TT unit-test call sites with explicit contexts.
for old, new, label in [
    ("table.store(42, 5, 123, Bound::Exact, best_move);", "table.store(42, 0x1111, 5, 123, Bound::Exact, best_move);", "tt test store 42"),
    ("table.store(7, 8, 10, Bound::Lower, PackedMove::NONE);", "table.store(7, 0x7777, 8, 10, Bound::Lower, PackedMove::NONE);", "tt test deep store"),
    ("table.store(7, 3, 20, Bound::Upper, PackedMove::NONE);", "table.store(7, 0x7777, 3, 20, Bound::Upper, PackedMove::NONE);", "tt test shallow store"),
    ("table.store(9, 1, 1, Bound::Exact, PackedMove::NONE);", "table.store(9, 0x9999, 1, 1, Bound::Exact, PackedMove::NONE);", "tt test clear store"),
    ("table.store(key, 8, iteration, Bound::Exact, mv);", "table.store(key, writer_id, 8, iteration, Bound::Exact, mv);", "tt collision store"),
]:
    replace_once(tt, old, new, label)

replace_once(
    tt,
    """                    table.store(\n                        thread_id,\n                        6,\n                        i32::try_from(thread_id).unwrap(),\n                        Bound::Exact,\n                        mv,\n                    );\n""",
    """                    table.store(\n                        thread_id,\n                        thread_id.wrapping_mul(0x1001),\n                        6,\n                        i32::try_from(thread_id).unwrap(),\n                        Bound::Exact,\n                        mv,\n                    );\n""",
    "tt concurrent store",
)

replace_once(
    tt,
    """        assert_eq!(entry.best_move, best_move);\n        assert!(table.probe(43).is_none());\n    }\n\n    #[test]\n    fn deeper_entry_is_not_replaced_by_shallower_non_exact_entry() {\n""",
    """        assert_eq!(entry.best_move, best_move);\n        assert_eq!(entry.history_context, 0x1111);\n        assert!(table.probe(43).is_none());\n    }\n\n    #[test]\n    fn same_position_key_keeps_move_reuse_across_history_contexts() {\n        let table = TranspositionTable::with_megabytes(1);\n        let first = PackedMove::from_raw(0x0123);\n        let second = PackedMove::from_raw(0x0456);\n        table.store(55, 0xaaaa, 6, 30, Bound::Lower, first);\n        let hit = table.probe(55).unwrap();\n        assert_eq!(hit.best_move, first);\n        assert_eq!(hit.history_context, 0xaaaa);\n\n        table.store(55, 0xbbbb, 6, 31, Bound::Exact, second);\n        let replaced = table.probe(55).unwrap();\n        assert_eq!(replaced.best_move, second);\n        assert_eq!(replaced.history_context, 0xbbbb);\n    }\n\n    #[test]\n    fn deeper_entry_is_not_replaced_by_shallower_non_exact_entry() {\n""",
    "tt context move reuse test",
)

replace_once(
    tt,
    """    #[test]\n    fn table_size_is_a_nonzero_power_of_two() {\n        let table = TranspositionTable::with_megabytes(3);\n        assert!(table.len().is_power_of_two());\n        assert!(!table.is_empty());\n    }\n""",
    """    #[test]\n    fn table_size_is_a_nonzero_power_of_two() {\n        let table = TranspositionTable::with_megabytes(3);\n        assert!(table.len().is_power_of_two());\n        assert!(!table.is_empty());\n    }\n\n    #[test]\n    fn history_context_sidecar_capacity_is_explicit() {\n        let slot_size = std::mem::size_of::<Slot>();\n        assert!(slot_size >= 3 * std::mem::size_of::<u64>());\n        for megabytes in [1usize, 16, 256] {\n            let table = TranspositionTable::with_megabytes(megabytes);\n            assert!(table.len().is_power_of_two());\n            println!(\n                \"TE1_S2_TT_LAYOUT mb={megabytes} slot_size={slot_size} entries={}\",\n                table.len()\n            );\n        }\n    }\n""",
    "tt layout test",
)

# ---- te1-search: history-gated score trust and complete PV exact cutoffs ----
replace_once(
    search,
    """            self.table.store(\n                position.search_key(),\n                depth,\n                score_to_table(best_score, 0),\n                bound,\n                best_move,\n            );\n""",
    """            self.table.store(\n                position.search_key(),\n                position.history_context(),\n                depth,\n                score_to_table(best_score, 0),\n                bound,\n                best_move,\n            );\n""",
    "search root store context",
)

replace_once(
    search,
    """        let key = position.search_key();\n        let original_alpha = alpha;\n        let mut tt_move = PackedMove::NONE;\n        let mut tt_upper_contradicts = false;\n        if let Some(entry) = self.table.probe(key) {\n            self.stats.tt_hits = self.stats.tt_hits.saturating_add(1);\n            tt_move = entry.best_move;\n            tt_upper_contradicts = entry.depth >= depth\n                && entry.bound == Bound::Upper\n                && score_from_table(entry.score, ply) < beta;\n            if position.tt_cutoff_safe() && entry.depth >= depth {\n                let score = score_from_table(entry.score, ply);\n                match entry.bound {\n                    Bound::Exact => {\n                        if pv_node {\n                            reconstruct_exact_tt_pv(self.table.as_ref(), position, depth, pv);\n                        }\n                        return score;\n                    }\n                    Bound::Lower if score >= beta => return score,\n                    Bound::Upper if score <= alpha => return score,\n                    Bound::Lower | Bound::Upper => {}\n                }\n            }\n        }\n""",
    """        let key = position.search_key();\n        let history_context = position.history_context();\n        let original_alpha = alpha;\n        let mut tt_move = PackedMove::NONE;\n        let mut tt_upper_contradicts = false;\n        if let Some(entry) = self.table.probe(key) {\n            self.stats.tt_hits = self.stats.tt_hits.saturating_add(1);\n            tt_move = entry.best_move;\n            let context_match = entry.history_context == history_context;\n            if context_match {\n                tt_upper_contradicts = entry.depth >= depth\n                    && entry.bound == Bound::Upper\n                    && score_from_table(entry.score, ply) < beta;\n                if position.tt_cutoff_safe() && entry.depth >= depth {\n                    let score = score_from_table(entry.score, ply);\n                    match entry.bound {\n                        Bound::Exact if !pv_node => return score,\n                        Bound::Exact\n                            if reconstruct_exact_tt_pv(\n                                self.table.as_ref(),\n                                position,\n                                depth,\n                                pv,\n                            ) =>\n                        {\n                            return score;\n                        }\n                        Bound::Exact => {}\n                        Bound::Lower if score >= beta => return score,\n                        Bound::Upper if score <= alpha => return score,\n                        Bound::Lower | Bound::Upper => {}\n                    }\n                }\n            }\n        }\n""",
    "search context-gated probe",
)

replace_once(
    search,
    """            self.table.store(\n                key,\n                depth,\n                score_to_table(best_score, ply),\n                classify_bound(best_score, original_alpha, beta),\n                best_move,\n            );\n""",
    """            self.table.store(\n                key,\n                position.history_context(),\n                depth,\n                score_to_table(best_score, ply),\n                classify_bound(best_score, original_alpha, beta),\n                best_move,\n            );\n""",
    "search negamax store context",
)

replace_once(
    search,
    """fn reconstruct_exact_tt_pv(\n    table: &TranspositionTable,\n    position: &SearchPosition,\n    depth: i16,\n    pv: &mut PvLine,\n) {\n    pv.clear();\n    let mut cursor = position.clone();\n    let mut remaining = depth.max(0);\n    while remaining > 0 && pv.len < MAX_PLY {\n        if !cursor.tt_cutoff_safe() {\n            break;\n        }\n        let Some(entry) = table.probe(cursor.search_key()) else {\n            break;\n        };\n        if entry.bound != Bound::Exact || entry.depth < remaining {\n            break;\n        }\n        let Some(mv) = entry.best_move.to_move() else {\n            break;\n        };\n        if !cursor.board().is_legal(mv) {\n            break;\n        }\n        pv.moves[pv.len] = entry.best_move;\n        pv.len += 1;\n        let _ = cursor.make_move(mv);\n        remaining -= 1;\n        if cursor.is_draw() || !has_legal_moves(cursor.board()) {\n            break;\n        }\n    }\n}\n""",
    """fn reconstruct_exact_tt_pv(\n    table: &TranspositionTable,\n    position: &SearchPosition,\n    depth: i16,\n    pv: &mut PvLine,\n) -> bool {\n    pv.clear();\n    let mut cursor = position.clone();\n    let mut remaining = depth.max(0);\n    while remaining > 0 && pv.len < MAX_PLY {\n        if cursor.is_draw() || !has_legal_moves(cursor.board()) {\n            return true;\n        }\n        if !cursor.tt_cutoff_safe() {\n            pv.clear();\n            return false;\n        }\n        let Some(entry) = table.probe(cursor.search_key()) else {\n            pv.clear();\n            return false;\n        };\n        if entry.history_context != cursor.history_context()\n            || entry.bound != Bound::Exact\n            || entry.depth < remaining\n        {\n            pv.clear();\n            return false;\n        }\n        let Some(mv) = entry.best_move.to_move() else {\n            pv.clear();\n            return false;\n        };\n        if !cursor.board().is_legal(mv) {\n            pv.clear();\n            return false;\n        }\n        pv.moves[pv.len] = entry.best_move;\n        pv.len += 1;\n        let _ = cursor.make_move(mv);\n        remaining -= 1;\n    }\n    if remaining == 0 || cursor.is_draw() || !has_legal_moves(cursor.board()) {\n        true\n    } else {\n        pv.clear();\n        false\n    }\n}\n""",
    "search exact PV contract",
)

replace_once(
    search,
    """    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n        let game = Te1Game::from_fen(fen).unwrap();\n        search(\n            &game,\n            SearchLimits {\n                depth: Some(depth),\n                ..SearchLimits::default()\n            },\n            Arc::new(AtomicBool::new(false)),\n            Arc::new(TranspositionTable::with_megabytes(4)),\n            SearchOptions {\n                use_null_move_pruning: enabled,\n                ..SearchOptions::default()\n            },\n        )\n        .unwrap()\n    }\n\n    #[test]\n    fn returns_legal_move_from_start_position() {\n""",
    """    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n        let game = Te1Game::from_fen(fen).unwrap();\n        search(\n            &game,\n            SearchLimits {\n                depth: Some(depth),\n                ..SearchLimits::default()\n            },\n            Arc::new(AtomicBool::new(false)),\n            Arc::new(TranspositionTable::with_megabytes(4)),\n            SearchOptions {\n                use_null_move_pruning: enabled,\n                ..SearchOptions::default()\n            },\n        )\n        .unwrap()\n    }\n\n    fn run_game_with_table(\n        game: &Te1Game,\n        depth: u8,\n        table: Arc<TranspositionTable>,\n    ) -> SearchResult {\n        search(\n            game,\n            SearchLimits {\n                depth: Some(depth),\n                ..SearchLimits::default()\n            },\n            Arc::new(AtomicBool::new(false)),\n            table,\n            SearchOptions {\n                threads: 1,\n                deterministic: true,\n                use_lmr: true,\n                use_see_pruning: true,\n                use_null_move_pruning: true,\n            },\n        )\n        .unwrap()\n    }\n\n    fn ghi_histories() -> (Te1Game, Te1Game) {\n        let mut a = Te1Game::from_fen(START_FEN).unwrap();\n        for mv in [\n            \"g1f3\", \"g8f6\", \"f3g1\", \"f6g8\", \"g1f3\", \"g8f6\", \"b1c3\", \"b8c6\",\n        ] {\n            a.play_uci(mv).unwrap();\n        }\n\n        let mut seed = Te1Game::from_fen(START_FEN).unwrap();\n        for mv in [\"g1h3\", \"b8a6\", \"b1a3\", \"g8h6\"] {\n            seed.play_uci(mv).unwrap();\n        }\n        let mut fields: Vec<&str> = seed.fen().split_whitespace().collect();\n        fields[4] = \"0\";\n        let seed_fen = fields.join(\" \" );\n        let mut b = Te1Game::from_fen(&seed_fen).unwrap();\n        for mv in [\n            \"a3b1\", \"a6b8\", \"h3g1\", \"h6g8\", \"g1f3\", \"g8f6\", \"b1c3\", \"b8c6\",\n        ] {\n            b.play_uci(mv).unwrap();\n        }\n        (a, b)\n    }\n\n    #[test]\n    fn returns_legal_move_from_start_position() {\n""",
    "search GHI helpers",
)

replace_once(
    search,
    """    #[test]\n    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {\n""",
    """    #[test]\n    fn warm_exact_tt_preserves_full_ruy_lopez_pv() {\n        let mut game = Te1Game::from_fen(START_FEN).unwrap();\n        for mv in [\n            \"e2e4\", \"e7e5\", \"g1f3\", \"b8c6\", \"f1b5\", \"a7a6\", \"b5a4\", \"g8f6\", \"e1g1\", \"f8e7\",\n        ] {\n            game.play_uci(mv).unwrap();\n        }\n        let table = Arc::new(TranspositionTable::with_megabytes(16));\n        let cold = run_game_with_table(&game, 7, Arc::clone(&table));\n        let warm = run_game_with_table(&game, 7, Arc::clone(&table));\n        assert_eq!(warm.best_move, cold.best_move);\n        assert_eq!(warm.score_cp, cold.score_cp);\n        assert_eq!(warm.depth, cold.depth);\n        assert_eq!(warm.pv, cold.pv);\n        assert!(warm.nodes < cold.nodes);\n    }\n\n    #[test]\n    fn ghi_opposite_history_warm_tt_matches_fresh_search() {\n        let (a, b) = ghi_histories();\n        let pa = SearchPosition::from_game(&a);\n        let pb = SearchPosition::from_game(&b);\n        assert_eq!(pa.search_key(), pb.search_key());\n        assert_ne!(pa.history_context(), pb.history_context());\n\n        for (source, target) in [(&a, &b), (&b, &a)] {\n            let shared = Arc::new(TranspositionTable::with_megabytes(32));\n            let _source = run_game_with_table(source, 5, Arc::clone(&shared));\n            let warm = run_game_with_table(target, 5, Arc::clone(&shared));\n            let cold = run_game_with_table(\n                target,\n                5,\n                Arc::new(TranspositionTable::with_megabytes(32)),\n            );\n            assert_eq!(warm.best_move, cold.best_move);\n            assert_eq!(warm.score_cp, cold.score_cp);\n            assert_eq!(warm.depth, cold.depth);\n            assert_eq!(warm.pv, cold.pv);\n        }\n    }\n\n    #[test]\n    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {\n""",
    "search S2 product regressions",
)

print("S2 source patch applied")
