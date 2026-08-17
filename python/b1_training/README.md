# TE1 alpha.2.5B.1 — Production NNUE Training and Challenger Selection — HARDENED R2

This source package trains production NNUE challengers from the independently accepted
alpha.2.5A.1 FULL 200K corpus. It preserves the TE1-K32-RP11-v1 feature contract and the
TE1NN001 transport format consumed by the validated Rust D.2 runtime.

Production families:
- k32-w128-h32-crelu
- k32-w256-h32-crelu

Two independent training seeds are run per family. Development data chooses the best seed
within each family and names a provisional offline winner. Reserve data is sequestered until
that choice is frozen. Both family winners are exported for the later Rust parity and D.3
paired-match/SPRT gates; offline loss alone never promotes an evaluator.

The real run requires CUDA. Eager PyTorch, torch.compile(mode="default"), and
mode="reduce-overhead" are benchmarked on a full training epoch with compile/warm-up excluded.
A compiled mode is retained only if it passes same-weight semantic checks, produces an
acceptably equivalent one-epoch training result, and improves full-epoch throughput.

## B.1 target semantics

The clean FULL-200K parent retains raw Stockfish centipawn scores, including mate-like sentinel magnitudes up to ±31999. B.1 preserves those raw values for provenance but clips only the CP-regression target to ±2000 cp before `tanh(cp/600)`. The WDL and real-game-result heads continue to supervise decisive/mating positions. This follows the established TE1 NNUE training contract and prevents mate sentinels from dominating a centipawn regression metric.

The compile selector also compares unscaled backward gradients on identical weights/batches; compiled execution is retained only when forward outputs, loss, gradients, development behavior, and complete epoch+development throughput all satisfy the gate.

## HARDENED R2 runtime fixes

R2 repairs the Colab input harness rather than changing dataset semantics or the NNUE architecture. The notebook accepts both established TE1 manifest list keys (`records` and `files`) while retaining exact coverage/hash/size checks, recursively searches only the bounded `MyDrive/TestEngine1-Colab` project tree, and permits renamed copies only after exact byte-size and SHA-256 identity verification.

Training/compile resume state is also sealed to the actual CUDA device name and compute capability. A Colab reconnect that changes GPU class therefore cannot silently reuse a compile-speed decision or optimizer campaign measured on different hardware.
