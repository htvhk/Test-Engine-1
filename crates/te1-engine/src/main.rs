#![forbid(unsafe_code)]

use cozy_chess::Color;
use std::io::{self, BufRead, Write};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::Duration;
use te1_chess::{START_FEN, Te1Game, perft};
use te1_search::{SearchLimits, SearchOptions, search};
use te1_tt::TranspositionTable;

const ENGINE_NAME: &str = "Test Engine 1 v1.0.0-alpha.2.5D.2";
const ENGINE_AUTHOR: &str = "TE1 Project";

#[derive(Debug, Clone)]
struct EngineOptions {
    hash_megabytes: usize,
    threads: usize,
    move_overhead_ms: u64,
    deterministic: bool,
    use_lmr: bool,
    use_see_pruning: bool,
    use_null_move_pruning: bool,
    use_nnue: bool,
    use_hybrid_eval: bool,
    eval_file: String,
}

impl Default for EngineOptions {
    fn default() -> Self {
        Self {
            hash_megabytes: 16,
            threads: 1,
            move_overhead_ms: 30,
            deterministic: true,
            use_lmr: true,
            use_see_pruning: true,
            use_null_move_pruning: false,
            use_nnue: true,
            use_hybrid_eval: false,
            eval_file: "<embedded>".to_owned(),
        }
    }
}

impl EngineOptions {
    fn search_options(&self) -> SearchOptions {
        SearchOptions {
            threads: self.threads,
            deterministic: self.deterministic,
            use_lmr: self.use_lmr,
            use_see_pruning: self.use_see_pruning,
            use_null_move_pruning: self.use_null_move_pruning,
        }
    }
}

#[derive(Debug)]
struct SearchWorker {
    stop: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct OptionEffects {
    clear_hash: bool,
    reload_eval: bool,
}

#[derive(Debug, Default)]
struct GoParameters {
    depth: Option<u8>,
    nodes: Option<u64>,
    movetime_ms: Option<u64>,
    wtime_ms: Option<u64>,
    btime_ms: Option<u64>,
    winc_ms: u64,
    binc_ms: u64,
    moves_to_go: Option<u64>,
    infinite: bool,
}

fn main() {
    if std::env::args().nth(1).as_deref() == Some("bench") {
        if let Err(error) = command_line_bench() {
            eprintln!("bench error: {error}");
            std::process::exit(1);
        }
        return;
    }

    let stdin = io::stdin();
    let mut game = Te1Game::from_fen(START_FEN).expect("built-in start FEN must be valid");
    let mut options = EngineOptions::default();
    if let Err(error) = apply_eval_options(&options) {
        eprintln!("embedded NNUE initialization failed: {error}");
        std::process::exit(1);
    }
    let mut table = Arc::new(TranspositionTable::with_megabytes(options.hash_megabytes));
    let mut worker: Option<SearchWorker> = None;

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(value) => value,
            Err(error) => {
                println!("info string stdin error: {error}");
                break;
            }
        };
        let command = line.trim();
        if command.is_empty() {
            continue;
        }

