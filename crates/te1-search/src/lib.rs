#![forbid(unsafe_code)]

use cozy_chess::{
    BitBoard, Board, Color, Move, Piece, Square, get_bishop_moves, get_king_moves,
    get_knight_moves, get_pawn_attacks, get_rook_moves,
};
use serde::Serialize;
use std::cmp::Ordering as CmpOrdering;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant};
use te1_chess::{
    PackedMove, SearchPosition, Te1Game, captured_piece, has_legal_moves, is_capture,
    legal_moves_unsorted, move_to_uci, piece_index, piece_value, promotion_gain,
};
use te1_tt::{Bound, TranspositionTable};

pub const MATE_SCORE: i32 = 30_000;
const INFINITY: i32 = 32_000;
const MAX_PLY: usize = 128;
const MAX_QUIESCENCE_PLY: usize = 16;
const ASPIRATION_WINDOW: i32 = 50;
const HISTORY_LIMIT: i32 = 16_384;
const CONTINUATION_DIM: usize = 6 * 64;
const SEARCH_THREAD_STACK_BYTES: usize = 4 * 1024 * 1024;
const MAX_SEARCH_TIME: Duration = Duration::from_secs(24 * 60 * 60);

#[derive(Debug, Clone, Copy, Default)]
pub struct SearchLimits {
    pub depth: Option<u8>,
    pub nodes: Option<u64>,
    pub movetime: Option<Duration>,
    pub infinite: bool,
}

#[derive(Debug, Clone, Copy)]
pub struct SearchOptions {
    pub threads: usize,
    pub deterministic: bool,
    pub use_lmr: bool,
    pub use_see_pruning: bool,
    pub use_null_move_pruning: bool,
}

