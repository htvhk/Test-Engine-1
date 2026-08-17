# TE1 R3 Hybrid — 32-Game 100k-Node Diagnostic

Status: **DIAGNOSTIC_FAIL (non-catastrophic, below classical)**

This is a bounded diagnostic, not a formal strength gate and not a promotion result.

## Identities

- Hybrid source commit: `f4b2b404508986378700defc315acc79f155c1e1`
- R3 network: `r3-cp-decoupled-k32-w128-h32-crelu.te1nn`
- R3 network SHA-256: `822c59d9adccaecc52a5f91991d3c5e85bb4f569e165b9c215c56d79c8bbc65c`
- R3 network size: `5,784,602 bytes`
- Linux x64 release engine SHA-256: `b81f274caa20786ccd34c4232a4e3248d640bdc32ce588bd1698bdf49c9201c1`
- Diagnostic harness SHA-256: `6c4e25b6b2ec09117cf81720c95cdc38f6405cdaef3b3d5f0ed12708b832baf2`
- 100k result JSON SHA-256: `a80343b63e2b923f4731f2187f4d17f29f264c1f3e37ddfe3dc1d7cf8bd28c21`

Preflight exercised the real UCI path with the exact R3 network:
- raw NNUE: `nnue:k32-w128-h32-crelu:avx2-fma`
- hybrid: `hybrid:k32-w128-h32-crelu:avx2-fma`
- classical: `classical`

## Campaign contract

- Classical vs Hybrid-R3
- 32 games = 16 fixed opening lines with reversed colors
- Threads = 1
- Deterministic = true
- 100,000 nodes/move
- Clear Hash each game
- maximum 200 plies
- identical engine/search code; evaluator mode is the intended variable

## Primary 100k result

Hybrid-R3: **7 wins / 9 draws / 16 losses**

- Score: **11.5 / 32 = 35.9375%**
- Hybrid as White: **5W / 4D / 7L**
- Hybrid as Black: **2W / 5D / 9L**
- Reversed-pair point bins `[0, 0.5, 1, 1.5, 2]`: **[5, 2, 7, 1, 1]**
- Illegal moves: **0**
- Protocol errors: **0**
- Engine failures: **0**
- Terminations: 23 checkmates, 8 max-ply adjudications, 1 draw

Per-pair Hybrid points:

| Pair | Hybrid as Black | Hybrid as White | Pair points |
|---|---:|---:|---:|
| 1 | W | W | 2.0 |
| 2 | L | L | 0.0 |
| 3 | L | D | 0.5 |
| 4 | W | L | 1.0 |
| 5 | L | L | 0.0 |
| 6 | D | D | 1.0 |
| 7 | L | L | 0.0 |
| 8 | D | L | 0.5 |
| 9 | L | W | 1.0 |
| 10 | D | W | 1.5 |
| 11 | L | L | 0.0 |
| 12 | D | D | 1.0 |
| 13 | L | L | 0.0 |
| 14 | L | W | 1.0 |
| 15 | L | W | 1.0 |
| 16 | D | D | 1.0 |

## Secondary node-sensitivity evidence

These are diagnostic context only; 100k is the primary result.

- 5k nodes/move: 4W / 3D / 25L = **17.1875%**; repeated run reproduced the same game results and terminations.
- 20k nodes/move: 14W / 3D / 15L = **48.4375%**.
- 100k nodes/move: 7W / 9D / 16L = **35.9375%**.

The large node-budget sensitivity means shallow results are not stable enough to support a promotion or rejection by themselves.

## Interpretation

The artifact/authentication and runtime-integration problem is resolved: the exact R3 network loads in the exact hybrid source, evaluator identities are explicit, and the bounded match showed no illegal moves, protocol errors, or engine failures.

The offline hybrid improvement did **not** translate into a clear playing-strength advantage over classical at the representative 100k-node diagnostic. The 35.9375% result is below classical but not a catastrophic runtime collapse. This result does not authorize promotion, merging to `main`, SPRT, retraining, h64, or coefficient retuning.

The next milestone should be a **bounded evaluator/search interaction attribution diagnostic**, not another training run. The cheapest useful attribution is to triangulate the exact same R3 network in three modes—classical, raw R3 NNUE, and hybrid R3—under the same paired fixed-node harness, then inspect root-score/PV stability across node budgets before changing any evaluator or search constants.