        if command == "uci" {
            print_uci_identity();
        } else if command == "isready" {
            println!("readyok");
        } else if command == "ucinewgame" {
            stop_worker(&mut worker);
            table.clear();
            game = Te1Game::from_fen(START_FEN).expect("built-in start FEN must be valid");
        } else if command.starts_with("setoption ") {
            stop_worker(&mut worker);
            let old_options = options.clone();
            let old_hash = options.hash_megabytes;
            match set_option(command, &mut options) {
                Ok(effects) => {
                    let eval_result = if effects.reload_eval {
                        apply_eval_options(&options)
                    } else {
                        Ok(())
                    };
                    if let Err(error) = eval_result {
                        options = old_options;
                        if let Err(restore_error) = apply_eval_options(&options) {
                            println!("info string NNUE restore error: {restore_error}");
                        }
                        println!("info string NNUE option error: {error}");
                    } else if options.hash_megabytes != old_hash {
                        table =
                            Arc::new(TranspositionTable::with_megabytes(options.hash_megabytes));
                    } else if effects.clear_hash || effects.reload_eval {
                        table.clear();
                    }
                }
                Err(error) => println!("info string setoption error: {error}"),
            }
        } else if command == "position" || command.starts_with("position ") {
            stop_worker(&mut worker);
            match parse_position(command) {
                Ok(position) => game = position,
                Err(error) => println!("info string position error: {error}"),
            }
        } else if command == "go" || command.starts_with("go ") {
            stop_worker(&mut worker);
            match parse_go(command) {
                Ok(parameters) => {
                    let limits = limits_from_go(&game, &options, &parameters);
                    println!(
                        "info string host threads {} deterministic {} lmr {} seepruning {} evaluator {}",
                        effective_threads(&options),
                        options.deterministic,
                        options.use_lmr,
                        options.use_see_pruning,
                        te1_eval::evaluator_name()
                    );
                    match start_search(
                        game.clone(),
                        limits,
                        Arc::clone(&table),
                        options.search_options(),
                    ) {
                        Ok(active) => worker = Some(active),
                        Err(error) => println!("info string search start error: {error}"),
                    }
                }
                Err(error) => println!("info string go error: {error}"),
            }
        } else if command == "stop" {
            stop_worker(&mut worker);
        } else if command == "ponderhit" {
            println!("info string ponder is not enabled in alpha.2.5C");
        } else if command == "quit" {
            stop_worker(&mut worker);
            break;
        } else if command == "d" {
            println!("{}", game.fen());
        } else if command == "eval" {
            println!(
                "info string eval {} cp {}",
                te1_eval::evaluator_name(),
                te1_eval::evaluate(game.board())
            );
        } else if let Some(depth_text) = command.strip_prefix("perft ") {
            match depth_text.trim().parse::<u8>() {
                Ok(depth) => println!(
                    "info string perft {depth} nodes {}",
                    perft(game.board(), depth)
                ),
                Err(error) => println!("info string perft error: {error}"),
            }
        } else if command == "bench" {
            stop_worker(&mut worker);
            if let Err(error) = run_bench(&game, Arc::clone(&table), &options) {
                println!("info string bench error: {error}");
            }
        } else {
            println!("info string unknown command: {command}");
        }
        flush_stdout();
    }

    stop_worker(&mut worker);
    flush_stdout();
}

fn print_uci_identity() {
    println!("id name {ENGINE_NAME}");
    println!("id author {ENGINE_AUTHOR}");
    println!("option name Hash type spin default 16 min 1 max 4096");
    println!("option name Threads type spin default 1 min 1 max 256");
    println!("option name MoveOverhead type spin default 30 min 0 max 5000");
    println!("option name MultiPV type spin default 1 min 1 max 1");
    println!("option name Deterministic type check default true");
    println!("option name UseLMR type check default true");
    println!("option name UseSEEPruning type check default true");
    println!("option name UseNullMovePruning type check default false");
    println!("option name UseNNUE type check default true");
    println!("option name UseHybridEval type check default false");
    println!("option name EvalFile type string default <embedded>");
    println!("option name Clear Hash type button");
    println!("uciok");
}

fn set_option(command: &str, options: &mut EngineOptions) -> Result<OptionEffects, String> {
    let rest = command
        .strip_prefix("setoption ")
        .ok_or_else(|| "missing setoption prefix".to_owned())?;
    let name_marker = "name ";
    let name_start = rest
        .find(name_marker)
        .ok_or_else(|| "missing option name".to_owned())?
        + name_marker.len();
    let value_marker = " value ";
    let (name, value) = if let Some(offset) = rest[name_start..].find(value_marker) {
        let split = name_start + offset;
        (
            rest[name_start..split].trim(),
            Some(rest[split + value_marker.len()..].trim()),
        )
    } else {
        (rest[name_start..].trim(), None)
    };

    let mut effects = OptionEffects::default();
    match name.to_ascii_lowercase().as_str() {
        "hash" => {
            let parsed = parse_value::<usize>(value, "Hash")?;
            options.hash_megabytes = parsed.clamp(1, 4_096);
        }
        "threads" => {
            let parsed = parse_value::<usize>(value, "Threads")?;
            options.threads = parsed.clamp(1, 256);
            if options.threads > 1 {
                options.deterministic = false;
            }
        }
        "moveoverhead" => {
            let parsed = parse_value::<u64>(value, "MoveOverhead")?;
            options.move_overhead_ms = parsed.min(5_000);
        }
        "multipv" => {
            let parsed = parse_value::<usize>(value, "MultiPV")?;
            if parsed != 1 {
                return Err("alpha.2.5C supports MultiPV=1 only".to_owned());
            }
        }
        "deterministic" => {
            options.deterministic = parse_bool(value, "Deterministic")?;
            if options.deterministic {
                options.threads = 1;
            }
        }
        "uselmr" => options.use_lmr = parse_bool(value, "UseLMR")?,
        "useseepruning" => {
            options.use_see_pruning = parse_bool(value, "UseSEEPruning")?;
        }
        "usenullmovepruning" => {
            options.use_null_move_pruning = parse_bool(value, "UseNullMovePruning")?;
        }
        "usennue" => {
            options.use_nnue = parse_bool(value, "UseNNUE")?;
            effects.reload_eval = true;
        }
        "usehybrideval" => {
            options.use_hybrid_eval = parse_bool(value, "UseHybridEval")?;
            effects.reload_eval = true;
        }
        "evalfile" => {
            let raw = value.ok_or_else(|| "EvalFile requires a value".to_owned())?;
            if raw.is_empty() {
                return Err("EvalFile must not be empty".to_owned());
            }
            options.eval_file = raw.to_owned();
            effects.reload_eval = true;
        }
        "clear hash" => effects.clear_hash = true,
        other => return Err(format!("unknown option: {other}")),
    }
    Ok(effects)
}