impl Default for SearchOptions {
    fn default() -> Self {
        Self {
            threads: 1,
            deterministic: true,
            use_lmr: true,
            use_see_pruning: true,
            use_null_move_pruning: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SearchResult {
    pub best_move: Option<String>,
    pub score_cp: i32,
    pub depth: u8,
    pub seldepth: u16,
    pub nodes: u64,
    pub qnodes: u64,
    pub tt_hits: u64,
    pub beta_cutoffs: u64,
    pub elapsed_ms: u128,
    pub pv: Vec<String>,
    pub stopped: bool,
    pub threads: usize,
    pub hashfull_per_mille: u16,
}

#[derive(Debug)]
struct SharedControl {
    stop: Arc<AtomicBool>,
    total_nodes: AtomicU64,
    node_limit: Option<u64>,
    deadline: Option<Instant>,
}

impl SharedControl {
    fn reserve_node(&self) -> bool {
        if self.stop.load(Ordering::Relaxed) {
            return false;
        }

        let previous = if let Some(limit) = self.node_limit {
            match self
                .total_nodes
                .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |nodes| {
                    (nodes < limit).then_some(nodes.saturating_add(1))
                }) {
                Ok(nodes) => nodes,
                Err(_) => {
                    self.stop.store(true, Ordering::Relaxed);
                    return false;
                }
            }
        } else {
            self.total_nodes.fetch_add(1, Ordering::Relaxed)
        };

        if previous & 1_023 == 0
            && self
                .deadline
                .is_some_and(|deadline| Instant::now() >= deadline)
        {
            self.stop.store(true, Ordering::Relaxed);
            return false;
        }
        true
    }

    fn should_stop(&self) -> bool {
        if self.stop.load(Ordering::Relaxed) {
            return true;
        }
        if self
            .deadline
            .is_some_and(|deadline| Instant::now() >= deadline)
        {
            self.stop.store(true, Ordering::Relaxed);
            return true;
        }
        false
    }
}

#[derive(Debug, Clone, Copy, Default)]
struct WorkerStats {
    nodes: u64,
    qnodes: u64,
    tt_hits: u64,
    beta_cutoffs: u64,
    seldepth: u16,
}

#[derive(Debug, Clone)]
struct WorkerOutput {
    worker_id: usize,
    best_move: PackedMove,
    score: i32,
    depth: u8,
    pv: PvLine,
    stats: WorkerStats,
    stopped: bool,
}

#[derive(Debug, Clone)]
struct PvLine {
    moves: [PackedMove; MAX_PLY],
    len: usize,
}

impl Default for PvLine {
    fn default() -> Self {
        Self {
            moves: [PackedMove::NONE; MAX_PLY],
            len: 0,
        }
    }
}

impl PvLine {
    fn clear(&mut self) {
        self.len = 0;
    }

    fn prepend(&mut self, mv: Move, child: &Self) {
        self.moves[0] = PackedMove::from_move(mv);
        let copied = child.len.min(MAX_PLY - 1);
        if copied > 0 {
            self.moves[1..1 + copied].copy_from_slice(&child.moves[..copied]);
        }
        self.len = copied + 1;
    }

    fn first(&self) -> PackedMove {
        self.moves[0]
    }
}

#[derive(Debug, Clone, Copy)]
struct MoveContext {
    packed: PackedMove,
    piece: Piece,
    to: usize,
}

#[derive(Debug, Clone, Copy)]
struct ScoredMove {
    mv: Move,
    score: i32,
    tactical: bool,
    see: i32,
}

#[derive(Debug)]
struct HistoryTables {
    quiet: Box<[i16]>,
    capture: Box<[i16]>,
    continuation: Box<[i16]>,
    countermove: Box<[PackedMove]>,
    killers: [[PackedMove; 2]; MAX_PLY],
}

impl Default for HistoryTables {
    fn default() -> Self {
        Self {
            quiet: vec![0; 2 * 64 * 64].into_boxed_slice(),
            capture: vec![0; 2 * 6 * 64 * 6].into_boxed_slice(),
            continuation: vec![0; CONTINUATION_DIM * CONTINUATION_DIM].into_boxed_slice(),
            countermove: vec![PackedMove::NONE; 64 * 64].into_boxed_slice(),
            killers: [[PackedMove::NONE; 2]; MAX_PLY],
        }
    }
}

impl HistoryTables {
    fn quiet_score(&self, board: &Board, mv: Move, previous: Option<MoveContext>) -> i32 {
        let side = color_index(board.side_to_move());
        let mut score = i32::from(self.quiet[quiet_index(side, mv)]);
        if let (Some(previous), Some(piece)) = (previous, board.piece_on(mv.from)) {
            score += i32::from(
                self.continuation
                    [continuation_index(previous.piece, previous.to, piece, mv.to as usize)],
            );
            if self.countermove[counter_index(previous.packed)] == PackedMove::from_move(mv) {
                score += 8_000;
            }
        }
        score
    }

    fn capture_score(&self, board: &Board, mv: Move) -> i32 {
        let Some(moving) = board.piece_on(mv.from) else {
            return 0;
        };
        let victim = captured_piece(board, mv).unwrap_or(Piece::Pawn);
        i32::from(
            self.capture[capture_index(
                color_index(board.side_to_move()),
                moving,
                mv.to as usize,
                victim,
            )],
        )
    }

    fn record_quiet_cutoff(
        &mut self,
        board: &Board,
        mv: Move,
        previous: Option<MoveContext>,
        ply: usize,
        depth: i16,
        searched_quiets: &[Move],
    ) {
        let packed = PackedMove::from_move(mv);
        if ply < MAX_PLY && self.killers[ply][0] != packed {
            self.killers[ply][1] = self.killers[ply][0];
            self.killers[ply][0] = packed;
        }
        let bonus = history_bonus(depth);
        let side = color_index(board.side_to_move());
        update_history(&mut self.quiet[quiet_index(side, mv)], bonus);
        if let (Some(previous), Some(piece)) = (previous, board.piece_on(mv.from)) {
            let index = continuation_index(previous.piece, previous.to, piece, mv.to as usize);
            update_history(&mut self.continuation[index], bonus);
            self.countermove[counter_index(previous.packed)] = packed;
        }
        let penalty = -(bonus / 2);
        for searched in searched_quiets {
            if *searched == mv {
                continue;
            }
            update_history(&mut self.quiet[quiet_index(side, *searched)], penalty);
            if let (Some(previous), Some(piece)) = (previous, board.piece_on(searched.from)) {
                let index =
                    continuation_index(previous.piece, previous.to, piece, searched.to as usize);
                update_history(&mut self.continuation[index], penalty);
            }
        }
    }

    fn record_capture_cutoff(
        &mut self,
        board: &Board,
        mv: Move,
        depth: i16,
        searched_captures: &[Move],
    ) {
        let Some(moving) = board.piece_on(mv.from) else {
            return;
        };
        let victim = captured_piece(board, mv).unwrap_or(Piece::Pawn);
        let side = color_index(board.side_to_move());
        let bonus = history_bonus(depth);
        let index = capture_index(side, moving, mv.to as usize, victim);
        update_history(&mut self.capture[index], bonus);
        let penalty = -(bonus / 2);
        for searched in searched_captures {
            if *searched == mv {
                continue;
            }
            let Some(searched_piece) = board.piece_on(searched.from) else {
                continue;
            };
            let searched_victim = captured_piece(board, *searched).unwrap_or(Piece::Pawn);
            let index = capture_index(side, searched_piece, searched.to as usize, searched_victim);
            update_history(&mut self.capture[index], penalty);
        }
    }
}

#[derive(Debug)]
struct Worker {
    id: usize,
    shared: Arc<SharedControl>,
    table: Arc<TranspositionTable>,
    options: SearchOptions,
    histories: HistoryTables,
    stats: WorkerStats,
    aborted: bool,
}

pub fn search(
    game: &Te1Game,
    limits: SearchLimits,
    stop: Arc<AtomicBool>,
    table: Arc<TranspositionTable>,
    options: SearchOptions,
) -> Result<SearchResult, String> {
    let start = Instant::now();
    let deadline = if limits.infinite {
        None
    } else {
        limits.movetime.map(|duration| {
            start
                .checked_add(duration.min(MAX_SEARCH_TIME))
                .unwrap_or(start)
        })
    };
    let thread_count = if options.deterministic {
        1
    } else {
        options.threads.clamp(1, 256)
    };
    let shared = Arc::new(SharedControl {
        stop,
        total_nodes: AtomicU64::new(0),
        node_limit: limits.nodes,
        deadline,
    });
    table.new_search();
    let root = SearchPosition::from_game(game);

    let outputs = thread::scope(|scope| -> Result<Vec<WorkerOutput>, String> {
        let mut handles = Vec::with_capacity(thread_count);
        for worker_id in 0..thread_count {
            let worker_root = root.clone();
            let worker_shared = Arc::clone(&shared);
            let worker_table = Arc::clone(&table);
            let worker_options = options;
            let handle = match thread::Builder::new()
                .name(format!("te1-search-{worker_id}"))
                .stack_size(SEARCH_THREAD_STACK_BYTES)
                .spawn_scoped(scope, move || {
                    let panic_stop = Arc::clone(&worker_shared.stop);
                    let result = catch_unwind(AssertUnwindSafe(|| {
                        let mut worker = Worker {
                            id: worker_id,
                            shared: worker_shared,
                            table: worker_table,
                            options: worker_options,
                            histories: HistoryTables::default(),
                            stats: WorkerStats::default(),
                            aborted: false,
                        };
                        worker.iterative_deepening(worker_root, limits)
                    }));
                    match result {
                        Ok(output) => Ok(output),
                        Err(_) => {
                            panic_stop.store(true, Ordering::Relaxed);
                            Err(format!("search worker {worker_id} panicked"))
                        }
                    }
                }) {
                Ok(handle) => handle,
                Err(error) => {
                    shared.stop.store(true, Ordering::Relaxed);
                    return Err(format!(
                        "failed to spawn search worker {worker_id}: {error}"
                    ));
                }
            };
            handles.push((worker_id, handle));
        }

        let mut outputs = Vec::with_capacity(thread_count);
        for (worker_id, handle) in handles {
            match handle.join() {
                Ok(Ok(output)) => outputs.push(output),
                Ok(Err(error)) => return Err(error),
                Err(_) => {
                    shared.stop.store(true, Ordering::Relaxed);
                    return Err(format!(
                        "search worker {worker_id} panicked outside the guarded search body"
                    ));
                }
            }
        }
        Ok(outputs)
    })?;

    let selected = outputs
        .iter()
        .max_by(|left, right| compare_worker_outputs(left, right))
        .cloned()
        .ok_or_else(|| "search produced no worker output".to_owned())?;
    let pv = pv_to_uci(&root, &selected.pv);
    let best_move = pv.first().cloned().or_else(|| {
        selected
            .best_move
            .to_move()
            .filter(|mv| root.board().is_legal(*mv))
            .map(|mv| move_to_uci(root.board(), mv))
    });
    let elapsed_ms = start.elapsed().as_millis();
    let aggregate = aggregate_stats(&outputs);

    Ok(SearchResult {
        best_move,
        score_cp: selected.score,
        depth: selected.depth,
        seldepth: aggregate.seldepth,
        nodes: shared.total_nodes.load(Ordering::Relaxed),
        qnodes: aggregate.qnodes,
        tt_hits: aggregate.tt_hits,
        beta_cutoffs: aggregate.beta_cutoffs,
        elapsed_ms,
        pv,
        stopped: shared.stop.load(Ordering::Relaxed) || selected.stopped,
        threads: thread_count,
        hashfull_per_mille: table.hashfull_per_mille(),
    })
}

impl Worker {
    fn iterative_deepening(
        &mut self,
        mut root: SearchPosition,
        limits: SearchLimits,
    ) -> WorkerOutput {
        let maximum_depth = limits.depth.unwrap_or(64).max(1);
        let fallback = legal_moves_unsorted(root.board())
            .first()
            .copied()
            .map(PackedMove::from_move)
            .unwrap_or(PackedMove::NONE);
        let mut completed = WorkerOutput {
            worker_id: self.id,
            best_move: fallback,
            score: 0,
            depth: 0,
            pv: PvLine::default(),
            stats: self.stats,
            stopped: false,
        };
        let mut previous_score = 0i32;

        for depth in 1..=maximum_depth {
            if self.shared.should_stop() {
                self.aborted = true;
                break;
            }
            let helper_width = i32::try_from(self.id.min(8)).unwrap_or(8) * 8;
            let window = ASPIRATION_WINDOW + helper_width;
            let (mut alpha, mut beta) = if depth >= 2 {
                (
                    previous_score.saturating_sub(window),
                    previous_score.saturating_add(window),
                )
            } else {
                (-INFINITY, INFINITY)
            };

            let mut pv = PvLine::default();
            let mut score = self.root_search(&mut root, i16::from(depth), alpha, beta, &mut pv);
            if !self.aborted && (score <= alpha || score >= beta) {
                alpha = -INFINITY;
                beta = INFINITY;
                pv.clear();
                score = self.root_search(&mut root, i16::from(depth), alpha, beta, &mut pv);
            }
            if self.aborted {
                break;
            }

            previous_score = score;
            completed.best_move = if pv.first().is_none() {
                completed.best_move
            } else {
                pv.first()
            };
            completed.score = score;
            completed.depth = depth;
            completed.pv = pv;
            completed.stats = self.stats;
            if score.abs() >= MATE_SCORE - i32::from(depth) {
                break;
            }
        }
        if limits.infinite {
            while !self.shared.should_stop() {
                thread::sleep(Duration::from_millis(1));
            }
            self.aborted = true;
        }
        completed.stats = self.stats;
        completed.stopped = self.aborted;
        completed
    }

    fn root_search(
        &mut self,
        position: &mut SearchPosition,
        depth: i16,
        mut alpha: i32,
        beta: i32,
        pv: &mut PvLine,
    ) -> i32 {
        pv.clear();
        if !self.enter_node(false, 0) {
            return te1_eval::evaluate(position.board());
        }
        if let Some(score) = draw_score(position, 0) {
            return score;
        }
        let original_alpha = alpha;
        let tt_move = self
            .table
            .probe(position.search_key())
            .map_or(PackedMove::NONE, |entry| entry.best_move);
        let mut moves = self.ordered_moves(position.board(), tt_move, None, 0);
        if moves.is_empty() {
            return if position.board().checkers().is_empty() {
                0
            } else {
                -MATE_SCORE
            };
        }
        if self.id > 0 && moves.len() > 1 {
            let shift = self.id % moves.len();
            moves.rotate_left(shift);
        }

        let mut best_score = -INFINITY;
        let mut best_move = PackedMove::NONE;
        for (index, scored) in moves.into_iter().enumerate() {
            if self.shared.should_stop() {
                self.aborted = true;
                break;
            }
            let Some(moved_piece) = position.board().piece_on(scored.mv.from) else {
                debug_assert!(false, "legal root move must have a moving piece");
                continue;
            };
            let context = MoveContext {
                packed: PackedMove::from_move(scored.mv),
                piece: moved_piece,
                to: scored.mv.to as usize,
            };
            let undo = position.make_move(scored.mv);
            let mut child_pv = PvLine::default();
            let score = if index == 0 {
                -self.negamax(
                    position,
                    depth - 1,
                    1,
                    -beta,
                    -alpha,
                    true,
                    Some(context),
                    false,
                    &mut child_pv,
                )
            } else {
                let mut value = -self.negamax(
                    position,
                    depth - 1,
                    1,
                    -alpha - 1,
                    -alpha,
                    false,
                    Some(context),
                    false,
                    &mut child_pv,
                );
                if !self.aborted && value > alpha && value < beta {
                    child_pv.clear();
                    value = -self.negamax(
                        position,
                        depth - 1,
                        1,
                        -beta,
                        -alpha,
                        true,
                        Some(context),
                        false,
                        &mut child_pv,
                    );
                }
                value
            };
            position.unmake_move(undo);
            if self.aborted {
                break;
            }
            if score > best_score {
                best_score = score;
                best_move = PackedMove::from_move(scored.mv);
                pv.prepend(scored.mv, &child_pv);
            }
            alpha = alpha.max(score);
            if alpha >= beta {
                self.stats.beta_cutoffs = self.stats.beta_cutoffs.saturating_add(1);
                break;
            }
        }

        if best_score == -INFINITY {
            return te1_eval::evaluate(position.board());
        }
        if !self.aborted {
            let bound = classify_bound(best_score, original_alpha, beta);
            self.table.store(
                position.search_key(),
                position.history_context(),
                depth,
                score_to_table(best_score, 0),
                bound,
                best_move,
            );
        }
        best_score
    }

    #[allow(clippy::too_many_arguments)]
    fn negamax(
        &mut self,
        position: &mut SearchPosition,
        depth: i16,
        ply: usize,
        mut alpha: i32,
        mut beta: i32,
        pv_node: bool,
        previous: Option<MoveContext>,
        null_subtree: bool,
        pv: &mut PvLine,
    ) -> i32 {
        pv.clear();
        if depth <= 0 {
            return self.quiescence(position, ply, 0, alpha, beta, previous, pv);
        }
        if !self.enter_node(false, ply) {
            return te1_eval::evaluate(position.board());
        }
        if let Some(score) = draw_score(position, ply) {
            return score;
        }
        if ply >= MAX_PLY - 1 {
            let in_check = !position.board().checkers().is_empty();
            if in_check && !has_legal_moves(position.board()) {
                return -MATE_SCORE + i32::try_from(ply).unwrap_or(i32::MAX);
            }
            return te1_eval::evaluate(position.board());
        }

        let ply_i32 = i32::try_from(ply).unwrap_or(i32::MAX);
        alpha = alpha.max(-MATE_SCORE + ply_i32);
        beta = beta.min(MATE_SCORE - ply_i32 - 1);
        if alpha >= beta {
            return alpha;
        }

        let key = position.search_key();
        let history_context = position.history_context();
        let original_alpha = alpha;
        let mut tt_move = PackedMove::NONE;
        let mut tt_upper_contradicts = false;
        if let Some(entry) = self.table.probe(key) {
            self.stats.tt_hits = self.stats.tt_hits.saturating_add(1);
            tt_move = entry.best_move;
            let context_match = entry.history_context == history_context;
            if context_match {
                tt_upper_contradicts = entry.depth >= depth
                    && entry.bound == Bound::Upper
                    && score_from_table(entry.score, ply) < beta;
                if position.tt_cutoff_safe() && entry.depth >= depth {
                    let score = score_from_table(entry.score, ply);
                    match entry.bound {
                        Bound::Exact if !pv_node => return score,
                        Bound::Exact
                            if reconstruct_exact_tt_pv(
                                self.table.as_ref(),
                                position,
                                depth,
                                pv,
                            ) =>
                        {
                            return score;
                        }
                        Bound::Exact => {}
                        Bound::Lower if score >= beta => return score,
                        Bound::Upper if score <= alpha => return score,
                        Bound::Lower | Bound::Upper => {}
                    }
                }
            }
        }

        let in_check = !position.board().checkers().is_empty();
        let static_eval = if self.options.use_null_move_pruning {
            te1_eval::evaluate(position.board())
        } else {
            i32::MIN
        };
        let can_try_null = null_move_eligible(
            self.options,
            position.board(),
            depth,
            beta,
            pv_node,
            null_subtree,
            tt_upper_contradicts,
            static_eval,
        );
        if can_try_null
            && has_legal_moves(position.board())
            && let Some(undo) = position.make_null_move()
        {
            let mut null_pv = PvLine::default();
            let null_score = -self.negamax(
                position,
                depth - 3,
                ply + 1,
                -beta,
                -beta + 1,
                false,
                None,
                true,
                &mut null_pv,
            );
            position.unmake_null_move(undo);
            if self.aborted {
                return static_eval;
            }
            if null_score >= beta {
                if depth < 8 {
                    return beta;
                }
                let mut verification_pv = PvLine::default();
                let verification = self.negamax(
                    position,
                    depth - 1,
                    ply,
                    beta - 1,
                    beta,
                    false,
                    previous,
                    true,
                    &mut verification_pv,
                );
                if self.aborted {
                    return static_eval;
                }
                if verification >= beta {
                    return beta;
                }
            }
        }
        let moves = self.ordered_moves(position.board(), tt_move, previous, ply);
        if moves.is_empty() {
            return if in_check { -MATE_SCORE + ply_i32 } else { 0 };
        }

        let mut best_score = -INFINITY;
        let mut best_move = PackedMove::NONE;
        let mut searched_quiets = Vec::with_capacity(32);
        let mut searched_captures = Vec::with_capacity(16);

        for (index, scored) in moves.into_iter().enumerate() {
            if self.shared.should_stop() {
                self.aborted = true;
                break;
            }
            let Some(moved_piece) = position.board().piece_on(scored.mv.from) else {
                debug_assert!(false, "legal move must have a moving piece");
                continue;
            };
            let context = MoveContext {
                packed: PackedMove::from_move(scored.mv),
                piece: moved_piece,
                to: scored.mv.to as usize,
            };
            let undo = position.make_move(scored.mv);
            let gives_check = !position.board().checkers().is_empty();
            let new_depth = depth - 1;
            let reduction =
                self.late_move_reduction(depth, index, scored, in_check, gives_check, pv_node);
            let mut child_pv = PvLine::default();
            let score = if index == 0 {
                -self.negamax(
                    position,
                    new_depth,
                    ply + 1,
                    -beta,
                    -alpha,
                    pv_node,
                    Some(context),
                    null_subtree,
                    &mut child_pv,
                )
            } else {
                let reduced_depth = new_depth.saturating_sub(reduction);
                let mut value = -self.negamax(
                    position,
                    reduced_depth,
                    ply + 1,
                    -alpha - 1,
                    -alpha,
                    false,
                    Some(context),
                    null_subtree,
                    &mut child_pv,
                );
                if !self.aborted && reduction > 0 && value > alpha {
                    child_pv.clear();
                    value = -self.negamax(
                        position,
                        new_depth,
                        ply + 1,
                        -alpha - 1,
                        -alpha,
                        false,
                        Some(context),
                        null_subtree,
                        &mut child_pv,
                    );
                }
                if !self.aborted && value > alpha && value < beta {
                    child_pv.clear();
                    value = -self.negamax(
                        position,
                        new_depth,
                        ply + 1,
                        -beta,
                        -alpha,
                        pv_node,
                        Some(context),
                        null_subtree,
                        &mut child_pv,
                    );
                }
                value
            };
            position.unmake_move(undo);
            if self.aborted {
                break;
            }

            if scored.tactical {
                searched_captures.push(scored.mv);
            } else {
                searched_quiets.push(scored.mv);
            }
            if score > best_score {
                best_score = score;
                best_move = PackedMove::from_move(scored.mv);
                if score > alpha {
                    pv.prepend(scored.mv, &child_pv);
                }
            }
            alpha = alpha.max(score);
            if alpha >= beta {
                self.stats.beta_cutoffs = self.stats.beta_cutoffs.saturating_add(1);
                if scored.tactical {
                    self.histories.record_capture_cutoff(
                        position.board(),
                        scored.mv,
                        depth,
                        &searched_captures,
                    );
                } else {
                    self.histories.record_quiet_cutoff(
                        position.board(),
                        scored.mv,
                        previous,
                        ply,
                        depth,
                        &searched_quiets,
                    );
                }
                break;
            }
        }

        if best_score == -INFINITY {
            return te1_eval::evaluate(position.board());
        }
        if !self.aborted {
            self.table.store(
                key,
                position.history_context(),
                depth,
                score_to_table(best_score, ply),
                classify_bound(best_score, original_alpha, beta),
                best_move,
            );
        }
        best_score
    }

    #[allow(clippy::too_many_arguments)]
    fn quiescence(
        &mut self,
        position: &mut SearchPosition,
        ply: usize,
        qply: usize,
        mut alpha: i32,
        beta: i32,
        previous: Option<MoveContext>,
        pv: &mut PvLine,
    ) -> i32 {
        pv.clear();
        if !self.enter_node(true, ply) {
            return te1_eval::evaluate(position.board());
        }
        let in_check = !position.board().checkers().is_empty();
        if !has_legal_moves(position.board()) {
            return if in_check {
                -MATE_SCORE + i32::try_from(ply).unwrap_or(i32::MAX)
            } else {
                0
            };
        }
        if position.is_draw() {
            return 0;
        }
        if ply >= MAX_PLY - 1 || (qply >= MAX_QUIESCENCE_PLY && !in_check) {
            return te1_eval::evaluate(position.board());
        }
        let stand_pat = te1_eval::evaluate(position.board());
        if !in_check {
            if stand_pat >= beta {
                return stand_pat;
            }
            alpha = alpha.max(stand_pat);
        }

        let tt_move = self
            .table
            .probe(position.search_key())
            .map_or(PackedMove::NONE, |entry| entry.best_move);
        let moves = self.ordered_moves(position.board(), tt_move, previous, ply);
        debug_assert!(!moves.is_empty());

        let mut searched_legal = false;
        for scored in moves {
            if !in_check && !scored.tactical {
                continue;
            }
            searched_legal = true;
            if !in_check && self.options.use_see_pruning && scored.see < 0 {
                continue;
            }
            let gain = captured_piece(position.board(), scored.mv).map_or(0, piece_value)
                + promotion_gain(scored.mv);
            if !in_check && stand_pat.saturating_add(gain).saturating_add(200) <= alpha {
                continue;
            }
            let Some(moved_piece) = position.board().piece_on(scored.mv.from) else {
                debug_assert!(false, "legal quiescence move must have a moving piece");
                continue;
            };
            let context = MoveContext {
                packed: PackedMove::from_move(scored.mv),
                piece: moved_piece,
                to: scored.mv.to as usize,
            };
            let undo = position.make_move(scored.mv);
            let mut child_pv = PvLine::default();
            let score = -self.quiescence(
                position,
                ply + 1,
                qply + 1,
                -beta,
                -alpha,
                Some(context),
                &mut child_pv,
            );
            position.unmake_move(undo);
            if self.aborted {
                break;
            }
            if score > alpha {
                alpha = score;
                pv.prepend(scored.mv, &child_pv);
                if alpha >= beta {
                    self.stats.beta_cutoffs = self.stats.beta_cutoffs.saturating_add(1);
                    break;
                }
            }
        }
        if in_check && !searched_legal {
            -MATE_SCORE + i32::try_from(ply).unwrap_or(i32::MAX)
        } else {
            alpha
        }
    }

    fn ordered_moves(
        &self,
        board: &Board,
        tt_move: PackedMove,
        previous: Option<MoveContext>,
        ply: usize,
    ) -> Vec<ScoredMove> {
        let mut scored = Vec::with_capacity(96);
        for mv in legal_moves_unsorted(board) {
            let packed = PackedMove::from_move(mv);
            let capture = is_capture(board, mv);
            let promotion = mv.promotion.is_some();
            let tactical = capture || promotion;
            let see = if tactical {
                static_exchange_eval_fast(board, mv)
            } else {
                0
            };
            let score = if packed == tt_move {
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
                mv,
                score,
                tactical,
                see,
            });
        }
        scored.sort_unstable_by(|left, right| {
            right.score.cmp(&left.score).then_with(|| {
                PackedMove::from_move(left.mv)
                    .raw()
                    .cmp(&PackedMove::from_move(right.mv).raw())
            })
        });
        scored
    }

    fn late_move_reduction(
        &self,
        depth: i16,
        move_index: usize,
        scored: ScoredMove,
        in_check: bool,
        gives_check: bool,
        pv_node: bool,
    ) -> i16 {
        if !self.options.use_lmr
            || depth < 3
            || move_index < 3
            || scored.tactical
            || in_check
            || gives_check
        {
            return 0;
        }
        let mut reduction = 1i16;
        if depth >= 6 && move_index >= 8 {
            reduction += 1;
        }
        if depth >= 10 && move_index >= 16 && !pv_node {
            reduction += 1;
        }
        reduction.min(depth - 2)
    }

    fn enter_node(&mut self, quiescence: bool, ply: usize) -> bool {
        if !self.shared.reserve_node() {
            self.aborted = true;
            return false;
        }
        self.stats.nodes = self.stats.nodes.saturating_add(1);
        if quiescence {
            self.stats.qnodes = self.stats.qnodes.saturating_add(1);
        }
        self.stats.seldepth = self
            .stats
            .seldepth
            .max(u16::try_from(ply).unwrap_or(u16::MAX));
        true
    }
}

#[derive(Clone, Copy)]
struct SeePosition {
    pieces: [BitBoard; Piece::NUM],
    colors: [BitBoard; Color::NUM],
    kings: [Square; Color::NUM],
}

impl SeePosition {
    fn from_board(board: &Board) -> Self {
        let mut pieces = [BitBoard::EMPTY; Piece::NUM];
        let mut colors = [BitBoard::EMPTY; Color::NUM];
        for piece in Piece::ALL {
            pieces[piece as usize] = board.pieces(piece);
        }
        for color in Color::ALL {
            colors[color as usize] = board.colors(color);
        }
        Self {
            pieces,
            colors,
            kings: [board.king(Color::White), board.king(Color::Black)],
        }
    }

