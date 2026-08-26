#![forbid(unsafe_code)]

use cozy_chess::util::{display_uci_move, parse_uci_move};
use cozy_chess::{Board, Move, Piece, Square};
use serde::Serialize;
use std::collections::HashMap;

pub const HISTORY_LENGTH: usize = 8;
pub const INPUT_PLANES: usize = 119;
pub const POLICY_PLANES: usize = 73;
pub const POLICY_SIZE: usize = 64 * POLICY_PLANES;
pub const START_FEN: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

/// Compact, allocation-free move representation used by search and the TT.
///
/// Bits 0..=5 store the source square, bits 6..=11 the destination, and
/// bits 12..=14 the promotion code. Zero is reserved for `NONE`; no legal
/// chess move has identical source and destination squares.
#[repr(transparent)]
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Serialize)]
pub struct PackedMove(u16);

impl PackedMove {
    pub const NONE: Self = Self(0);

    #[must_use]
    pub const fn from_raw(raw: u16) -> Self {
        Self(raw)
    }

    #[must_use]
    pub const fn raw(self) -> u16 {
        self.0
    }

    #[must_use]
    pub const fn is_none(self) -> bool {
        self.0 == 0
    }

    #[must_use]
    pub fn from_move(mv: Move) -> Self {
        let promotion = match mv.promotion {
            None => 0u16,
            Some(Piece::Knight) => 1,
            Some(Piece::Bishop) => 2,
            Some(Piece::Rook) => 3,
            Some(Piece::Queen) => 4,
            Some(Piece::Pawn) | Some(Piece::King) => {
                panic!("only knight, bishop, rook, and queen promotions are legal")
            }
        };
        let from = mv.from as u16;
        let to = mv.to as u16;
        Self(from | (to << 6) | (promotion << 12))
    }

    #[must_use]
    pub fn to_move(self) -> Option<Move> {
        if self.is_none() {
            return None;
        }
        let from = Square::try_index(usize::from(self.0 & 0x3f))?;
        let to = Square::try_index(usize::from((self.0 >> 6) & 0x3f))?;
        let promotion = match (self.0 >> 12) & 0x7 {
            0 => None,
            1 => Some(Piece::Knight),
            2 => Some(Piece::Bishop),
            3 => Some(Piece::Rook),
            4 => Some(Piece::Queen),
            _ => return None,
        };
        Some(Move {
            from,
            to,
            promotion,
        })
    }
}

#[derive(Debug, Clone)]
pub struct SearchPosition {
    board: Board,
    repetition_keys: Vec<u64>,
    halfmove_clock: u16,
    repetition_count: u8,
    repetition_start: usize,
}

#[derive(Debug, Clone)]
pub struct SearchUndo {
    board: Board,
    halfmove_clock: u16,
    repetition_count: u8,
}

/// Exact state needed to undo an artificial search-only pass.
#[derive(Debug, Clone)]
pub struct NullMoveUndo {
    board: Board,
    repetition_count: u8,
    repetition_start: usize,
}

impl SearchPosition {
    #[must_use]
    pub fn from_game(game: &Te1Game) -> Self {
        let halfmove_clock = u16::from(game.board.halfmove_clock());
        let mut repetition_keys = Vec::with_capacity(game.board_history.len().saturating_add(128));
        repetition_keys.extend(game.board_history.iter().map(fide_position_hash));
        let repetition_count = count_current_repetitions(&repetition_keys, halfmove_clock);
        Self {
            board: game.board.clone(),
            repetition_keys,
            halfmove_clock,
            repetition_count,
            repetition_start: 0,
        }
    }

    #[must_use]
    pub fn board(&self) -> &Board {
        &self.board
    }

    #[must_use]
    pub fn halfmove_clock(&self) -> u16 {
        self.halfmove_clock
    }

    #[must_use]
    pub fn ply_count(&self) -> usize {
        self.repetition_keys.len().saturating_sub(1)
    }

    #[must_use]
    pub fn repetition_count(&self) -> usize {
        usize::from(self.repetition_count)
    }

    #[must_use]
    pub fn is_draw(&self) -> bool {
        self.repetition_count() >= 3
            || self.halfmove_clock >= 100
            || is_insufficient_material(&self.board)
    }

    #[must_use]
    pub fn tt_cutoff_safe(&self) -> bool {
        self.halfmove_clock < 90 && self.repetition_count() <= 1
    }

    #[must_use]
    pub fn search_key(&self) -> u64 {
        const HALFMOVE_MIX: u64 = 0x9e37_79b9_7f4a_7c15;
        const REPETITION_MIX: u64 = 0xd1b5_4a32_d192_ed03;
        let board_key = fide_position_hash(&self.board);
        let halfmove = u64::from(self.halfmove_clock).wrapping_mul(HALFMOVE_MIX);
        let repetition = u64::try_from(self.repetition_count())
            .unwrap_or(u64::MAX)
            .wrapping_mul(REPETITION_MIX);
        board_key ^ halfmove.rotate_left(17) ^ repetition.rotate_left(41)
    }

    pub fn make_move(&mut self, mv: Move) -> SearchUndo {
        debug_assert!(self.board.is_legal(mv));
        let moving_piece = self.board.piece_on(mv.from);
        let capture = is_capture(&self.board, mv);
        let undo = SearchUndo {
            board: self.board.clone(),
            halfmove_clock: self.halfmove_clock,
            repetition_count: self.repetition_count,
        };
        self.board.play_unchecked(mv);
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
        undo
    }

    pub fn unmake_move(&mut self, undo: SearchUndo) {
        let popped = self.repetition_keys.pop();
        debug_assert!(popped.is_some());
        self.board = undo.board;
        self.halfmove_clock = undo.halfmove_clock;
        self.repetition_count = undo.repetition_count;
    }

