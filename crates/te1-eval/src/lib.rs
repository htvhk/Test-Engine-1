#![forbid(unsafe_code)]

use cozy_chess::{Board, Color, Piece, Square};
use std::cell::RefCell;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, OnceLock, RwLock};
use te1_nnue::{Accumulator, InferenceScratch, Network};

const PAWN_VALUE: i32 = 100;
const KNIGHT_VALUE: i32 = 320;
const BISHOP_VALUE: i32 = 330;
const ROOK_VALUE: i32 = 500;
const QUEEN_VALUE: i32 = 900;
const BISHOP_PAIR_BONUS: i32 = 28;
const DOUBLED_PAWN_PENALTY: i32 = 12;
const ISOLATED_PAWN_PENALTY: i32 = 10;
const TEMPO_BONUS: i32 = 8;
const CHECK_PENALTY: i32 = 24;
const EMBEDDED_NETWORK_BYTES: &[u8] = include_bytes!("../networks/default.te1nn");

#[derive(Debug)]
struct EvaluatorState {
    enabled: bool,
    external: Option<Arc<Network>>,
}

impl Default for EvaluatorState {
    fn default() -> Self {
        Self {
            enabled: true,
            external: None,
        }
    }
}

#[derive(Debug)]
struct CachedAccumulator {
    generation: u64,
    board: Board,
    accumulator: Accumulator,
    network: Arc<Network>,
    scratch: InferenceScratch,
}

static STATE: OnceLock<RwLock<EvaluatorState>> = OnceLock::new();
static EMBEDDED_NETWORK: OnceLock<Result<Arc<Network>, String>> = OnceLock::new();
static GENERATION: AtomicU64 = AtomicU64::new(1);

std::thread_local! {
    static THREAD_CACHE: RefCell<Option<CachedAccumulator>> = const { RefCell::new(None) };
}

fn state() -> &'static RwLock<EvaluatorState> {
    STATE.get_or_init(|| RwLock::new(EvaluatorState::default()))
}

fn embedded_network() -> Result<Arc<Network>, String> {
    match EMBEDDED_NETWORK.get_or_init(|| Network::from_bytes(EMBEDDED_NETWORK_BYTES).map(Arc::new))
    {
        Ok(network) => Ok(Arc::clone(network)),
        Err(error) => Err(error.clone()),
    }
}

fn active_network() -> Result<Option<Arc<Network>>, String> {
    let guard = state()
        .read()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if !guard.enabled {
        return Ok(None);
    }
    if let Some(network) = &guard.external {
        return Ok(Some(Arc::clone(network)));
    }
    drop(guard);
    embedded_network().map(Some)
}

fn invalidate_cache() {
    GENERATION.fetch_add(1, Ordering::AcqRel);
}

pub fn load_nnue_file(path: impl AsRef<Path>) -> Result<String, String> {
    let network = Arc::new(Network::from_file(path)?);
    let name = network.name().to_owned();
    let mut guard = state()
        .write()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.external = Some(network);
    guard.enabled = true;
    drop(guard);
    invalidate_cache();
    Ok(name)
}

pub fn use_embedded_nnue() -> Result<String, String> {
    let name = embedded_network()?.name().to_owned();
    let mut guard = state()
        .write()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    guard.external = None;
    guard.enabled = true;
    drop(guard);
    invalidate_cache();
    Ok(name)
}

pub fn set_nnue_enabled(enabled: bool) {
    let mut guard = state()
        .write()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if guard.enabled != enabled {
        guard.enabled = enabled;
        drop(guard);
        invalidate_cache();
    }
}

#[must_use]
pub fn nnue_enabled() -> bool {
    state()
        .read()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .enabled
}

#[must_use]
pub fn evaluator_name() -> String {
    match active_network() {
        Ok(Some(network)) => format!(
            "nnue:{}:{}",
            network.name(),
            network.inference_kernel_name()
        ),
        Ok(None) => "classical".to_owned(),
        Err(error) => format!("classical (NNUE unavailable: {error})"),
    }
}

#[must_use]
pub fn evaluate(board: &Board) -> i32 {
    evaluate_nnue(board).unwrap_or_else(|_| evaluate_classical(board))
}

pub fn evaluate_nnue(board: &Board) -> Result<i32, String> {
    let generation = GENERATION.load(Ordering::Acquire);
    THREAD_CACHE.with(|cache| {
        let mut cache = cache.borrow_mut();
        let stale = cache
            .as_ref()
            .is_none_or(|current| current.generation != generation);
        if stale {
            let Some(network) = active_network()? else {
                *cache = None;
                return Ok(evaluate_classical(board));
            };
            let accumulator = network.accumulator(board)?;
            let scratch = network.inference_scratch();
            *cache = Some(CachedAccumulator {
                generation,
                board: board.clone(),
                accumulator,
                network,
                scratch,
            });
        }

        let current = cache.as_mut().expect("NNUE cache was just populated");
        if current.board != *board {
            let CachedAccumulator {
                board: cached_board,
                accumulator,
                network,
                ..
            } = current;
            accumulator.update_between(network.as_ref(), cached_board, board)?;
            *cached_board = board.clone();
        }
        let CachedAccumulator {
            accumulator,
            network,
            scratch,
            ..
        } = current;
        Ok(network.evaluate_accumulator_cp(accumulator, board.side_to_move(), scratch))
    })
}