    fn occupied(&self) -> BitBoard {
        self.colors[Color::White as usize] | self.colors[Color::Black as usize]
    }

    fn piece_on(&self, square: Square) -> Option<Piece> {
        Piece::ALL
            .into_iter()
            .find(|piece| self.pieces[*piece as usize].has(square))
    }

    fn remove(&mut self, color: Color, piece: Piece, square: Square) {
        self.colors[color as usize] ^= square.bitboard();
        self.pieces[piece as usize] ^= square.bitboard();
    }

    fn add(&mut self, color: Color, piece: Piece, square: Square) {
        self.colors[color as usize] |= square.bitboard();
        self.pieces[piece as usize] |= square.bitboard();
        if piece == Piece::King {
            self.kings[color as usize] = square;
        }
    }

    fn attacked(&self, square: Square, by: Color) -> bool {
        let occupied = self.occupied();
        !(get_pawn_attacks(square, !by)
            & self.pieces[Piece::Pawn as usize]
            & self.colors[by as usize])
            .is_empty()
            || !(get_knight_moves(square)
                & self.pieces[Piece::Knight as usize]
                & self.colors[by as usize])
                .is_empty()
            || !(get_bishop_moves(square, occupied)
                & (self.pieces[Piece::Bishop as usize] | self.pieces[Piece::Queen as usize])
                & self.colors[by as usize])
                .is_empty()
            || !(get_rook_moves(square, occupied)
                & (self.pieces[Piece::Rook as usize] | self.pieces[Piece::Queen as usize])
                & self.colors[by as usize])
                .is_empty()
            || !(get_king_moves(square)
                & self.pieces[Piece::King as usize]
                & self.colors[by as usize])
                .is_empty()
    }

