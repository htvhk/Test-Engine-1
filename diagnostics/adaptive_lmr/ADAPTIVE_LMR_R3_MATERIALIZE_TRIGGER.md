# Adaptive LMR R3 materialization trigger

One-shot source materialization from the exact frozen R2 parent plus the committed R3 transformation transaction. The workflow must fail closed on lineage/blob drift, run the pinned Rust 1.97.1 fmt/check/clippy/test gates plus Python attribution regressions, and only then persist the transformed `crates/te1-search/src/lib.rs`.
