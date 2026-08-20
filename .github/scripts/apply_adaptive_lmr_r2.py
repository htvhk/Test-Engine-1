#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEARCH = ROOT / "crates/te1-search/src/lib.rs"
ENGINE = ROOT / "crates/te1-engine/src/main.rs"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one target, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


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
replace_once(
    SEARCH,
    """struct ScoredMove {\n    mv: Move,\n    score: i32,\n    tactical: bool,\n    see: i32,\n}\n""",
    """struct ScoredMove {\n    mv: Move,\n    score: i32,\n    quiet_history: i32,\n    tactical: bool,\n    see: i32,\n}\n""",
)

# Preserve legacy OFF-path work exactly: only compute an extra history value for
# TT/killer quiets when the experimental R2 switch is ON. Ordinary quiet moves
# still perform the same single quiet_score call they did in the baseline.
replace_once(
    SEARCH,
    """            let see = if tactical {\n                static_exchange_eval(board, mv)\n            } else {\n                0\n            };\n            let score = if packed == tt_move {\n""",
    """            let see = if tactical {\n                static_exchange_eval(board, mv)\n            } else {\n                0\n            };\n            let quiet_history = if !tactical && self.options.use_adaptive_lmr {\n                self.histories.quiet_score(board, mv, previous)\n            } else {\n                0\n            };\n            let score = if packed == tt_move {\n""",
)
replace_once(
    SEARCH,
    """            } else if tactical {\n                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)\n            } else {\n                self.histories.quiet_score(board, mv, previous)\n            };\n            scored.push(ScoredMove {\n                mv,\n                score,\n                tactical,\n                see,\n            });\n""",
    """            } else if tactical {\n                -1_000_000 + see.saturating_mul(16) + self.histories.capture_score(board, mv)\n            } else if self.options.use_adaptive_lmr {\n                quiet_history\n            } else {\n                self.histories.quiet_score(board, mv, previous)\n            };\n            scored.push(ScoredMove {\n                mv,\n                score,\n                quiet_history,\n                tactical,\n                see,\n            });\n""",
)
replace_once(
    SEARCH,
    """        if !self.options.use_lmr\n            || depth < 3\n            || move_index < 3\n            || scored.tactical\n            || in_check\n            || gives_check\n        {\n            return 0;\n        }\n        let mut reduction = 1i16;\n        if depth >= 6 && move_index >= 8 {\n            reduction += 1;\n        }\n        if depth >= 10 && move_index >= 16 && !pv_node {\n            reduction += 1;\n        }\n        reduction.min(depth - 2)\n""",
    """        lmr_reduction(\n            self.options,\n            depth,\n            move_index,\n            scored.tactical,\n            in_check,\n            gives_check,\n            pv_node,\n            scored.quiet_history,\n        )\n""",
)
replace_once(
    SEARCH,
    """fn history_bonus(depth: i16) -> i32 {\n""",
    """fn fixed_lmr_reduction(depth: i16, move_index: usize, pv_node: bool) -> i16 {\n    let mut reduction = 1i16;\n    if depth >= 6 && move_index >= 8 {\n        reduction += 1;\n    }\n    if depth >= 10 && move_index >= 16 && !pv_node {\n        reduction += 1;\n    }\n    reduction.min(depth - 2)\n}\n\nfn adaptive_lmr_reduction(\n    depth: i16,\n    move_index: usize,\n    pv_node: bool,\n    quiet_history: i32,\n) -> i16 {\n    const HISTORY_THRESHOLD: i32 = HISTORY_LIMIT / 4;\n    const MAX_REDUCTION: i16 = 4;\n\n    let mut reduction = fixed_lmr_reduction(depth, move_index, pv_node);\n    if !pv_node {\n        if depth >= 6 && move_index >= 12 {\n            reduction += 1;\n        }\n        if quiet_history <= -HISTORY_THRESHOLD {\n            reduction += 1;\n        }\n    }\n    reduction.min(MAX_REDUCTION).min(depth - 2)\n}\n\n#[allow(clippy::too_many_arguments)]\nfn lmr_reduction(\n    options: SearchOptions,\n    depth: i16,\n    move_index: usize,\n    tactical: bool,\n    in_check: bool,\n    gives_check: bool,\n    pv_node: bool,\n    quiet_history: i32,\n) -> i16 {\n    if !options.use_lmr\n        || depth < 3\n        || move_index < 3\n        || tactical\n        || in_check\n        || gives_check\n    {\n        return 0;\n    }\n    if options.use_adaptive_lmr {\n        adaptive_lmr_reduction(depth, move_index, pv_node, quiet_history)\n    } else {\n        fixed_lmr_reduction(depth, move_index, pv_node)\n    }\n}\n\nfn history_bonus(depth: i16) -> i32 {\n""",
)

