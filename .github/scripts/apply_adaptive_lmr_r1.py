#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / "crates/te1-search/src/lib.rs"
ENGINE = ROOT / "crates/te1-engine/src/main.rs"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement target, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


# SearchOptions: add an independently switchable, default-OFF adaptive LMR candidate.
replace_once(
    SEARCH,
    """    pub use_lmr: bool,\n    pub use_see_pruning: bool,\n    pub use_null_move_pruning: bool,\n""",
    """    pub use_lmr: bool,\n    pub use_adaptive_lmr: bool,\n    pub use_see_pruning: bool,\n    pub use_null_move_pruning: bool,\n""",
)
replace_once(
    SEARCH,
    """            use_lmr: true,\n            use_see_pruning: true,\n            use_null_move_pruning: false,\n""",
    """            use_lmr: true,\n            use_adaptive_lmr: false,\n            use_see_pruning: true,\n            use_null_move_pruning: false,\n""",
)

# Carry the raw quiet/continuation/countermove history signal separately from the
# large move-ordering category score so adaptive LMR never mistakes TT/killer
# category bonuses for learned history.
replace_once(
    SEARCH,
    """struct ScoredMove {\n    mv: Move,\n    score: i32,\n    tactical: bool,\n    see: i32,\n}\n""",
    """struct ScoredMove {\n    mv: Move,\n    score: i32,\n    quiet_history: i32,\n    tactical: bool,\n    see: i32,\n}\n""",
)
replace_once(
    SEARCH,
    """            let see = if tactical {\n                static_exchange_eval(board, mv)\n            } else {\n                0\n            };\n            let score = if packed == tt_move {\n""",
    """            let see = if tactical {\n                static_exchange_eval(board, mv)\n            } else {\n                0\n            };\n            let quiet_history = if tactical {\n                0\n            } else {\n                self.histories.quiet_score(board, mv, previous)\n            };\n            let score = if packed == tt_move {\n""",
)
replace_once(
    SEARCH,
    """            } else if tactical {\n                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)\n            } else {\n                self.histories.quiet_score(board, mv, previous)\n            };\n            scored.push(ScoredMove {\n                mv,\n                score,\n                tactical,\n                see,\n            });\n""",
    """            } else if tactical {\n                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)\n            } else {\n                quiet_history\n            };\n            scored.push(ScoredMove {\n                mv,\n                score,\n                quiet_history,\n                tactical,\n                see,\n            });\n""",
)

# Preserve the existing fixed schedule bit-for-bit when the experimental switch
# is OFF. The candidate adds bounded depth/move scaling plus a one-ply learned
# history modifier and PV protection. Full-depth re-search semantics remain in
# the existing caller and are intentionally unchanged.
replace_once(
    SEARCH,
    """        if !self.options.use_lmr\n            || depth < 3\n            || move_index < 3\n            || scored.tactical\n            || in_check\n            || gives_check\n        {\n            return 0;\n        }\n        let mut reduction = 1i16;\n        if depth >= 6 && move_index >= 8 {\n            reduction += 1;\n        }\n        if depth >= 10 && move_index >= 16 && !pv_node {\n            reduction += 1;\n        }\n        reduction.min(depth - 2)\n""",
    """        lmr_reduction(\n            self.options,\n            depth,\n            move_index,\n            scored.tactical,\n            in_check,\n            gives_check,\n            pv_node,\n            scored.quiet_history,\n        )\n""",
)

replace_once(
    SEARCH,
    """fn history_bonus(depth: i16) -> i32 {\n""",
    """fn fixed_lmr_reduction(depth: i16, move_index: usize, pv_node: bool) -> i16 {\n    let mut reduction = 1i16;\n    if depth >= 6 && move_index >= 8 {\n        reduction += 1;\n    }\n    if depth >= 10 && move_index >= 16 && !pv_node {\n        reduction += 1;\n    }\n    reduction.min(depth - 2)\n}\n\nfn adaptive_lmr_reduction(\n    depth: i16,\n    move_index: usize,\n    pv_node: bool,\n    quiet_history: i32,\n) -> i16 {\n    const HISTORY_THRESHOLD: i32 = HISTORY_LIMIT / 4;\n    const MAX_REDUCTION: i16 = 4;\n\n    let mut reduction = 1i16;\n    if depth >= 6 && move_index >= 8 {\n        reduction += 1;\n    }\n    if depth >= 10 && move_index >= 16 {\n        reduction += 1;\n    }\n    if !pv_node && depth >= 8 && move_index >= 12 {\n        reduction += 1;\n    }\n    if pv_node {\n        reduction = reduction.saturating_sub(1);\n    }\n    if quiet_history >= HISTORY_THRESHOLD {\n        reduction = reduction.saturating_sub(1);\n    } else if quiet_history <= -HISTORY_THRESHOLD {\n        reduction += 1;\n    }\n\n    reduction.clamp(0, MAX_REDUCTION).min(depth - 2)\n}\n\n#[allow(clippy::too_many_arguments)]\nfn lmr_reduction(\n    options: SearchOptions,\n    depth: i16,\n    move_index: usize,\n    tactical: bool,\n    in_check: bool,\n    gives_check: bool,\n    pv_node: bool,\n    quiet_history: i32,\n) -> i16 {\n    if !options.use_lmr\n        || depth < 3\n        || move_index < 3\n        || tactical\n        || in_check\n        || gives_check\n    {\n        return 0;\n    }\n    if options.use_adaptive_lmr {\n        adaptive_lmr_reduction(depth, move_index, pv_node, quiet_history)\n    } else {\n        fixed_lmr_reduction(depth, move_index, pv_node)\n    }\n}\n\nfn history_bonus(depth: i16) -> i32 {\n""",
)

