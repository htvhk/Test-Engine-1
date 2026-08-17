#![forbid(unsafe_code)]

use cozy_chess::Board;
use serde::Serialize;
use std::hint::black_box;
use std::time::Instant;
use te1_nnue::Network;

const NETWORK: &[u8] = include_bytes!("../../fixtures/network.te1nn");

#[derive(Serialize)]
struct Report {
    status: &'static str,
    network: String,
    iterations: u64,
    positions: u64,
    elapsed_ms: u128,
    positions_per_second: f64,
    checksum: i64,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("te1-nnue-bench error: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let iterations = std::env::args()
        .nth(1)
        .map_or(Ok(20_000u64), |value| value.parse::<u64>())
        .map_err(|error| format!("invalid iteration count: {error}"))?;
    let network = Network::from_bytes(NETWORK)?;
    let fens = [
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "r1bq1rk1/ppp2ppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 4 8",
        "8/5pk1/3p2p1/4p3/4P3/3P2P1/5PK1/8 w - - 0 1",
        "r3k2r/pppq1ppp/2n1bn2/3p4/3P4/2N1PN2/PPQ2PPP/R3K2R w KQkq - 0 1",
        "4rrk1/1pp2ppp/p1n5/3qp3/8/2P1PN2/PPQ2PPP/2RR2K1 w - - 0 1",
        "2r3k1/5ppp/4p3/3pP3/3P4/5P2/5KPP/2R5 w - - 0 1",
        "r1b1k2r/pp3ppp/2n1pn2/q1bp4/8/2P1PN2/PPBN1PPP/R2Q1RK1 w kq - 4 9",
        "6k1/5pp1/4p2p/8/2P5/1P3P2/P5PP/6K1 w - - 0 1",
    ];
    let boards: Vec<Board> = fens
        .iter()
        .map(|fen| fen.parse::<Board>().map_err(|error| error.to_string()))
        .collect::<Result<_, _>>()?;
    for board in &boards {
        black_box(network.evaluate_board(board)?);
    }
    let start = Instant::now();
    let mut checksum = 0i64;
    for _ in 0..iterations {
        for board in &boards {
            let output = network.evaluate_board(black_box(board))?;
            checksum = checksum.wrapping_add(i64::from(output.cp));
        }
    }
    let elapsed = start.elapsed();
    let positions = iterations.saturating_mul(u64::try_from(boards.len()).unwrap_or(0));
    let positions_per_second = positions as f64 / elapsed.as_secs_f64().max(f64::EPSILON);
    println!(
        "{}",
        serde_json::to_string_pretty(&Report {
            status: "PASS",
            network: network.name().to_owned(),
            iterations,
            positions,
            elapsed_ms: elapsed.as_millis(),
            positions_per_second,
            checksum,
        })
        .map_err(|error| error.to_string())?
    );
    Ok(())
}
