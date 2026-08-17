#![deny(unsafe_op_in_unsafe_fn)]
#![deny(clippy::undocumented_unsafe_blocks)]

mod simd;

use cozy_chess::{BitBoard, Board, Color, Piece, Square};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

pub const NUM_FEATURES: usize = 22_528;
pub const PAD_INDEX: usize = NUM_FEATURES;
pub const MAX_ACTIVE_FEATURES: usize = 31;
const RELATIVE_PIECE_CLASSES: usize = 11;
const BOARD_SQUARES: usize = 64;
const MAGIC: &[u8; 8] = b"TE1NN001";
const FORMAT_VERSION: u32 = 1;
const DTYPE_INT16: u8 = 1;
const MAX_METADATA_BYTES: usize = 1 << 20;
const MAX_TENSORS: usize = 64;
const MAX_TENSOR_BYTES: usize = 1 << 30;
const COLORS: [Color; 2] = [Color::White, Color::Black];
const PIECES: [Piece; 6] = [
    Piece::Pawn,
    Piece::Knight,
    Piece::Bishop,
    Piece::Rook,
    Piece::Queen,
    Piece::King,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Activation {
    CReLU,
    SCReLU,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NnueOutput {
    pub wdl: [f32; 3],
    pub cp_normalized: f32,
    pub cp: i32,
}

#[derive(Debug, Clone)]
struct Tensor {
    shape: Vec<usize>,
    values: Vec<f32>,
}

#[derive(Debug)]
pub struct Network {
    name: String,
    feature_set: String,
    width: usize,
    hidden: usize,
    activation: Activation,
    simd_backend: Option<simd::Avx2Fma>,
    feature_weights: Vec<f32>,
    feature_bias: Vec<f32>,
    hidden_weights: Vec<f32>,
    hidden_bias: Vec<f32>,
    wdl_weights: Vec<f32>,
    wdl_bias: [f32; 3],
    cp_weights: Vec<f32>,
    cp_bias: f32,
}

#[derive(Debug, Deserialize)]
struct Metadata {
    candidate: CandidateMetadata,
    feature_set: String,
    max_active_features: usize,
    num_features: usize,
}

#[derive(Debug, Deserialize)]
struct CandidateMetadata {
    name: String,
    width: usize,
    hidden: usize,
    activation: String,
}

#[derive(Debug, Clone)]
struct PerspectiveAccumulator {
    king_bucket: usize,
    mirror: bool,
    values: Vec<f32>,
}

#[derive(Debug, Clone)]
pub struct Accumulator {
    white: PerspectiveAccumulator,
    black: PerspectiveAccumulator,
}

/// Reusable per-thread inference buffers. Search keeps one instance per worker,
/// eliminating activation and hidden-layer allocations from the node hot path.
#[derive(Debug, Clone)]
pub struct InferenceScratch {
    activated: Vec<f32>,
    hidden: Vec<f32>,
}

impl InferenceScratch {
    fn for_network(network: &Network) -> Self {
        Self {
            activated: vec![0.0; 2 * network.width],
            hidden: vec![0.0; network.hidden],
        }
    }

    fn prepare(&mut self, network: &Network) {
        if self.activated.len() != 2 * network.width {
            self.activated.resize(2 * network.width, 0.0);
        }
        if self.hidden.len() != network.hidden {
            self.hidden.resize(network.hidden, 0.0);
        }
    }
}

impl Network {
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self, String> {
        let path = path.as_ref();
        let bytes = fs::read(path)
            .map_err(|error| format!("failed to read {}: {error}", path.display()))?;
        Self::from_bytes(&bytes)
    }

    pub fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        let mut reader = Reader::new(bytes);
        if reader.take(MAGIC.len())? != MAGIC.as_slice() {
            return Err("invalid TE1 NNUE magic".to_owned());
        }
        let version = reader.u32()?;
        if version != FORMAT_VERSION {
            return Err(format!("unsupported TE1 NNUE format version: {version}"));
        }
        let metadata_length = usize::try_from(reader.u32()?)
            .map_err(|_| "metadata length does not fit usize".to_owned())?;
        if !(1..=MAX_METADATA_BYTES).contains(&metadata_length) {
            return Err("TE1 NNUE metadata length is unreasonable".to_owned());
        }
        let metadata: Metadata = serde_json::from_slice(reader.take(metadata_length)?)
            .map_err(|error| format!("invalid TE1 NNUE metadata: {error}"))?;
        if metadata.num_features != NUM_FEATURES {
            return Err(format!(
                "unsupported feature count: expected {NUM_FEATURES}, got {}",
                metadata.num_features
            ));
        }
        if metadata.max_active_features != MAX_ACTIVE_FEATURES {
            return Err(format!(
                "unsupported active-feature count: expected {MAX_ACTIVE_FEATURES}, got {}",
                metadata.max_active_features
            ));
        }
        if metadata.feature_set != "TE1-K32-RP11-v1" {
            return Err(format!("unsupported feature set: {}", metadata.feature_set));
        }
        if !(1..=4096).contains(&metadata.candidate.width) {
            return Err("NNUE width is outside the supported range".to_owned());
        }
        if !(1..=4096).contains(&metadata.candidate.hidden) {
            return Err("NNUE hidden width is outside the supported range".to_owned());
        }
        let activation = match metadata.candidate.activation.as_str() {
            "crelu" => Activation::CReLU,
            "screlu" => Activation::SCReLU,
            other => return Err(format!("unsupported activation: {other}")),
        };

        let tensor_count = usize::try_from(reader.u32()?)
            .map_err(|_| "tensor count does not fit usize".to_owned())?;
        if !(1..=MAX_TENSORS).contains(&tensor_count) {
            return Err("TE1 NNUE tensor count is unreasonable".to_owned());
        }
        let mut tensors = BTreeMap::new();
        for _ in 0..tensor_count {
            let name_length = usize::from(reader.u16()?);
            let dtype = reader.u8()?;
            let rank = usize::from(reader.u8()?);
            if !(1..=4096).contains(&name_length) {
                return Err("invalid tensor name length".to_owned());
            }
            if dtype != DTYPE_INT16 {
                return Err(format!("unsupported tensor dtype code: {dtype}"));
            }
            if !(1..=8).contains(&rank) {
                return Err("unreasonable tensor rank".to_owned());
            }
            let name = std::str::from_utf8(reader.take(name_length)?)
                .map_err(|error| format!("tensor name is not UTF-8: {error}"))?
                .to_owned();
            if tensors.contains_key(&name) {
                return Err(format!("duplicate tensor name: {name}"));
            }
            let mut shape = Vec::with_capacity(rank);
            let mut element_count = 1usize;
            for _ in 0..rank {
                let dimension = usize::try_from(reader.u32()?)
                    .map_err(|_| "tensor dimension does not fit usize".to_owned())?;
                if dimension == 0 {
                    return Err(format!("tensor {name} has a zero-sized dimension"));
                }
                element_count = element_count
                    .checked_mul(dimension)
                    .ok_or_else(|| "tensor element count overflow".to_owned())?;
                shape.push(dimension);
            }
            let scale = reader.f32()?;
            if !scale.is_finite() || scale <= 0.0 {
                return Err(format!("tensor {name} has invalid scale"));
            }
            let byte_length = usize::try_from(reader.u64()?)
                .map_err(|_| "tensor byte length does not fit usize".to_owned())?;
            let expected_bytes = element_count
                .checked_mul(2)
                .ok_or_else(|| "tensor byte length overflow".to_owned())?;
            if byte_length != expected_bytes || byte_length > MAX_TENSOR_BYTES {
                return Err(format!(
                    "tensor {name} byte length does not match its shape"
                ));
            }
            let payload = reader.take(byte_length)?;
            let mut values = Vec::with_capacity(element_count);
            for pair in payload.chunks_exact(2) {
                let quantized = i16::from_le_bytes([pair[0], pair[1]]);
                values.push(f32::from(quantized) * scale);
            }
            tensors.insert(name, Tensor { shape, values });
        }
        if !reader.is_finished() {
            return Err("unexpected trailing bytes in TE1 NNUE file".to_owned());
        }

        let width = metadata.candidate.width;
        let hidden = metadata.candidate.hidden;
        let feature_weights = take_tensor(&mut tensors, "feature.weight", &[NUM_FEATURES, width])?;
        let feature_bias = take_tensor(&mut tensors, "feature_bias", &[width])?;
        let hidden_weights = take_tensor(&mut tensors, "hidden.weight", &[hidden, 2 * width])?;
        let hidden_bias = take_tensor(&mut tensors, "hidden.bias", &[hidden])?;
        let wdl_weights = take_tensor(&mut tensors, "wdl_head.weight", &[3, hidden])?;
        let wdl_bias_values = take_tensor(&mut tensors, "wdl_head.bias", &[3])?;
        let cp_weights = take_tensor(&mut tensors, "cp_head.weight", &[1, hidden])?;
        let cp_bias_values = take_tensor(&mut tensors, "cp_head.bias", &[1])?;
        if !tensors.is_empty() {
            return Err(format!(
                "unexpected tensors in TE1 NNUE file: {:?}",
                tensors.keys().collect::<Vec<_>>()
            ));
        }

        Ok(Self {
            name: metadata.candidate.name,
            feature_set: metadata.feature_set,
            width,
            hidden,
            activation,
            simd_backend: if activation == Activation::CReLU && width.is_multiple_of(16) {
                simd::Avx2Fma::detect()
            } else {
                None
            },
            feature_weights,
            feature_bias,
            hidden_weights,
            hidden_bias,
            wdl_weights,
            wdl_bias: [wdl_bias_values[0], wdl_bias_values[1], wdl_bias_values[2]],
            cp_weights,
            cp_bias: cp_bias_values[0],
        })
    }

    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    #[must_use]
    pub fn feature_set(&self) -> &str {
        &self.feature_set
    }

    #[must_use]
    pub fn width(&self) -> usize {
        self.width
    }

    #[must_use]
    pub fn hidden(&self) -> usize {
        self.hidden
    }

    #[must_use]
    pub fn activation(&self) -> Activation {
        self.activation
    }

    #[must_use]
    pub fn inference_kernel_name(&self) -> &'static str {
        if self.simd_backend.is_some() {
            "avx2-fma"
        } else {
            "scalar"
        }
    }

    /// Force the scalar reference path for differential benchmarking.
    pub fn force_scalar_kernel(&mut self) {
        self.simd_backend = None;
    }

    /// Force the AVX2+FMA path when the current CPU and network support it.
    pub fn force_avx2_fma_kernel(&mut self) -> Result<(), String> {
        if self.activation != Activation::CReLU {
            return Err("AVX2 kernel currently supports CReLU networks only".to_owned());
        }
        if !self.width.is_multiple_of(16) {
            return Err("AVX2 kernel requires a width divisible by 16".to_owned());
        }
        self.simd_backend = Some(
            simd::Avx2Fma::detect()
                .ok_or_else(|| "AVX2+FMA is not available on this CPU".to_owned())?,
        );
        Ok(())
    }

    #[must_use]
    pub fn inference_scratch(&self) -> InferenceScratch {
        InferenceScratch::for_network(self)
    }

    pub fn encode_board(&self, board: &Board) -> Result<(Vec<usize>, Vec<usize>), String> {
        Ok((
            encode_perspective(board, Color::White)?,
            encode_perspective(board, Color::Black)?,
        ))
    }

    pub fn accumulator(&self, board: &Board) -> Result<Accumulator, String> {
        Ok(Accumulator {
            white: self.refresh_perspective(board, Color::White)?,
            black: self.refresh_perspective(board, Color::Black)?,
        })
    }

    pub fn evaluate_board(&self, board: &Board) -> Result<NnueOutput, String> {
        let accumulator = self.accumulator(board)?;
        Ok(self.evaluate_accumulator(&accumulator, board.side_to_move()))
    }

    pub fn evaluate_features(
        &self,
        white_features: &[usize],
        black_features: &[usize],
        white_to_move: bool,
    ) -> Result<NnueOutput, String> {
        let white = self.accumulate_features(white_features)?;
        let black = self.accumulate_features(black_features)?;
        let accumulator = Accumulator {
            white: PerspectiveAccumulator {
                king_bucket: 0,
                mirror: false,
                values: white,
            },
            black: PerspectiveAccumulator {
                king_bucket: 0,
                mirror: false,
                values: black,
            },
        };
        Ok(self.evaluate_accumulator(
            &accumulator,
            if white_to_move {
                Color::White
            } else {
                Color::Black
            },
        ))
    }

    #[must_use]
    pub fn evaluate_accumulator(
        &self,
        accumulator: &Accumulator,
        side_to_move: Color,
    ) -> NnueOutput {
        let mut scratch = self.inference_scratch();
        self.evaluate_accumulator_with_scratch(accumulator, side_to_move, &mut scratch)
    }

    #[must_use]
    pub fn evaluate_accumulator_with_scratch(
        &self,
        accumulator: &Accumulator,
        side_to_move: Color,
        scratch: &mut InferenceScratch,
    ) -> NnueOutput {
        self.prepare_hidden(accumulator, side_to_move, scratch);

        let mut logits = [0.0f32; 3];
        for (row, output) in logits.iter_mut().enumerate() {
            let weights = &self.wdl_weights[row * self.hidden..(row + 1) * self.hidden];
            let mut value = self.wdl_bias[row];
            for (&weight, &input) in weights.iter().zip(&scratch.hidden) {
                value = weight.mul_add(input, value);
            }
            *output = value;
        }
        let wdl = softmax(logits);
        let (cp_normalized, cp) = self.cp_from_hidden(&scratch.hidden);
        NnueOutput {
            wdl,
            cp_normalized,
            cp,
        }
    }

    /// Search-only CP path. WDL softmax is intentionally skipped because alpha-beta
    /// consumes only the scalar centipawn score. The scalar result is identical to
    /// `evaluate_accumulator(...).cp`.
    #[must_use]
    pub fn evaluate_accumulator_cp(
        &self,
        accumulator: &Accumulator,
        side_to_move: Color,
        scratch: &mut InferenceScratch,
    ) -> i32 {
        self.prepare_hidden(accumulator, side_to_move, scratch);
        self.cp_from_hidden(&scratch.hidden).1
    }

    fn prepare_hidden(
        &self,
        accumulator: &Accumulator,
        side_to_move: Color,
        scratch: &mut InferenceScratch,
    ) {
        scratch.prepare(self);
        let (first, second) = if side_to_move == Color::White {
            (&accumulator.white.values, &accumulator.black.values)
        } else {
            (&accumulator.black.values, &accumulator.white.values)
        };
        if self.activation == Activation::CReLU
            && self.simd_backend.is_some_and(|backend| {
                backend.hidden_crelu(
                    first,
                    second,
                    self.width,
                    &self.hidden_weights,
                    &self.hidden_bias,
                    &mut scratch.hidden,
                )
            })
        {
            return;
        }

        let (first_activated, second_activated) = scratch.activated.split_at_mut(self.width);
        for (output, &input) in first_activated.iter_mut().zip(first) {
            *output = activate(input, self.activation);
        }
        for (output, &input) in second_activated.iter_mut().zip(second) {
            *output = activate(input, self.activation);
        }

        for (row, output) in scratch.hidden.iter_mut().enumerate() {
            let weights = &self.hidden_weights[row * 2 * self.width..(row + 1) * 2 * self.width];
            let mut value = self.hidden_bias[row];
            for (&weight, &input) in weights.iter().zip(&scratch.activated) {
                value = weight.mul_add(input, value);
            }
            *output = activate(value, self.activation);
        }
    }

    fn cp_from_hidden(&self, hidden: &[f32]) -> (f32, i32) {
        let mut cp_raw = self.cp_bias;
        for (&weight, &input) in self.cp_weights.iter().zip(hidden) {
            cp_raw = weight.mul_add(input, cp_raw);
        }
        let cp_normalized = cp_raw.tanh();
        let bounded = cp_normalized.clamp(-0.999_999, 0.999_999);
        let cp_float = 600.0 * bounded.atanh();
        let cp = cp_float.round().clamp(-20_000.0, 20_000.0) as i32;
        (cp_normalized, cp)
    }

    fn accumulate_features(&self, features: &[usize]) -> Result<Vec<f32>, String> {
        if features.len() > MAX_ACTIVE_FEATURES {
            return Err("too many active NNUE features".to_owned());
        }
        let mut values = self.feature_bias.clone();
        for &feature in features {
            if feature == PAD_INDEX {
                continue;
            }
            if feature >= NUM_FEATURES {
                return Err(format!("feature index outside table: {feature}"));
            }
            self.add_feature(&mut values, feature);
        }
        Ok(values)
    }

    fn refresh_perspective(
        &self,
        board: &Board,
        perspective: Color,
    ) -> Result<PerspectiveAccumulator, String> {
        let frame = king_frame(board, perspective)?;
        let mut values = self.feature_bias.clone();
        for color in COLORS {
            for piece in PIECES {
                for square in board.colored_pieces(color, piece) {
                    if let Some(feature) = feature_index(frame, perspective, color, piece, square) {
                        self.add_feature(&mut values, feature);
                    }
                }
            }
        }
        Ok(PerspectiveAccumulator {
            king_bucket: frame.king_bucket,
            mirror: frame.mirror,
            values,
        })
    }

    fn add_feature(&self, accumulator: &mut [f32], feature: usize) {
        let start = feature * self.width;
        let weights = &self.feature_weights[start..start + self.width];
        if self
            .simd_backend
            .is_some_and(|backend| backend.add_assign(accumulator, weights))
        {
            return;
        }
        for (value, weight) in accumulator.iter_mut().zip(weights) {
            *value += *weight;
        }
    }

    fn remove_feature(&self, accumulator: &mut [f32], feature: usize) {
        let start = feature * self.width;
        let weights = &self.feature_weights[start..start + self.width];
        if self
            .simd_backend
            .is_some_and(|backend| backend.sub_assign(accumulator, weights))
        {
            return;
        }
        for (value, weight) in accumulator.iter_mut().zip(weights) {
            *value -= *weight;
        }
    }
}