#[must_use]
pub fn evaluate_classical(board: &Board) -> i32 {
    let white = evaluate_color(board, Color::White);
    let black = evaluate_color(board, Color::Black);
    let absolute = white - black;
    let perspective = if board.side_to_move() == Color::White {
        absolute
    } else {
        -absolute
    };
    let check = if board.checkers().is_empty() {
        0
    } else {
        -CHECK_PENALTY
    };
    perspective + TEMPO_BONUS + check
}

fn evaluate_color(board: &Board, color: Color) -> i32 {
    let mut score = 0;
    score += piece_score(board, color, Piece::Pawn, PAWN_VALUE);
    score += piece_score(board, color, Piece::Knight, KNIGHT_VALUE);
    score += piece_score(board, color, Piece::Bishop, BISHOP_VALUE);
    score += piece_score(board, color, Piece::Rook, ROOK_VALUE);
    score += piece_score(board, color, Piece::Queen, QUEEN_VALUE);
    score += piece_score(board, color, Piece::King, 0);

    if board.colored_pieces(color, Piece::Bishop).len() >= 2 {
        score += BISHOP_PAIR_BONUS;
    }

    score + pawn_structure(board, color)
}

fn piece_score(board: &Board, color: Color, piece: Piece, value: i32) -> i32 {
    board
        .colored_pieces(color, piece)
        .iter()
        .map(|square| value + positional_bonus(piece, color, square))
        .sum()
}

fn positional_bonus(piece: Piece, color: Color, square: Square) -> i32 {
    let index = square as usize;
    let file = i32::try_from(index & 7).unwrap_or_default();
    let rank = i32::try_from(index >> 3).unwrap_or_default();
    let relative_rank = if color == Color::White {
        rank
    } else {
        7 - rank
    };
    let centre_distance =
        (file - 3).abs().min((file - 4).abs()) + (rank - 3).abs().min((rank - 4).abs());

    match piece {
        Piece::Pawn => relative_rank * 4 - centre_distance,
        Piece::Knight => 14 - centre_distance * 5,
        Piece::Bishop => 10 - centre_distance * 3,
        Piece::Rook => relative_rank * 2,
        Piece::Queen => 4 - centre_distance,
        Piece::King => {
            if relative_rank <= 1 {
                8
            } else {
                -relative_rank * 3
            }
        }
    }
}

fn pawn_structure(board: &Board, color: Color) -> i32 {
    let pawns = board.colored_pieces(color, Piece::Pawn);
    let mut counts = [0u8; 8];
    for square in pawns {
        counts[square as usize & 7] = counts[square as usize & 7].saturating_add(1);
    }

    let mut score = 0;
    for (file, &file_count) in counts.iter().enumerate() {
        let count = i32::from(file_count);
        if count > 1 {
            score -= (count - 1) * DOUBLED_PAWN_PENALTY;
        }
        if count > 0 {
            let left_empty = file == 0 || counts[file - 1] == 0;
            let right_empty = file == 7 || counts[file + 1] == 0;
            if left_empty && right_empty {
                score -= count * ISOLATED_PAWN_PENALTY;
            }
        }
    }
    score
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn embedded_nnue_loads_and_evaluates() {
        let _guard = TEST_LOCK.lock().unwrap();
        let name = use_embedded_nnue().unwrap();
        assert_eq!(name, "k32-w128-h32-crelu");
        let board = Board::default();
        let score = evaluate_nnue(&board).unwrap();
        assert!(score.abs() < 20_000);
    }

    #[test]
    fn classical_fallback_remains_available() {
        let _guard = TEST_LOCK.lock().unwrap();
        let board = Board::default();
        set_nnue_enabled(false);
        assert_eq!(evaluate(&board), evaluate_classical(&board));
        set_nnue_enabled(true);
    }

    #[test]
    fn incremental_thread_cache_is_stable_across_unrelated_positions() {
        let _guard = TEST_LOCK.lock().unwrap();
        use_embedded_nnue().unwrap();
        let first: Board = "7k/8/8/8/8/8/4Q3/4K3 w - - 0 1".parse().unwrap();
        let second: Board = "4k3/4q3/8/8/8/8/8/7K w - - 0 1".parse().unwrap();
        let first_score = evaluate_nnue(&first).unwrap();
        let second_score = evaluate_nnue(&second).unwrap();
        let first_again = evaluate_nnue(&first).unwrap();
        assert_eq!(first_score, first_again);
        assert!(second_score.abs() < 20_000);
    }

    #[test]
    fn classical_material_advantage_is_detected() {
        let _guard = TEST_LOCK.lock().unwrap();
        let white_extra_queen: Board = "7k/8/8/8/8/8/4Q3/4K3 w - - 0 1".parse().unwrap();
        let black_extra_queen: Board = "4k3/4q3/8/8/8/8/8/7K w - - 0 1".parse().unwrap();
        assert!(evaluate_classical(&white_extra_queen) > 700);
        assert!(evaluate_classical(&black_extra_queen) < -700);
    }
}
