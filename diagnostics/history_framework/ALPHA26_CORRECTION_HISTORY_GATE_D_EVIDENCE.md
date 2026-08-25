# Alpha 2.6 Correction History — Gate D Evidence

## Identity and scope

- Observed Gate C base/starting HEAD: `23604704ec5cc71d7fd19b8b46c64dd4aaf2a061`.
- Observed starting tree: `c74126ad61e9e2f93984c7ff636d6f076f9bb161`.
- Candidate `crates/te1-search/src/lib.rs` Git blob before commit:
  `2c488172be59b781575d356c199a400df2b85180`.
- Candidate source SHA-256 before commit:
  `e232e77d584044bf906e43aca4d704c81a93aa33c2a14ec0ba989cf7e82884b2`.
- Changed surface is exactly this evidence file and `crates/te1-search/src/lib.rs`.
- `Cargo.lock` and every forbidden path are unchanged.

The source change adds a parameterized, deterministic Correction History substrate, a
side-aware pawn-structure key, distinct raw/correction/corrected evaluation types, a fail-closed
update-eligibility predicate, and typed move-ordering history accessors. **No production consumer
is connected.** Correction History is not owned by `Worker`, and it is not read or updated by
negamax, NMP, qsearch, TT, the evaluator, move ordering, returned scores, or UCI. Public `search()`
and `SearchOptions` are unchanged.

## Fresh command evidence

| Command | Result |
| --- | --- |
| `python3 scripts/verify_repo_integrity.py` | Expected authorization-boundary **FAIL**: the intentional candidate search blob/SHA differs from the currently authorized production identity. The script was not changed or bypassed. The candidate remains untrusted pending a separate exact-byte authorization transaction. |
| `python3 scripts/hydrate_repo_assets.py` | PASS; generated Rust NNUE assets and production network identity verified. |
| `cargo fmt --all -- --check` | PASS. |
| `cargo check --workspace --all-targets --locked` | PASS. |
| `cargo clippy --workspace --all-targets --locked -- -D warnings` | PASS. |
| `cargo test --workspace --locked` | PASS; all workspace unit and doc tests passed, including 23 `te1-search` tests. |
| `cargo test -p te1-search correction --locked` | PASS; four focused Correction History tests. Other focused primitives also passed in the complete workspace test run. |
| `cargo test -p te1-search named_history_accessors --locked` | PASS; typed accessors reproduce the legacy component/combined values. |
| `cargo test -p te1-search null_pruning --locked` | PASS; all six existing focused NMP regression tests. |
| source-level qsearch/raw-evaluator regression assertion in `correction_activity_cannot_modify_raw_evaluator_and_consumers_stay_raw` | PASS; NMP still obtains production raw `static_eval`, qsearch still obtains raw `stand_pat`, and table activity cannot mutate evaluator output. |
| `git diff --check` | PASS. |
| changed-file allowlist check | PASS; exactly the two authorized Gate D paths. |
| `git diff --quiet HEAD -- Cargo.lock` | PASS; unchanged. |
| forbidden-path diff check | PASS; no forbidden path changed. |

## Integrity transaction status

Repository integrity correctly rejects the un-authorized candidate bytes rather than treating an
intentional source candidate as the authorized production source. The exact blob and SHA-256 above
are recorded so a separately authorized transaction can bind the reviewed bytes. Gate D does not
modify `EXPERIMENTAL_SOURCE_AUTHORIZATION.json`, `CANONICAL_BASELINE.json`, or the integrity script.

## Deferred numeric and activation risks

No production table size, correction limit, update scale, depth scaling, lookup scaling, or
consumer threshold has been chosen. Constructors supply table size and signed limit; focused tests
use small values only to expose collisions and saturation. Pawn-only collision/coverage behavior,
cross-search persistence, multithread learning, update scaling, and each possible corrected-eval
consumer require measurement and separate authorization. This foundation makes no strength,
performance, or production-tuning claim.
