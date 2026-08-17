#![forbid(unsafe_code)]

use serde::Serialize;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};
use te1_chess::{START_FEN, Te1Game, parse_board, parse_legal_uci_move, perft};
use te1_search::{SearchLimits, SearchOptions, SearchResult, search, static_exchange_eval};
use te1_tt::TranspositionTable;

#[derive(Debug, Serialize)]
struct PerftCase {
    name: &'static str,
    depth: u8,
    expected: u64,
    actual: u64,
    passed: bool,
}

#[derive(Debug, Serialize)]
struct SearchCase {
    name: &'static str,
    depth: u8,
    best_move: Option<String>,
    score_cp: i32,
    nodes: u64,
    pv: Vec<String>,
    deterministic: bool,
    legal_best_move: bool,
}

#[derive(Debug, Serialize)]
struct SeeCase {
    name: &'static str,
    score: i32,
    minimum: i32,
    maximum: i32,
    passed: bool,
}

#[derive(Debug, Serialize)]
struct SmpCase {
    threads: usize,
    best_move: Option<String>,
    depth: u8,
    nodes: u64,
    elapsed_ms: u128,
    legal_best_move: bool,
    stopped: bool,
}

#[derive(Debug, Serialize)]
struct StopCase {
    node_limit: u64,
    actual_nodes: u64,
    elapsed_ms: u128,
    passed: bool,
}

#[derive(Debug, Serialize)]
struct ExternalStopCase {
    threads: usize,
    signal_after_ms: u128,
    join_latency_ms: u128,
    best_move: Option<String>,
    legal_best_move: bool,
    stopped: bool,
    passed: bool,
}

#[derive(Debug, Serialize)]
struct ValidationReport {
    version: &'static str,
    perft: Vec<PerftCase>,
    search: Vec<SearchCase>,
    see: Vec<SeeCase>,
    smp: Vec<SmpCase>,
    stop: StopCase,
    external_stop: ExternalStopCase,
    status: &'static str,
}

fn main() -> Result<(), String> {
    let perft_cases = run_perft_cases()?;
    let search_cases = run_search_cases()?;
    let see_cases = run_see_cases()?;
    let smp_cases = run_smp_cases()?;
    let stop_case = run_stop_case()?;
    let external_stop_case = run_external_stop_case()?;
    let passed = perft_cases.iter().all(|case| case.passed)
        && search_cases
            .iter()
            .all(|case| case.deterministic && case.legal_best_move)
        && see_cases.iter().all(|case| case.passed)
        && smp_cases
            .iter()
            .all(|case| case.legal_best_move && !case.stopped)
        && stop_case.passed
        && external_stop_case.passed;
    let report = ValidationReport {
        version: "1.0.0-alpha.2.5C",
        perft: perft_cases,
        search: search_cases,
        see: see_cases,
        smp: smp_cases,
        stop: stop_case,
        external_stop: external_stop_case,
        status: if passed { "PASS" } else { "FAIL" },
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
    );
    if passed {
        Ok(())
    } else {
        Err("validation report contains failures".to_owned())
    }
}

fn run_perft_cases() -> Result<Vec<PerftCase>, String> {
    let definitions = [
        ("startpos-d1", START_FEN, 1, 20),
        ("startpos-d2", START_FEN, 2, 400),
        ("startpos-d3", START_FEN, 3, 8_902),
        ("startpos-d4", START_FEN, 4, 197_281),
        (
            "kiwipete-d3",
            "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
            3,
            97_862,
        ),
    ];
    definitions
        .into_iter()
        .map(|(name, fen, depth, expected)| {
            let board = parse_board(fen)?;
            let actual = perft(&board, depth);
            Ok(PerftCase {
                name,
                depth,
                expected,
                actual,
                passed: actual == expected,
            })
        })
        .collect()
}

fn run_search_cases() -> Result<Vec<SearchCase>, String> {
    let definitions = [
        ("startpos", START_FEN, 4),
        (
            "open-centre",
            "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
            4,
        ),
        ("mate-in-one", "7k/5Q2/6K1/8/8/8/8/8 w - - 0 1", 3),
    ];
    let mut reports = Vec::new();
    for (name, fen, depth) in definitions {
        let game = Te1Game::from_fen(fen)?;
        let first = run_search(&game, depth, 1, true)?;
        let second = run_search(&game, depth, 1, true)?;
        let legal_best_move = first
            .best_move
            .as_ref()
            .is_none_or(|best| game.legal_moves().contains(best));
        reports.push(SearchCase {
            name,
            depth,
            best_move: first.best_move.clone(),
            score_cp: first.score_cp,
            nodes: first.nodes,
            pv: first.pv.clone(),
            deterministic: first.best_move == second.best_move
                && first.score_cp == second.score_cp
                && first.nodes == second.nodes
                && first.pv == second.pv,
            legal_best_move,
        });
    }
    Ok(reports)
}

