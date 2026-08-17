# Development protocol

## Roles

1. Director — scope, immutable baseline, allowed files, promotion requirement.
2. Architect — algorithm/data/interface/performance design.
3. Toolchain manager — compiler/dependency/platform/API compatibility.
4. Interface manager — cross-component contracts, especially Python ↔ Rust and training ↔ inference.
5. Builder — bounded implementation.
6. Rival / verifier — independently attempts to falsify correctness.

## Evidence hierarchy

Direct reproducible runtime evidence outranks prose summaries or reviewer agreement. A passing unit test proves only the behavior it exercises.

## Change discipline

- Root-cause before repair.
- Smallest coherent change.
- One integration step active at a time.
- No hidden fixture relaxation.
- No strength promotion without match evidence.
- Preserve rollback points and canonical hashes.

## Storage policy

Git contains source, tests, small reference fixtures, production `.te1nn` networks, and compact provenance. Large datasets, checkpoints, training scratch, match bulk logs, and historical ZIP archives remain external.
