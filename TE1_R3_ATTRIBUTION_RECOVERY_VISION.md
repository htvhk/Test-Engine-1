# TE1 R3 Attribution Recovery Vision

## Shared objective

Recover one reproducible, falsifiable TE1 R3 attribution checkpoint without retraining, substitution, search tuning, or weakening deterministic gates, then run the frozen Classical/raw-R3/Hybrid-R3 experiment.

## Critical identity distinction

`f4b2b404508986378700defc315acc79f155c1e1` is the **historical Hybrid implementation**. Its source blobs are historical reference identities:

- `crates/te1-engine/src/main.rs`: `f26b50d4ed22271bd9526de8f771b98dea91d316`
- `crates/te1-eval/src/lib.rs`: `b4ba21bee58003046e66b3e8dbb1eae2fce377a3`

The later Hybrid work was an **independent reconstruction on `f0ad93ad1940b964c513fc56175c7f907b114be9`**, not a requirement to reproduce the historical source bytes. The reconstruction report already had a different source-delta shape (main.rs +30/-0 and lib.rs +140/-2 versus historical +86/-0 and +182/-5), so historical blob equality is not a valid reconstruction gate.

The consolidated local Codex checkpoint currently observed is:

- commit `a007cbbe8c8e6fc147c866cc2be67547c96bdad5`
- parent `f0ad93ad1940b964c513fc56175c7f907b114be9`
- tree `aa462267a113f9db8b07bd6b36d9c22ed9ea68d5`
- main.rs blob `7e2e5816b860be9ed7914f61bd636ecacfa01564`
- lib.rs blob `5626d3e620285cb4e966f41f50ed4886f2735274`
- exactly 19 expected changed paths relative to f0ad

Those reconstructed blobs become canonical only if the fresh falsification gates below pass.

## Reconstruction acceptance gates

Accept the consolidated checkpoint only if fresh evidence establishes all of the following:

1. Exact parent `f0ad93ad...` and only the expected 19 changed paths.
2. Reconstruction source delta and changed hunks are confined to the authorized Hybrid UCI/evaluator surfaces plus diagnostic/harness files; no search, pruning, TT, move ordering, time-management, or unrelated chess behavior changes.
3. Exact frozen Hybrid constants/formula, side-to-move material differences, signed rounding, i128 intermediates, no ordinary-score clamp, raw `evaluate_nnue()` purity, mode hierarchy, evaluator identities, reload/cache invalidation, and invalid-EvalFile rollback semantics.
4. Fresh f0ad release build reproduces the original before-binary SHA-256 `19e1ee85453b437364f7f9e0eec3d37cde28074315e91bd25c02f1e381c2ab17` in the same environment.
5. Fresh consolidated build reproduces the verified after-binary SHA-256 `91abf1f8b094596835e86de92c675e64e6e34674b74d7349c5ead4602a85d725`.
6. Fresh two-binary replay of the frozen 12-position preservation suite shows exact Classical and raw-NNUE equality for evaluator identity, evaluation cp, deterministic fixed-node search score/depth/nodes/PV/bestmove, excluding only elapsed-time/NPS fields.
7. Three-mode switching Classical -> raw -> Hybrid -> raw -> Classical -> Hybrid succeeds with correct identities and no stale state.
8. Harness tests and fresh non-strength smoke pass; checkpoint/resume is idempotent and fail-closed.
9. Opening SHA-256 is `018d1cad476c6d1afcbd611ed6d69eb36f28f8fa88523e57fad5861a0ff46873`.
10. Campaign fingerprint is `2cf5ac07270975a0597c3242d4da5d107daff382b20af792759652addd8966bb`.
11. Full Rust gates pass and root Cargo.lock pre-existing working-tree change remains untouched.

If these pass, `a007cbbe...` is accepted as the **canonical consolidated reconstructed Hybrid + attribution-harness checkpoint**, and its measured source blobs are canonical for that checkpoint. This does not claim source-byte identity with historical f4b2b.

## Exact R3 transport identity

The transport branch carries the diagnostic R3 input only:

- filename `r3-cp-decoupled-k32-w128-h32-crelu.te1nn`
- size `5,784,602`
- SHA-256 `822c59d9adccaecc52a5f91991d3c5e85bb4f569e165b9c215c56d79c8bbc65c`
- Git blob SHA-1 `60adc178b9afb75b6800587722867b933973f0ca`

Never substitute or retrain it.

## Frozen attribution matrix after preflight

Only after exact checkpoint + R3 preflight passes:

- Classical vs raw R3: 32 games / 16 reversed pairs
- raw R3 vs Hybrid R3: 32 games / 16 reversed pairs
- Classical vs Hybrid R3: 32 games / 16 reversed pairs
- 100,000 nodes/move
- Threads=1
- Deterministic=true
- Clear Hash each game
- max 200 total plies = draw
- no resign/eval adjudication

Historical match results remain context only and are not mixed with this fresh matrix.