fn run_see_cases() -> Result<Vec<SeeCase>, String> {
    let definitions = [
        (
            "pawn-takes-free-queen",
            "7k/8/8/3q4/4P3/8/8/7K w - - 0 1",
            "e4d5",
            800,
            1_000,
        ),
        (
            "queen-takes-defended-pawn",
            "7k/8/2p5/3p4/4Q3/8/8/7K w - - 0 1",
            "e4d5",
            -1_000,
            0,
        ),
        (
            "en-passant-wins-pawn",
            "7k/8/8/3pP3/8/8/8/7K w - d6 0 1",
            "e5d6",
            90,
            110,
        ),
    ];
    definitions
        .into_iter()
        .map(|(name, fen, uci, minimum, maximum)| {
            let board = parse_board(fen)?;
            let mv = parse_legal_uci_move(&board, uci)?;
            let score = static_exchange_eval(&board, mv);
            Ok(SeeCase {
                name,
                score,
                minimum,
                maximum,
                passed: (minimum..=maximum).contains(&score),
            })
        })
        .collect()
}

fn run_smp_cases() -> Result<Vec<SmpCase>, String> {
    let game = Te1Game::from_fen(START_FEN)?;
    [1usize, 2, 4]
        .into_iter()
        .map(|threads| {
            let result = run_search(&game, 4, threads, threads == 1)?;
            let legal_best_move = result
                .best_move
                .as_ref()
                .is_none_or(|best| game.legal_moves().contains(best));
            Ok(SmpCase {
                threads: result.threads,
                best_move: result.best_move,
                depth: result.depth,
                nodes: result.nodes,
                elapsed_ms: result.elapsed_ms,
                legal_best_move,
                stopped: result.stopped,
            })
        })
        .collect()
}

fn run_stop_case() -> Result<StopCase, String> {
    let game = Te1Game::from_fen(START_FEN)?;
    let limit = 5_000u64;
    let start = Instant::now();
    let result = search(
        &game,
        SearchLimits {
            nodes: Some(limit),
            ..SearchLimits::default()
        },
        Arc::new(AtomicBool::new(false)),
        Arc::new(TranspositionTable::with_megabytes(8)),
        SearchOptions {
            threads: 4,
            deterministic: false,
            ..SearchOptions::default()
        },
    )?;
    let elapsed_ms = start.elapsed().as_millis();
    Ok(StopCase {
        node_limit: limit,
        actual_nodes: result.nodes,
        elapsed_ms,
        passed: result.stopped && result.nodes <= limit && elapsed_ms < 5_000,
    })
}

fn run_external_stop_case() -> Result<ExternalStopCase, String> {
    let game = Te1Game::from_fen(START_FEN)?;
    let legal_moves = game.legal_moves();
    let stop = Arc::new(AtomicBool::new(false));
    let thread_stop = Arc::clone(&stop);
    let started = Instant::now();
    let handle = thread::Builder::new()
        .name("te1-external-stop-validation".to_owned())
        .spawn(move || {
            search(
                &game,
                SearchLimits {
                    depth: Some(64),
                    infinite: true,
                    ..SearchLimits::default()
                },
                thread_stop,
                Arc::new(TranspositionTable::with_megabytes(8)),
                SearchOptions {
                    threads: 4,
                    deterministic: false,
                    ..SearchOptions::default()
                },
            )
        })
        .map_err(|error| format!("failed to spawn external-stop validation: {error}"))?;
    thread::sleep(Duration::from_millis(75));
    let signal_after_ms = started.elapsed().as_millis();
    let stop_started = Instant::now();
    stop.store(true, Ordering::Relaxed);
    let result = handle
        .join()
        .map_err(|_| "external-stop validation thread panicked".to_owned())??;
    let join_latency_ms = stop_started.elapsed().as_millis();
    let legal_best_move = result
        .best_move
        .as_ref()
        .is_some_and(|best| legal_moves.contains(best));
    let passed = result.stopped && legal_best_move && join_latency_ms < 2_000;
    Ok(ExternalStopCase {
        threads: result.threads,
        signal_after_ms,
        join_latency_ms,
        best_move: result.best_move,
        legal_best_move,
        stopped: result.stopped,
        passed,
    })
}

fn run_search(
    game: &Te1Game,
    depth: u8,
    threads: usize,
    deterministic: bool,
) -> Result<SearchResult, String> {
    search(
        game,
        SearchLimits {
            depth: Some(depth),
            movetime: Some(Duration::from_secs(30)),
            ..SearchLimits::default()
        },
        Arc::new(AtomicBool::new(false)),
        Arc::new(TranspositionTable::with_megabytes(16)),
        SearchOptions {
            threads,
            deterministic,
            ..SearchOptions::default()
        },
    )
}