# Test helpers and acceptance tests. Candidate tests run with the production NMP
# feature enabled so the first integration evidence covers the actual baseline.
replace_once(
    SEARCH,
    """    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n""",
    """    fn run_with_adaptive_lmr(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n        let game = Te1Game::from_fen(fen).unwrap();\n        search(\n            &game,\n            SearchLimits {\n                depth: Some(depth),\n                ..SearchLimits::default()\n            },\n            Arc::new(AtomicBool::new(false)),\n            Arc::new(TranspositionTable::with_megabytes(4)),\n            SearchOptions {\n                use_null_move_pruning: true,\n                use_adaptive_lmr: enabled,\n                ..SearchOptions::default()\n            },\n        )\n        .unwrap()\n    }\n\n    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n""",
)
replace_once(
    SEARCH,
    """    #[test]\n    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {\n""",
    """    #[test]\n    fn adaptive_lmr_defaults_off_and_preserves_fixed_schedule() {\n        let options = SearchOptions::default();\n        assert!(!options.use_adaptive_lmr);\n        assert_eq!(lmr_reduction(options, 3, 3, false, false, false, false, 0), 1);\n        assert_eq!(lmr_reduction(options, 6, 8, false, false, false, false, 0), 2);\n        assert_eq!(lmr_reduction(options, 10, 16, false, false, false, true, 0), 2);\n        assert_eq!(lmr_reduction(options, 10, 16, false, false, false, false, 0), 3);\n    }\n\n    #[test]\n    fn adaptive_lmr_history_and_pv_modifiers_are_bounded() {\n        let options = SearchOptions {\n            use_adaptive_lmr: true,\n            ..SearchOptions::default()\n        };\n        let strong = lmr_reduction(options, 8, 8, false, false, false, false, HISTORY_LIMIT / 2);\n        let neutral = lmr_reduction(options, 8, 8, false, false, false, false, 0);\n        let poor = lmr_reduction(options, 8, 8, false, false, false, false, -HISTORY_LIMIT / 2);\n        let pv = lmr_reduction(options, 8, 8, false, false, false, true, 0);\n        assert!(strong < neutral && neutral < poor);\n        assert!(pv < neutral);\n\n        for depth in 3..=20 {\n            let mut previous = 0;\n            for move_index in 3..=32 {\n                let reduction =\n                    lmr_reduction(options, depth, move_index, false, false, false, false, 0);\n                assert!(reduction >= previous);\n                assert!(reduction <= (depth - 2).min(4));\n                previous = reduction;\n            }\n        }\n    }\n\n    #[test]\n    fn adaptive_lmr_preserves_tactical_and_check_exclusions() {\n        let options = SearchOptions {\n            use_adaptive_lmr: true,\n            ..SearchOptions::default()\n        };\n        assert_eq!(lmr_reduction(options, 10, 20, true, false, false, false, 0), 0);\n        assert_eq!(lmr_reduction(options, 10, 20, false, true, false, false, 0), 0);\n        assert_eq!(lmr_reduction(options, 10, 20, false, false, true, false, 0), 0);\n        assert_eq!(\n            lmr_reduction(\n                SearchOptions {\n                    use_lmr: false,\n                    use_adaptive_lmr: true,\n                    ..SearchOptions::default()\n                },\n                10,\n                20,\n                false,\n                false,\n                false,\n                false,\n                0,\n            ),\n            0\n        );\n    }\n\n    #[test]\n    fn adaptive_lmr_is_deterministic_with_production_nmp_enabled() {\n        let fen = \"r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1\";\n        let first = run_with_adaptive_lmr(fen, 6, true);\n        let second = run_with_adaptive_lmr(fen, 6, true);\n        assert_eq!(first.best_move, second.best_move);\n        assert_eq!(first.score_cp, second.score_cp);\n        assert_eq!(first.nodes, second.nodes);\n        assert_eq!(first.qnodes, second.qnodes);\n        assert_eq!(first.pv, second.pv);\n        let game = Te1Game::from_fen(fen).unwrap();\n        assert!(game.legal_moves().contains(first.best_move.as_ref().unwrap()));\n    }\n\n    #[test]\n    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {\n""",
)

