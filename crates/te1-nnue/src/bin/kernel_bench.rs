#![forbid(unsafe_code)]

use cozy_chess::Board;
use cozy_chess::util::parse_uci_move;
use serde::Serialize;
use std::hint::black_box;
use std::path::PathBuf;
use std::time::Instant;
use te1_nnue::Network;

#[derive(Debug, Serialize)]
struct KernelResult {
    kernel: String,
    operations: u64,
    trials: usize,
    elapsed_ms: Vec<u128>,
    trial_operations_per_second: Vec<f64>,
    operations_per_second: f64,
    checksum: i64,
}

#[derive(Debug, Serialize)]
struct Report {
    status: &'static str,
    network: String,
    width: usize,
    hidden: usize,
    scalar_kernel: String,
    simd_kernel: String,
    positions_checked: usize,
    max_accumulator_error: f32,
    max_wdl_error: f32,
    max_cp_normalized_error: f32,
    max_cp_integer_delta: i32,
    scalar: KernelResult,
    avx2_fma: KernelResult,
    speedup: f64,
}

fn position_sequence() -> Result<Vec<Board>, String> {
    let moves = [
        "e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1", "f8e7", "f1e1",
        "b7b5", "a4b3", "d7d6", "c2c3", "e8g8", "h2h3", "c6b8", "d2d4", "b8d7", "b1d2", "c7c5",
        "d4e5", "d6e5",
    ];
    let mut board = Board::default();
    let mut boards = Vec::with_capacity(moves.len() + 1);
    boards.push(board.clone());
    for text in moves {
        let mv = parse_uci_move(&board, text)
            .map_err(|error| format!("failed to parse {text}: {error}"))?;
        if !board.is_legal(mv) {
            return Err(format!("illegal kernel-benchmark move: {text}"));
        }
        board.play_unchecked(mv);
        boards.push(board.clone());
    }
    Ok(boards)
}

fn bench_once(
    network: &Network,
    accumulator: &te1_nnue::Accumulator,
    board: &Board,
    scratch: &mut te1_nnue::InferenceScratch,
    iterations: u64,
) -> (u128, f64, i64) {
    let start = Instant::now();
    let mut checksum = 0i64;
    for _ in 0..iterations {
        let cp =
            network.evaluate_accumulator_cp(black_box(accumulator), board.side_to_move(), scratch);
        checksum = checksum.wrapping_add(i64::from(black_box(cp)));
    }
    let elapsed = start.elapsed();
    (
        elapsed.as_millis(),
        iterations as f64 / elapsed.as_secs_f64().max(f64::EPSILON),
        checksum,
    )
}

fn warm_up(network: &Network, board: &Board) -> Result<(), String> {
    let accumulator = network.accumulator(board)?;
    let mut scratch = network.inference_scratch();
    for _ in 0..512u64 {
        black_box(network.evaluate_accumulator_cp(
            black_box(&accumulator),
            board.side_to_move(),
            &mut scratch,
        ));
    }
    Ok(())
}

fn median(mut values: Vec<f64>) -> f64 {
    values.sort_by(|left, right| left.total_cmp(right));
    let middle = values.len() / 2;
    if values.len().is_multiple_of(2) {
        (values[middle - 1] + values[middle]) * 0.5
    } else {
        values[middle]
    }
}

fn benchmark_pair(
    scalar: &Network,
    simd: &Network,
    board: &Board,
    iterations: u64,
) -> Result<(KernelResult, KernelResult), String> {
    const TRIALS: usize = 4;
    warm_up(scalar, board)?;
    warm_up(simd, board)?;

    let scalar_acc = scalar.accumulator(board)?;
    let simd_acc = simd.accumulator(board)?;
    let mut scalar_scratch = scalar.inference_scratch();
    let mut simd_scratch = simd.inference_scratch();
    let mut scalar_elapsed = Vec::with_capacity(TRIALS);
    let mut simd_elapsed = Vec::with_capacity(TRIALS);
    let mut scalar_rates = Vec::with_capacity(TRIALS);
    let mut simd_rates = Vec::with_capacity(TRIALS);
    let mut scalar_checksum = 0i64;
    let mut simd_checksum = 0i64;

    for trial in 0..TRIALS {
        let scalar_first = trial.is_multiple_of(2);
        if scalar_first {
            let (elapsed, rate, checksum) =
                bench_once(scalar, &scalar_acc, board, &mut scalar_scratch, iterations);
            scalar_elapsed.push(elapsed);
            scalar_rates.push(rate);
            scalar_checksum = scalar_checksum.wrapping_add(checksum);

            let (elapsed, rate, checksum) =
                bench_once(simd, &simd_acc, board, &mut simd_scratch, iterations);
            simd_elapsed.push(elapsed);
            simd_rates.push(rate);
            simd_checksum = simd_checksum.wrapping_add(checksum);
        } else {
            let (elapsed, rate, checksum) =
                bench_once(simd, &simd_acc, board, &mut simd_scratch, iterations);
            simd_elapsed.push(elapsed);
            simd_rates.push(rate);
            simd_checksum = simd_checksum.wrapping_add(checksum);

            let (elapsed, rate, checksum) =
                bench_once(scalar, &scalar_acc, board, &mut scalar_scratch, iterations);
            scalar_elapsed.push(elapsed);
            scalar_rates.push(rate);
            scalar_checksum = scalar_checksum.wrapping_add(checksum);
        }
    }

    let scalar_median = median(scalar_rates.clone());
    let simd_median = median(simd_rates.clone());
    Ok((
        KernelResult {
            kernel: scalar.inference_kernel_name().to_owned(),
            operations: iterations,
            trials: TRIALS,
            elapsed_ms: scalar_elapsed,
            trial_operations_per_second: scalar_rates,
            operations_per_second: scalar_median,
            checksum: scalar_checksum,
        },
        KernelResult {
            kernel: simd.inference_kernel_name().to_owned(),
            operations: iterations,
            trials: TRIALS,
            elapsed_ms: simd_elapsed,
            trial_operations_per_second: simd_rates,
            operations_per_second: simd_median,
            checksum: simd_checksum,
        },
    ))
}

