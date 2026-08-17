# Test Engine 1 (TE1)

Performance-focused Rust chess engine with quantized NNUE evaluation.

## Canonical baseline

This repository is bootstrapped from the independently accepted **alpha.2.5B.2 HARDENED-R2** runtime, using the **B.1 HARDENED-R2** production networks.

- Production/default NNUE: `k32-w128-h32-crelu`
- 128 SHA-256: `9cba80ed00f31946b54179d2ed63b4639ef3ba62d1a96ba2bca4fca4fc846974`
- 256 challenger SHA-256: `5e006228004e489de0f1a18e279281c75b91d65e90be0749d8a3244f72382367`
- Rust toolchain: `1.97.1`

The large clean-room 200K training dataset and historical Colab/output archives are intentionally **not** stored in Git. Their canonical artifact identities are recorded in `docs/LINEAGE.md`.

## Fresh clone

```bash
python3 scripts/verify_repo_integrity.py
python3 scripts/hydrate_repo_assets.py
cargo fmt --all -- --check
cargo check --workspace --all-targets --locked
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
```

`hydrate_repo_assets.py` deterministically creates compile-time fixture/default-network copies from the tracked canonical 128-wide network. Those generated duplicates are ignored by Git.

## Agent instructions

Read **`AGENTS.md` before making changes**.

The next campaign is described in `docs/CODEX_CAMPAIGN_1.md`; the first Codex interaction is planning-only.
