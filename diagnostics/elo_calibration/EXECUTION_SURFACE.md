# TE1 Production Elo Calibration — Durable Execution Surface

This diagnostic branch is rooted exactly at production commit `1e750218f43fa5129cb82f19b107555a1343d878` and exists only to make Elo-calibration evidence durable and independently auditable.

## Scope

- Subject under test: exact production TE1 commit above.
- No production Rust, Cargo, evaluator, NNUE, TT, search defaults, source authorization, or Alpha 2.6 History Framework files may change.
- Screening evidence is diagnostic only and is not feature-promotion evidence.
- Previously reported 216-game numbers are non-admissible until raw evidence is reproduced or durably materialized here and independently audited.
- A final current-strength claim requires the separately predeclared confirmatory campaign with at least 1,000 admissible games / 500 complete reversed-color pairs using fresh openings.

## Required durable screening evidence

The screening execution must persist, before any rating claim is accepted:

1. exact TE1 source and executable identity;
2. exact Stockfish 18, fastchess, Ordo, and BayesianElo source/binary identities;
3. UCI-compliance output;
4. frozen engine options, time control, host metadata, and opening-suite bytes/hash;
5. raw PGN for every admissible game;
6. complete runner/engine logs or losslessly compressed logs;
7. machine-readable schedule, pair reconciliation, and failure accounting;
8. per-anchor W/D/L and color split;
9. exact Ordo and BayesianElo inputs, commands, and outputs;
10. hashes for every evidence artifact and a final manifest.

No rating result is admissible if the raw PGN or estimator inputs are absent from the GitHub-backed branch or a GitHub Actions artifact tied to an exact commit/run.
