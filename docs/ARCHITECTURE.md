# Architecture overview

## Workspace crates

- `te1-chess` — board/game compatibility and chess primitives
- `te1-compat-bridge` — compatibility bridge tooling
- `te1-nnue` — quantized NNUE loader, features, accumulator, scalar/SIMD inference
- `te1-eval` — evaluator selection and embedded/external NNUE handling
- `te1-tt` — transposition table
- `te1-search` — alpha-beta/PVS search, qsearch, ordering/pruning foundations, SMP/search controls
- `te1-engine` — UCI engine
- `te1-tools` — utility binaries

## Evaluation

Current accepted runtime supports classical evaluation and NNUE. The production/default NNUE is 128-wide; a 256-wide network is retained as a challenger/control.

The B.2 runtime proved Python/quantized/Rust parity, scalar/SIMD equivalence, accumulator integrity, UCI switching/rollback, and generic/x86-64-v3 builds. See `provenance/b2/`.

## Data/training

The complete 200K clean-room training dataset is external to Git. Reproducible B.1 training/export source is retained under `python/b1_training/`; production checkpoints are deliberately excluded from this repo.