fn apply_eval_options(options: &EngineOptions) -> Result<(), String> {
    if !options.use_nnue {
        te1_eval::set_nnue_enabled(false);
        te1_eval::set_hybrid_enabled(options.use_hybrid_eval);
        return Ok(());
    }
    if options.eval_file.eq_ignore_ascii_case("<embedded>")
        || options.eval_file.eq_ignore_ascii_case("embedded")
        || options.eval_file.eq_ignore_ascii_case("default")
    {
        te1_eval::use_embedded_nnue()?;
    } else {
        te1_eval::load_nnue_file(&options.eval_file)?;
    }
    te1_eval::set_nnue_enabled(true);
    te1_eval::set_hybrid_enabled(options.use_hybrid_eval);
    Ok(())
}

fn parse_bool(value: Option<&str>, name: &str) -> Result<bool, String> {
    let raw = value.ok_or_else(|| format!("{name} requires a value"))?;
    match raw.to_ascii_lowercase().as_str() {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err(format!("invalid {name} value: {raw}")),
    }
}

fn parse_value<T>(value: Option<&str>, name: &str) -> Result<T, String>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .ok_or_else(|| format!("{name} requires a value"))?
        .parse::<T>()
        .map_err(|error| format!("invalid {name} value: {error}"))
}

fn parse_position(command: &str) -> Result<Te1Game, String> {
    let tokens: Vec<&str> = command.split_whitespace().collect();
    if tokens.first() != Some(&"position") {
        return Err("not a position command".to_owned());
    }
    let mut index = 1usize;
    let mut game = match tokens.get(index).copied() {
        Some("startpos") => {
            index += 1;
            Te1Game::from_fen(START_FEN)?
        }
        Some("fen") => {
            index += 1;
            if tokens.len() < index + 6 {
                return Err("FEN requires six fields".to_owned());
            }
            let fen = tokens[index..index + 6].join(" ");
            index += 6;
            Te1Game::from_fen(&fen)?
        }
        Some(other) => return Err(format!("unsupported position form: {other}")),
        None => return Err("position requires startpos or fen".to_owned()),
    };

    if index < tokens.len() {
        if tokens[index] != "moves" {
            return Err(format!("unexpected position token: {}", tokens[index]));
        }
        index += 1;
        for mv in &tokens[index..] {
            game.play_uci(mv)?;
        }
    }
    Ok(game)
}

fn parse_go(command: &str) -> Result<GoParameters, String> {
    let tokens: Vec<&str> = command.split_whitespace().collect();
    if tokens.first() != Some(&"go") {
        return Err("not a go command".to_owned());
    }

    let mut parameters = GoParameters::default();
    let mut index = 1usize;
    while index < tokens.len() {
        match tokens[index] {
            "depth" => parameters.depth = Some(parse_go_value(&tokens, &mut index, "depth")?),
            "nodes" => parameters.nodes = Some(parse_go_value(&tokens, &mut index, "nodes")?),
            "movetime" => {
                parameters.movetime_ms = Some(parse_go_value(&tokens, &mut index, "movetime")?);
            }
            "wtime" => parameters.wtime_ms = Some(parse_go_value(&tokens, &mut index, "wtime")?),
            "btime" => parameters.btime_ms = Some(parse_go_value(&tokens, &mut index, "btime")?),
            "winc" => parameters.winc_ms = parse_go_value(&tokens, &mut index, "winc")?,
            "binc" => parameters.binc_ms = parse_go_value(&tokens, &mut index, "binc")?,
            "movestogo" => {
                parameters.moves_to_go = Some(parse_go_value(&tokens, &mut index, "movestogo")?);
            }
            "infinite" => parameters.infinite = true,
            "ponder" => return Err("ponder is not implemented in alpha.2.5C".to_owned()),
            "searchmoves" => {
                return Err("searchmoves is not implemented in alpha.2.5C".to_owned());
            }
            other => return Err(format!("unsupported go token: {other}")),
        }
        index += 1;
    }
    Ok(parameters)
}