impl Accumulator {
    pub fn update_between(
        &mut self,
        network: &Network,
        before: &Board,
        after: &Board,
    ) -> Result<(), String> {
        update_perspective(&mut self.white, network, before, after, Color::White)?;
        update_perspective(&mut self.black, network, before, after, Color::Black)?;
        Ok(())
    }

    #[must_use]
    pub fn max_abs_difference(&self, other: &Self) -> f32 {
        self.white
            .values
            .iter()
            .chain(&self.black.values)
            .zip(other.white.values.iter().chain(&other.black.values))
            .map(|(left, right)| (left - right).abs())
            .fold(0.0f32, f32::max)
    }
}

fn update_perspective(
    accumulator: &mut PerspectiveAccumulator,
    network: &Network,
    before: &Board,
    after: &Board,
    perspective: Color,
) -> Result<(), String> {
    let after_frame = king_frame(after, perspective)?;
    if accumulator.king_bucket != after_frame.king_bucket
        || accumulator.mirror != after_frame.mirror
    {
        *accumulator = network.refresh_perspective(after, perspective)?;
        return Ok(());
    }
    let frame = KingFrame {
        king_bucket: accumulator.king_bucket,
        mirror: accumulator.mirror,
    };
    for square in changed_squares(before, after) {
        if let Some((color, piece)) = piece_at(before, square)
            && let Some(feature) = feature_index(frame, perspective, color, piece, square)
        {
            network.remove_feature(&mut accumulator.values, feature);
        }
        if let Some((color, piece)) = piece_at(after, square)
            && let Some(feature) = feature_index(frame, perspective, color, piece, square)
        {
            network.add_feature(&mut accumulator.values, feature);
        }
    }
    Ok(())
}

