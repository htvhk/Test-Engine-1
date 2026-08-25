# Alpha 2.6 Correction History — Gate C Architecture Freeze

## Verdict and authenticated baseline

**Gate C is CLOSED / PASS for architecture. Gate D implementation is authorized only within the exact scope below.**

TE1 production baseline is `1e750218f43fa5129cb82f19b107555a1343d878` / tree `df59aa937bbff6736af25304ca990a69d06ae49f`. The feature branch before this document was `f49d222a9641a676f6ae3eceaaee19676937e3bb`. The authenticated production `crates/te1-search/src/lib.rs` blob is `cd393b65085cdfa1b327f00f23c69f61763fcb2e`; production engine blob is `b2facdb682c7841e1b3c77db43dffdcc7fb59fcd`.

Gate A is the accepted production baseline audit in `diagnostics/history_framework/ALPHA26_HISTORY_FRAMEWORK_BASELINE_AUDIT.md`. Gate B v2 is accepted frozen evidence: Actions run `32808720662` SUCCESS, diagnostic head `0e3af824331c8d814b27321d05cadb4077401ad0`, artifact `9549078542`, with untouched production = instrumented OFF = instrumented ON for every predeclared deterministic semantic/search field.

This freeze imports **concepts only**, never external code or tuned constants.

## External architecture reconnaissance

Exact references inspected:

- Stockfish `official-stockfish/Stockfish@598ae2c46500e6dc50b54919ea4bebfe213ddcd1`: `src/search.cpp`, `src/history.h`, `src/search.h`.
- Berserk `jhonnold/berserk@b0c05b0f0138aeb694a84ac74e1d750d8f0d76d2`: `src/history.c`, `src/history.h`, `src/search.c`, `src/types.h`.
- Stormphrax `Ciekce/Stormphrax@2402cae156a11eb5433e07dd8ec8ed7a9d67b750`: `src/correction.h`, `src/correction.cpp`, `src/search.cpp`, `src/search.h`.

Shared architecture across the three engines:

1. Correction History is separate from move-ordering history.
2. It estimates signed static-evaluation error from completed search outcomes.
3. It generalizes through deliberately selected structural/context keys, not by reusing a combined move-ordering score.
4. Entries are bounded and updated with gravity/saturation-style learning.
5. Updates are suppressed when search state or bound semantics make the target untrustworthy.
6. Raw static evaluation remains separately identifiable at the evaluator/TT boundary.
7. Mature engines route corrected evaluation into many consumers, but those consumer networks and constants are engine-specific and are **not** part of TE1 Gate D.

TE1 currently lacks the external engines' richer structure hashes, continuation-correction stacks, improving framework, ProbCut/singular infrastructure, tablebase score taxonomy, and tuned consumer network. Gate D must not manufacture those dependencies.

## TE1-native frozen decisions

### 1. Signal ownership
Move-ordering history and Correction History are distinct families. Quiet history, continuation history, categorical countermove status, capture history, killers, and combined move-ordering score remain move-ordering machinery only. Correction History has separate storage, update, lookup, and interpretation.

### 2. First-generation key
The first TE1 key is a **side-aware pawn-structure key** computed locally in `te1-search` from the current board. It generalizes across positions sharing pawn placement and side to move. It must not silently fall back to the full TT/search key, material-only keys, or external layouts. Collisions are deterministic bounded aliasing, not position identity.

If this key cannot be implemented and tested entirely inside the Gate D allowed search file, Gate D stops.

### 3. Ownership, lifetime, reset
Correction storage is worker-local inside `te1-search`, initialized neutrally for each `search()` invocation, retained across iterative-deepening iterations within that invocation, and discarded afterward. It is not global, persisted across UCI `go`, shared through TT, or stored in the evaluator.

### 4. Bounded update form without premature tuning
The conceptual update is bounded gravity:

`next = clamp(current + bonus - current * abs(bonus) / limit, -limit, limit)`

Intermediate arithmetic must be overflow-safe. **Gate D must not invent production tuning constants.** The foundation should therefore prefer parameterized/const-generic storage and limits, or otherwise keep numeric choices test-only and explicitly unpromoted. Table size, correction limit, lookup scale, depth scale, bonus cap, and activation thresholds remain measurement/tuning decisions for a later card.

### 5. Learning target and eligibility
The trustworthy target is a completed negamax node's returned ordinary search score relative to that node's raw static evaluation. A sample is eligible only when node completion, raw evaluation, score class, move class, bound direction, and synthetic-null ancestry are all unambiguous and safe.

### 6. Forbidden learning
No update from:

