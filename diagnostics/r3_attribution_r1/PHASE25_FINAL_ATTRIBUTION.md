# TE1 Phase 2.5 Final Attribution

Tested source: `5eda01710e6d8f87672e8ce3382ecf8e03fd4cbe` (tree `3f35942845324d57e7f270f1c37630d195b2885b`).

## Result

- Evidence reconciliation: **PASS**
- Games/pairs: **96/96 games, 48/48 pairs**
- Fatal/error counters: **all zero**

- `classical_vs_raw` (CLASSICAL perspective): **30/2/0 W/D/L**, 31.0/32 (96.875%), pair scores `[2, 2, 2, 2, 1.5, 2, 2, 1.5, 2, 2, 2, 2, 2, 2, 2, 2]`.
- `raw_vs_hybrid` (RAW perspective): **1/3/28 W/D/L**, 2.5/32 (7.8125%), pair scores `[0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0.5, 0, 0.5, 0.5, 0]`.
- `classical_vs_hybrid` (CLASSICAL perspective): **13/7/12 W/D/L**, 16.5/32 (51.5625%), pair scores `[0, 2, 1, 0.5, 1.5, 0, 1, 1, 1, 0.5, 0.5, 0.5, 1.5, 2, 2, 1.5]`.

## Attribution verdict

RAW/R3 itself is the main weakness; Hybrid material correction materially repairs RAW and is approximately level with Classical in this 32-game diagnostic leg.

**Production decision:** DIAGNOSTIC_ONLY / NO PRODUCTION PROMOTION

## Exactly one next experiment

Run one larger paired Classical-vs-Hybrid match with the campaign statistical promotion gate.

Complete machine-readable identities and deterministic evidence hashes are in `PHASE25_FINAL_ATTRIBUTION.json`.