fn main() {
    if let Err(error) = run() {
        eprintln!("te1-nnue-kernel-bench error: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let mut args = std::env::args().skip(1);
    let network_path = args
        .next()
        .map(PathBuf::from)
        .ok_or_else(|| "usage: te1-nnue-kernel-bench NETWORK [ITERATIONS]".to_owned())?;
    let iterations = args
        .next()
        .map_or(Ok(100_000u64), |value| value.parse::<u64>())
        .map_err(|error| format!("invalid iteration count: {error}"))?;
    if iterations == 0 {
        return Err("iteration count must be positive".to_owned());
    }
    if args.next().is_some() {
        return Err("too many arguments".to_owned());
    }

    let mut scalar = Network::from_file(&network_path)?;
    scalar.force_scalar_kernel();
    let mut simd = Network::from_file(&network_path)?;
    simd.force_avx2_fma_kernel()?;
    let boards = position_sequence()?;

    let mut max_accumulator_error = 0.0f32;
    let mut max_wdl_error = 0.0f32;
    let mut max_cp_normalized_error = 0.0f32;
    let mut max_cp_integer_delta = 0i32;
    for board in &boards {
        let scalar_acc = scalar.accumulator(board)?;
        let simd_acc = simd.accumulator(board)?;
        max_accumulator_error = max_accumulator_error.max(scalar_acc.max_abs_difference(&simd_acc));
        let scalar_output = scalar.evaluate_accumulator(&scalar_acc, board.side_to_move());
        let simd_output = simd.evaluate_accumulator(&simd_acc, board.side_to_move());
        for (&left, &right) in scalar_output.wdl.iter().zip(&simd_output.wdl) {
            max_wdl_error = max_wdl_error.max((left - right).abs());
        }
        max_cp_normalized_error = max_cp_normalized_error
            .max((scalar_output.cp_normalized - simd_output.cp_normalized).abs());
        max_cp_integer_delta = max_cp_integer_delta.max((scalar_output.cp - simd_output.cp).abs());
    }

    if max_accumulator_error > 3.0e-5 {
        return Err(format!(
            "SIMD accumulator parity exceeded tolerance: {max_accumulator_error}"
        ));
    }
    if max_wdl_error > 2.0e-4 || max_cp_normalized_error > 2.0e-4 {
        return Err(format!(
            "SIMD inference parity exceeded tolerance: WDL={max_wdl_error}, CP={max_cp_normalized_error}"
        ));
    }
    if max_cp_integer_delta > 1 {
        return Err(format!(
            "SIMD centipawn output differs by more than 1 cp: {max_cp_integer_delta}"
        ));
    }

    let fixed = &boards[boards.len() / 2];
    let (scalar_result, simd_result) = benchmark_pair(&scalar, &simd, fixed, iterations)?;
    let speedup =
        simd_result.operations_per_second / scalar_result.operations_per_second.max(f64::EPSILON);
    let report = Report {
        status: "PASS",
        network: simd.name().to_owned(),
        width: simd.width(),
        hidden: simd.hidden(),
        scalar_kernel: scalar.inference_kernel_name().to_owned(),
        simd_kernel: simd.inference_kernel_name().to_owned(),
        positions_checked: boards.len(),
        max_accumulator_error,
        max_wdl_error,
        max_cp_normalized_error,
        max_cp_integer_delta,
        scalar: scalar_result,
        avx2_fma: simd_result,
        speedup,
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
    );
    Ok(())
}
