# Alpha 2.6 History Framework — Production Baseline Audit

## Verdict

**Gate A: PASS.** The current production/NMP source is internally understandable enough to proceed to instrumentation-only profiling. This audit changes no Rust source, Cargo metadata, workflow, authorization manifest, evaluator, TT, or Adaptive LMR artifact.

The audit is rooted at production commit `1e750218f43fa5129cb82f19b107555a1343d878`, tree `df59aa937bbff6736af25304ca990a69d06ae49f`. The History Framework branch was created directly from that commit. Gate A itself adds only this audit document on top of the already-committed work-order document.

No Correction History table size, keying scheme, scaling constant, update magnitude, or search-consumer threshold is selected here.

## 1. Authenticated source identities

| Path | Git blob | SHA-256 / authenticated content identity | Role in audit |
|---|---|---|---|
| `crates/te1-search/src/lib.rs` | `cd393b65085cdfa1b327f00f23c69f61763fcb2e` | `943cd3320e0538b5e30763ebc1858cbe0fd53d94b6f9c31a1fc0b6364e397a26` | search, histories, LMR, NMP, qsearch |
| `crates/te1-engine/src/main.rs` | `b2facdb682c7841e1b3c77db43dffdcc7fb59fcd` | `61c0b03d3bd274f281a5c375cd0f3b3dd37f48adca4c2fcc42e477471e867f7d` | UCI/default search options |
| `crates/te1-chess/src/lib.rs` | `aaca8a00442fa4de91e9368feb3d96718030ccd0` | `0f285918bc49620a0aeacf9865eae250dee034a603c95e2d87778c803b2a271a` | `SearchPosition`, search key, draw/null state |
| `crates/te1-tt/src/lib.rs` | `111f237552999f93675c67674675996ab4f4506e` | `270c50d3801c7d9c509f2c47434ac0173c3e2ce8358cb260c1eaad5c2890c414` | TT score/bound/depth/move semantics |
| `crates/te1-eval/src/lib.rs` | `5626d3e620285cb4e966f41f50ed4886f2735274` | `cc580876f54986e9dcf14a563b46728e87638a1b4a61c4b6a83cbd36893cf504` | raw classical/NNUE/hybrid evaluation |
| `EXPERIMENTAL_SOURCE_AUTHORIZATION.json` | `b5ec252f17a8b449e705bb9659c9b9e4ae40be30` | `00c2ad895b554ed4018bef3bd0ba0dedc04646ce62aa1d5edafdcfeb5ed73121` | current production-source authorization |
| `.github/workflows/rust-ci.yml` | `8bbe1e950d3996e4a2b73112045b845759a40168` | `322ed3b032e6742673634d0272ee9910067d8af4099053da88c341927f6965ed` | repository-native validation gates |

The authorization manifest binds the current production search, engine, chess and eval blobs and descends from authorized checkpoint `b4303c2fab89516e660dcde744b8177538c9e5a1` / tree `bae37dacfdf6f0626ecc0f7b96022cb4c631a5d7`. `scripts/verify_repo_integrity.py` requires HEAD ancestry from that checkpoint and validates tracked content hashes/blobs.

## 2. Existing move-history storage and lifetime

**Source:** `crates/te1-search/src/lib.rs`, symbols `HISTORY_LIMIT`, `CONTINUATION_DIM`, `HistoryTables`, `HistoryTables::default`, `search`.

`HISTORY_LIMIT` is `16_384`. `CONTINUATION_DIM` is `6 * 64 = 384`.

`HistoryTables` owns five independent search-memory structures:

1. `quiet: Box<[i16]>`, allocated as `2 * 64 * 64` entries.
2. `capture: Box<[i16]>`, allocated as `2 * 6 * 64 * 6` entries.
3. `continuation: Box<[i16]>`, allocated as `384 * 384` entries.
4. `countermove: Box<[PackedMove]>`, allocated as `64 * 64` entries.
5. `killers: [[PackedMove; 2]; MAX_PLY]`.

All numeric histories start at zero; countermoves and killers start as `PackedMove::NONE`.

A fresh `HistoryTables::default()` is constructed inside each search worker spawned by `search()`. Therefore current history is **per worker and per `search()` invocation**. It survives the worker's iterative-deepening iterations, but is not persisted across separate UCI `go` searches. With deterministic mode, `search()` forces one worker even if a larger thread count was requested.

This lifetime is a design fact to preserve during baseline profiling. Whether a future Correction History should have the same lifetime is **unresolved** and must not be assumed from the move-history lifetime.

## 3. Quiet history

**Storage:** `HistoryTables::quiet`.

**Index:** `quiet_index(side, mv)` computes `(side * 64 + from) * 64 + to`.

