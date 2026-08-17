# Test Engine 1 — Agent Engineering Contract

## Authority and current baseline

This repository is the canonical code baseline derived from **TE1 alpha.2.5B.2 HARDENED-R2 REAL PASS**. The accepted production NNUE was trained in B.1 HARDENED-R2 and integrated/parity-validated in B.2 HARDENED-R2. Repository documentation and `CANONICAL_BASELINE.json` are authoritative for lineage identity; historical version strings inside imported Rust source are not permission to revert to older branches.

## Mission

TE1 is a performance-focused competitive chess engine. Preserve correctness and deterministic evidence while allowing advanced performance engineering. Do not make the engine artificially defensive at the cost of strength or speed.

## Immutable / prohibited lineage rules

- Do not import code, data, weights, or assumptions from discarded pre-clean-room or alternate experimental branches.
- Do not replace the production clean-room dataset lineage without an explicit approved work card.
- Do not retrain or silently replace production NNUE weights as part of search/runtime work.
- Do not change test fixtures merely to make a failure disappear.

## Toolchain

- Rust: **1.97.1**
- Edition: **2024**
- Workspace dependency lock must remain respected with `--locked`.
- Python tooling is retained under `python/b1_training/`; GPU training is not a normal repository CI task.

Before Rust build/test in a fresh clone, run:

```bash
python3 scripts/verify_repo_integrity.py
python3 scripts/hydrate_repo_assets.py
```

## Mandatory Rust gates

For any Rust/source change, at minimum run:

```bash
cargo fmt --all -- --check
cargo check --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
```

A green command proves only what it exercised. Search/evaluator changes require the additional domain gates below.

## NNUE contract

- Feature set: `TE1-K32-RP11-v1`
- Features: 22,528
- Max active features: 31
- Transport: `TE1NN001`, format version 1, int16 tensors with independent scales
- Production/default architecture: `k32-w128-h32-crelu`
- Production/default network SHA-256: `9cba80ed00f31946b54179d2ed63b4639ef3ba62d1a96ba2bca4fca4fc846974`
- 256-wide challenger SHA-256: `5e006228004e489de0f1a18e279281c75b91d65e90be0749d8a3244f72382367`

NNUE/runtime modifications must preserve or explicitly re-prove:
- Python ↔ quantized Rust reference parity
- scalar ↔ SIMD numerical equivalence
- accumulator make/unmake and special-move restoration
- UCI `UseNNUE` / `EvalFile` switching and invalid-file rollback

## Unsafe Rust policy

Performance-critical unsafe Rust is allowed only when justified and verified. Current accepted unsafe scope is `crates/te1-nnue/src/simd.rs`. Do not expand unsafe scope without an explicit work card, documented safety invariants, and differential tests.

## Search changes

Search work must be isolated enough to attribute strength/performance effects. Do not batch multiple independent Elo-sensitive heuristics into one promotion decision. Preserve deterministic one-thread regression modes.

Do not promote changes based on NPS, benchmark speed, tactical anecdotes, or offline evaluator loss alone. Playing-strength promotion requires paired game evidence and the campaign's statistical gate.

## Multi-phase campaign rule

A broad Codex task may understand and plan multiple future cards at once. Execution must remain dependency-gated:

1. each card has explicit scope and acceptance evidence;
2. a failed load-bearing card blocks dependent cards;
3. independent read-only analysis may run in parallel;
4. one owner per write surface;
5. integrated state is independently re-verified before promotion.

If a prompt says **PLAN ONLY**, do not edit files, commit, open a PR, or execute implementation work. Return a dependency-aware plan and stop.

## Current next campaign

Read `docs/CODEX_CAMPAIGN_1.md`. The immediate next strength gate is D.3 production-NNUE paired match/SPRT validation. Later cards are conditional on the D.3 result.