fn changed_squares(before: &Board, after: &Board) -> BitBoard {
    let mut changed = BitBoard::EMPTY;
    for color in COLORS {
        for piece in PIECES {
            changed |= before.colored_pieces(color, piece) ^ after.colored_pieces(color, piece);
        }
    }
    changed
}

#[derive(Debug, Clone, Copy)]
struct KingFrame {
    king_bucket: usize,
    mirror: bool,
}

fn king_frame(board: &Board, perspective: Color) -> Result<KingFrame, String> {
    let mut kings = board.colored_pieces(perspective, Piece::King).iter();
    let king = kings
        .next()
        .ok_or_else(|| format!("position has no {perspective:?} king"))?;
    if kings.next().is_some() {
        return Err(format!("position has more than one {perspective:?} king"));
    }
    let square = king as usize;
    let file = square & 7;
    let rank = square >> 3;
    let relative_rank = if perspective == Color::White {
        rank
    } else {
        7 - rank
    };
    let mirror = file >= 4;
    let canonical_file = if mirror { 7 - file } else { file };
    Ok(KingFrame {
        king_bucket: relative_rank * 4 + canonical_file,
        mirror,
    })
}

fn piece_at(board: &Board, square: Square) -> Option<(Color, Piece)> {
    Some((board.color_on(square)?, board.piece_on(square)?))
}

