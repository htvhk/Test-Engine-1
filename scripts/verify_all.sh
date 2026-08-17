#!/usr/bin/env bash
set -euo pipefail
python3 scripts/verify_repo_integrity.py
python3 scripts/hydrate_repo_assets.py
rustup toolchain install 1.97.1 --profile minimal --component rustfmt,clippy
rustup override set 1.97.1
cargo fmt --all -- --check
cargo check --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
printf '\nTE1 repository bootstrap verification: PASS\n'