replace_once(
    SEARCH,
    """    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n""",
    """    fn run_with_adaptive_lmr(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n        let game = Te1Game::from_fen(fen).unwrap();\n        search(\n            &game,\n            SearchLimits {\n                depth: Some(depth),\n                ..SearchLimits::default()\n            },\n            Arc::new(AtomicBool::new(false)),\n            Arc::new(TranspositionTable::with_megabytes(4)),\n            SearchOptions {\n                use_null_move_pruning: true,\n                use_adaptive_lmr: enabled,\n                ..SearchOptions::default()\n            },\n        )\n        .unwrap()\n    }\n\n    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {\n""",
)
replace_once(
    SEARCH,
    """    #[test]\n    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {\n""",
    """    #[test]\n    fn adaptive_lmr_r2_defaults_off_and_preserves_fixed_schedule() {\n        let options = SearchOptions::default();\n        assert!(!options.use_adaptive_lmr);\n        for (depth, index, pv) in [(3, 3, false), (6, 8, false), (8, 12, true), (10, 16, false)] {\n            assert_eq!(\n                lmr_reduction(options, depth, index, false, false, false, pv, -HISTORY_LIMIT),\n                fixed_lmr_reduction(depth, index, pv)\n            );\n        }\n    }\n\n    #[test]\n    fn adaptive_lmr_r2_never_reduces_less_than_fixed_lmr() {\n        let options = SearchOptions {\n            use_adaptive_lmr: true,\n            ..SearchOptions::default()\n        };\n        for depth in 3..=20 {\n            for move_index in 3..=32 {\n                for pv_node in [false, true] {\n                    for history in [-HISTORY_LIMIT, -HISTORY_LIMIT / 2, 0, HISTORY_LIMIT / 2, HISTORY_LIMIT] {\n                        let fixed = fixed_lmr_reduction(depth, move_index, pv_node);\n                        let adaptive = lmr_reduction(\n                            options, depth, move_index, false, false, false, pv_node, history\n                        );\n                        assert!(adaptive >= fixed);\n                        assert!(adaptive <= (depth - 2).min(4));\n                    }\n                }\n            }\n        }\n    }\n\n    #[test]\n    fn adaptive_lmr_r2_targets_only_late_non_pv_or_bad_history() {\n        let options = SearchOptions {\n            use_adaptive_lmr: true,\n            ..SearchOptions::default()\n        };\n        let fixed = fixed_lmr_reduction(6, 12, false);\n        let neutral = lmr_reduction(options, 6, 12, false, false, false, false, 0);\n        let poor = lmr_reduction(options, 6, 12, false, false, false, false, -HISTORY_LIMIT / 2);\n        let pv = lmr_reduction(options, 6, 12, false, false, false, true, -HISTORY_LIMIT);\n        assert_eq!(neutral, fixed + 1);\n        assert_eq!(poor, (fixed + 2).min(4));\n        assert_eq!(pv, fixed_lmr_reduction(6, 12, true));\n    }\n\n    #[test]\n    fn adaptive_lmr_r2_preserves_tactical_and_check_exclusions() {\n        let options = SearchOptions {\n            use_adaptive_lmr: true,\n            ..SearchOptions::default()\n        };\n        assert_eq!(lmr_reduction(options, 10, 20, true, false, false, false, -HISTORY_LIMIT), 0);\n        assert_eq!(lmr_reduction(options, 10, 20, false, true, false, false, -HISTORY_LIMIT), 0);\n        assert_eq!(lmr_reduction(options, 10, 20, false, false, true, false, -HISTORY_LIMIT), 0);\n    }\n\n    #[test]\n    fn adaptive_lmr_r2_is_deterministic_with_production_nmp_enabled() {\n        let fen = \"r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1\";\n        let first = run_with_adaptive_lmr(fen, 6, true);\n        let second = run_with_adaptive_lmr(fen, 6, true);\n        assert_eq!(first.best_move, second.best_move);\n        assert_eq!(first.score_cp, second.score_cp);\n        assert_eq!(first.nodes, second.nodes);\n        assert_eq!(first.qnodes, second.qnodes);\n        assert_eq!(first.pv, second.pv);\n        let game = Te1Game::from_fen(fen).unwrap();\n        assert!(game.legal_moves().contains(first.best_move.as_ref().unwrap()));\n    }\n\n    #[test]\n    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {\n""",
)

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
    """    #[test]\n    fn adaptive_lmr_defaults_off_and_toggle_clears_tt_only_on_value_change() {\n        let mut options = EngineOptions::default();\n        assert!(!options.use_adaptive_lmr);\n        assert!(!options.search_options().use_adaptive_lmr);\n        let same = set_option(\"setoption name UseAdaptiveLMR value false\", &mut options).unwrap();\n        assert_eq!(same, OptionEffects::default());\n        let effects = set_option(\"setoption name UseAdaptiveLMR value true\", &mut options).unwrap();\n        assert!(options.use_adaptive_lmr);\n        assert_eq!(effects, OptionEffects { clear_hash: true, reload_eval: false });\n        let same = set_option(\"setoption name UseAdaptiveLMR value true\", &mut options).unwrap();\n        assert_eq!(same, OptionEffects::default());\n        let effects = set_option(\"setoption name UseAdaptiveLMR value false\", &mut options).unwrap();\n        assert!(!options.use_adaptive_lmr);\n        assert_eq!(effects, OptionEffects { clear_hash: true, reload_eval: false });\n    }\n\n    #[test]\n    fn null_move_pruning_defaults_on_and_toggle_clears_tt_only_on_value_change() {\n""",
)

print("TE1_ADAPTIVE_LMR_R2_PATCH_APPLIED")
