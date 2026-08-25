# Alpha 2.6 History Framework / Correction History — Codex Work Order

## 0. Mission

Build the reusable History Framework that must precede further Adaptive LMR work. The objective is not merely to add another heuristic. It is to create a measured, bounded, independently verifiable search-memory substrate for TE1 while preserving current production behavior unless a later candidate is explicitly enabled.

There is **no promise of Elo gain**. Process success means every result is attributable, reversible, and authenticated; an evidence-backed rejection is a valid outcome. No merge/default flip is allowed from this work order.

## 1. Immutable production baseline

- Repository: `htvhk/Test-Engine-1`
- Base branch: `main`
- Base commit: `1e750218f43fa5129cb82f19b107555a1343d878`
- Base tree: `df59aa937bbff6736af25304ca990a69d06ae49f`
- Production search blob: `cd393b65085cdfa1b327f00f23c69f61763fcb2e`
- Production search SHA-256: `943cd3320e0538b5e30763ebc1858cbe0fd53d94b6f9c31a1fc0b6364e397a26`
- Production engine blob: `b2facdb682c7841e1b3c77db43dffdcc7fb59fcd`
- Production engine SHA-256: `61c0b03d3bd274f281a5c375cd0f3b3dd37f48adca4c2fcc42e477471e867f7d`
- Current source authorization manifest blob: `b5ec252f17a8b449e705bb9659c9b9e4ae40be30`
- Pinned Rust: `1.97.1`

The existing signed-history Adaptive LMR candidate is frozen evidence and is **not** the base of this feature.

## 2. Existing authenticated history machinery on `main`

`crates/te1-search/src/lib.rs` already contains:

- bounded quiet history, `i16`, `HISTORY_LIMIT = 16384`;
- bounded capture history;
- continuation history indexed by previous piece/to and current piece/to;
- countermove table indexed by previous packed move;
- two killers per ply;
- `quiet_score()` combining quiet + continuation + a countermove ordering bonus;
- `record_quiet_cutoff()` with positive cutoff bonus and penalties for previously searched quiets;
- `record_capture_cutoff()` with analogous capture updates;
- fixed LMR that currently does not consume history.

Important known lesson: a combined move-ordering score must not automatically be treated as a clean learned-history signal. The previous Adaptive LMR experiment exposed this distinction.

## 3. Role protocol

### Director / Architect — ChatGPT
Own scope, source lineage, invariants, acceptance criteria, branch topology, evidence interpretation, and promotion decisions.

### Implementer — Codex
Execute **one bounded work card at a time**. Before editing, restate exact files allowed to change, forbidden files, source identities, intended behavior, tests, and stop conditions. Never expand scope to make a test pass.

### Toolchain verifier — GitHub Actions
Fresh checkout; source-auth verification; asset hydration; pinned Rust; Python attribution regression; `cargo fmt`; `cargo check --workspace --all-targets --locked`; `cargo clippy --workspace --all-targets --locked -- -D warnings`; `cargo test --workspace --locked`.

### Adversarial reviewer — ChatGPT + second Codex review pass
After implementation, perform a review-only pass. The reviewer may produce findings but **must not modify source**. Findings are adjudicated before any repair card is opened.

## 4. Global invariants

1. No source change before exact baseline authentication.
2. No threshold derived from intuition when a measurable TE1 signal distribution exists.
3. No silent coupling of move-ordering history and Correction History.
4. No special score (mate/draw/TT score) may be stored as or transformed into ordinary static evaluation.
5. No Correction History update from aborted/incomplete search states.
6. Default/OFF mode must preserve current search semantics exactly until a candidate explicitly enables correction.
7. NMP production behavior is preserved; any later corrected-eval use by NMP must be separately attributable and tested.
8. Adaptive LMR, Singular Extensions, Improving integration, and ProbCut are out of scope.
9. No change to NNUE/hybrid evaluator semantics in this feature.
10. No merge/default flip based on compilation, node savings, or smoke results alone.

## 5. Gate ladder

### Gate A — exact source audit, no production edits
Deliver a durable audit containing:

- every move-history storage array and index function;
- every history read site;
- every update site;
- ranges and saturation equations;
- countermove/killers semantics;
- every static-eval call site in root, negamax, qsearch, NMP, stop/ply fallbacks;
- TT interactions relevant to static-eval correction;
- candidate Correction History insertion points;
- list of files that a minimal implementation would require.

