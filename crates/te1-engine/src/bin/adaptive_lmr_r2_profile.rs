#![forbid(unsafe_code)]

use std::sync::Arc;
use std::sync::atomic::AtomicBool;
use te1_chess::{START_FEN, Te1Game};
use te1_search::{AdaptiveLmrProfile, SearchLimits, SearchOptions, SearchResult, search};
use te1_tt::TranspositionTable;

struct PositionSpec {
    id: &'static str,
    fen: &'static str,
    moves: &'static str,
    depth10: bool,
}

const POSITIONS: &[PositionSpec] = &[
    PositionSpec {
        id: "startpos",
        fen: START_FEN,
        moves: "",
        depth10: true,
    },
    PositionSpec {
        id: "ruy_lopez",
        fen: START_FEN,
        moves: "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7",
        depth10: false,
    },
    PositionSpec {
        id: "nimzo_indian",
        fen: START_FEN,
        moves: "d2d4 g8f6 c2c4 e7e6 b1c3 f8b4 e2e3 e8g8 f1d3 d7d5",
        depth10: false,
    },
    PositionSpec {
        id: "sicilian_classical",
        fen: START_FEN,
        moves: "e2e4 c7c5 g1f3 d7d6 d2d4 c5d4 f3d4 g8f6 b1c3 b8c6",
        depth10: false,
    },
    PositionSpec {
        id: "queens_gambit",
        fen: START_FEN,
        moves: "d2d4 d7d5 c2c4 e7e6 b1c3 g8f6 c1g5 f8e7 e2e3 e8g8",
        depth10: false,
    },
    PositionSpec {
        id: "caro_kann_advance",
        fen: START_FEN,
        moves: "e2e4 c7c6 d2d4 d7d5 e4e5 c8f5 g1f3 e7e6 f1e2",
        depth10: false,
    },
    PositionSpec {
        id: "english_four_knights",
        fen: START_FEN,
        moves: "c2c4 e7e5 b1c3 g8f6 g1f3 b8c6 g2g3 f8b4 f1g2 e8g8",
        depth10: false,
    },
    PositionSpec {
        id: "kings_indian",
        fen: START_FEN,
        moves: "d2d4 g8f6 c2c4 g7g6 b1c3 f8g7 e2e4 d7d6 g1f3 e8g8",
        depth10: false,
    },
    PositionSpec {
        id: "kiwipete_family",
        fen: "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1",
        moves: "",
        depth10: true,
    },
    PositionSpec {
        id: "quiet_middlegame",
        fen: "2r2rk1/pp1nbppp/2p1pn2/q2p4/3P4/2N1PN2/PPQ1BPPP/2RR2K1 w - - 0 1",
        moves: "",
        depth10: true,
    },
];

fn game(spec: &PositionSpec) -> Te1Game {
    let mut game = Te1Game::from_fen(spec.fen).expect("profile FEN must be valid");
    for mv in spec.moves.split_whitespace() {
        game.play_uci(mv).expect("profile opening move must be legal");
    }
    game
}

fn run_profile(spec: &PositionSpec, depth: u8) -> SearchResult {
    search(
        &game(spec),
        SearchLimits {
            depth: Some(depth),
            ..SearchLimits::default()
        },
        Arc::new(AtomicBool::new(false)),
        Arc::new(TranspositionTable::with_megabytes(16)),
        SearchOptions {
            threads: 1,
            deterministic: true,
            use_lmr: true,
            use_adaptive_lmr: true,
            profile_adaptive_lmr: true,
            use_see_pruning: true,
            use_null_move_pruning: true,
        },
    )
    .expect("profile search must complete")
}

fn array(values: &[u64]) -> String {
    let body = values
        .iter()
        .map(u64::to_string)
        .collect::<Vec<_>>()
        .join(",");
    format!("[{body}]")
}

fn emit(id: &str, depth: u8, result: &SearchResult, p: AdaptiveLmrProfile) {
    println!(
        concat!(
            "TE1_ADAPTIVE_LMR_R2_PROFILE",
            "\tid={id}\tdepth={depth}\tbestmove={bestmove}\tscore_cp={score}",
            "\tnodes={nodes}\tqnodes={qnodes}\teligible={eligible}",
            "\tpv={pv}\tnon_pv={non_pv}\tlate={late}\tpoor={poor}\tboth={both}",
            "\textra={extra}\tsaturated={saturated}",
            "\tcountermove={countermove}\tcountermove_fixed_ge2={countermove_fixed_ge2}",
            "\ttotal_ge4096_fixed_ge2={total_ge4096}\ttotal_ge8192_fixed_ge2={total_ge8192}",
            "\tbase_ge4096_fixed_ge2={base_ge4096}\tbase_ge8192_fixed_ge2={base_ge8192}",
            "\tfixed_hist={fixed_hist}\tadaptive_hist={adaptive_hist}",
            "\ttotal_history_bins={total_history_bins}\tbase_history_bins={base_history_bins}",
            "\tdepth_bins={depth_bins}\tmove_bins={move_bins}\tdepth_move_matrix={matrix}"
        ),
        id = id,
        depth = depth,
        bestmove = result.best_move.as_deref().unwrap_or("0000"),
        score = result.score_cp,
        nodes = result.nodes,
        qnodes = result.qnodes,
        eligible = p.eligible_quiet_moves,
        pv = p.pv_eligible,
        non_pv = p.non_pv_eligible,
        late = p.current_r2_late_trigger,
        poor = p.current_r2_poor_history_trigger,
        both = p.current_r2_both_triggers,
        extra = p.current_r2_extra_reduction,
        saturated = p.current_r2_saturated_reduction,
        countermove = p.countermove_eligible,
        countermove_fixed_ge2 = p.countermove_fixed_ge2,
        total_ge4096 = p.total_history_ge4096_fixed_ge2,
        total_ge8192 = p.total_history_ge8192_fixed_ge2,
        base_ge4096 = p.base_history_ge4096_fixed_ge2,
        base_ge8192 = p.base_history_ge8192_fixed_ge2,
        fixed_hist = array(&p.fixed_reduction_hist),
        adaptive_hist = array(&p.adaptive_reduction_hist),
        total_history_bins = array(&p.total_history_bins),
        base_history_bins = array(&p.base_history_bins),
        depth_bins = array(&p.depth_bins),
        move_bins = array(&p.move_index_bins),
        matrix = array(&p.depth_move_matrix),
    );
}

fn main() {
    for spec in POSITIONS {
        let result = run_profile(spec, 8);
        assert_eq!(result.depth, 8, "depth-8 profile did not complete for {}", spec.id);
        assert!(result.adaptive_lmr_profile.eligible_quiet_moves > 0);
        emit(spec.id, 8, &result, result.adaptive_lmr_profile);
        if spec.depth10 {
            let result = run_profile(spec, 10);
            assert_eq!(result.depth, 10, "depth-10 profile did not complete for {}", spec.id);
            assert!(result.adaptive_lmr_profile.eligible_quiet_moves > 0);
            emit(spec.id, 10, &result, result.adaptive_lmr_profile);
        }
    }
}
