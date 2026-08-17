# TE1 NNUE Network-Quality Root-Cause Preflight Summary

Date: 2026-08-17

Authoritative base: `f0ad93ad1940b964c513fc56175c7f907b114be9`

Production NNUE: `k32-w128-h32-crelu`
SHA-256: `9cba80ed00f31946b54179d2ed63b4639ef3ba62d1a96ba2bca4fca4fc846974`

256 challenger SHA-256: `5e006228004e489de0f1a18e279281c75b91d65e90be0749d8a3244f72382367`

## Established external engine result

The prior NNUE truth campaign completed 64 games / 32 reversed-color pairs at 250,000 nodes per move. Production NNUE scored 4 wins, 0 draws, 60 losses = 6.25%. Independent replay reported zero illegal moves, crashes, protocol failures, identity mismatches, verifier disagreements, abnormalities, or NNUE fallbacks. No runtime POV/sign, incremental-accumulator, or search-interface correctness defect was proven.

## Preflight conclusion

`INSUFFICIENT_EVIDENCE_ONE_PILOT_REQUIRED`

The B.1 forensic reconstruction found:

- train/dev/reserve split = 160,000 / 20,000 / 20,000;
- four production runs (128/256 widths x two seeds) all selected best epoch 1 and stopped after epoch 7/8 through patience exhaustion;
- durable provenance discarded full per-epoch histories, so undertraining, learning-rate instability, early overfit, or per-objective trajectory claims are not proven;
- development and reserve metrics are very similar, strongly disfavoring ordinary random dev-set overfit;
- 128->256 feature-transform width did not improve the regime materially and did not test the shared hidden width, which remained 32;
- quantization/export error is orders of magnitude smaller than model-vs-teacher CP error and is contradicted as an explanation for the 6.25% game score;
- runtime/search integration, random seed variance, PAD/sample-mask leakage, and global output collapse are contradicted or low-value next targets;
- WDL/result classification contributes nominal 0.60 objective weight through the shared 32-unit trunk while alpha-beta search consumes CP, so multitask-objective conflict is a supported but unproven mechanism;
- data/teacher quality and training dynamics are also supported alternatives; checkpoint-selection mismatch and hidden-trunk architecture limitation remain plausible.

## Highest-value next experiment

One deterministic two-arm micro-ablation using authenticated B.1 data:

- exact `train.npz` and `development.npz` (or a provably deterministic re-derivation from the exact canonical parent and exact B.1 preparation source);
- 20,000 stratified train rows and 10,000 stratified development rows;
- identical fresh `k32-w128-h32-crelu` initialization for both arms;
- Arm A = current production multitask objective;
- Arm B = CP SmoothL1 only, diagnostic only;
- same architecture, data order, optimizer, LR, AMP, clipping;
- <=8 epochs / <=1,600 steps per arm;
- target <=15 minutes GPU, hard <=30 minutes total;
- evaluate held-out CP pairwise ordering, Pearson, Spearman, sign accuracy, MAE and subgroup MAE, plus fixed-batch task-gradient norms/cosines.

No export, winner promotion, formal strength gate, search tuning, alpha.2.6, alpha.2.7, or multi-hour production training is authorized by this preflight.

## Load-bearing input blocker

The Codex checkout did not contain the authenticated prepared train/development NPZs or preparation report. Substituted/generated data must not be used. Before GPU work, recover the original arrays or deterministically reproduce them from the exact canonical 200K parent (`ac516efbb1d2cfb3e882a3dfc4cefc9c3bc4064b510c295eb98d433ec89e85e4`) with the exact B.1 preparation source and provenance checks.

## Long-run governance

No multi-hour training may begin until a cheap pilot demonstrates a mechanism-specific improvement on alpha-beta-relevant CP evidence and the exact lineage, inputs, acceptance criteria, persistence path, and abort conditions are predeclared.