    fn attackers(&self, side: Color, piece: Piece, target: Square) -> BitBoard {
        let occupied = self.occupied();
        let attacks = match piece {
            Piece::Pawn => get_pawn_attacks(target, !side),
            Piece::Knight => get_knight_moves(target),
            Piece::Bishop => get_bishop_moves(target, occupied),
            Piece::Rook => get_rook_moves(target, occupied),
            Piece::Queen => get_bishop_moves(target, occupied) | get_rook_moves(target, occupied),
            Piece::King => get_king_moves(target),
        };
        attacks & self.pieces[piece as usize] & self.colors[side as usize]
    }

    fn legal_capture_score(
        &self,
        side: Color,
        piece: Piece,
        from: Square,
        target: Square,
        victim: Piece,
        placed: Piece,
    ) -> Option<i32> {
        let mut next = *self;
        next.remove(!side, victim, target);
        next.remove(side, piece, from);
        next.add(side, placed, target);
        if next.attacked(next.kings[side as usize], !side) {
            return None;
        }
        let promotion_bonus = piece_value(placed).saturating_sub(piece_value(piece));
        let gain = piece_value(victim).saturating_add(promotion_bonus);
        Some(gain.saturating_sub(next.best_reply(!side, target)))
    }

    fn best_reply(&self, side: Color, target: Square) -> i32 {
        let Some(victim) = self.piece_on(target) else {
            return 0;
        };
        let mut best = 0i32;
        let promotes =
            target.rank() == cozy_chess::Rank::First || target.rank() == cozy_chess::Rank::Eighth;
        for piece in Piece::ALL {
            for from in self.attackers(side, piece, target) {
                if piece == Piece::Pawn && promotes {
                    for placed in [Piece::Knight, Piece::Bishop, Piece::Rook, Piece::Queen] {
                        if let Some(score) =
                            self.legal_capture_score(side, piece, from, target, victim, placed)
                        {
                            best = best.max(score);
                        }
                    }
                } else if let Some(score) =
                    self.legal_capture_score(side, piece, from, target, victim, piece)
                {
                    best = best.max(score);
                }
            }
        }
        best
    }
}

fn static_exchange_eval_fast(board: &Board, mv: Move) -> i32 {
    debug_assert!(board.is_legal(mv));
    let side = board.side_to_move();
    let moving = board.piece_on(mv.from).expect("legal move has a piece");
    // cozy-chess encodes castling as king-to-friendly-rook internally.
    // TE1 capture semantics correctly treat it as a quiet zero-gain move.
    if moving == Piece::King && board.color_on(mv.to) == Some(side) {
        return 0;
    }
    let captured = captured_piece(board, mv);
    let gain = captured.map_or(0, piece_value) + promotion_gain(mv);
    let placed = mv.promotion.unwrap_or(moving);
    let mut position = SeePosition::from_board(board);
    position.remove(side, moving, mv.from);
    if let Some(victim) = captured {
        let capture_square = if moving == Piece::Pawn
            && mv.from.file() != mv.to.file()
            && board.piece_on(mv.to).is_none()
        {
            Square::new(mv.to.file(), mv.from.rank())
        } else {
            mv.to
        };
        position.remove(!side, victim, capture_square);
    }
    position.add(side, placed, mv.to);
    gain.saturating_sub(position.best_reply(!side, mv.to))
}

#[must_use]
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

#[cfg(test)]
fn static_exchange_eval_oracle(board: &Board, mv: Move) -> i32 {
    debug_assert!(board.is_legal(mv));
    let gain = captured_piece(board, mv).map_or(0, piece_value) + promotion_gain(mv);
    let mut next = board.clone();
    next.play_unchecked(mv);
    gain - see_reply_oracle(&next, mv.to as usize)
}

#[cfg(test)]
fn see_reply_oracle(board: &Board, target: usize) -> i32 {
    let mut best = 0i32;
    for mv in legal_moves_unsorted(board) {
        if mv.to as usize != target || !is_capture(board, mv) {
            continue;
        }
        let gain = captured_piece(board, mv).map_or(0, piece_value) + promotion_gain(mv);
        let mut next = board.clone();
        next.play_unchecked(mv);
        let score = gain - see_reply_oracle(&next, target);
        best = best.max(score);
    }
    best
}

fn draw_score(position: &SearchPosition, ply: usize) -> Option<i32> {
    if !position.is_draw() {
        return None;
    }
    let in_check = !position.board().checkers().is_empty();
    if in_check && !has_legal_moves(position.board()) {
        return Some(-MATE_SCORE + i32::try_from(ply).unwrap_or(i32::MAX));
    }
    Some(0)
}

fn classify_bound(score: i32, original_alpha: i32, beta: i32) -> Bound {
    if score <= original_alpha {
        Bound::Upper
    } else if score >= beta {
        Bound::Lower
    } else {
        Bound::Exact
    }
}

fn score_to_table(score: i32, ply: usize) -> i32 {
    let ply = i32::try_from(ply).unwrap_or(i32::MAX);
    if score >= MATE_SCORE - i32::try_from(MAX_PLY).unwrap_or(i32::MAX) {
        score.saturating_add(ply)
    } else if score <= -MATE_SCORE + i32::try_from(MAX_PLY).unwrap_or(i32::MAX) {
        score.saturating_sub(ply)
    } else {
        score
    }
}

fn score_from_table(score: i32, ply: usize) -> i32 {
    let ply = i32::try_from(ply).unwrap_or(i32::MAX);
    if score >= MATE_SCORE - i32::try_from(MAX_PLY).unwrap_or(i32::MAX) {
        score.saturating_sub(ply)
    } else if score <= -MATE_SCORE + i32::try_from(MAX_PLY).unwrap_or(i32::MAX) {
        score.saturating_add(ply)
    } else {
        score
    }
}

fn reconstruct_exact_tt_pv(
    table: &TranspositionTable,
    position: &SearchPosition,
    depth: i16,
    pv: &mut PvLine,
) -> bool {
    pv.clear();
    let mut cursor = position.clone();
    let mut remaining = depth.max(0);
    while remaining > 0 && pv.len < MAX_PLY {
        if cursor.is_draw() || !has_legal_moves(cursor.board()) {
            return true;
        }
        if !cursor.tt_cutoff_safe() {
            pv.clear();
            return false;
        }
        let Some(entry) = table.probe(cursor.search_key()) else {
            pv.clear();
            return false;
        };
        if entry.history_context != cursor.history_context()
            || entry.bound != Bound::Exact
            || entry.depth < remaining
        {
            pv.clear();
            return false;
        }
        let Some(mv) = entry.best_move.to_move() else {
            pv.clear();
            return false;
        };
        if !cursor.board().is_legal(mv) {
            pv.clear();
            return false;
        }
        pv.moves[pv.len] = entry.best_move;
        pv.len += 1;
        let _ = cursor.make_move(mv);
        remaining -= 1;
    }
    // Reaching nominal depth is not a complete PV: quiescence may extend the
    // principal line, and quiescence does not store exact TT continuations.
    // A PV exact cutoff is therefore complete only if the authenticated chain
    // reaches a genuine draw, checkmate, or stalemate.
    if cursor.is_draw() || !has_legal_moves(cursor.board()) {
        true
    } else {
        pv.clear();
        false
    }
}

fn pv_to_uci(root: &SearchPosition, pv: &PvLine) -> Vec<String> {
    let mut board = root.board().clone();
    let mut result = Vec::with_capacity(pv.len);
    for packed in &pv.moves[..pv.len] {
        let Some(mv) = packed.to_move() else {
            break;
        };
        if !board.is_legal(mv) {
            break;
        }
        result.push(move_to_uci(&board, mv));
        board.play_unchecked(mv);
    }
    result
}

fn compare_worker_outputs(left: &WorkerOutput, right: &WorkerOutput) -> CmpOrdering {
    left.depth
        .cmp(&right.depth)
        .then_with(|| (left.worker_id == 0).cmp(&(right.worker_id == 0)))
        .then_with(|| left.score.cmp(&right.score))
        .then_with(|| right.worker_id.cmp(&left.worker_id))
}

fn aggregate_stats(outputs: &[WorkerOutput]) -> WorkerStats {
    outputs
        .iter()
        .fold(WorkerStats::default(), |mut total, output| {
            total.nodes = total.nodes.saturating_add(output.stats.nodes);
            total.qnodes = total.qnodes.saturating_add(output.stats.qnodes);
            total.tt_hits = total.tt_hits.saturating_add(output.stats.tt_hits);
            total.beta_cutoffs = total.beta_cutoffs.saturating_add(output.stats.beta_cutoffs);
            total.seldepth = total.seldepth.max(output.stats.seldepth);
            total
        })
}

fn color_index(color: cozy_chess::Color) -> usize {
    match color {
        cozy_chess::Color::White => 0,
        cozy_chess::Color::Black => 1,
    }
}

fn has_null_move_material(board: &Board) -> bool {
    let side = board.colors(board.side_to_move());
    [Piece::Knight, Piece::Bishop, Piece::Rook, Piece::Queen]
        .into_iter()
        .any(|piece| !(side & board.pieces(piece)).is_empty())
}

#[allow(clippy::too_many_arguments)]
fn null_move_eligible(
    options: SearchOptions,
    board: &Board,
    depth: i16,
    beta: i32,
    pv_node: bool,
    null_subtree: bool,
    tt_upper_contradicts: bool,
    static_eval: i32,
) -> bool {
    let mate_margin = i32::try_from(MAX_PLY).unwrap_or(i32::MAX);
    options.use_null_move_pruning
        && !pv_node
        && !null_subtree
        && board.checkers().is_empty()
        && depth >= 4
        && beta > -MATE_SCORE + mate_margin
        && beta < MATE_SCORE - mate_margin
        && !tt_upper_contradicts
        && static_eval >= beta
        && has_null_move_material(board)
}

fn quiet_index(side: usize, mv: Move) -> usize {
    (side * 64 + mv.from as usize) * 64 + mv.to as usize
}

fn capture_index(side: usize, moving: Piece, to: usize, captured: Piece) -> usize {
    (((side * 6 + piece_index(moving)) * 64 + to) * 6) + piece_index(captured)
}

fn continuation_index(previous_piece: Piece, previous_to: usize, piece: Piece, to: usize) -> usize {
    let previous = piece_index(previous_piece) * 64 + previous_to;
    let current = piece_index(piece) * 64 + to;
    previous * CONTINUATION_DIM + current
}

fn counter_index(previous: PackedMove) -> usize {
    let raw = usize::from(previous.raw());
    let from = raw & 0x3f;
    let to = (raw >> 6) & 0x3f;
    from * 64 + to
}

fn history_bonus(depth: i16) -> i32 {
    let depth = i32::from(depth.max(1));
    (depth.saturating_mul(depth).saturating_mul(32)).min(2_048)
}

fn update_history(value: &mut i16, bonus: i32) {
    let bonus = bonus.clamp(-HISTORY_LIMIT, HISTORY_LIMIT);
    let current = i32::from(*value);
    let adjusted = current
        .saturating_add(bonus)
        .saturating_sub(current.saturating_mul(bonus.abs()) / HISTORY_LIMIT)
        .clamp(-HISTORY_LIMIT, HISTORY_LIMIT);
    *value = i16::try_from(adjusted).unwrap_or_else(|_| {
        if adjusted.is_negative() {
            i16::MIN
        } else {
            i16::MAX
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use te1_chess::{START_FEN, parse_board, parse_legal_uci_move};

    fn run(fen: &str, depth: u8, threads: usize) -> SearchResult {
        let game = Te1Game::from_fen(fen).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let table = Arc::new(TranspositionTable::with_megabytes(4));
        search(
            &game,
            SearchLimits {
                depth: Some(depth),
                ..SearchLimits::default()
            },
            stop,
            table,
            SearchOptions {
                threads,
                deterministic: threads == 1,
                ..SearchOptions::default()
            },
        )
        .unwrap()
    }

    fn run_with_null(fen: &str, depth: u8, enabled: bool) -> SearchResult {
        let game = Te1Game::from_fen(fen).unwrap();
        search(
            &game,
            SearchLimits {
                depth: Some(depth),
                ..SearchLimits::default()
            },
            Arc::new(AtomicBool::new(false)),
            Arc::new(TranspositionTable::with_megabytes(4)),
            SearchOptions {
                use_null_move_pruning: enabled,
                ..SearchOptions::default()
            },
        )
        .unwrap()
    }

    fn run_game_with_table(
        game: &Te1Game,
        depth: u8,
        table: Arc<TranspositionTable>,
    ) -> SearchResult {
        search(
            game,
            SearchLimits {
                depth: Some(depth),
                ..SearchLimits::default()
            },
            Arc::new(AtomicBool::new(false)),
            table,
            SearchOptions {
                threads: 1,
                deterministic: true,
                use_lmr: true,
                use_see_pruning: true,
                use_null_move_pruning: true,
            },
        )
        .unwrap()
    }

    fn ghi_histories() -> (Te1Game, Te1Game) {
        let mut a = Te1Game::from_fen(START_FEN).unwrap();
        for mv in [
            "g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "b1c3", "b8c6",
        ] {
            a.play_uci(mv).unwrap();
        }

        let mut seed = Te1Game::from_fen(START_FEN).unwrap();
        for mv in ["g1h3", "b8a6", "b1a3", "g8h6"] {
            seed.play_uci(mv).unwrap();
        }
        let seed_text = seed.fen();
        let mut fields: Vec<&str> = seed_text.split_whitespace().collect();
        fields[4] = "0";
        let seed_fen = fields.join(" ");
        let mut b = Te1Game::from_fen(&seed_fen).unwrap();
        for mv in [
            "a3b1", "a6b8", "h3g1", "h6g8", "g1f3", "g8f6", "b1c3", "b8c6",
        ] {
            b.play_uci(mv).unwrap();
        }
        (a, b)
    }

    #[test]
    fn returns_legal_move_from_start_position() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let result = run(START_FEN, 3, 1);
        assert!(result.best_move.is_some());
        assert!(
            game.legal_moves()
                .contains(result.best_move.as_ref().unwrap())
        );
    }

    #[test]
    fn deterministic_search_repeats_best_move_score_nodes_and_pv() {
        let first = run(START_FEN, 3, 1);
        let second = run(START_FEN, 3, 1);
        assert_eq!(first.best_move, second.best_move);
        assert_eq!(first.score_cp, second.score_cp);
        assert_eq!(first.nodes, second.nodes);
        assert_eq!(first.pv, second.pv);
    }

    #[test]
    fn warm_exact_tt_preserves_full_deterministic_pv() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let table = Arc::new(TranspositionTable::with_megabytes(16));
        let options = SearchOptions {
            use_null_move_pruning: true,
            ..SearchOptions::default()
        };
        let run = || {
            search(
                &game,
                SearchLimits {
                    depth: Some(6),
                    ..SearchLimits::default()
                },
                Arc::new(AtomicBool::new(false)),
                Arc::clone(&table),
                options,
            )
            .unwrap()
        };

        let cold = run();
        let warm = run();

        assert_eq!(warm.best_move, cold.best_move);
        assert_eq!(warm.score_cp, cold.score_cp);
        assert_eq!(warm.depth, cold.depth);
        assert_eq!(warm.pv, cold.pv);
        assert!(
            warm.nodes < cold.nodes,
            "warmed TT must reduce work: warm={} cold={}",
            warm.nodes,
            cold.nodes
        );
        assert!(warm.pv.len() > 1);

        let mut replay = Te1Game::from_fen(START_FEN).unwrap();
        for mv in &warm.pv {
            replay.play_uci(mv).unwrap();
        }
    }

    #[test]
    fn warm_exact_tt_preserves_full_ruy_lopez_pv() {
        let mut game = Te1Game::from_fen(START_FEN).unwrap();
        for mv in [
            "e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1", "f8e7",
        ] {
            game.play_uci(mv).unwrap();
        }
        let table = Arc::new(TranspositionTable::with_megabytes(16));
        let cold = run_game_with_table(&game, 7, Arc::clone(&table));
        let warm = run_game_with_table(&game, 7, Arc::clone(&table));
        assert_eq!(warm.best_move, cold.best_move);
        assert_eq!(warm.score_cp, cold.score_cp);
        assert_eq!(warm.depth, cold.depth);
        assert_eq!(warm.pv, cold.pv);
        assert!(warm.nodes < cold.nodes);
    }

    #[test]
    fn ghi_opposite_history_warm_tt_matches_fresh_search() {
        let (a, b) = ghi_histories();
        let pa = SearchPosition::from_game(&a);
        let pb = SearchPosition::from_game(&b);
        assert_eq!(pa.search_key(), pb.search_key());
        assert_ne!(pa.history_context(), pb.history_context());

        for (source, target) in [(&a, &b), (&b, &a)] {
            let shared = Arc::new(TranspositionTable::with_megabytes(32));
            let _source = run_game_with_table(source, 5, Arc::clone(&shared));
            let warm = run_game_with_table(target, 5, Arc::clone(&shared));
            let cold =
                run_game_with_table(target, 5, Arc::new(TranspositionTable::with_megabytes(32)));
            assert_eq!(warm.best_move, cold.best_move);
            assert_eq!(warm.score_cp, cold.score_cp);
            assert_eq!(warm.depth, cold.depth);
            assert_eq!(warm.pv, cold.pv);
        }
    }

    #[test]
    fn null_pruning_disabled_is_the_exact_deterministic_baseline() {
        let default_result = run(START_FEN, 4, 1);
        let explicitly_disabled = run_with_null(START_FEN, 4, false);
        assert_eq!(default_result.best_move, explicitly_disabled.best_move);
        assert_eq!(default_result.score_cp, explicitly_disabled.score_cp);
        assert_eq!(default_result.pv, explicitly_disabled.pv);
        assert_eq!(default_result.nodes, explicitly_disabled.nodes);
        assert_eq!(default_result.qnodes, explicitly_disabled.qnodes);
    }

    #[test]
    fn null_pruning_respects_pawn_only_guard() {
        let fen = "8/5k2/7p/8/8/P7/2K5/8 w - - 0 1";
        assert!(!has_null_move_material(&parse_board(fen).unwrap()));
        let disabled = run_with_null(fen, 5, false);
        let enabled = run_with_null(fen, 5, true);
        assert_eq!(disabled.best_move, enabled.best_move);
        assert_eq!(disabled.score_cp, enabled.score_cp);
        assert_eq!(disabled.nodes, enabled.nodes);
        assert_eq!(disabled.qnodes, enabled.qnodes);
    }

    #[test]
    fn null_pruning_eligibility_rejects_all_r1_prohibitions() {
        let options = SearchOptions {
            use_null_move_pruning: true,
            ..SearchOptions::default()
        };
        let normal = parse_board(START_FEN).unwrap();
        let checked = parse_board("4k3/8/8/8/8/8/4R3/4K3 b - - 0 1").unwrap();
        let eligible = |board: &Board, depth, beta, pv, nested, contradicted| {
            null_move_eligible(options, board, depth, beta, pv, nested, contradicted, beta)
        };
        assert!(eligible(&normal, 4, 0, false, false, false));
        assert!(!eligible(&checked, 4, 0, false, false, false));
        assert!(!eligible(&normal, 3, 0, false, false, false));
        assert!(!eligible(&normal, 4, 0, true, false, false));
        assert!(!eligible(&normal, 4, 0, false, true, false));
        assert!(!eligible(&normal, 4, 0, false, false, true));
        assert!(!eligible(
            &normal,
            4,
            MATE_SCORE - i32::try_from(MAX_PLY).unwrap(),
            false,
            false,
            false
        ));
    }

    #[test]
    fn null_pruning_reduces_middlegame_nodes_and_returns_legal_move() {
        let fen = "r3k2r/p1ppqpb1/bn2pnp1/2pP4/1p2P3/2N2N2/PPQBBPPP/R3K2R w KQkq - 0 1";
        let disabled = run_with_null(fen, 6, false);
        let enabled = run_with_null(fen, 6, true);
        let game = Te1Game::from_fen(fen).unwrap();
        assert!(
            game.legal_moves()
                .contains(enabled.best_move.as_ref().unwrap())
        );
        assert!(
            enabled.nodes < disabled.nodes,
            "enabled={} disabled={}",
            enabled.nodes,
            disabled.nodes
        );
    }

    #[test]
    fn null_pruning_never_prunes_non_pawn_stalemate() {
        let fen = "k7/n1K5/8/8/8/8/8/R7 b - - 0 1";
        let board = parse_board(fen).unwrap();
        assert!(board.checkers().is_empty());
        assert!(has_null_move_material(&board));
        assert!(!has_legal_moves(&board));
        let options = SearchOptions {
            use_null_move_pruning: true,
            ..SearchOptions::default()
        };
        let beta = -10_000;
        let static_eval = te1_eval::evaluate(&board);
        assert!(null_move_eligible(
            options,
            &board,
            5,
            beta,
            false,
            false,
            false,
            static_eval
        ));
        let score = |enabled| {
            let game = Te1Game::from_fen(fen).unwrap();
            let mut position = SearchPosition::from_game(&game);
            let shared = Arc::new(SharedControl {
                stop: Arc::new(AtomicBool::new(false)),
                total_nodes: AtomicU64::new(0),
                node_limit: None,
                deadline: None,
            });
            let mut worker = Worker {
                id: 0,
                shared,
                table: Arc::new(TranspositionTable::with_megabytes(4)),
                options: SearchOptions {
                    use_null_move_pruning: enabled,
                    ..SearchOptions::default()
                },
                histories: HistoryTables::default(),
                stats: WorkerStats::default(),
                aborted: false,
            };
            let mut pv = PvLine::default();
            worker.negamax(
                &mut position,
                5,
                1,
                beta - 1,
                beta,
                false,
                None,
                false,
                &mut pv,
            )
        };
        assert_eq!(score(false), 0);
        assert_eq!(score(true), 0);
    }

    #[test]
    fn null_pruning_handles_terminal_ep_and_node_limit_paths() {
        for fen in [
            "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1",
            "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1",
            "4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1",
        ] {
            let result = run_with_null(fen, 5, true);
            if let Some(best_move) = &result.best_move {
                assert!(
                    Te1Game::from_fen(fen)
                        .unwrap()
                        .legal_moves()
                        .contains(best_move)
                );
            }
        }

        let game = Te1Game::from_fen(START_FEN).unwrap();
        let result = search(
            &game,
            SearchLimits {
                nodes: Some(500),
                ..SearchLimits::default()
            },
            Arc::new(AtomicBool::new(false)),
            Arc::new(TranspositionTable::with_megabytes(4)),
            SearchOptions {
                use_null_move_pruning: true,
                ..SearchOptions::default()
            },
        )
        .unwrap();
        assert!(result.stopped);
        assert!(result.nodes <= 500);
        assert!(
            game.legal_moves()
                .contains(result.best_move.as_ref().unwrap())
        );
    }

    #[test]
    fn finds_a_mating_move_when_one_is_available() {
        let fen = "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1";
        let result = run(fen, 3, 1);
        let mut game = Te1Game::from_fen(fen).unwrap();
        game.play_uci(result.best_move.as_deref().unwrap()).unwrap();
        assert_eq!(game.status(), te1_chess::Te1Status::Checkmate);
    }

    #[test]
    fn stalemate_scores_as_draw() {
        let fen = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1";
        let result = run(fen, 2, 1);
        assert!(result.best_move.is_none());
        assert_eq!(result.score_cp, 0);
    }

    #[test]
    fn infinite_search_waits_for_external_stop() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = Arc::clone(&stop);
        let handle = thread::spawn(move || {
            search(
                &game,
                SearchLimits {
                    depth: Some(2),
                    infinite: true,
                    ..SearchLimits::default()
                },
                thread_stop,
                Arc::new(TranspositionTable::with_megabytes(4)),
                SearchOptions::default(),
            )
            .unwrap()
        });
        thread::sleep(Duration::from_millis(25));
        assert!(!handle.is_finished());
        stop.store(true, Ordering::Relaxed);
        let result = handle.join().unwrap();
        assert!(result.stopped);
        assert!(result.best_move.is_some());
    }

    #[test]
    fn node_limit_is_global_and_not_exceeded() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let result = search(
            &game,
            SearchLimits {
                nodes: Some(1_000),
                ..SearchLimits::default()
            },
            Arc::new(AtomicBool::new(false)),
            Arc::new(TranspositionTable::with_megabytes(4)),
            SearchOptions {
                threads: 4,
                deterministic: false,
                ..SearchOptions::default()
            },
        )
        .unwrap();
        assert!(result.stopped);
        assert!(result.nodes <= 1_000);
        assert!(result.best_move.is_some());
    }

