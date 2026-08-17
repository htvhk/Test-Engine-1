# D.3 production evaluator match gate — planning specification

This document defines requirements for the **planning phase**. Codex must inspect the existing search/UCI architecture and propose the exact implementation before changing code.

## Primary question

Does the B.1/B.2 production 128-wide NNUE improve TE1 playing strength sufficiently and safely versus the accepted classical evaluator?

## Required experimental properties

- Paired games with colors reversed.
- Shared opening positions/order across competitors.
- Deterministic/reproducible configuration recording.
- Explicit engine crash, illegal move, timeout/time-forfeit, and protocol-error accounting.
- Fixed-node diagnostic matches plus time-control matches.
- Statistical promotion decision using an explicitly specified SPRT or equivalent sequential test with predeclared hypotheses/bounds.
- 256-wide network may be retained as a challenger/control, but it must not silently replace the B.1 offline winner.
- No search tuning is allowed merely to rescue NNUE during the evaluator promotion experiment.

## Stop condition

If the production NNUE fails the predeclared promotion gate, dependent strength-development cards must stop and the campaign must diagnose evaluator/search interaction rather than pretending promotion succeeded.