fn feature_index(
    frame: KingFrame,
    perspective: Color,
    color: Color,
    piece: Piece,
    square: Square,
) -> Option<usize> {
    let own = color == perspective;
    if own && piece == Piece::King {
        return None;
    }
    let piece_kind = piece_code(piece);
    let relative_piece = if own {
        piece_kind
    } else if piece == Piece::King {
        10
    } else {
        5 + piece_kind
    };
    let square_index = square as usize;
    let file = square_index & 7;
    let rank = square_index >> 3;
    let transformed_rank = if perspective == Color::White {
        rank
    } else {
        7 - rank
    };
    let transformed_file = if frame.mirror { 7 - file } else { file };
    let transformed_square = transformed_rank * 8 + transformed_file;
    Some(
        (frame.king_bucket * RELATIVE_PIECE_CLASSES + relative_piece) * BOARD_SQUARES
            + transformed_square,
    )
}

const fn piece_code(piece: Piece) -> usize {
    match piece {
        Piece::Pawn => 0,
        Piece::Knight => 1,
        Piece::Bishop => 2,
        Piece::Rook => 3,
        Piece::Queen => 4,
        Piece::King => 5,
    }
}

fn activate(value: f32, activation: Activation) -> f32 {
    let clipped = value.clamp(0.0, 1.0);
    match activation {
        Activation::CReLU => clipped,
        Activation::SCReLU => clipped * clipped,
    }
}