**PASS:** audit reconciles with exact source.  
**FAIL/BLOCK:** any unexplained consumer or lineage mismatch.

### Gate B — instrumentation-only baseline profile
Use a separate diagnostic branch from the exact production baseline. Do not change search decisions. Measure, over a fixed representative suite:

- quiet-history distribution;
- continuation-history distribution;
- combined quiet score distribution;
- countermove frequency and bonus contribution;
- capture-history distribution;
- update counts by sign/depth;
- saturation/near-saturation frequency;
- cutoff attribution;
- per-depth and per-move-index traffic relevant to later consumers.

Instrumentation must prove decision neutrality on deterministic searches.

### Gate C — top-engine architecture reconnaissance
Primary reference: current Stockfish Correction History architecture. Cross-check at least two other strong open-source engines if available. Record concepts, not copied constants.

Required questions:

- What is used as the correction key/generalization unit?
- What signals are corrected?
- What search outcomes update correction?
- What bound/score conditions suppress updates?
- How is correction bounded/aged/scaled?
- Which search decisions consume corrected evaluation?
- Which pieces can TE1 support cleanly today without importing unrelated features?

### Gate D — foundation implementation, default OFF
Only after Gates A–C.

Target the smallest reusable API:

- explicit typed accessors that distinguish raw quiet history, continuation component, countermove status, capture history, and combined ordering score;
- a **separate** bounded Correction History storage/update/corrected-eval API;
- deterministic initialization/reset semantics;
- a `UseCorrectionHistory`-style experimental gate only if UCI exposure is needed for testing;
- OFF path with exact current behavior.

Do not decide final keying/table size/update constants until Gates B and C are reviewed.

### Gate E — correctness CI
Must pass fresh GitHub CI with pinned Rust 1.97.1 and locked dependencies. Add focused unit/property tests for:

- bounds and saturation;
- deterministic updates;
- sign behavior;
- OFF-path parity;
- no update on forbidden states;
- mate/draw/score-clamping safety;
- TT-score conversion remains independent;
- qsearch semantics remain valid;
- existing NMP tests remain green.

### Gate F — Correction History activation/distribution
On a fresh diagnostic branch, measure:

- lookup/update count;
- signed correction distribution;
- corrected-minus-raw static eval;
- saturation frequency;
- update conditions by bound/depth;
- percentage of nodes materially changed by correction;
- exact number of NMP/qsearch/other consumers affected, if any are enabled in that candidate.

A signal that is dormant, always saturated, or overwhelmingly one-sided cannot advance.

### Gate G — semantic + node safety
Require deterministic completion, no operational failures, no mate/draw regressions, no illegal move/PV corruption, no pathological tree explosion, and predeclared node-ratio bounds. Node savings alone do not imply strength.

### Gate H — paired smoke
Small reversed-color paired test using exact same executable, classical evaluator for clean attribution unless the contract explicitly says otherwise. Diagnostic only.

### Gate I — formal proof
Launch only if A–H pass. Predeclare fresh openings, pair unit, confidence interval rule, first-attempt identity, evidence transaction format, replay/re-adjudication, PASS/FAIL/INCONCLUSIVE/BLOCKED behavior. Do not tune after seeing formal result.

## 6. Codex work-card template

Every Codex implementation task must begin with this header:

```text
TE1 WORK CARD
Base commit/tree:
Allowed files:
Forbidden files:
Behavior to preserve:
Behavior to add:
No-go scope:
Required tests:
Required evidence:
Stop immediately if:
```

Codex must finish with:

1. exact `git diff --stat`;
2. exact changed-file list;
3. compile/test commands and results;
4. unresolved risks;
5. statement that no forbidden file changed;
6. commit SHA only after all local gates are green.

## 7. Repair protocol

A failed gate does not authorize broad edits.

1. Classify failure: lineage / syntax / compile / lint / test / interface / semantic / activation / performance / strength / infrastructure.
2. Reproduce with the smallest evidence.
3. Open a **repair card** limited to the causal defect.
4. Run the full gate again after repair.
5. Never weaken a test merely to make the candidate green.

## 8. Promotion rule

The History Framework may be merged only when the exact candidate has:

- authenticated lineage;
- green repository-native CI;
- active and sane learned signal;
- semantic safety;
- bounded performance behavior;
- positive predeclared playing-strength evidence where behavior changes search;
- explicit promotion authorization.

Until then it is experimental.