    /// Makes a synthetic null move without advancing legal-game rule-50 state.
    ///
    /// The repetition barrier prevents positions preceding the artificial pass
    /// from being treated as legal ancestors of its descendants.
    pub fn make_null_move(&mut self) -> Option<NullMoveUndo> {
        let board = self.board.null_move()?;
        let undo = NullMoveUndo {
            board: self.board.clone(),
            repetition_count: self.repetition_count,
            repetition_start: self.repetition_start,
        };
        self.board = board;
        self.repetition_keys.push(fide_position_hash(&self.board));
        self.repetition_start = self.repetition_keys.len() - 1;
        self.repetition_count = 1;
        Some(undo)
    }

    pub fn unmake_null_move(&mut self, undo: NullMoveUndo) {
        let popped = self.repetition_keys.pop();
        debug_assert!(popped.is_some());
        self.board = undo.board;
        self.repetition_count = undo.repetition_count;
        self.repetition_start = undo.repetition_start;
    }
}

fn count_current_repetitions(keys: &[u64], halfmove_clock: u16) -> u8 {
    let Some(&current) = keys.last() else {
        return 0;
    };
    let reversible = usize::from(halfmove_clock).saturating_add(1);
    let count = keys
        .iter()
        .rev()
        .take(reversible)
        .filter(|&&key| key == current)
        .count();
    u8::try_from(count).unwrap_or(u8::MAX)
}

const QUEEN_DIRECTIONS: [(i32, i32); 8] = [
    (0, 1),
    (1, 1),
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, -1),
    (-1, 0),
    (-1, 1),
];

const KNIGHT_DIRECTIONS: [(i32, i32); 8] = [
    (1, 2),
    (2, 1),
    (2, -1),
    (1, -2),
    (-1, -2),
    (-2, -1),
    (-2, 1),
    (-1, 2),
];

