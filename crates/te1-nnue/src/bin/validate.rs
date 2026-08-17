#![forbid(unsafe_code)]

use cozy_chess::Board;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use te1_nnue::Network;

#[derive(Debug, Deserialize)]
struct ReferenceVector {
    white_features: Vec<usize>,
    black_features: Vec<usize>,
    white_to_move: bool,
    quantized_wdl: [f32; 3],
    quantized_cp_normalized: f32,
}

#[derive(Debug, Deserialize)]
struct FeatureFixture {
    fen: String,
    white_features: Vec<usize>,
    black_features: Vec<usize>,
}

#[derive(Debug, Serialize)]
struct Report {
    status: &'static str,
    network: String,
    feature_set: String,
    width: usize,
    hidden: usize,
    reference_vectors: usize,
    feature_fixtures: usize,
    max_wdl_error: f32,
    max_cp_normalized_error: f32,
}

fn main() {
    if let Err(error) = run() {
        eprintln!("te1-nnue-validate error: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let network_path = args
        .get(1)
        .map_or_else(|| manifest.join("fixtures/network.te1nn"), PathBuf::from);
    let reference_path = args.get(2).map_or_else(
        || manifest.join("fixtures/reference-vectors.jsonl"),
        PathBuf::from,
    );
    let feature_path = args.get(3).map_or_else(
        || manifest.join("fixtures/feature-fixtures.jsonl"),
        PathBuf::from,
    );

    let network = Network::from_file(&network_path)?;
    let (reference_vectors, max_wdl_error, max_cp_error) =
        validate_reference_vectors(&network, &reference_path)?;
    let feature_fixtures = validate_feature_fixtures(&network, &feature_path)?;
    if max_wdl_error > 2.0e-4 || max_cp_error > 2.0e-4 {
        return Err(format!(
            "inference parity exceeded tolerance: WDL={max_wdl_error}, CP={max_cp_error}"
        ));
    }
    let report = Report {
        status: "PASS",
        network: network.name().to_owned(),
        feature_set: network.feature_set().to_owned(),
        width: network.width(),
        hidden: network.hidden(),
        reference_vectors,
        feature_fixtures,
        max_wdl_error,
        max_cp_normalized_error: max_cp_error,
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
    );
    Ok(())
}

fn validate_reference_vectors(network: &Network, path: &Path) -> Result<(usize, f32, f32), String> {
    let file = fs::File::open(path)
        .map_err(|error| format!("failed to open {}: {error}", path.display()))?;
    let mut count = 0usize;
    let mut max_wdl = 0.0f32;
    let mut max_cp = 0.0f32;
    for (line_number, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(|error| error.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let vector: ReferenceVector = serde_json::from_str(&line)
            .map_err(|error| format!("{}:{}: {error}", path.display(), line_number + 1))?;
        let output = network.evaluate_features(
            &vector.white_features,
            &vector.black_features,
            vector.white_to_move,
        )?;
        for (&actual, &expected) in output.wdl.iter().zip(&vector.quantized_wdl) {
            max_wdl = max_wdl.max((actual - expected).abs());
        }
        max_cp = max_cp.max((output.cp_normalized - vector.quantized_cp_normalized).abs());
        count += 1;
    }
    if count == 0 {
        return Err("reference vector file is empty".to_owned());
    }
    Ok((count, max_wdl, max_cp))
}

fn validate_feature_fixtures(network: &Network, path: &Path) -> Result<usize, String> {
    let file = fs::File::open(path)
        .map_err(|error| format!("failed to open {}: {error}", path.display()))?;
    let mut count = 0usize;
    for (line_number, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(|error| error.to_string())?;
        if line.trim().is_empty() {
            continue;
        }
        let fixture: FeatureFixture = serde_json::from_str(&line)
            .map_err(|error| format!("{}:{}: {error}", path.display(), line_number + 1))?;
        let board: Board = fixture
            .fen
            .parse()
            .map_err(|error| format!("invalid fixture FEN {}: {error}", fixture.fen))?;
        let (white, black) = network.encode_board(&board)?;
        if white != fixture.white_features || black != fixture.black_features {
            return Err(format!("feature mismatch for fixture {}", fixture.fen));
        }
        count += 1;
    }
    if count == 0 {
        return Err("feature fixture file is empty".to_owned());
    }
    Ok(count)
}