- stopped, aborted, node/time-limited, or incomplete search;
- mate/mate-distance, infinity, sentinel, tablebase-like, or other special score;
- terminal/draw fallback, repetition, fifty-move, insufficient-material, stalemate, or checkmate path;
- ply/stop fallback evaluation;
- qsearch;
- synthetic null nodes or null-descended subtrees;
- in-check nodes;
- captures, en-passant, promotions, castling, or other move state whose quiet semantics are not explicitly safe;
- ambiguous/missing raw evaluation, best move, bound, completion, or score classification;
- excluded/singular/ProbCut states, which Gate D does not implement.

Ambiguity suppresses learning.

### 7. Evaluator/API boundary
`te1_eval::evaluate()` remains the raw evaluator and is unchanged. Correction lives entirely in `te1-search`. Raw static eval, correction delta, and corrected static eval must remain explicitly distinguishable.

### 8. TT boundary
`te1-tt` layout, packing, replacement, generation, score range, bound semantics, mate-distance conversion, probe behavior, and public API remain unchanged. Correction is never stored in TT and TT search scores are never reinterpreted as raw evaluation samples.

### 9. No NMP/qsearch consumer in Gate D
Gate D must not route corrected evaluation into production NMP eligibility/reduction/return behavior or qsearch stand-pat/futility/TT behavior. Those consumers continue to use production raw-eval semantics exactly.

### 10. OFF/default equivalence
The foundation is behaviorally OFF. No correction lookup/update may alter search score, ordering, TT interaction, NMP, qsearch, UCI output, bestmove, PV, nodes, or qnodes. Gate D uses an internal/test activation seam only; `te1-engine` UCI changes are not authorized.

### 11. Typed history access
`HistoryTables` must expose clearly named accessors for raw quiet history, continuation component, countermove status, raw capture history, and combined move-ordering score so categorical/composite ordering data cannot masquerade as an individual learned signal. Correction accessors remain separate again.

### 12. Gate D is foundation, not activation
Gate D implements storage/key/update/eligibility/corrected-eval primitives and tests, while production search remains behaviorally unchanged. Strength promotion and consumer activation require later isolated cards.

## Gate D exact write surface

Allowed writes:

1. `crates/te1-search/src/lib.rs`
2. `diagnostics/history_framework/ALPHA26_CORRECTION_HISTORY_GATE_D_EVIDENCE.md`

No other path is authorized.

Forbidden writes include all `te1-engine`, `te1-chess`, `te1-tt`, `te1-eval`, `te1-nnue`, other Rust/source files, every Cargo manifest/lockfile, workflows, authorization manifests, NNUE assets/training data, integrity/hydration scripts, Adaptive LMR, Singular Extensions, Improving integration, ProbCut, NMP behavior, qsearch behavior, TT layout, evaluator semantics, and unrelated paths.

## Gate D acceptance contract

Gate D must freshly prove:

- deterministic neutral initialization and reset;
- worker/search-invocation isolation and intended lifetime;
- bounded gravity, saturation, sign direction, and overflow safety;
- side-aware pawn-key isolation, intended sharing, and deterministic collision semantics;
- direct rejection of every forbidden learning category above;
- mate/draw/terminal/special-score safety;
- unchanged raw evaluator boundary;
- unchanged TT code/layout/score conversion;
- unchanged NMP raw `static_eval` consumer path;
- unchanged qsearch raw stand-pat path;
- typed move-history accessors preserve existing combined ordering behavior;
- exact deterministic production parity when the foundation is not connected to consumers: bestmove, score/kind, complete PV, nodes, qnodes, and stopped/completion status over the predeclared parity suite;
- `Cargo.lock` unchanged and forbidden paths unchanged.

Mandatory repository gates with Rust 1.97.1:

1. `python3 scripts/verify_repo_integrity.py`
2. `python3 scripts/hydrate_repo_assets.py`
3. `cargo fmt --all -- --check`
4. `cargo check --workspace --all-targets --locked`
5. `cargo clippy --workspace --all-targets --locked -- -D warnings`
6. `cargo test --workspace --locked`
7. focused Correction History tests
8. focused existing NMP/qsearch regression tests
9. deterministic OFF production-parity comparison
10. `git diff --check`
11. exact changed-file allowlist, `Cargo.lock` unchanged, forbidden-path unchanged checks.

## Stop conditions and deferred work

Gate D stops instead of broadening scope if source lineage differs, the pawn key needs a forbidden file, a semantic distinction cannot be represented inside the allowed surface, a numeric constant must be guessed merely to make production behavior work, any forbidden path changes, OFF parity changes, or any abort/null/mate/draw/terminal/special state can train correction.

Deferred: production numeric tuning, correction-error distribution measurement, cross-search persistence, richer structure keys, multithread strength semantics, UCI exposure, NMP/qsearch consumers, Improving/ProbCut/Singular integration, and all Elo/strength claims.

**Gate C changes no production behavior. The next authorized step is Gate D default-OFF foundation implementation.**