fn parse_go_value<T>(tokens: &[&str], index: &mut usize, name: &str) -> Result<T, String>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    *index += 1;
    tokens
        .get(*index)
        .ok_or_else(|| format!("{name} requires a value"))?
        .parse::<T>()
        .map_err(|error| format!("invalid {name} value: {error}"))
}

fn limits_from_go(
    game: &Te1Game,
    options: &EngineOptions,
    parameters: &GoParameters,
) -> SearchLimits {
    let movetime = if parameters.infinite {
        None
    } else if let Some(milliseconds) = parameters.movetime_ms {
        Some(safe_think_time(milliseconds, options.move_overhead_ms))
    } else {
        let (remaining, increment) = if game.board().side_to_move() == Color::White {
            (parameters.wtime_ms, parameters.winc_ms)
        } else {
            (parameters.btime_ms, parameters.binc_ms)
        };
        remaining.map(|clock| {
            let moves = parameters.moves_to_go.unwrap_or(30).max(1);
            let allocation = clock / moves + increment.saturating_mul(3) / 4;
            let capped = allocation.min(clock / 2).max(1);
            safe_think_time(capped, options.move_overhead_ms)
        })
    };

    if parameters.depth.is_none()
        && parameters.nodes.is_none()
        && movetime.is_none()
        && !parameters.infinite
    {
        return SearchLimits {
            depth: Some(5),
            ..SearchLimits::default()
        };
    }

    SearchLimits {
        depth: parameters.depth,
        nodes: parameters.nodes,
        movetime,
        infinite: parameters.infinite,
    }
}

fn safe_think_time(milliseconds: u64, overhead: u64) -> Duration {
    Duration::from_millis(milliseconds.saturating_sub(overhead).max(1))
}

fn effective_threads(options: &EngineOptions) -> usize {
    if options.deterministic {
        1
    } else {
        options.threads
    }
}

fn start_search(
    game: Te1Game,
    limits: SearchLimits,
    table: Arc<TranspositionTable>,
    options: SearchOptions,
) -> Result<SearchWorker, String> {
    let stop = Arc::new(AtomicBool::new(false));
    let thread_stop = Arc::clone(&stop);
    let handle = thread::Builder::new()
        .name("te1-uci-search".to_owned())
        .spawn(move || {
            match search(&game, limits, thread_stop, table, options) {
                Ok(result) => {
                    let elapsed = result.elapsed_ms.max(1);
                    let nps = u128::from(result.nodes).saturating_mul(1_000) / elapsed;
                    let pv = if result.pv.is_empty() {
                        String::new()
                    } else {
                        format!(" pv {}", result.pv.join(" "))
                    };
                    println!(
                        "info depth {} seldepth {} score cp {} nodes {} nps {} hashfull {} time {}{}",
                        result.depth,
                        result.seldepth,
                        result.score_cp,
                        result.nodes,
                        nps,
                        result.hashfull_per_mille,
                        result.elapsed_ms,
                        pv
                    );
                    println!(
                        "info string threads {} tthits {} cutoffs {} qnodes {}",
                        result.threads, result.tt_hits, result.beta_cutoffs, result.qnodes
                    );
                    println!("bestmove {}", result.best_move.as_deref().unwrap_or("0000"));
                    flush_stdout();
                }
                Err(error) => {
                    println!("info string search error: {error}");
                    println!("bestmove 0000");
                    flush_stdout();
                }
            }
        })
        .map_err(|error| format!("failed to spawn UCI search thread: {error}"))?;
    Ok(SearchWorker { stop, handle })
}

fn stop_worker(worker: &mut Option<SearchWorker>) {
    if let Some(active) = worker.take() {
        active.stop.store(true, Ordering::Relaxed);
        if active.handle.join().is_err() {
            println!("info string search thread panicked");
        }
    }
}

fn flush_stdout() {
    let _ = io::stdout().flush();
}

fn run_bench(
    game: &Te1Game,
    table: Arc<TranspositionTable>,
    options: &EngineOptions,
) -> Result<(), String> {
    table.clear();
    let result = search(
        game,
        SearchLimits {
            depth: Some(5),
            ..SearchLimits::default()
        },
        Arc::new(AtomicBool::new(false)),
        table,
        options.search_options(),
    )?;
    println!(
        "info string bench depth {} nodes {} nps {} score {} bestmove {} threads {} signature {}:{}:{}:{}",
        result.depth,
        result.nodes,
        u128::from(result.nodes).saturating_mul(1_000) / result.elapsed_ms.max(1),
        result.score_cp,
        result.best_move.as_deref().unwrap_or("0000"),
        result.threads,
        result.nodes,
        result.score_cp,
        result.best_move.as_deref().unwrap_or("0000"),
        result.pv.join("-")
    );
    Ok(())
}

