from __future__ import annotations
import json
from pathlib import Path
from te1_b1.features import encode_fen


def test_feature_fixtures_match_validated_rust_contract():
    path = Path(__file__).parent / "fixtures" / "feature-fixtures.jsonl"
    for line in path.read_text().splitlines():
        row = json.loads(line)
        white, black, _ = encode_fen(row["fen"])
        assert white == row["white_features"]
        assert black == row["black_features"]
