from __future__ import annotations
import importlib.util, os, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/".github"/"scripts"/"nmp_r2_time_proof_2048.py"
SPEC=importlib.util.spec_from_file_location("te1_nmp_r2_time_proof",SCRIPT)
if SPEC is None or SPEC.loader is None: raise RuntimeError("cannot load R2 proof")
proof=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(proof)
PRIOR_FINAL='{"COMBINED_SUPPORTIVE":{"games":2048,"mode":"COMBINED","on_score":1088.0,"on_score_pct":53.125,"on_wdl":{"draw":604,"loss":658,"win":786},"paired_statistics":{"ci95_lower":0.5138170025532812,"ci95_upper":0.5486829974467188,"pairs":1024,"sample_sd":0.28462559653917036,"score":0.53125,"score_pct":53.125,"standard_error":0.008894549891849074},"penta":{"0.0":93,"0.5":191,"1.0":372,"1.5":231,"2.0":137}},"NODES":{"games":1024,"mode":"NODES","on_score":556.0,"on_score_pct":54.296875,"on_wdl":{"draw":292,"loss":322,"win":410},"paired_statistics":{"ci95_lower":0.5183795622080772,"ci95_upper":0.5675579377919228,"pairs":512,"sample_sd":0.28387756621955834,"score":0.54296875,"score_pct":54.296875,"standard_error":0.01254573450628643},"penta":{"0.0":39,"0.5":96,"1.0":195,"1.5":102,"2.0":80}},"TIME":{"games":1024,"mode":"TIME","on_score":532.0,"on_score_pct":51.953125,"on_wdl":{"draw":312,"loss":336,"win":376},"paired_statistics":{"ci95_lower":0.49483035606669495,"ci95_upper":0.5442321439333051,"pairs":512,"sample_sd":0.2851671926934263,"score":0.51953125,"score_pct":51.953125,"standard_error":0.012602728482840787},"penta":{"0.0":54,"0.5":95,"1.0":177,"1.5":129,"2.0":57}},"binary_sha256":"8d3920a6d244b040874c8fe95f9ea04a7c6fb2288d0680f9c88bbd3156710136","campaign_id":"alpha26-nmp-r1-proof-2048g-v1","contract_sha256":"dcc7fc6a4e4ce201c2287ae0fea91cb78b5042467f439ffe6bc7da6c2ca67e55","decision":"INCONCLUSIVE","default_on_authorized":false,"nodes_selection_sha256":"404792ea5969adf0f3224dab4632a51f9e9d8b53626a501ea6eed0af0968b504","openings_file_sha256":"fa595beae1d303c63815ebd0b06c36ab54c0c509b35d7f5e10977fa78d6843a2","operational_failures":0,"prior_512_games_pooled":false,"run_attempt":1,"run_id":"32240977886","schema":"TE1-ALPHA26-NMP-R1-PROOF-FINAL-v1","source_head":"051f00af1ad88b0bc742eda0dbcc544c68d35824","source_tree":"a888d393839e934c3d2bfb523c1a00be4e3dbf27","time_selection_sha256":"1dd11e71752a8409150086f5b327b4079007000ca458dd8b3866ffa4ef67b4da","total_games":2048,"total_pairs":1024}\n'
PRIOR_PREFLIGHT='{"baseline_commit":"7820b54d511afbf5dd2d38a3f686af97c14de639","baseline_tree":"465e442fb26f8ad5ee6a793f35edb22d7f66f8b0","binary_sha256":"8d3920a6d244b040874c8fe95f9ea04a7c6fb2288d0680f9c88bbd3156710136","book_git_blob_sha1":"b851fc8c484b9e36b178131a7f47269bfdfacd39","book_sha256":"c20483ecca07676c10ad3fb5acad6370fc75a5e6bf3935a7255bb2a73fe8deac","campaign_id":"alpha26-nmp-r1-proof-2048g-v1","contract_sha256":"dcc7fc6a4e4ce201c2287ae0fea91cb78b5042467f439ffe6bc7da6c2ca67e55","nodes_selection_sha256":"404792ea5969adf0f3224dab4632a51f9e9d8b53626a501ea6eed0af0968b504","openings_file_sha256":"fa595beae1d303c63815ebd0b06c36ab54c0c509b35d7f5e10977fa78d6843a2","run_attempt":1,"run_id":"32240977886","schema":"TE1-ALPHA26-NMP-R1-PROOF-PREFLIGHT-v1","source_head":"051f00af1ad88b0bc742eda0dbcc544c68d35824","source_tree":"a888d393839e934c3d2bfb523c1a00be4e3dbf27","status":"PASS","time_selection_sha256":"1dd11e71752a8409150086f5b327b4079007000ca458dd8b3866ffa4ef67b4da"}\n'

