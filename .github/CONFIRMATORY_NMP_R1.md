# TE1 NMP R1 confirmatory gate

This diagnostic branch runs exactly one fixed confirmatory batch after the original 128-pair/256-game NMP strength batch.

- Production baseline: `cd5b6666c58d82a51e58132d419bc316b1415ee6`
- Batch 1: opening ranks 0..127, selection SHA-256 `832435a9505b4b11abc4c1103f111add09348a80e83b63d313d7bb1409f080fe`
- Confirmatory batch: opening ranks 128..255 from the same deterministic ordering, with no overlap permitted.
- 128 reversed-color pairs / 256 games.
- Classical evaluator only; same executable; NMP OFF versus ON.
- 100,000 nodes/move, Threads=1, Deterministic=true, Hash=16 MB, clear hash each game.
- 28-ply opening positions, total 200-ply cap.
- Pinned source: official-stockfish/books commit `65815ccdbc7727cd4f6aee252ba8f67fb740e92f`, `Drawkiller_balanced_big.epd.zip`, Git blob `b851fc8c484b9e36b178131a7f47269bfdfacd39`, SHA-256 `c20483ecca07676c10ad3fb5acad6370fc75a5e6bf3935a7255bb2a73fe8deac`.

Stopping rule: do not extend this confirmatory batch after seeing its result. Evaluate batch 2 separately, then evaluate the cumulative 256-pair/512-game evidence. Default-ON promotion requires the confirmatory batch to be non-materially negative and the cumulative paired 95% score interval to exclude 50% on the positive side. Otherwise NMP remains default OFF.