fn command_line_bench() -> Result<(), String> {
    let game = Te1Game::from_fen(START_FEN)?;
    let options = EngineOptions::default();
    let table = Arc::new(TranspositionTable::with_megabytes(options.hash_megabytes));
    run_bench(&game, table, &options)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_start_position_with_moves() {
        let game = parse_position("position startpos moves e2e4 e7e5").unwrap();
        assert_eq!(game.ply_count(), 2);
        assert_eq!(
            game.fen(),
            "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2"
        );
    }

    #[test]
    fn parses_six_field_fen() {
        let game = parse_position("position fen 8/8/8/8/8/8/4K3/7k w - - 0 1").unwrap();
        assert_eq!(game.fen(), "8/8/8/8/8/8/4K3/7k w - - 0 1");
    }

    #[test]
    fn go_depth_and_nodes_are_parsed() {
        let parsed = parse_go("go depth 7 nodes 12345").unwrap();
        assert_eq!(parsed.depth, Some(7));
        assert_eq!(parsed.nodes, Some(12_345));
    }

    #[test]
    fn time_allocation_subtracts_overhead_without_underflow() {
        assert_eq!(safe_think_time(10, 30), Duration::from_millis(1));
        assert_eq!(safe_think_time(100, 30), Duration::from_millis(70));
    }

    #[test]
    fn threads_above_one_enable_nondeterministic_mode() {
        let mut options = EngineOptions::default();
        set_option("setoption name Threads value 4", &mut options).unwrap();
        assert_eq!(options.threads, 4);
        assert!(!options.deterministic);
    }

    #[test]
    fn deterministic_mode_forces_one_thread() {
        let mut options = EngineOptions {
            threads: 8,
            deterministic: false,
            ..EngineOptions::default()
        };
        set_option("setoption name Deterministic value true", &mut options).unwrap();
        assert_eq!(effective_threads(&options), 1);
    }

    #[test]
    fn clear_hash_option_returns_an_effect() {
        let mut options = EngineOptions::default();
        let effects = set_option("setoption name Clear Hash", &mut options).unwrap();
        assert!(effects.clear_hash);
    }

    #[test]
    fn rejects_incomplete_fen() {
        assert!(parse_position("position fen 8/8/8/8/8/8/4K3/7k w").is_err());
    }

    #[test]
    fn nnue_options_request_evaluator_reload() {
        let mut options = EngineOptions::default();
        assert!(!options.use_hybrid_eval);
        let effect = set_option("setoption name UseNNUE value false", &mut options).unwrap();
        assert!(!options.use_nnue);
        assert!(effect.reload_eval);
        let effect = set_option("setoption name UseHybridEval value true", &mut options).unwrap();
        assert!(options.use_hybrid_eval);
        assert!(effect.reload_eval);
        let effect = set_option("setoption name EvalFile value default", &mut options).unwrap();
        assert_eq!(options.eval_file, "default");
        assert!(effect.reload_eval);
    }

    #[test]
    fn null_move_pruning_option_defaults_off_and_can_be_enabled() {
        let mut options = EngineOptions::default();
        assert!(!options.use_null_move_pruning);
        let effects =
            set_option("setoption name UseNullMovePruning value true", &mut options).unwrap();
        assert!(options.use_null_move_pruning);
        assert_eq!(effects, OptionEffects::default());
        assert!(options.search_options().use_null_move_pruning);
    }

    #[test]
    fn embedded_evaluator_can_be_applied() {
        let options = EngineOptions::default();
        apply_eval_options(&options).unwrap();
        assert!(te1_eval::nnue_enabled());
        assert!(te1_eval::evaluator_name().contains("k32-w128-h32-crelu"));
    }

    #[test]
    fn failed_eval_file_restore_preserves_hybrid_mode() {
        let mut options = EngineOptions {
            use_hybrid_eval: true,
            ..EngineOptions::default()
        };
        apply_eval_options(&options).unwrap();
        let old_options = options.clone();
        options.eval_file = "/definitely/not/a/te1/network".to_owned();
        assert!(apply_eval_options(&options).is_err());
        options = old_options;
        apply_eval_options(&options).unwrap();
        assert!(te1_eval::hybrid_enabled());
        assert!(te1_eval::evaluator_name().starts_with("hybrid:"));
        te1_eval::set_hybrid_enabled(false);
    }
}
