# Canonical lineage

## Accepted anchors

### A.1.1 full approved general corpus
- Artifact: `TE1-v1.0.0-alpha.2.5A.1.1-General-Corpus-Source-Expansion-and-Diversity-Gate-Output-full-STORAGE-SAFE-CURRICULUM-DIVERSITY-FIXED-R5-HARDENED-R3-20260815.zip`
- SHA-256: `c4808bedd2c18e95b1cfbf856d5bf9e7002fcf6b422ab84d05277be7e696bd18`

### A.1 FULL-200K clean-room dataset
- Artifact: `TE1-v1.0.0-alpha.2.5A.1-Clean-Room-Dataset-Hardening-and-Semantic-Purity-Audit-Output-full-200K-CURATED-ROOT-ONLY-TEACHER-BINDING-R5-HARDENED-R1-20260815.zip`
- SHA-256: `ac516efbb1d2cfb3e882a3dfc4cefc9c3bc4064b510c295eb98d433ec89e85e4`

### B.1 production NNUE training
- Artifact: `TE1-v1.0.0-alpha.2.5B.1-Production-NNUE-Training-and-Challenger-Selection-Output-HARDENED-R2-20260816.zip`
- SHA-256: `a824af87ef52ff37d0993327cb641c26804a8d56524ed5c040a4bc0b2a0fa002`
- Decision: REAL PASS
- Offline winner: `k32-w128-h32-crelu`, seed `20260816`

### B.2 production NNUE → Rust parity
- Artifact: `TE1-v1.0.0-alpha.2.5B.2-Production-NNUE-Rust-Integration-and-SIMD-Parity-Gate-Output-HARDENED-R2-20260817.zip`
- SHA-256: `2a725611c96dba6d88897dddc24e7927d430796a3738ada024b2f919566db67a`
- Decision: REAL PASS

## Important interpretation

The Rust source was inherited from the validated D.2 control and then exercised with B.1 production networks in B.2. Historical `alpha.2.5D.2` strings in imported Rust source are provenance remnants, not the current project-stage authority.

## Next gate

D.3: paired match/statistical validation of the production NNUE before evaluator promotion.