# Engine/UCI experimental switch. It is default OFF and clears TT only when the
# adaptive-LMR value actually changes.
replace_once(
    ENGINE,
    """    use_lmr: bool,\n    use_see_pruning: bool,\n""",
    """    use_lmr: bool,\n    use_adaptive_lmr: bool,\n    use_see_pruning: bool,\n""",
)
replace_once(
    ENGINE,
    """            use_lmr: true,\n            use_see_pruning: true,\n""",
    """            use_lmr: true,\n            use_adaptive_lmr: false,\n            use_see_pruning: true,\n""",
)
replace_once(
    ENGINE,
    """            use_lmr: self.use_lmr,\n            use_see_pruning: self.use_see_pruning,\n""",
    """            use_lmr: self.use_lmr,\n            use_adaptive_lmr: self.use_adaptive_lmr,\n            use_see_pruning: self.use_see_pruning,\n""",
)
replace_once(
    ENGINE,
    """                        \"info string host threads {} deterministic {} lmr {} seepruning {} evaluator {}\",\n                        effective_threads(&options),\n                        options.deterministic,\n                        options.use_lmr,\n                        options.use_see_pruning,\n""",
    """                        \"info string host threads {} deterministic {} lmr {} adaptivelmr {} seepruning {} evaluator {}\",\n                        effective_threads(&options),\n                        options.deterministic,\n                        options.use_lmr,\n                        options.use_adaptive_lmr,\n                        options.use_see_pruning,\n""",
)
replace_once(
    ENGINE,
    """    println!(\"option name UseLMR type check default true\");\n    println!(\"option name UseSEEPruning type check default true\");\n""",
    """    println!(\"option name UseLMR type check default true\");\n    println!(\"option name UseAdaptiveLMR type check default false\");\n    println!(\"option name UseSEEPruning type check default true\");\n""",
)
replace_once(
    ENGINE,
    """        \"uselmr\" => options.use_lmr = parse_bool(value, \"UseLMR\")?,\n        \"useseepruning\" => {\n""",
    """        \"uselmr\" => options.use_lmr = parse_bool(value, \"UseLMR\")?,\n        \"useadaptivelmr\" => {\n            let enabled = parse_bool(value, \"UseAdaptiveLMR\")?;\n            if enabled != options.use_adaptive_lmr {\n                options.use_adaptive_lmr = enabled;\n                effects.clear_hash = true;\n            }\n        }\n        \"useseepruning\" => {\n""",
)
replace_once(
    ENGINE,
    """    #[test]\n    fn null_move_pruning_defaults_on_and_toggle_clears_tt_only_on_value_change() {\n""",
    """    #[test]\n    fn adaptive_lmr_defaults_off_and_toggle_clears_tt_only_on_value_change() {\n        let mut options = EngineOptions::default();\n        assert!(!options.use_adaptive_lmr);\n        assert!(!options.search_options().use_adaptive_lmr);\n\n        let same = set_option(\"setoption name UseAdaptiveLMR value false\", &mut options).unwrap();\n        assert_eq!(same, OptionEffects::default());\n\n        let effects = set_option(\"setoption name UseAdaptiveLMR value true\", &mut options).unwrap();\n        assert!(options.use_adaptive_lmr);\n        assert_eq!(\n            effects,\n            OptionEffects {\n                clear_hash: true,\n                reload_eval: false\n            }\n        );\n        assert!(options.search_options().use_adaptive_lmr);\n\n        let same = set_option(\"setoption name UseAdaptiveLMR value true\", &mut options).unwrap();\n        assert_eq!(same, OptionEffects::default());\n\n        let effects = set_option(\"setoption name UseAdaptiveLMR value false\", &mut options).unwrap();\n        assert!(!options.use_adaptive_lmr);\n        assert_eq!(\n            effects,\n            OptionEffects {\n                clear_hash: true,\n                reload_eval: false\n            }\n        );\n    }\n\n    #[test]\n    fn null_move_pruning_defaults_on_and_toggle_clears_tt_only_on_value_change() {\n""",
)

print("TE1_ADAPTIVE_LMR_R1_PATCH_APPLIED")
