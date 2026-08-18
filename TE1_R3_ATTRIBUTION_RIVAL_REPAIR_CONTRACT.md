# TE1 R3 Attribution Rival Repair Contract

## Verdict

The 2026-08-18 independent Rival review was correct to stop canonicalization. The defects are in the attribution harness, not in the reconstructed Hybrid engine. Fresh executable evidence already reproduced the f0ad before binary, the a007 after binary, exact 12-position Classical/raw preservation, exact six-step Classical/raw/Hybrid switching, Hybrid arithmetic, full Rust gates, and a fresh non-strength smoke.

## Finding 1 — evaluator identity is under-authenticated

Prefix-only acceptance of `nnue:` or `hybrid:` is invalid. Raw and Hybrid modes must require the exact architecture/name `k32-w128-h32-crelu` and a valid supported kernel identity, with Classical equal to exactly `classical`.

However, architecture/name validation alone is still insufficient: the embedded production network and R3 share `k32-w128-h32-crelu`. Therefore final neural preflight must bind all of the following:

1. R3 file size = `5,784,602` bytes.
2. R3 SHA-256 = `822c59d9adccaecc52a5f91991d3c5e85bb4f569e165b9c215c56d79c8bbc65c`.
3. Exact evaluator mode/name structure (`nnue:k32-w128-h32-crelu:<supported-kernel>` or `hybrid:k32-w128-h32-crelu:<supported-kernel>`).
4. A runtime R3-specific witness proving the engine actually uses the supplied weights rather than silently retaining/using the embedded network. Prefer the repository's existing independent Python/quantized reference evaluator on a frozen position set; compare its R3 static-eval vector to the Rust engine. If no independent reference path exists, do not invent a new NNUE implementation; fail closed or use a separately justified diagnostic witness.

No strength game may start until this preflight passes.

## Finding 2 — source identity is stale and caller-trusted

The hardcoded historical local reconstruction SHA `94258e2ee18cd7f783a8c626218293490699a74e` is invalid for the consolidated/restored topology. The harness must not trust a caller-supplied source identity.

For every fresh campaign initialization the harness must directly measure Git state from the executing repository:

- `git rev-parse HEAD`
- `git rev-parse HEAD^{tree}`
- relevant-path cleanliness against HEAD

It must record measured HEAD + tree in checkpoint state. On every resume it must remeasure and require exact equality. Relevant production/harness/contract/opening paths must have no staged or unstaged changes. A pre-existing unrelated root Cargo.lock working-tree modification may remain outside this relevant-path cleanliness gate.

After the harness repair is committed, the repaired checkpoint will be a new child of `a007cbbe8c8e6fc147c866cc2be67547c96bdad5`. The harness should verify that `a007cbbe...` is an ancestor and that no production Rust path changed after a007. Production reconstructed blobs must remain:

- `crates/te1-engine/src/main.rs`: `7e2e5816b860be9ed7914f61bd636ecacfa01564`
- `crates/te1-eval/src/lib.rs`: `5626d3e620285cb4e966f41f50ed4886f2735274`

Release binary must remain SHA-256:

`91abf1f8b094596835e86de92c675e64e6e34674b74d7349c5ead4602a85d725`

## Repair scope

Authorized changes only:

- `scripts/r3_attribution_campaign.py`
- `python/tests/test_r3_attribution_campaign.py`
- diagnostic-only attribution identity/schema/contract files if required

Do not modify production Rust, search, evaluator arithmetic, NNUE implementation, openings, or gameplay parameters.

If state schema changes, fail closed on old incompatible state. There are no strength games to migrate. Preserve historical non-strength smoke evidence rather than silently rewriting it.

## Reverification philosophy

Do not repeat the already-proven 12-position engine preservation replay unless production Rust changes. After the harness-only repair, prove instead that production blobs are unchanged and the rebuilt release binary still hashes to `91abf1f8...`.

Freshly test the repaired harness with malicious evaluator identities, wrong architecture, wrong network, stale/fake source identity, relevant-path dirty state, source/tree drift on resume, completed-game and half-pair resume, corrupt state, opening/config drift, and a fresh non-strength smoke with zero replay on immediate resume.

Require another independent Rival review before canonicalization/export.
