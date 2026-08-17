#![forbid(unsafe_code)]

use std::io::{self, BufRead, Write};
use te1_chess::{
    Te1Game, encode_move, encoding_digest, legal_uci_moves, next_fen, parse_board, perft,
    policy_map, repetition_count, static_status,
};

fn clean_error(text: &str) -> String {
    text.chars()
        .map(|character| {
            if matches!(character, '\t' | '\n' | '\r') {
                ' '
            } else {
                character
            }
        })
        .collect()
}

fn require_fields(fields: &[&str], expected: usize) -> Result<(), String> {
    if fields.len() == expected {
        Ok(())
    } else {
        Err(format!(
            "wrong field count: expected {expected}, got {}",
            fields.len()
        ))
    }
}

fn native_mix(mut value: u64) -> u64 {
    value ^= value >> 33;
    value = value.wrapping_mul(0xff51_afd7_ed55_8ccd);
    value ^= value >> 33;
    value = value.wrapping_mul(0xc4ce_b9fe_1a85_ec53);
    value ^= value >> 33;
    value
}

fn command(fields: &[&str]) -> Result<String, String> {
    let name = fields
        .first()
        .copied()
        .ok_or_else(|| "empty command".to_owned())?;
    match name {
        "PING" => {
            require_fields(fields, 1)?;
            Ok(format!("PONG {}", native_mix(42)))
        }
        "NATIVE" => {
            require_fields(fields, 2)?;
            let value = fields[1]
                .parse::<u64>()
                .map_err(|error| error.to_string())?;
            Ok(native_mix(value).to_string())
        }
        "LEGAL" => {
            require_fields(fields, 2)?;
            let board = parse_board(fields[1])?;
            Ok(legal_uci_moves(&board).join(" "))
        }
        "NEXT" => {
            require_fields(fields, 3)?;
            next_fen(fields[1], fields[2])
        }
        "STATUS" => {
            require_fields(fields, 2)?;
            serde_json::to_string(&static_status(fields[1])?).map_err(|error| error.to_string())
        }
        "POLICY" => {
            require_fields(fields, 3)?;
            Ok(encode_move(fields[1], fields[2])?.to_string())
        }
        "POLICY_MAP" => {
            require_fields(fields, 2)?;
            serde_json::to_string(&policy_map(fields[1])?).map_err(|error| error.to_string())
        }
        "ENCODE_HASH" => {
            require_fields(fields, 2)?;
            let history: Vec<String> =
                serde_json::from_str(fields[1]).map_err(|error| error.to_string())?;
            serde_json::to_string(&encoding_digest(&history)?).map_err(|error| error.to_string())
        }
        "REP_COUNT" => {
            require_fields(fields, 2)?;
            let history: Vec<String> =
                serde_json::from_str(fields[1]).map_err(|error| error.to_string())?;
            Ok(repetition_count(&history).to_string())
        }
        "UNDO" => {
            require_fields(fields, 3)?;
            let mut game = Te1Game::from_fen(fields[1])?;
            game.play_uci(fields[2])?;
            game.undo()
        }
        "PERFT" => {
            require_fields(fields, 3)?;
            let board = parse_board(fields[1])?;
            let depth = fields[2].parse::<u8>().map_err(|error| error.to_string())?;
            Ok(perft(&board, depth).to_string())
        }
        "QUIT" => {
            require_fields(fields, 1)?;
            Ok("BYE".to_owned())
        }
        _ => Err(format!("unknown command: {name}")),
    }
}

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(error) => {
                let _ = writeln!(stdout, "ERR\t{}", clean_error(&error.to_string()));
                let _ = stdout.flush();
                continue;
            }
        };
        let fields: Vec<&str> = line.split('\t').collect();
        match command(&fields) {
            Ok(result) => {
                let _ = writeln!(stdout, "OK\t{result}");
                let _ = stdout.flush();
                if fields.first() == Some(&"QUIT") {
                    break;
                }
            }
            Err(error) => {
                let _ = writeln!(stdout, "ERR\t{}", clean_error(&error));
                let _ = stdout.flush();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_native_mix_matches_known_vector() {
        assert_eq!(native_mix(0), 0);
        assert_eq!(native_mix(42), 9_297_814_886_316_923_340);
    }

    #[test]
    fn ping_works() {
        assert!(command(&["PING"]).unwrap().starts_with("PONG "));
    }
}