fn softmax(logits: [f32; 3]) -> [f32; 3] {
    let maximum = logits.into_iter().fold(f32::NEG_INFINITY, f32::max);
    let exp = logits.map(|value| (value - maximum).exp());
    let sum = exp.iter().sum::<f32>();
    if !sum.is_finite() || sum <= 0.0 {
        return [1.0 / 3.0; 3];
    }
    [exp[0] / sum, exp[1] / sum, exp[2] / sum]
}

fn encode_perspective(board: &Board, perspective: Color) -> Result<Vec<usize>, String> {
    let frame = king_frame(board, perspective)?;
    let mut active = Vec::with_capacity(MAX_ACTIVE_FEATURES);
    for color in COLORS {
        for piece in PIECES {
            for square in board.colored_pieces(color, piece) {
                if let Some(feature) = feature_index(frame, perspective, color, piece, square) {
                    if feature >= NUM_FEATURES {
                        return Err("generated feature is outside the table".to_owned());
                    }
                    active.push(feature);
                }
            }
        }
    }
    if active.len() > MAX_ACTIVE_FEATURES {
        return Err("position has too many active NNUE features".to_owned());
    }
    active.sort_unstable();
    if active.windows(2).any(|window| window[0] == window[1]) {
        return Err("duplicate NNUE feature generated".to_owned());
    }
    active.resize(MAX_ACTIVE_FEATURES, PAD_INDEX);
    Ok(active)
}