**Read:** `HistoryTables::quiet_score()` starts from `quiet[quiet_index(side, mv)]`.

**Positive update:** `HistoryTables::record_quiet_cutoff()` computes `bonus = history_bonus(depth)` and calls `update_history()` on the cutoff quiet move.

**Negative update:** previously searched quiets other than the cutoff move receive `-(bonus / 2)` through the same `update_history()` function.

**Bonus function:** `history_bonus(depth)` uses `min(max(depth,1)^2 * 32, 2048)`.

**Range/saturation:** `update_history()` clamps incoming bonus to `[-16384, 16384]`, then applies the gravity update

`new = current + bonus - current * abs(bonus) / 16384`

and clamps the result to `[-16384, 16384]` before converting to `i16`.

Thus the table is signed and bounded, but its *observed* operating distribution is not established by source inspection alone. Gate B must measure it.

## 4. Continuation history

**Storage:** `HistoryTables::continuation`, a `384 x 384` `i16` table.

**Index:** `continuation_index(previous_piece, previous_to, piece, to)` maps previous `[piece,to]` and current `[piece,to]` into a single flat index.

**Read:** `HistoryTables::quiet_score()` adds one continuation component when both `previous` move context and the current moving piece are available.

**Positive update:** `record_quiet_cutoff()` updates the previous→current continuation entry with the same positive `history_bonus(depth)` as the quiet table.

**Negative update:** each previously searched losing quiet receives the same `-(bonus / 2)` continuation penalty when previous context/current piece are available.

**Range/saturation:** exactly the shared `update_history()` gravity rule and `HISTORY_LIMIT = 16384`.

TE1 currently has one previous-move continuation component, not the multiple-ply continuation stack commonly used by some top engines. Expanding continuation depth is not Gate A scope.

## 5. Capture history

**Storage:** `HistoryTables::capture`, indexed by side, moving piece, destination and captured piece.

**Index:** `capture_index(side, moving, to, captured)` flattens `2 x 6 x 64 x 6` dimensions.

**Read:** `HistoryTables::capture_score()` determines moving piece and victim, using Pawn as the fallback victim representation where the helper returns none, then returns the signed capture-history entry.

**Positive update:** `record_capture_cutoff()` updates the cutoff capture with `history_bonus(depth)`.

**Negative update:** other searched captures receive `-(bonus / 2)`.

**Range/saturation:** same `update_history()` gravity and ±16384 bound.

`ordered_moves()` uses capture history inside the tactical ordering classes after TT move / SEE / promotion classing. Capture history is currently an ordering statistic, not a static-evaluation correction statistic.

## 6. Countermove history

**Storage:** `HistoryTables::countermove`, one `PackedMove` per previous from/to pair.

**Index:** `counter_index(previous)` extracts the previous packed move's `from` and `to`, ignoring promotion code, then computes `from * 64 + to`.

**Update:** on a quiet beta cutoff with valid `previous` context, `record_quiet_cutoff()` stores the cutoff move as the countermove for that previous packed from/to pair.

**Read:** `quiet_score()` compares the stored countermove to the current quiet move.

**Ordering contribution:** an exact countermove match adds **+8000** to the combined quiet ordering score.

This +8000 is categorical move-ordering priority. It is not an `update_history()` value and must not be silently interpreted as evidence that the underlying signed quiet/continuation histories are +8000 stronger. This distinction is mandatory for future consumers.

## 7. Killer moves

**Storage:** two `PackedMove` values per ply in `HistoryTables::killers`.

**Update:** `record_quiet_cutoff()` promotes a new quiet cutoff move to killer slot 0 and shifts the previous slot 0 to slot 1 when the move is new.

**Read/order:** `ordered_moves()` gives killer 0 and killer 1 dedicated ordering classes ahead of ordinary quiet-history ordering.

Killers are categorical search context, not a signed learned-value table.

## 8. Exact composition of `quiet_score()`

For a non-tactical move:

`quiet_score = quiet_history + continuation_history_if_available + 8000_if_countermove_match`

The three terms have different semantics:

- quiet history: signed learned success/failure for side/from/to;
- continuation history: signed learned success/failure conditioned on previous piece/to;
- countermove: categorical lookup represented as a fixed ordering bonus.

Therefore **combined `quiet_score()` is an ordering score, not a clean history feature**. Any future Adaptive LMR/history pruning consumer must request the component it actually intends to use, or an explicitly documented composite, rather than reusing the ordering score by accident.

## 9. Static-evaluation call sites in search

All runtime sites below are in `crates/te1-search/src/lib.rs`.

### `Worker::root_search`

1. If `enter_node(false, 0)` fails, returns raw `te1_eval::evaluate(position.board())` as an abort/limit fallback.
2. If no searched move produced a score (`best_score == -INFINITY`), returns raw evaluator output.

