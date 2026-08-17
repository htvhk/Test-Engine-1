#![forbid(unsafe_code)]

use cozy_chess::Board;
use cozy_chess::util::parse_uci_move;
use serde::Serialize;
use std::hint::black_box;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use te1_nnue::Network;

#[derive(Debug, Serialize)]
struct Stage {
    operations: u64,
    elapsed_ms: u128,
    operations_per_second: f64,
    checksum: i64,
}

#[derive(Debug, Serialize)]
struct Report {
    status: &'static str,
    network: String,
    kernel: String,
    width: usize,
    hidden: usize,
    positions: usize,
    iterations: u64,
    full_refresh_and_cp: Stage,
    incremental_update_and_cp: Stage,
    cp_inference_only: Stage,
}

fn stage(operations: u64, elapsed: Duration, checksum: i64) -> Stage {
    Stage {
        operations,
        elapsed_ms: elapsed.as_millis(),
        operations_per_second: operations as f64 / elapsed.as_secs_f64().max(f64::EPSILON),
        checksum,
    }
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
            return Err(format!("illegal profiling move: {text}"));
        }
        board.play_unchecked(mv);
        boards.push(board.clone());
    }
    Ok(boards)
}

fn main() {
    if let Err(error) = run() {
        eprintln!("te1-nnue-profile error: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let mut args = std::env::args().skip(1);
    let network_path = args
        .next()
        .map(PathBuf::from)
        .ok_or_else(|| "usage: te1-nnue-profile NETWORK [ITERATIONS]".to_owned())?;
    let iterations = args
        .next()
        .map_or(Ok(5_000u64), |value| value.parse::<u64>())
        .map_err(|error| format!("invalid iteration count: {error}"))?;
    if args.next().is_some() {
        return Err("too many arguments".to_owned());
    }

    let network = Network::from_file(network_path)?;
    let boards = position_sequence()?;
    let operations = iterations.saturating_mul(u64::try_from(boards.len()).unwrap_or(0));

    let mut scratch = network.inference_scratch();
    let mut full_checksum = 0i64;
    let full_start = Instant::now();
    for _ in 0..iterations {
        for board in &boards {
            let accumulator = network.accumulator(black_box(board))?;
            let cp =
                network.evaluate_accumulator_cp(&accumulator, board.side_to_move(), &mut scratch);
            full_checksum = full_checksum.wrapping_add(i64::from(black_box(cp)));
        }
    }
    let full_elapsed = full_start.elapsed();

    let mut incremental_scratch = network.inference_scratch();
    let mut incremental_checksum = 0i64;
    let incremental_start = Instant::now();
    for _ in 0..iterations {
        let mut accumulator = network.accumulator(&boards[0])?;
        let mut previous = &boards[0];
        for board in &boards {
            accumulator.update_between(&network, black_box(previous), black_box(board))?;
            let cp = network.evaluate_accumulator_cp(
                &accumulator,
                board.side_to_move(),
                &mut incremental_scratch,
            );
            incremental_checksum = incremental_checksum.wrapping_add(i64::from(black_box(cp)));
            previous = board;
        }
    }
    let incremental_elapsed = incremental_start.elapsed();

    let fixed_board = &boards[boards.len() / 2];
    let fixed_accumulator = network.accumulator(fixed_board)?;
    let mut inference_scratch = network.inference_scratch();
    let mut inference_checksum = 0i64;
    let inference_operations = operations.max(1);
    let inference_start = Instant::now();
    for _ in 0..inference_operations {
        let cp = network.evaluate_accumulator_cp(
            black_box(&fixed_accumulator),
            fixed_board.side_to_move(),
            &mut inference_scratch,
        );
        inference_checksum = inference_checksum.wrapping_add(i64::from(black_box(cp)));
    }
    let inference_elapsed = inference_start.elapsed();

    let report = Report {
        status: "PASS",
        network: network.name().to_owned(),
        kernel: network.inference_kernel_name().to_owned(),
        width: network.width(),
        hidden: network.hidden(),
        positions: boards.len(),
        iterations,
        full_refresh_and_cp: stage(operations, full_elapsed, full_checksum),
        incremental_update_and_cp: stage(operations, incremental_elapsed, incremental_checksum),
        cp_inference_only: stage(inference_operations, inference_elapsed, inference_checksum),
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
    );
    Ok(())
}