#[derive(Debug, Clone)]
struct FenState {
    board: [Option<char>; 64],
    side_to_move: char,
    castling: String,
    ep_square: Option<String>,
    halfmove_clock: u32,
    fullmove_number: u32,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct EncodingDigest {
    pub hash_hex: String,
    pub nonzero: usize,
    pub quantized_sum: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Te1Status {
    Ongoing,
    Checkmate,
    Stalemate,
    Draw,
    DrawRepetition,
}

#[derive(Debug, Clone)]
pub struct Te1Game {
    board: Board,
    board_history: Vec<Board>,
    history: Vec<String>,
    moves: Vec<String>,
}

impl Te1Game {
    pub fn from_fen(fen: &str) -> Result<Self, String> {
        let board = parse_board(fen)?;
        Ok(Self {
            board: board.clone(),
            board_history: vec![board],
            history: vec![fen.to_owned()],
            moves: Vec::new(),
        })
    }

    pub fn from_history(history: &[String]) -> Result<Self, String> {
        let board_history = parse_history_boards(history)?;
        let board = board_history
            .last()
            .cloned()
            .ok_or_else(|| "history must contain at least one FEN".to_owned())?;
        Ok(Self {
            board,
            board_history,
            history: history.to_vec(),
            moves: Vec::new(),
        })
    }

    #[must_use]
    pub fn fen(&self) -> String {
        self.history
            .last()
            .cloned()
            .unwrap_or_else(|| self.board.to_string())
    }

    #[must_use]
    pub fn history(&self) -> &[String] {
        &self.history
    }

    #[must_use]
    pub fn board(&self) -> &Board {
        &self.board
    }

    #[must_use]
    pub fn board_hash(&self) -> u64 {
        self.board.hash()
    }

    #[must_use]
    pub fn search_key(&self) -> u64 {
        let mut hash = 14_695_981_039_346_656_037u64;
        for board in &self.board_history {
            hash = fnv_mix(hash, fide_position_hash(board));
        }
        fnv_mix(hash, u64::from(self.board.halfmove_clock()))
    }

    #[must_use]
    pub fn ply_count(&self) -> usize {
        self.moves.len()
    }

    #[must_use]
    pub fn played_moves(&self) -> &[String] {
        &self.moves
    }

    #[must_use]
    pub fn legal_moves(&self) -> Vec<String> {
        legal_uci_moves(&self.board)
    }

    pub fn play_uci(&mut self, uci: &str) -> Result<(), String> {
        let current = self
            .history
            .last()
            .ok_or_else(|| "internal game history error".to_owned())?
            .clone();
        let exact_next = next_fen(&current, uci)?;
        let mv = parse_uci_move(&self.board, uci).map_err(|error| error.to_string())?;
        let displayed = display_uci_move(&self.board, mv).to_string();
        if !self.board.is_legal(mv) {
            return Err(format!("illegal move: {uci}"));
        }
        self.board.play_unchecked(mv);
        self.board_history.push(self.board.clone());
        self.history.push(exact_next);
        self.moves.push(displayed);
        Ok(())
    }

    pub fn undo(&mut self) -> Result<String, String> {
        if self.history.len() <= 1 {
            return Err("no move to undo".to_owned());
        }
        self.history.pop();
        self.moves.pop();
        self.board_history.pop();
        self.board = self
            .board_history
            .last()
            .cloned()
            .ok_or_else(|| "internal undo board-history error".to_owned())?;
        self.history
            .last()
            .cloned()
            .ok_or_else(|| "internal undo history error".to_owned())
    }

    #[must_use]
    pub fn repetition_count(&self) -> usize {
        let Some(current) = self.board_history.last() else {
            return 0;
        };
        self.board_history
            .iter()
            .filter(|candidate| candidate.same_position(current))
            .count()
    }

    #[must_use]
    pub fn status(&self) -> Te1Status {
        // Checkmate and stalemate terminate the game before claimable or
        // automatic draw conditions are considered.
        if !has_legal_moves(&self.board) {
            return if self.board.checkers().is_empty() {
                Te1Status::Stalemate
            } else {
                Te1Status::Checkmate
            };
        }
        if self.repetition_count() >= 3 {
            return Te1Status::DrawRepetition;
        }
        if u16::from(self.board.halfmove_clock()) >= 100 || is_insufficient_material(&self.board) {
            Te1Status::Draw
        } else {
            Te1Status::Ongoing
        }
    }
}

pub fn parse_board(fen: &str) -> Result<Board, String> {
    fen.parse::<Board>()
        .map_err(|error| format!("invalid FEN ({error}): {fen}"))
}

fn parse_history_boards(history: &[String]) -> Result<Vec<Board>, String> {
    if history.is_empty() {
        return Err("history must contain at least one FEN".to_owned());
    }
    history
        .iter()
        .enumerate()
        .map(|(index, fen)| parse_board(fen).map_err(|error| format!("history[{index}]: {error}")))
        .collect()
}

#[must_use]
pub fn has_legal_en_passant(board: &Board) -> bool {
    if board.en_passant().is_none() {
        return false;
    }
    board.generate_moves(|moves| {
        for mv in moves {
            if board.piece_on(mv.from) == Some(Piece::Pawn)
                && mv.from.file() != mv.to.file()
                && board.piece_on(mv.to).is_none()
            {
                return true;
            }
        }
        false
    })
}

#[must_use]
pub fn fide_position_hash(board: &Board) -> u64 {
    if has_legal_en_passant(board) {
        board.hash()
    } else {
        board.hash_without_ep()
    }
}

#[must_use]
pub fn has_legal_moves(board: &Board) -> bool {
    board.generate_moves(|moves| !moves.is_empty())
}

#[must_use]
pub fn legal_moves_unsorted(board: &Board) -> Vec<Move> {
    let mut result = Vec::with_capacity(96);
    board.generate_moves(|moves| {
        result.extend(moves);
        false
    });
    result
}

#[must_use]
pub fn legal_moves(board: &Board) -> Vec<Move> {
    let mut result = legal_moves_unsorted(board);
    result.sort_unstable_by_key(|mv| PackedMove::from_move(*mv).raw());
    result
}

pub fn parse_legal_uci_move(board: &Board, uci: &str) -> Result<Move, String> {
    let mv = parse_uci_move(board, uci).map_err(|error| error.to_string())?;
    if board.is_legal(mv) {
        Ok(mv)
    } else {
        Err(format!("illegal move: {uci}"))
    }
}

#[must_use]
pub fn move_to_uci(board: &Board, mv: Move) -> String {
    display_uci_move(board, mv).to_string()
}

#[must_use]
pub fn piece_index(piece: Piece) -> usize {
    match piece {
        Piece::Pawn => 0,
        Piece::Knight => 1,
        Piece::Bishop => 2,
        Piece::Rook => 3,
        Piece::Queen => 4,
        Piece::King => 5,
    }
}

#[must_use]
pub fn piece_value(piece: Piece) -> i32 {
    match piece {
        Piece::Pawn => 100,
        Piece::Knight => 320,
        Piece::Bishop => 330,
        Piece::Rook => 500,
        Piece::Queen => 900,
        Piece::King => 20_000,
    }
}

#[must_use]
pub fn captured_piece(board: &Board, mv: Move) -> Option<Piece> {
    // cozy-chess represents castling internally as the king moving onto its
    // own rook square. Only a piece belonging to the opponent is a capture.
    if board.colors(!board.side_to_move()).has(mv.to) {
        return board.piece_on(mv.to);
    }
    let is_en_passant = board.piece_on(mv.from) == Some(Piece::Pawn)
        && mv.from.file() != mv.to.file()
        && board.piece_on(mv.to).is_none();
    is_en_passant.then_some(Piece::Pawn)
}

#[must_use]
pub fn is_capture(board: &Board, mv: Move) -> bool {
    captured_piece(board, mv).is_some()
}

#[must_use]
pub fn promotion_gain(mv: Move) -> i32 {
    mv.promotion
        .map_or(0, |piece| piece_value(piece) - piece_value(Piece::Pawn))
}

#[must_use]
pub fn legal_uci_moves(board: &Board) -> Vec<String> {
    let mut result = Vec::new();
    board.generate_moves(|moves| {
        for mv in moves {
            result.push(display_uci_move(board, mv).to_string());
        }
        false
    });
    result.sort_unstable();
    result
}

pub fn next_fen(fen: &str, uci: &str) -> Result<String, String> {
    if !uci.is_ascii() || !matches!(uci.len(), 4 | 5) {
        return Err(format!("invalid UCI move: {uci}"));
    }
    let state = parse_fen(fen)?;
    let mut board = parse_board(fen)?;
    let mv = parse_uci_move(&board, uci).map_err(|error| error.to_string())?;

    let from = square_index(&uci[0..2])?;
    let to = square_index(&uci[2..4])?;
    let moving_piece =
        state.board[from].ok_or_else(|| format!("no piece on source square for move: {uci}"))?;
    let is_pawn = moving_piece.eq_ignore_ascii_case(&'P');
    let is_en_passant_capture = is_pawn && (from & 7) != (to & 7) && state.board[to].is_none();
    let is_capture = state.board[to].is_some() || is_en_passant_capture;

    if !board.is_legal(mv) {
        return Err(format!("illegal move: {uci}"));
    }
    board.play_unchecked(mv);
    let rendered = board.to_string();
    let fields: Vec<&str> = rendered.split_whitespace().collect();
    if fields.len() != 6 {
        return Err(format!("cozy-chess produced invalid FEN: {rendered}"));
    }

    let from_rank = from >> 3;
    let to_rank = to >> 3;
    let en_passant = if is_pawn && from_rank.abs_diff(to_rank) == 2 {
        let middle = ((from_rank + to_rank) / 2) * 8 + (from & 7);
        square_name(middle)
    } else {
        "-".to_owned()
    };
    let halfmove = if is_pawn || is_capture {
        0
    } else {
        state.halfmove_clock.saturating_add(1)
    };
    let fullmove = state
        .fullmove_number
        .saturating_add(if state.side_to_move == 'b' { 1 } else { 0 });

    Ok(format!(
        "{} {} {} {} {} {}",
        fields[0], fields[1], fields[2], en_passant, halfmove, fullmove
    ))
}

pub fn static_status(fen: &str) -> Result<Te1Status, String> {
    let state = parse_fen(fen)?;
    let board = parse_board(fen)?;
    Ok(static_status_board(&board, state.halfmove_clock))
}

fn static_status_board(board: &Board, halfmove_clock: u32) -> Te1Status {
    let legal = legal_uci_moves(board);
    if legal.is_empty() {
        return if board.checkers().is_empty() {
            Te1Status::Stalemate
        } else {
            Te1Status::Checkmate
        };
    }
    if halfmove_clock >= 100 || is_insufficient_material(board) {
        Te1Status::Draw
    } else {
        Te1Status::Ongoing
    }
}

#[must_use]
pub fn is_insufficient_material(board: &Board) -> bool {
    if !board.pieces(Piece::Pawn).is_empty()
        || !board.pieces(Piece::Rook).is_empty()
        || !board.pieces(Piece::Queen).is_empty()
    {
        return false;
    }

    let knights = board.pieces(Piece::Knight).len();
    let bishops = board.pieces(Piece::Bishop);
    let bishop_count = bishops.len();
    let total_minor = knights + bishop_count;

    if total_minor <= 1 {
        return true;
    }
    if knights != 0 {
        return false;
    }

    let mut square_colours = None;
    for square in bishops {
        let index = square as usize;
        let colour = ((index & 7) + (index >> 3)) & 1;
        match square_colours {
            None => square_colours = Some(colour),
            Some(existing) if existing != colour => return false,
            Some(_) => {}
        }
    }
    true
}

/// Alpha.2.2 compatibility key used by the frozen neural input format.
///
/// This intentionally preserves the first four FEN fields exactly, including
/// an irrelevant en-passant target. Do not use it for FIDE draw adjudication;
/// use `Te1Game::repetition_count` or `fide_repetition_count` instead.
#[must_use]
pub fn repetition_key(fen: &str) -> String {
    fen.split_whitespace().take(4).collect::<Vec<_>>().join(" ")
}

/// Alpha.2.2 compatibility repetition count for differential parity and the
/// existing 119-plane model input. This is not the production draw counter.
#[must_use]
pub fn repetition_count(history: &[String]) -> usize {
    let Some(current) = history.last() else {
        return 0;
    };
    let key = repetition_key(current);
    history
        .iter()
        .filter(|fen| repetition_key(fen) == key)
        .count()
}

pub fn fide_repetition_count(history: &[String]) -> Result<usize, String> {
    let boards = parse_history_boards(history)?;
    let current = boards
        .last()
        .ok_or_else(|| "history must contain at least one FEN".to_owned())?;
    Ok(boards
        .iter()
        .filter(|candidate| candidate.same_position(current))
        .count())
}

fn square_index(name: &str) -> Result<usize, String> {
    let bytes = name.as_bytes();
    if bytes.len() != 2 || !(b'a'..=b'h').contains(&bytes[0]) || !(b'1'..=b'8').contains(&bytes[1])
    {
        return Err(format!("invalid square: {name}"));
    }
    Ok(usize::from(bytes[0] - b'a') + 8 * usize::from(bytes[1] - b'1'))
}

fn square_name(index: usize) -> String {
    debug_assert!(index < 64);
    let file = char::from(b'a' + u8::try_from(index & 7).unwrap_or_default());
    let rank = char::from(b'1' + u8::try_from(index >> 3).unwrap_or_default());
    format!("{file}{rank}")
}

fn canonical_square(index: usize, perspective: char) -> usize {
    if perspective == 'w' {
        index
    } else {
        index ^ 56
    }
}

fn parse_fen(fen: &str) -> Result<FenState, String> {
    let fields: Vec<&str> = fen.split_whitespace().collect();
    if fields.len() != 6 {
        return Err(format!("expected six FEN fields: {fen}"));
    }
    let side_to_move = match fields[1] {
        "w" => 'w',
        "b" => 'b',
        other => return Err(format!("invalid side to move: {other}")),
    };

    let mut board = [None; 64];
    let ranks: Vec<&str> = fields[0].split('/').collect();
    if ranks.len() != 8 {
        return Err("invalid FEN board field".to_owned());
    }
    for (fen_rank, rank_text) in ranks.iter().enumerate() {
        let rank = 7usize
            .checked_sub(fen_rank)
            .ok_or_else(|| "invalid FEN rank".to_owned())?;
        let mut file_index = 0usize;
        for token in rank_text.chars() {
            if let Some(empty) = token.to_digit(10) {
                if !(1..=8).contains(&empty) {
                    return Err(format!("invalid empty-square count: {empty}"));
                }
                let empty =
                    usize::try_from(empty).map_err(|_| "invalid empty-square count".to_owned())?;
                file_index = file_index
                    .checked_add(empty)
                    .ok_or_else(|| "FEN file count overflow".to_owned())?;
                if file_index > 8 {
                    return Err("too many files in FEN rank".to_owned());
                }
            } else if "PNBRQKpnbrqk".contains(token) {
                if file_index >= 8 {
                    return Err("too many files in FEN rank".to_owned());
                }
                board[rank * 8 + file_index] = Some(token);
                file_index += 1;
            } else {
                return Err(format!("invalid FEN token: {token}"));
            }
        }
        if file_index != 8 {
            return Err("FEN rank does not contain eight files".to_owned());
        }
    }

    let ep_square = if fields[3] == "-" {
        None
    } else {
        square_index(fields[3])?;
        Some(fields[3].to_owned())
    };
    let halfmove_clock = fields[4]
        .parse::<u32>()
        .map_err(|error| error.to_string())?;
    let fullmove_number = fields[5]
        .parse::<u32>()
        .map_err(|error| error.to_string())?;
    if fullmove_number == 0 {
        return Err("fullmove number must be positive".to_owned());
    }

    Ok(FenState {
        board,
        side_to_move,
        castling: fields[2].to_owned(),
        ep_square,
        halfmove_clock,
        fullmove_number,
    })
}

fn piece_channel(piece: char, perspective: char) -> Result<usize, String> {
    let piece_index = match piece.to_ascii_uppercase() {
        'P' => 0,
        'N' => 1,
        'B' => 2,
        'R' => 3,
        'Q' => 4,
        'K' => 5,
        other => return Err(format!("invalid piece: {other}")),
    };
    let piece_is_white = piece.is_ascii_uppercase();
    let own_is_white = perspective == 'w';
    Ok(if piece_is_white == own_is_white {
        piece_index
    } else {
        6 + piece_index
    })
}

pub fn encode_history(history: &[String]) -> Result<Vec<f32>, String> {
    if history.is_empty() {
        return Err("at least one FEN is required".to_owned());
    }
    let _validated_boards = parse_history_boards(history)?;
    let parsed: Vec<FenState> = history
        .iter()
        .map(|fen| parse_fen(fen))
        .collect::<Result<_, _>>()?;
    let perspective = parsed
        .last()
        .ok_or_else(|| "history unexpectedly empty".to_owned())?
        .side_to_move;
    let mut planes = vec![0.0f32; INPUT_PLANES * 64];

    for time_offset in 0..HISTORY_LENGTH {
        let Some(source_index) = parsed.len().checked_sub(1 + time_offset) else {
            break;
        };
        let state = &parsed[source_index];
        let base = time_offset * 14;
        for (original_square, piece) in state.board.iter().enumerate() {
            let Some(piece) = piece else {
                continue;
            };
            let canonical = canonical_square(original_square, perspective);
            let channel = piece_channel(*piece, perspective)?;
            planes[(base + channel) * 64 + canonical] = 1.0;
        }

        let key = repetition_key(&history[source_index]);
        let count = history[..=source_index]
            .iter()
            .filter(|fen| repetition_key(fen) == key)
            .count();
        if count >= 2 {
            planes[(base + 12) * 64..(base + 13) * 64].fill(1.0);
        }
        if count >= 3 {
            planes[(base + 13) * 64..(base + 14) * 64].fill(1.0);
        }
    }

    let current = parsed
        .last()
        .ok_or_else(|| "history unexpectedly empty".to_owned())?;
    let own_white = perspective == 'w';
    let rights = if own_white {
        ['K', 'Q', 'k', 'q']
    } else {
        ['k', 'q', 'K', 'Q']
    };
    for (offset, symbol) in rights.into_iter().enumerate() {
        if current.castling.contains(symbol) {
            planes[(112 + offset) * 64..(113 + offset) * 64].fill(1.0);
        }
    }

    if let Some(ep_square) = &current.ep_square {
        let ep = canonical_square(square_index(ep_square)?, perspective);
        planes[116 * 64 + ep] = 1.0;
    }

    let halfmove = current.halfmove_clock.min(100) as f32 / 100.0;
    let fullmove = current.fullmove_number.min(200) as f32 / 200.0;
    planes[117 * 64..118 * 64].fill(halfmove);
    planes[118 * 64..119 * 64].fill(fullmove);
    Ok(planes)
}

fn fnv_mix(mut hash: u64, value: u64) -> u64 {
    const PRIME: u64 = 1_099_511_628_211;
    for byte in value.to_le_bytes() {
        hash ^= u64::from(byte);
        hash = hash.wrapping_mul(PRIME);
    }
    hash
}

pub fn encoding_digest(history: &[String]) -> Result<EncodingDigest, String> {
    let planes = encode_history(history)?;
    let mut hash = 14_695_981_039_346_656_037u64;
    let mut nonzero = 0usize;
    let mut quantized_sum = 0i64;
    for (index, value) in planes.iter().enumerate() {
        let quantized = (f64::from(*value) * 1_000_000.0).round() as i64;
        if quantized == 0 {
            continue;
        }
        nonzero += 1;
        quantized_sum += quantized;
        hash = fnv_mix(hash, index as u64);
        hash = fnv_mix(hash, quantized as u64);
    }
    Ok(EncodingDigest {
        hash_hex: format!("{hash:016x}"),
        nonzero,
        quantized_sum,
    })
}

pub fn encode_move(fen: &str, uci: &str) -> Result<usize, String> {
    let bytes = uci.as_bytes();
    if !uci.is_ascii() || (bytes.len() != 4 && bytes.len() != 5) {
        return Err(format!("invalid UCI move: {uci}"));
    }
    let state = parse_fen(fen)?;
    let board = parse_board(fen)?;
    let parsed_move = parse_uci_move(&board, uci).map_err(|error| error.to_string())?;
    if !board.is_legal(parsed_move) {
        return Err(format!("illegal move for policy encoding: {uci}"));
    }
    let perspective = state.side_to_move;
    let from_original = square_index(&uci[0..2])?;
    let to_original = square_index(&uci[2..4])?;
    let from_square = canonical_square(from_original, perspective);
    let to_square = canonical_square(to_original, perspective);

    let fx = i32::try_from(from_square & 7).map_err(|error| error.to_string())?;
    let fy = i32::try_from(from_square >> 3).map_err(|error| error.to_string())?;
    let tx = i32::try_from(to_square & 7).map_err(|error| error.to_string())?;
    let ty = i32::try_from(to_square >> 3).map_err(|error| error.to_string())?;
    let dx = tx - fx;
    let dy = ty - fy;

    let promotion = bytes
        .get(4)
        .map(|value| char::from(value.to_ascii_lowercase()));
    let plane = if let Some(piece @ ('n' | 'b' | 'r')) = promotion {
        if dy != 1 || !(-1..=1).contains(&dx) {
            return Err(format!("invalid canonical underpromotion: {uci}"));
        }
        let piece_index = match piece {
            'n' => 0usize,
            'b' => 1usize,
            'r' => 2usize,
            _ => unreachable!(),
        };
        let direction = usize::try_from(dx + 1).map_err(|error| error.to_string())?;
        64 + piece_index * 3 + direction
    } else if let Some(index) = KNIGHT_DIRECTIONS
        .iter()
        .position(|direction| *direction == (dx, dy))
    {
        56 + index
    } else {
        let distance = dx.abs().max(dy.abs());
        if !(1..=7).contains(&distance) || !(dx == 0 || dy == 0 || dx.abs() == dy.abs()) {
            return Err(format!("move is not queen-like or knight-like: {uci}"));
        }
        let unit = (dx.signum(), dy.signum());
        let direction = QUEEN_DIRECTIONS
            .iter()
            .position(|candidate| *candidate == unit)
            .ok_or_else(|| format!("unsupported move direction: {uci}"))?;
        direction * 7 + usize::try_from(distance - 1).map_err(|error| error.to_string())?
    };

    let index = from_square * POLICY_PLANES + plane;
    if index >= POLICY_SIZE {
        return Err(format!("policy index out of range: {index}"));
    }
    Ok(index)
}

pub fn policy_map(fen: &str) -> Result<HashMap<String, usize>, String> {
    let board = parse_board(fen)?;
    let legal = legal_uci_moves(&board);
    let mut result = HashMap::with_capacity(legal.len());
    let mut seen = HashMap::with_capacity(legal.len());
    for uci in legal {
        let index = encode_move(fen, &uci)?;
        if let Some(previous) = seen.insert(index, uci.clone()) {
            return Err(format!(
                "policy collision: {previous} and {uci} both map to {index}"
            ));
        }
        result.insert(uci, index);
    }
    Ok(result)
}

pub fn move_is_capture(board: &Board, uci: &str) -> Result<bool, String> {
    let mv = parse_legal_uci_move(board, uci)?;
    Ok(is_capture(board, mv))
}

pub fn move_is_promotion(board: &Board, uci: &str) -> Result<bool, String> {
    let mv = parse_legal_uci_move(board, uci)?;
    Ok(mv.promotion.is_some())
}

#[must_use]
pub fn perft(board: &Board, depth: u8) -> u64 {
    if depth == 0 {
        return 1;
    }
    let mut moves: Vec<Move> = Vec::new();
    board.generate_moves(|piece_moves| {
        moves.extend(piece_moves);
        false
    });
    if depth == 1 {
        return moves.len() as u64;
    }

    moves
        .into_iter()
        .map(|mv| {
            let mut next = board.clone();
            next.play_unchecked(mv);
            perft(&next, depth - 1)
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packed_move_round_trips_normal_and_promotion_moves() {
        let board = parse_board(START_FEN).unwrap();
        let normal = parse_legal_uci_move(&board, "e2e4").unwrap();
        assert_eq!(PackedMove::from_move(normal).to_move(), Some(normal));

        let promotion_board = parse_board("7k/P7/8/8/8/8/8/7K w - - 0 1").unwrap();
        let promotion = parse_legal_uci_move(&promotion_board, "a7a8q").unwrap();
        assert_eq!(PackedMove::from_move(promotion).to_move(), Some(promotion));
        assert!(PackedMove::NONE.to_move().is_none());
    }

    #[test]
    fn search_position_make_unmake_restores_state() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let mut position = SearchPosition::from_game(&game);
        let original_board = position.board().clone();
        let original_key = position.search_key();
        let mv = parse_legal_uci_move(position.board(), "e2e4").unwrap();
        let undo = position.make_move(mv);
        assert_ne!(position.board(), &original_board);
        position.unmake_move(undo);
        assert_eq!(position.board(), &original_board);
        assert_eq!(position.search_key(), original_key);
    }

    #[test]
    fn synthetic_null_transition_is_exact_and_clears_en_passant() {
        let game = Te1Game::from_fen("4k3/8/8/8/3pP3/8/8/4K3 b - e3 17 1").unwrap();
        let mut position = SearchPosition::from_game(&game);
        let original = position.clone();
        let original_side = position.board().side_to_move();
        let original_pieces = position.board().occupied();
        let original_key = position.search_key();

        let undo = position.make_null_move().unwrap();
        assert_ne!(position.board().side_to_move(), original_side);
        assert_eq!(position.board().occupied(), original_pieces);
        for piece in Piece::ALL {
            assert_eq!(
                position.board().pieces(piece),
                original.board().pieces(piece)
            );
        }
        assert_eq!(
            position.board().castle_rights(cozy_chess::Color::White),
            original.board().castle_rights(cozy_chess::Color::White)
        );
        assert_eq!(
            position.board().castle_rights(cozy_chess::Color::Black),
            original.board().castle_rights(cozy_chess::Color::Black)
        );
        assert!(position.board().en_passant().is_none());
        assert_eq!(position.halfmove_clock(), 17);
        assert_ne!(position.search_key(), original_key);

        position.unmake_null_move(undo);
        assert_eq!(position.board(), original.board());
        assert_eq!(position.search_key(), original_key);
        assert_eq!(position.repetition_count(), original.repetition_count());
        assert_eq!(position.halfmove_clock(), original.halfmove_clock());
        assert_eq!(position.repetition_keys, original.repetition_keys);
    }

    #[test]
    fn synthetic_null_rejects_check_and_barriers_real_repetition() {
        let checked = Te1Game::from_fen("4k3/8/8/8/8/8/4R3/4K3 b - - 0 1").unwrap();
        assert!(
            SearchPosition::from_game(&checked)
                .make_null_move()
                .is_none()
        );

        let mut game = Te1Game::from_fen(START_FEN).unwrap();
        for mv in ["g1f3", "g8f6", "f3g1", "f6g8"] {
            game.play_uci(mv).unwrap();
        }
        let mut position = SearchPosition::from_game(&game);
        assert_eq!(position.repetition_count(), 2);
        let null_undo = position.make_null_move().unwrap();
        assert_eq!(position.repetition_count(), 1);
        let mut move_undos = Vec::new();
        for uci in ["g8f6", "g1f3", "f6g8", "f3g1"] {
            let mv = parse_legal_uci_move(position.board(), uci).unwrap();
            move_undos.push(position.make_move(mv));
        }
        assert_eq!(position.repetition_count(), 2);
        assert!(!position.is_draw());
        for move_undo in move_undos.into_iter().rev() {
            position.unmake_move(move_undo);
        }
        position.unmake_null_move(null_undo);
        assert_eq!(position.repetition_count(), 2);
    }

    #[test]
    fn cached_repetition_count_tracks_make_and_unmake() {
        let game = Te1Game::from_fen(START_FEN).unwrap();
        let mut position = SearchPosition::from_game(&game);
        let mut undos = Vec::new();
        for uci in [
            "g1f3", "g8f6", "f3g1", "f6g8", "g1f3", "g8f6", "f3g1", "f6g8",
        ] {
            let mv = parse_legal_uci_move(position.board(), uci).unwrap();
            undos.push(position.make_move(mv));
        }
        assert_eq!(position.repetition_count(), 3);
        while let Some(undo) = undos.pop() {
            position.unmake_move(undo);
        }
        assert_eq!(position.repetition_count(), 1);
        assert_eq!(position.board(), &Board::default());
    }

    #[test]
    fn start_position_has_twenty_moves() {
        let board = parse_board(START_FEN).expect("start position must parse");
        assert_eq!(legal_uci_moves(&board).len(), 20);
    }

    #[test]
    fn policy_is_colour_canonical() {
        assert_eq!(encode_move(START_FEN, "e2e4").unwrap(), 877);
        let black_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1";
        assert_eq!(encode_move(black_fen, "e7e5").unwrap(), 877);
    }

    #[test]
    fn castling_uses_standard_uci_externally() {
        let fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1";
        let board = parse_board(fen).unwrap();
        let moves = legal_uci_moves(&board);
        assert!(moves.contains(&"e1g1".to_owned()));
        assert!(moves.contains(&"e1c1".to_owned()));
        assert_eq!(
            next_fen(fen, "e1g1").unwrap(),
            "r3k2r/8/8/8/8/8/8/R4RK1 b kq - 1 1"
        );
    }

    #[test]
    fn castling_capture_semantics_are_exact() {
        for (fen, castles) in [
            ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 17 1", ["e1g1", "e1c1"]),
            ("r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 17 1", ["e8g8", "e8c8"]),
        ] {
            let board = parse_board(fen).unwrap();
            for uci in castles {
                let mv = parse_legal_uci_move(&board, uci).unwrap();
                // Prove the regression fixture reaches cozy-chess's internal
                // king-to-own-rook castling representation.
                assert_eq!(board.piece_on(mv.to), Some(Piece::Rook));
                assert_eq!(captured_piece(&board, mv), None);
                assert!(!is_capture(&board, mv));
                assert!(!move_is_capture(&board, uci).unwrap());

                let game = Te1Game::from_fen(fen).unwrap();
                let mut position = SearchPosition::from_game(&game);
                let undo = position.make_move(mv);
                assert_eq!(position.halfmove_clock(), 18);
                position.unmake_move(undo);
                assert_eq!(position.halfmove_clock(), 17);
                assert_eq!(position.board(), &board);
            }
        }

        let capture_board = parse_board("7k/8/8/3q4/4P3/8/8/7K w - - 9 1").unwrap();
        let capture = parse_legal_uci_move(&capture_board, "e4d5").unwrap();
        assert_eq!(captured_piece(&capture_board, capture), Some(Piece::Queen));
        assert!(is_capture(&capture_board, capture));

        let ep_board = parse_board("7k/8/8/3pP3/8/8/8/7K w - d6 9 1").unwrap();
        let en_passant = parse_legal_uci_move(&ep_board, "e5d6").unwrap();
        assert_eq!(captured_piece(&ep_board, en_passant), Some(Piece::Pawn));
        assert!(is_capture(&ep_board, en_passant));
    }

    #[test]
    fn undo_restores_exact_fen() {
        let mut game = Te1Game::from_fen(START_FEN).unwrap();
        game.play_uci("e2e4").unwrap();
        assert_eq!(game.undo().unwrap(), START_FEN);
    }

    #[test]
    fn double_pawn_push_preserves_full_fen_en_passant_target() {
        assert_eq!(
            next_fen(START_FEN, "e2e4").unwrap(),
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
        );
    }

    #[test]
    fn game_status_prioritizes_checkmate_over_draw_counters() {
        let game = Te1Game::from_fen("7k/6Q1/6K1/8/8/8/8/8 b - - 100 1").unwrap();
        assert_eq!(game.status(), Te1Status::Checkmate);
    }

    #[test]
    fn two_knights_are_not_a_dead_position() {
        let fen = "7k/8/8/8/8/8/8/KNN5 w - - 0 1";
        assert_eq!(static_status(fen).unwrap(), Te1Status::Ongoing);
    }

    #[test]
    fn known_perft_positions_pass() {
        let start = parse_board(START_FEN).unwrap();
        assert_eq!(perft(&start, 1), 20);
        assert_eq!(perft(&start, 2), 400);
        assert_eq!(perft(&start, 3), 8_902);
        assert_eq!(perft(&start, 4), 197_281);

        let kiwipete =
            parse_board("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
                .unwrap();
        assert_eq!(perft(&kiwipete, 1), 48);
        assert_eq!(perft(&kiwipete, 2), 2_039);
        assert_eq!(perft(&kiwipete, 3), 97_862);
    }

    #[test]
    fn native_history_encoding_has_expected_shape() {
        let encoded = encode_history(&[START_FEN.to_owned()]).unwrap();
        assert_eq!(encoded.len(), INPUT_PLANES * 64);
        assert_eq!(
            encoding_digest(&[START_FEN.to_owned()]).unwrap().nonzero,
            352
        );
    }
    #[test]
    fn exact_repetition_ignores_irrelevant_en_passant() {
        // These positions are the documented cozy-chess equivalence case:
        // e3 is a syntactically valid FEN target, but Black has no legal
        // en-passant capture, so FIDE repetition treats it as irrelevant.
        let history = vec![
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1".to_owned(),
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 4 3".to_owned(),
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 8 5".to_owned(),
        ];
        let board_with_irrelevant_ep = parse_board(&history[0]).unwrap();
        let board_without_ep = parse_board(&history[1]).unwrap();
        assert!(!has_legal_en_passant(&board_with_irrelevant_ep));
        assert_eq!(
            fide_position_hash(&board_with_irrelevant_ep),
            fide_position_hash(&board_without_ep)
        );

        let game = Te1Game::from_history(&history).unwrap();
        assert_eq!(game.repetition_count(), 3);
        assert_eq!(fide_repetition_count(&history).unwrap(), 3);
        // The frozen alpha.2.2 network-compatibility counter intentionally
        // keeps the literal FEN en-passant field and therefore returns two.
        assert_eq!(repetition_count(&history), 2);
    }

    #[test]
    fn exact_repetition_preserves_legally_available_en_passant() {
        let with_ep = "rnbqkb1r/ppp1pppp/5n2/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3";
        let without_ep = "rnbqkb1r/ppp1pppp/5n2/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq - 4 5";
        let board_with_ep = parse_board(with_ep).unwrap();
        let board_without_ep = parse_board(without_ep).unwrap();
        assert!(has_legal_en_passant(&board_with_ep));
        assert!(!has_legal_en_passant(&board_without_ep));
        assert!(!board_with_ep.same_position(&board_without_ep));
        assert_ne!(
            fide_position_hash(&board_with_ep),
            fide_position_hash(&board_without_ep)
        );
    }

    #[test]
    fn search_key_normalizes_only_irrelevant_en_passant() {
        let with_irrelevant_ep =
            Te1Game::from_fen("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1")
                .unwrap();
        let without_ep =
            Te1Game::from_fen("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1")
                .unwrap();
        assert_eq!(with_irrelevant_ep.search_key(), without_ep.search_key());
    }

    #[test]
    fn invalid_history_reports_the_index_and_fen() {
        // e3 implies that the white pawn just moved from e2 to e4, but e2 is
        // occupied by the white king. cozy-chess correctly rejects this FEN.
        let invalid = vec!["8/8/8/8/4P3/8/4K3/7k b - e3 0 1".to_owned()];
        let error = Te1Game::from_history(&invalid).unwrap_err();
        assert!(error.contains("history[0]"));
        assert!(error.contains(&invalid[0]));
    }

    #[test]
    fn local_fen_parser_rejects_zero_width_empty_runs() {
        let invalid = "7k/8/8/8/8/8/0K7/8 w - - 0 1";
        assert!(parse_fen(invalid).is_err());
    }

    #[test]
    fn policy_encoder_rejects_illegal_moves() {
        assert!(encode_move(START_FEN, "e2e5").is_err());
    }

    #[test]
    fn capture_and_promotion_classification_is_legal_move_aware() {
        let capture_fen = "8/8/8/3p4/4P3/8/4K3/7k w - - 0 1";
        let capture_board = parse_board(capture_fen).unwrap();
        assert!(move_is_capture(&capture_board, "e4d5").unwrap());
        assert!(!move_is_capture(&capture_board, "e4e5").unwrap());

        let promotion_fen = "8/P7/8/8/8/8/4K3/7k w - - 0 1";
        let promotion_board = parse_board(promotion_fen).unwrap();
        assert!(move_is_promotion(&promotion_board, "a7a8q").unwrap());
    }

    #[test]
    fn search_key_is_history_and_clock_sensitive() {
        let direct = Te1Game::from_fen(START_FEN).unwrap();
        let mut repeated = Te1Game::from_fen(START_FEN).unwrap();
        for mv in ["g1f3", "g8f6", "f3g1", "f6g8"] {
            repeated.play_uci(mv).unwrap();
        }
        assert_eq!(direct.board_hash(), repeated.board_hash());
        assert_ne!(direct.search_key(), repeated.search_key());
    }
}
