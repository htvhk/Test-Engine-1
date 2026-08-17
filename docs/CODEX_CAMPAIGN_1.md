# Codex Campaign 1 — PLAN-ONLY brief

## First interaction

**PLAN ONLY. Do not edit, commit, create a PR, or implement.**

Codex should inspect the entire repository, `AGENTS.md`, source, tests, provenance, and existing commands, then return a dependency-aware engineering plan.

## Campaign horizon

### Card 0 — repository consolidation / reproducibility
Confirm the imported canonical baseline is internally coherent for normal Git/Codex development. Propose only the minimum repository fixes required for reliable builds, CI, test invocation, version/provenance clarity, and repeatability.

### Card 1 — D.3 paired-match / statistical promotion infrastructure
Design and implement only after Card 0 passes. Requirements are in `docs/D3_SPEC.md`.

### Card 2 — conditional alpha.2.6 profiling + bounded hot-path repair
Eligible only if D.3's load-bearing result permits continuation. Profile first; optimize measured bottlenecks; preserve correctness; separate speed from strength claims.

### Card 3 — conditional alpha.2.7 failure-mining instrumentation
Build reproducible instrumentation for positions/game segments where TE1 underperforms, without automatically changing evaluator/search behavior.

## Mandatory plan fields for every card

- objective and user-visible outcome
- exact files/modules likely affected
- immutable files/contracts
- dependencies and stop conditions
- known failure modes
- acceptance commands/tests
- benchmark/match evidence where relevant
- rollback strategy
- safe parallel work vs serialized work
- artifacts/reports produced

## Campaign stop rules

- A failed load-bearing gate blocks dependent cards.
- Do not merge independent Elo-sensitive search heuristics into one opaque change.
- Do not modify production NNUE weights or training data during D.3.
- Stop after the plan and wait for external review before implementation.