fn take_tensor(
    tensors: &mut BTreeMap<String, Tensor>,
    name: &str,
    expected_shape: &[usize],
) -> Result<Vec<f32>, String> {
    let tensor = tensors
        .remove(name)
        .ok_or_else(|| format!("missing tensor: {name}"))?;
    if tensor.shape.as_slice() != expected_shape {
        return Err(format!(
            "tensor {name} has shape {:?}, expected {expected_shape:?}",
            tensor.shape
        ));
    }
    if tensor.values.iter().any(|value| !value.is_finite()) {
        return Err(format!("tensor {name} contains non-finite values"));
    }
    Ok(tensor.values)
}

struct Reader<'a> {
    data: &'a [u8],
    offset: usize,
}

impl<'a> Reader<'a> {
    const fn new(data: &'a [u8]) -> Self {
        Self { data, offset: 0 }
    }

    fn take(&mut self, size: usize) -> Result<&'a [u8], String> {
        let end = self
            .offset
            .checked_add(size)
            .ok_or_else(|| "TE1 NNUE offset overflow".to_owned())?;
        if end > self.data.len() {
            return Err("truncated TE1 NNUE file".to_owned());
        }
        let result = &self.data[self.offset..end];
        self.offset = end;
        Ok(result)
    }

    fn u8(&mut self) -> Result<u8, String> {
        Ok(self.take(1)?[0])
    }

    fn u16(&mut self) -> Result<u16, String> {
        let bytes = self.take(2)?;
        Ok(u16::from_le_bytes([bytes[0], bytes[1]]))
    }

    fn u32(&mut self) -> Result<u32, String> {
        let bytes = self.take(4)?;
        Ok(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
    }

    fn u64(&mut self) -> Result<u64, String> {
        let bytes = self.take(8)?;
        Ok(u64::from_le_bytes([
            bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
        ]))
    }

    fn f32(&mut self) -> Result<f32, String> {
        let bytes = self.take(4)?;
        Ok(f32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
    }

    const fn is_finished(&self) -> bool {
        self.offset == self.data.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;
    use std::io::{BufRead, BufReader};
    use te1_chess::{START_FEN, Te1Game};

    const NETWORK: &[u8] = include_bytes!("../fixtures/network.te1nn");

    #[derive(Deserialize)]
    struct ReferenceVector {
        white_features: Vec<usize>,
        black_features: Vec<usize>,
        white_to_move: bool,
        quantized_wdl: [f32; 3],
        quantized_cp_normalized: f32,
    }

    #[derive(Deserialize)]
    struct FeatureFixture {
        fen: String,
        white_features: Vec<usize>,
        black_features: Vec<usize>,
    }

    #[test]
    fn rejects_bad_magic_truncation_and_trailing_bytes() {
        let mut bad_magic = NETWORK.to_vec();
        bad_magic[0] ^= 0xff;
        assert!(Network::from_bytes(&bad_magic).is_err());
        assert!(Network::from_bytes(&NETWORK[..NETWORK.len() / 2]).is_err());
        let mut trailing = NETWORK.to_vec();
        trailing.push(0);
        assert!(Network::from_bytes(&trailing).is_err());
    }

    #[test]
    fn reference_vectors_match_python_quantized_inference() {
        let network = Network::from_bytes(NETWORK).unwrap();
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/reference-vectors.jsonl");
        let file = fs::File::open(path).unwrap();
        let mut count = 0usize;
        let mut max_wdl = 0.0f32;
        let mut max_cp = 0.0f32;
        for line in BufReader::new(file).lines() {
            let vector: ReferenceVector = serde_json::from_str(&line.unwrap()).unwrap();
            let output = network
                .evaluate_features(
                    &vector.white_features,
                    &vector.black_features,
                    vector.white_to_move,
                )
                .unwrap();
            for (&actual, &expected) in output.wdl.iter().zip(&vector.quantized_wdl) {
                max_wdl = max_wdl.max((actual - expected).abs());
            }
            max_cp = max_cp.max((output.cp_normalized - vector.quantized_cp_normalized).abs());
            count += 1;
        }
        assert_eq!(count, 256);
        assert!(max_wdl <= 2.0e-4, "max WDL error {max_wdl}");
        assert!(max_cp <= 2.0e-4, "max CP error {max_cp}");
    }

    #[test]
    fn avx2_kernel_matches_scalar_reference_when_available() {
        if simd::Avx2Fma::detect().is_none() {
            return;
        }
        let mut scalar = Network::from_bytes(NETWORK).unwrap();
        scalar.force_scalar_kernel();
        let mut vector = Network::from_bytes(NETWORK).unwrap();
        vector.force_avx2_fma_kernel().unwrap();
        let boards = [
            Board::default(),
            "r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 4 8"
                .parse()
                .unwrap(),
            "8/5pk1/3p2p1/4p3/4P3/3P2P1/5PK1/8 b - - 0 1"
                .parse()
                .unwrap(),
        ];
        for board in boards {
            let scalar_acc = scalar.accumulator(&board).unwrap();
            let vector_acc = vector.accumulator(&board).unwrap();
            assert!(scalar_acc.max_abs_difference(&vector_acc) <= 3.0e-5);
            let scalar_output = scalar.evaluate_accumulator(&scalar_acc, board.side_to_move());
            let vector_output = vector.evaluate_accumulator(&vector_acc, board.side_to_move());
            for (&left, &right) in scalar_output.wdl.iter().zip(&vector_output.wdl) {
                assert!((left - right).abs() <= 2.0e-4);
            }
            assert!((scalar_output.cp_normalized - vector_output.cp_normalized).abs() <= 2.0e-4);
            assert!((scalar_output.cp - vector_output.cp).abs() <= 1);
        }
    }

    #[test]
    fn avx2_kernel_matches_scalar_feature_fixture_corpus_when_available() {
        if simd::Avx2Fma::detect().is_none() {
            return;
        }
        let mut scalar = Network::from_bytes(NETWORK).unwrap();
        scalar.force_scalar_kernel();
        let mut vector = Network::from_bytes(NETWORK).unwrap();
        vector.force_avx2_fma_kernel().unwrap();
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/feature-fixtures.jsonl");
        let file = fs::File::open(path).unwrap();
        let mut count = 0usize;
        let mut max_accumulator_error = 0.0f32;
        let mut max_wdl_error = 0.0f32;
        let mut max_cp_normalized_error = 0.0f32;
        let mut max_cp_integer_delta = 0i32;

        for line in BufReader::new(file).lines() {
            let fixture: FeatureFixture = serde_json::from_str(&line.unwrap()).unwrap();
            let board: Board = fixture.fen.parse().unwrap();
            let scalar_acc = scalar.accumulator(&board).unwrap();
            let vector_acc = vector.accumulator(&board).unwrap();
            max_accumulator_error =
                max_accumulator_error.max(scalar_acc.max_abs_difference(&vector_acc));
            let scalar_output = scalar.evaluate_accumulator(&scalar_acc, board.side_to_move());
            let vector_output = vector.evaluate_accumulator(&vector_acc, board.side_to_move());
            for (&left, &right) in scalar_output.wdl.iter().zip(&vector_output.wdl) {
                max_wdl_error = max_wdl_error.max((left - right).abs());
            }
            max_cp_normalized_error = max_cp_normalized_error
                .max((scalar_output.cp_normalized - vector_output.cp_normalized).abs());
            max_cp_integer_delta =
                max_cp_integer_delta.max((scalar_output.cp - vector_output.cp).abs());
            count += 1;
        }

        assert_eq!(count, 260);
        assert!(
            max_accumulator_error <= 3.0e-5,
            "max accumulator error {max_accumulator_error}"
        );
        assert!(max_wdl_error <= 2.0e-4, "max WDL error {max_wdl_error}");
        assert!(
            max_cp_normalized_error <= 2.0e-4,
            "max normalized CP error {max_cp_normalized_error}"
        );
        assert!(
            max_cp_integer_delta <= 1,
            "max integer CP delta {max_cp_integer_delta}"
        );
    }

    #[test]
    fn reusable_cp_path_matches_full_output() {
        let network = Network::from_bytes(NETWORK).unwrap();
        let boards = [
            Board::default(),
            "r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 4 8"
                .parse()
                .unwrap(),
            "8/5pk1/3p2p1/4p3/4P3/3P2P1/5PK1/8 b - - 0 1"
                .parse()
                .unwrap(),
        ];
        let mut scratch = network.inference_scratch();
        for board in boards {
            let accumulator = network.accumulator(&board).unwrap();
            let full = network.evaluate_accumulator(&accumulator, board.side_to_move());
            let cp =
                network.evaluate_accumulator_cp(&accumulator, board.side_to_move(), &mut scratch);
            assert_eq!(cp, full.cp);
        }
    }

    #[test]
    fn feature_encoding_matches_python_fixtures() {
        let network = Network::from_bytes(NETWORK).unwrap();
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/feature-fixtures.jsonl");
        let file = fs::File::open(path).unwrap();
        let mut count = 0usize;
        for line in BufReader::new(file).lines() {
            let fixture: FeatureFixture = serde_json::from_str(&line.unwrap()).unwrap();
            let board: Board = fixture.fen.parse().unwrap();
            let (white, black) = network.encode_board(&board).unwrap();
            assert_eq!(
                white, fixture.white_features,
                "white mismatch for {}",
                fixture.fen
            );
            assert_eq!(
                black, fixture.black_features,
                "black mismatch for {}",
                fixture.fen
            );
            count += 1;
        }
        assert!(count >= 256);
    }

    #[test]
    fn incremental_updates_match_full_refresh_forward_and_backward() {
        let network = Network::from_bytes(NETWORK).unwrap();
        let mut game = Te1Game::from_fen(START_FEN).unwrap();
        let mut accumulator = network.accumulator(game.board()).unwrap();
        let mut history: Vec<(Board, Accumulator)> = Vec::new();
        let mut state = 0x4d59_5df4_d0f3_3173u64;

        for _ in 0..512 {
            let legal = game.legal_moves();
            if legal.is_empty() {
                game = Te1Game::from_fen(START_FEN).unwrap();
                accumulator = network.accumulator(game.board()).unwrap();
                history.clear();
                continue;
            }
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            let index = usize::try_from(state % u64::try_from(legal.len()).unwrap()).unwrap();
            let before = game.board().clone();
            history.push((before.clone(), accumulator.clone()));
            game.play_uci(&legal[index]).unwrap();
            accumulator
                .update_between(&network, &before, game.board())
                .unwrap();
            let fresh = network.accumulator(game.board()).unwrap();
            assert!(accumulator.max_abs_difference(&fresh) <= 3.0e-5);
        }

        while let Some((previous_board, previous_accumulator)) = history.pop() {
            let current = game.board().clone();
            accumulator
                .update_between(&network, &current, &previous_board)
                .unwrap();
            assert!(accumulator.max_abs_difference(&previous_accumulator) <= 3.0e-5);
            game = Te1Game::from_fen(&previous_board.to_string()).unwrap();
        }
    }

    #[test]
    fn special_move_transitions_match_refresh() {
        let network = Network::from_bytes(NETWORK).unwrap();
        let cases = [
            ("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1", "e1g1"),
            ("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", "e5d6"),
            ("4k3/P7/8/8/8/8/8/4K3 w - - 0 1", "a7a8q"),
            ("4k3/8/8/8/3q4/4P3/8/4K3 w - - 0 1", "e3d4"),
        ];
        for (fen, mv) in cases {
            let mut game = Te1Game::from_fen(fen).unwrap();
            let before = game.board().clone();
            let mut incremental = network.accumulator(&before).unwrap();
            game.play_uci(mv).unwrap();
            incremental
                .update_between(&network, &before, game.board())
                .unwrap();
            let fresh = network.accumulator(game.board()).unwrap();
            assert!(
                incremental.max_abs_difference(&fresh) <= 3.0e-5,
                "incremental mismatch after {fen} {mv}"
            );
        }
    }
}
