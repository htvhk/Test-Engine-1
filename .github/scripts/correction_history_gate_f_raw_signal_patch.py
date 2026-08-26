#!/usr/bin/env python3
"""Authenticate Gate D source and apply decision-neutral Gate F1 instrumentation."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

FEATURE_BASE = "0c39989d17e1de7aae54e3db3b23039f1ae12990"
SOURCE_COMMIT = "9fd558f6c37f1843d2b3444900e757c36f9df353"
SOURCE_TREE = "10ae3046ffbd577ac525980c52f718e641f6b0bb"
SEARCH = Path("crates/te1-search/src/lib.rs")
SEARCH_BLOB = "dfdc4000a2c986732f39a9901cdfea286e1489d8"
SEARCH_SHA256 = "12d6c0ba791ddab9e3a9333cb9ca32338ef62462fb0bd3bf242749b48eff677c"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if git("rev-parse", f"{SOURCE_COMMIT}^{{tree}}") != SOURCE_TREE:
        raise SystemExit("authorized source tree drift")
    subprocess.run(["git", "merge-base", "--is-ancestor", FEATURE_BASE, "HEAD"], check=True)
    if git("hash-object", str(SEARCH)) != SEARCH_BLOB:
        raise SystemExit("authorized search blob drift")
    if hashlib.sha256(SEARCH.read_bytes()).hexdigest() != SEARCH_SHA256:
        raise SystemExit("authorized search SHA-256 drift")

    text = SEARCH.read_text(encoding="utf-8")
    text = replace_once(text, "use std::collections::hash_map::DefaultHasher;", "use std::collections::hash_map::DefaultHasher;\nuse std::collections::BTreeMap;", "map import")
    text = replace_once(text, "const MAX_SEARCH_TIME: Duration = Duration::from_secs(24 * 60 * 60);", "const MAX_SEARCH_TIME: Duration = Duration::from_secs(24 * 60 * 60);\nstatic CORRECTION_RAW_PROFILE: AtomicBool = AtomicBool::new(false);\n\n/// Temporary Gate F1 collection seam; never consumed by production search decisions.\npub fn set_correction_raw_profile(enabled: bool) { CORRECTION_RAW_PROFILE.store(enabled, Ordering::Relaxed); }", "profile seam")
    text = replace_once(text, "    pub hashfull_per_mille: u16,\n}", "    pub hashfull_per_mille: u16,\n    pub correction_raw_signal: CorrectionRawSignal,\n}\n\n#[derive(Debug, Clone, Default, Serialize, PartialEq, Eq)]\npub struct CorrectionRawSignal {\n    pub inspected: u64,\n    pub suppressed: BTreeMap<String, u64>,\n    pub samples: Vec<CorrectionRawSample>,\n}\n\n#[derive(Debug, Clone, Serialize, PartialEq, Eq)]\npub struct CorrectionRawSample {\n    pub error: i32,\n    pub depth: i16,\n    pub bound: String,\n    pub side: String,\n    pub pawn_key: String,\n}\n\nimpl CorrectionRawSignal {\n    fn suppress(&mut self, reason: &str) {\n        *self.suppressed.entry(reason.to_owned()).or_default() += 1;\n    }\n    fn merge_from(&mut self, mut other: Self) {\n        self.inspected += other.inspected;\n        for (reason, count) in other.suppressed { *self.suppressed.entry(reason).or_default() += count; }\n        self.samples.append(&mut other.samples);\n    }\n}", "result profile")
    text = replace_once(text, "    seldepth: u16,\n}", "    seldepth: u16,\n    correction_raw_signal: CorrectionRawSignal,\n}", "worker profile")
    text = replace_once(text, "        hashfull_per_mille: table.hashfull_per_mille(),\n", "        hashfull_per_mille: table.hashfull_per_mille(),\n        correction_raw_signal: aggregate.correction_raw_signal,\n", "result transfer")
    text = replace_once(text, "            total.seldepth = total.seldepth.max(output.stats.seldepth);\n            total\n", "            total.seldepth = total.seldepth.max(output.stats.seldepth);\n            total.correction_raw_signal.merge_from(output.stats.correction_raw_signal.clone());\n            total\n", "aggregate")
    text = replace_once(text, "        let in_check = !position.board().checkers().is_empty();\n        let static_eval = if self.options.use_null_move_pruning {", "        let in_check = !position.board().checkers().is_empty();\n        let diagnostic_key = CORRECTION_RAW_PROFILE.load(Ordering::Relaxed).then(|| PawnStructureKey::from_board(position.board()));\n        let static_eval = if self.options.use_null_move_pruning || CORRECTION_RAW_PROFILE.load(Ordering::Relaxed) {", "raw eval reuse seam")
    text = replace_once(text, "            i32::MIN\n        };\n        let can_try_null = null_move_eligible(", "            i32::MIN\n        };\n        let diagnostic_raw_eval = CORRECTION_RAW_PROFILE.load(Ordering::Relaxed).then_some(RawStaticEval(static_eval));\n        let can_try_null = null_move_eligible(", "raw diagnostic type")
    text = replace_once(text, "#[derive(Debug, Clone, Copy, Default)]\nstruct WorkerStats", "#[derive(Debug, Clone, Default)]\nstruct WorkerStats", "non-Copy worker stats")
    text = text.replace("stats: self.stats,", "stats: self.stats.clone(),")
    text = text.replace("completed.stats = self.stats;", "completed.stats = self.stats.clone();")
    text = replace_once(text, "        if !self.aborted {\n            self.table.store(\n                key,\n                depth,\n                score_to_table(best_score, ply),\n                classify_bound(best_score, original_alpha, beta),\n                best_move,\n            );\n        }\n        best_score\n", "        if !self.aborted {\n            let bound = classify_bound(best_score, original_alpha, beta);\n            self.table.store(key, depth, score_to_table(best_score, ply), bound, best_move);\n            if CORRECTION_RAW_PROFILE.load(Ordering::Relaxed) {\n                self.stats.correction_raw_signal.inspected += 1;\n                let best = best_move.to_move().map_or(CorrectionBestMove::Absent, |mv| {\n                    if mv.promotion.is_some() { CorrectionBestMove::Promotion }\n                    else if is_capture(position.board(), mv) { CorrectionBestMove::Capture }\n                    else { CorrectionBestMove::Quiet }\n                });\n                let facts = CorrectionUpdateFacts { completed: true, aborted_or_stopped: false, node_kind: CorrectionNodeKind::MainSearch, null_subtree, in_check, result_kind: CorrectionResultKind::Ordinary, raw_eval: diagnostic_raw_eval, search_score: best_score, bound, best_move: best };\n                if let Some(error) = correction_update_error(facts) {\n                    let pawn_key = diagnostic_key.expect(\"diagnostic key must exist\");\n                    self.stats.correction_raw_signal.samples.push(CorrectionRawSample { error, depth, bound: format!(\"{bound:?}\"), side: if pawn_key.side_to_move == 0 { \"White\" } else { \"Black\" }.to_owned(), pawn_key: format!(\"{:016x}:{:016x}:{}\", pawn_key.white_pawns, pawn_key.black_pawns, pawn_key.side_to_move) });\n                } else {\n                    let reason = if null_subtree { \"synthetic_null\" } else if in_check { \"in_check\" } else if diagnostic_raw_eval.is_none() { \"missing_raw_eval\" } else if best != CorrectionBestMove::Quiet && !(best == CorrectionBestMove::Absent && bound == Bound::Upper) { \"unsafe_best_move\" } else if best_score.abs() >= MATE_SCORE - MAX_PLY as i32 || diagnostic_raw_eval.is_some_and(|raw| raw.0.abs() >= MATE_SCORE - MAX_PLY as i32) { \"special_score\" } else { \"direction_inconsistent_bound\" };\n                    self.stats.correction_raw_signal.suppress(reason);\n                }\n            }\n        }\n        best_score\n", "completion instrumentation")
    SEARCH.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