class ContractTests(unittest.TestCase):
    def test_contract_is_frozen(self):
        x,d=proof.contract()
        self.assertEqual(len(d),64)
        self.assertEqual(x["campaign_id"],"alpha26-nmp-r2-time-proof-2048g-v1")
        self.assertEqual(x["arms"]["TIME"]["pairs"],1024)
        self.assertEqual(x["arms"]["TIME"]["games"],2048)
        self.assertEqual(x["opening_selection"]["time_confirmatory"],{"pairs":1024,"valid_rank_start":1280,"valid_rank_stop_exclusive":2304})
        self.assertTrue(x["feature"]["default_remains_off_during_campaign"])
        self.assertTrue(x["feature"]["parameters_frozen_from_r1"])
        self.assertEqual(x["rerun_policy"]["admissible_run_attempt"],1)
        self.assertIn("never statistically pooled",x["r1_prerequisite"]["use"])

    def test_first_attempt_only(self):
        with mock.patch.dict(os.environ,{"GITHUB_RUN_ID":"456","GITHUB_RUN_ATTEMPT":"1"},clear=False):
            self.assertEqual(proof.R.require_first_attempt(),("456",1))
        with mock.patch.dict(os.environ,{"GITHUB_RUN_ID":"456","GITHUB_RUN_ATTEMPT":"2"},clear=False):
            with self.assertRaises(proof.ProofError): proof.R.require_first_attempt()

class PriorTests(unittest.TestCase):
    def test_exact_r1_evidence_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/"final.json"; b=Path(td)/"preflight.json"
            a.write_text(PRIOR_FINAL,encoding="utf-8"); b.write_text(PRIOR_PREFLIGHT,encoding="utf-8")
            f=proof.prior(a,b)
            self.assertEqual(f["run_id"],"32240977886")
            self.assertGreater(f["NODES"]["paired_statistics"]["ci95_lower"],.5)

    def test_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/"final.json"
            a.write_text(PRIOR_FINAL.replace('"on_score":556.0','"on_score":555.5',1),encoding="utf-8")
            with self.assertRaises(proof.ProofError): proof.prior(a)

class DecisionTests(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(proof.decision({"ci95_lower":.501,"ci95_upper":.55},{"ci95_lower":.518}),"PASS_DEFAULT_ON")
    def test_inconclusive(self):
        self.assertEqual(proof.decision({"ci95_lower":.49,"ci95_upper":.55},{"ci95_lower":.518}),"INCONCLUSIVE")
    def test_fail(self):
        self.assertEqual(proof.decision({"ci95_lower":.44,"ci95_upper":.499},{"ci95_lower":.518}),"FAIL_NMP")
    def test_invalid_prior_fails_closed(self):
        with self.assertRaises(proof.ProofError): proof.decision({"ci95_lower":.51,"ci95_upper":.55},{"ci95_lower":.5})

class EvidenceTests(unittest.TestCase):
    def test_terminal_first_r1_reconciliation_remains_installed(self):
        self.assertIs(proof.R.reconcile_game,proof.E.reconcile_game_terminal_first)
    def test_fresh_opening_slice(self):
        x,cs=proof.contract(); v=[f"fen-{i}" for i in range(2304)]; z=proof.freeze(v,x,cs)
        self.assertEqual(z["pairs"],1024); self.assertEqual(z["valid_rank_range"],[1280,2304])
        self.assertEqual(z["openings"][0]["valid_rank"],1280); self.assertEqual(z["openings"][-1]["valid_rank"],2303)
    def test_historical_overlap_rejected(self):
        x,cs=proof.contract(); v=[f"fen-{i}" for i in range(2304)]; v[1280]=v[0]
        with self.assertRaises(proof.ProofError): proof.freeze(v,x,cs)

if __name__=="__main__": unittest.main()