Root search otherwise does not maintain a node static-eval field.

### `Worker::negamax`

3. If `enter_node(false, ply)` fails, returns raw evaluator output.
4. At the `MAX_PLY - 1` guard, a non-checkmate position returns raw evaluator output.
5. When production NMP is enabled, `static_eval` is computed with raw `te1_eval::evaluate(position.board())`; when NMP is disabled the sentinel is `i32::MIN`.
6. If a null search aborts, that NMP `static_eval` is returned as the fallback.
7. If no move obtains a search score (`best_score == -INFINITY`), returns raw evaluator output.

### `Worker::quiescence`

8. If `enter_node(true, ply)` fails, returns raw evaluator output.
9. At `MAX_PLY - 1`, or the maximum qsearch ply when not in check, returns raw evaluator output.
10. Normal non-check qsearch computes `stand_pat = te1_eval::evaluate(position.board())`.

### Test-only use

Search tests also call `te1_eval::evaluate()` directly when exercising NMP eligibility/stalemate behavior. Those calls are regression fixtures rather than runtime consumers and must remain valid when the future correction option is OFF.

### Evaluator semantics

`crates/te1-eval/src/lib.rs::evaluate()` is the raw evaluator interface from search's perspective. It dispatches to classical evaluation when NNUE is disabled, otherwise NNUE, optionally followed by the existing hybrid transform. Correction History does not belong inside this evaluator API for the first History Framework candidate: the raw evaluator identity must remain independently testable.

## 10. NMP static-eval path and risk

`Worker::negamax()` computes raw `static_eval` when `SearchOptions.use_null_move_pruning` is true and passes it to `null_move_eligible()`.

`null_move_eligible()` requires all of:

- NMP option enabled;
- non-PV node;
- not already inside a null subtree;
- side not in check;
- depth >= 4;
- beta outside the mate-score safety band;
- no sufficiently deep TT Upper bound contradicting beta;
- `static_eval >= beta`;
- side to move has non-pawn material.

The caller additionally requires at least one legal move before performing the synthetic null move. High-depth fail-highs receive verification search.

The UCI engine's `EngineOptions::default()` has NMP **ON** and passes that state into `SearchOptions`; the search crate's standalone `SearchOptions::default()` still has NMP false. This is existing production architecture, not a History Framework defect to rewrite incidentally.

A future corrected static eval could change whether `static_eval >= beta`, so routing corrected evaluation into NMP is Elo-sensitive and attribution-sensitive. It must not happen accidentally as a side effect of merely adding Correction History storage.

## 11. Qsearch stand-pat and pruning dependencies

For non-check qsearch:

1. raw evaluator output becomes `stand_pat`;
2. `stand_pat >= beta` returns immediately;
3. otherwise alpha becomes `max(alpha, stand_pat)`;
4. negative SEE captures may be pruned when SEE pruning is enabled;
5. a tactical move may also be skipped when
   `stand_pat + captured_piece_value + promotion_gain + 200 <= alpha`.

Thus changing qsearch's static evaluation can alter stand-pat cutoffs, alpha, and the delta/futility-style capture filter. A future Correction History consumer at qsearch is therefore a real search-behavior change and must be measured separately rather than treated as passive bookkeeping.

When in check, stand-pat is not used and the search requires legal evasions; no correction implementation may manufacture an ordinary static stand-pat for an in-check node.

## 12. TT semantics relevant to Correction History

`crates/te1-tt/src/lib.rs` stores only:

- search depth;
- search score;
- bound (`Exact`, `Lower`, `Upper`);
- best move;
- generation.

**TE1 currently does not store static evaluation in TT.** This differs from the current architecture of several top engines and is a crucial scope boundary.

`Worker::negamax()` probes TT search scores and uses `score_from_table(entry.score, ply)` for mate-distance normalization. A sufficiently deep exact/lower/upper entry can cut off if `SearchPosition::tt_cutoff_safe()` permits it. A sufficiently deep Upper entry below beta also sets `tt_upper_contradicts`, suppressing NMP.

`score_to_table()` and `score_from_table()` adjust mate scores by ply; ordinary centipawn-like values are unchanged. The TT implementation packs score into signed 16-bit storage and clamps before packing.

Root and normal negamax store completed search scores/bounds/best moves. Qsearch currently probes TT only for a move-ordering move and does not store a separate static-eval field.

**Gate A conclusion:** do not expand the TT layout merely to imitate another engine. The first Correction History foundation can keep raw evaluation local to search. If raw-static-eval TT caching is later justified, it requires a separate work card and evidence because it changes TT layout/replacement/cache behavior as well as evaluation flow.

## 13. Search-position keys and null-state semantics