    #[test]
    fn multithreaded_search_returns_a_legal_move() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let result = run(START_FEN, 3, 4);
        assert_eq!(result.threads, 4);
        assert!(result.best_move.is_some());
        assert!(
            game.legal_moves()
                .contains(result.best_move.as_ref().unwrap())
        );
    }

    #[test]
    fn see_values_winning_and_losing_captures() {
        let winning = parse_board("7k/8/8/3q4/4P3/8/8/7K w - - 0 1").unwrap();
        let winning_move = parse_legal_uci_move(&winning, "e4d5").unwrap();
        assert!(static_exchange_eval(&winning, winning_move) >= 800);

        let losing = parse_board("7k/8/2p5/3p4/4Q3/8/8/7K w - - 0 1").unwrap();
        let losing_move = parse_legal_uci_move(&losing, "e4d5").unwrap();
        assert!(static_exchange_eval(&losing, losing_move) <= -700);
    }

    fn assert_fast_see_matches_oracles(board: &Board, mv: Move) {
        let fast = static_exchange_eval_fast(board, mv);
        let oracle = static_exchange_eval_oracle(board, mv);
        let legacy = static_exchange_eval(board, mv);
        assert_eq!(
            fast, oracle,
            "fast SEE mismatch for {mv:?}: fast={fast} oracle={oracle} on {board}"
        );
        assert_eq!(
            legacy, oracle,
            "legacy SEE cap drift for {mv:?}: legacy={legacy} oracle={oracle} on {board}"
        );
        for threshold in [
            -1_000,
            -500,
            -100,
            -1,
            0,
            1,
            100,
            500,
            1_000,
            oracle.saturating_sub(1),
            oracle,
            oracle.saturating_add(1),
        ] {
            assert_eq!(fast >= threshold, oracle >= threshold);
        }
    }

    #[test]
    fn fast_exact_see_special_cases_match_uncapped_oracle() {
        let cases = [
            ("7k/8/8/3pP3/8/8/8/7K w - d6 0 1", "e5d6"),
            ("7k/P7/8/8/8/8/8/7K w - - 0 1", "a7a8n"),
            ("7k/P7/8/8/8/8/8/7K w - - 0 1", "a7a8b"),
            ("7k/P7/8/8/8/8/8/7K w - - 0 1", "a7a8r"),
            ("7k/P7/8/8/8/8/8/7K w - - 0 1", "a7a8q"),
            ("1r5k/P7/8/8/8/8/8/7K w - - 0 1", "a7b8n"),
            ("1r5k/P7/8/8/8/8/8/7K w - - 0 1", "a7b8b"),
            ("1r5k/P7/8/8/8/8/8/7K w - - 0 1", "a7b8r"),
            ("1r5k/P7/8/8/8/8/8/7K w - - 0 1", "a7b8q"),
            ("4k3/4n3/8/3p4/4Q3/8/4R3/4K3 w - - 0 1", "e4d5"),
            ("7k/1b6/8/3p4/4Q3/8/8/7K w - - 0 1", "e4d5"),
            ("3r3k/8/8/3p4/4Q3/8/8/7K w - - 0 1", "e4d5"),
            ("7k/1q6/8/3p4/4Q3/8/8/7K w - - 0 1", "e4d5"),
            ("7k/8/8/3p4/4K3/8/8/8 w - - 0 1", "e4d5"),
            ("7k/8/2r5/3p4/4K3/8/8/8 w - - 0 1", "e4d5"),
            ("4k2r/8/8/8/8/8/8/4K2R w Kk - 0 1", "e1h1"),
            // Black's first recapture promotes on b1; all four promotion
            // choices are legal and the exact oracle chooses the best line.
            ("7k/8/8/8/8/8/pR6/1n5K w - - 0 1", "b2b1"),
        ];
        for (fen, uci) in cases {
            let board = parse_board(fen).unwrap();
            let mv = parse_legal_uci_move(&board, uci).unwrap();
            assert_fast_see_matches_oracles(&board, mv);
        }
    }

    #[test]
    fn fast_exact_see_recapture_heavy_generated_corpus_matches_oracle() {
        let mut checked = 0usize;
        'seeds: for seed in 0..32usize {
            let mut board = Board::default();
            for ply in 0..160usize {
                let moves = legal_moves_unsorted(&board);
                if moves.is_empty() {
                    break;
                }
                for &mv in &moves {
                    if !is_capture(&board, mv) && mv.promotion.is_none() {
                        continue;
                    }
                    let mut next = board.clone();
                    next.play_unchecked(mv);
                    let has_recapture = legal_moves_unsorted(&next)
                        .into_iter()
                        .any(|reply| reply.to == mv.to && is_capture(&next, reply));
                    if !has_recapture {
                        continue;
                    }
                    assert_fast_see_matches_oracles(&board, mv);
                    checked += 1;
                    if checked >= 384 {
                        break 'seeds;
                    }
                }
                let mv = moves[(seed.wrapping_mul(37) + ply.wrapping_mul(17)) % moves.len()];
                board.play_unchecked(mv);
            }
        }
        assert!(
            checked >= 384,
            "only checked {checked} recapture-heavy tactical moves"
        );
    }

    #[test]
    fn checkmate_precedes_fifty_move_draw() {
        let fen = "7k/6Q1/6K1/8/8/8/8/8 b - - 100 1";
        let result = run(fen, 2, 1);
        assert!(result.best_move.is_none());
        assert!(result.score_cp <= -MATE_SCORE);
    }

    #[test]
    fn packed_pv_contains_only_legal_moves() {
        let result = run(START_FEN, 4, 1);
        let mut game = Te1Game::from_fen(START_FEN).unwrap();
        for mv in &result.pv {
            game.play_uci(mv).unwrap();
        }
    }
}
