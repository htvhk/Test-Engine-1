# Alpha 2.6 Correction History — Gate F1 raw-signal diagnostic

This marker seeds the fresh diagnostic branch for the first Gate F measurement card.

- Exact authorized feature base: `0c39989d17e1de7aae54e3db3b23039f1ae12990`
- Authorized source commit/tree: `9fd558f6c37f1843d2b3444900e757c36f9df353` / `10ae3046ffbd577ac525980c52f718e641f6b0bb`
- Authorized search blob: `dfdc4000a2c986732f39a9901cdfea286e1489d8`
- Authorized search SHA-256: `12d6c0ba791ddab9e3a9333cb9ca32338ef62462fb0bd3bf242749b48eff677c`
- Diagnostic branch: `diagnostic/alpha26-correction-history-gate-f-raw-signal`
- Persistent production Rust source change authorized: **no**
- Search-decision change authorized: **no**
- Production tuning constants authorized: **no**
- Corrected-eval production consumer authorized: **no**

## Gate F1 objective

Measure the raw, fail-closed Correction History learning signal before selecting any table size, correction limit, scaling, threshold, persistence model, or production consumer. Use the existing fixed Gate B representative 10-position depth-8 deterministic suite unless a load-bearing incompatibility is proven.

Required raw evidence includes: inspected completed main-search nodes; suppression counts/reasons; eligible update samples; signed `search_score - raw_static_eval` distribution by depth and bound; side-aware pawn-key reuse/uniqueness traffic; sign balance; deterministic repeatability; and exact semantic neutrality between frozen control, instrumented collection OFF, and instrumented collection ON for bestmove, score, PV, nodes, qnodes, and completion/stopped status.

Instrumentation must reuse already-computed raw static evaluation where possible and must not route correction into NMP, qsearch, TT, move ordering, evaluator, UCI, or returned scores. Ambiguous state suppresses sampling. No Elo/strength claim is authorized.

Gate F remains open after F1. F1 only earns authorization for a later parameter trial if the raw signal is non-dormant, internally consistent, reproducible, and decision-neutral.