`crates/te1-chess/src/lib.rs::SearchPosition::search_key()` combines the FIDE-normalized board-position hash with mixed halfmove-clock and repetition-count state. It is intentionally a **search/TT key**, not a documented Correction History generalization key.

`make_null_move()` creates a synthetic search-only pass, does not advance legal rule-50 state, and establishes a repetition barrier so pre-null ancestors are not treated as legal ancestors of the synthetic subtree.

Consequences:

- no Correction History keying scheme should automatically reuse `search_key()` merely because it already exists;
- no Correction History update should silently learn from aborted or synthetic null-search states until that behavior is explicitly designed and tested;
- the desirable generalization unit (for example pawn structure, piece subset, full position, or continuation context) remains unresolved until profiling/reconnaissance are reviewed.

## 14. Current CI and source-integrity contract

`.github/workflows/rust-ci.yml` uses a fresh checkout and pinned Rust 1.97.1, then runs:

1. `python3 scripts/verify_repo_integrity.py`
2. `python3 scripts/hydrate_repo_assets.py`
3. the Python attribution regression
4. `cargo fmt --all -- --check`
5. `cargo check --workspace --all-targets --locked`
6. `cargo clippy --workspace --all-targets --locked -- -D warnings`
7. `cargo test --workspace --locked`

Any future Rust-source candidate must explicitly update source authorization only after a source identity has been intentionally established; source-integrity failure is not permission to bypass `verify_repo_integrity.py`.

## 15. Minimal likely implementation surface — not yet authorization to edit

Based on the authenticated source, the smallest first implementation can likely remain centered on:

- `crates/te1-search/src/lib.rs` — typed move-history component access, Correction History storage/update/corrected-eval logic, search-side option/consumer plumbing and tests;
- `crates/te1-engine/src/main.rs` — **only if** an experimental UCI option is needed to select OFF/ON behavior;
- `EXPERIMENTAL_SOURCE_AUTHORIZATION.json` — only after an intentionally materialized source candidate is ready for CI authorization.

The following are **not currently required** by the first foundation and must not be added without a later explicit reason:

- `crates/te1-tt/src/lib.rs` layout changes;
- `crates/te1-eval/src/lib.rs` NNUE/classical/hybrid semantic changes;
- NNUE weights, training code or data;
- Adaptive LMR source;
- Singular Extensions, Improving integration, ProbCut.

`crates/te1-chess/src/lib.rs` might become necessary only if Gate B/C justify a dedicated inexpensive position-subset key (for example a pawn-structure key) that cannot be implemented safely and efficiently from existing public board access. That decision is intentionally deferred.

## 16. Unresolved questions carried to Gates B/C

1. What are the real distributions of quiet, continuation, combined quiet, capture, and countermove traffic over representative TE1 searches?
2. How often are current history entries near saturation, and how quickly do they learn during iterative deepening?
3. Should Correction History persist only within one `search()` like current move history, or across UCI searches/new-game boundaries? This is a lifecycle/performance/strength decision, not a source-audit conclusion.
4. Which TE1-native generalized key gives sufficient recurrence without destructive aliasing? `SearchPosition::search_key()` is not automatically suitable.
5. Can a useful first correction signal be keyed with existing `Board` data cheaply, or is a dedicated pawn/non-pawn key helper warranted?
6. Should the first behavioral candidate correct only main-search static evaluation, or also qsearch/NMP? These consumers have different attribution and safety effects and should not be enabled accidentally together.
7. What update guard best maps TE1's current bound/search structure to a trustworthy `search_score - raw_static_eval` learning target?
8. What correction value range/scaling is appropriate for TE1's NNUE/classical score distribution? No external-engine constant is presumed correct.

## 17. Gate A evidence closure

- Baseline commit/tree authenticated: **PASS**.
- Production search/engine/chess/eval authorization identities reconciled: **PASS**.
- TT and CI identities inspected: **PASS**.
- Existing history storage/read/update/index/lifetime mapped: **PASS**.
- Runtime raw-evaluation consumers mapped: **PASS**.
- NMP/qsearch/TT risks mapped: **PASS**.
- Correction History constants/keying chosen: **NO — deliberately deferred**.
- Production Rust source modified by Gate A: **NO**.
- Cargo/lock/workflow/authorization modified by Gate A: **NO**.
- Adaptive LMR touched by Gate A: **NO**.
- Unresolved source semantic that prevents Gate B profiling: **NONE**.

### Gate A commit-local diff contract

The parent state already contains `docs/alpha26-history-framework-work-order.md`. The Gate A commit itself is required to add exactly one path:

`diagnostics/history_framework/ALPHA26_HISTORY_FRAMEWORK_BASELINE_AUDIT.md`

Expected commit-local `git diff --stat`: one file added, documentation only. `git diff --check` is required before Gate A is treated as durably closed.
