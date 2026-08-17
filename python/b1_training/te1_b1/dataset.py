from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import torch

from .features import MAX_ACTIVE_FEATURES, encode_fen

CATEGORY_FIELDS = ("phase", "material_bucket", "tacticality", "source_kind")
CP_TARGET_CLIP = 2_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_wdl(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("teacher_wdl must be a length-3 list")
    result = [float(x) for x in value]
    if any(not math.isfinite(x) or x < 0.0 for x in result):
        raise ValueError("teacher_wdl contains invalid probability")
    total = sum(result)
    if total <= 0.0 or abs(total - 1.0) > 1e-5:
        raise ValueError(f"teacher_wdl does not sum to one: {total}")
    return result


def prepare_split(input_path: Path, output_path: Path, expected_split: str) -> dict[str, object]:
    white: list[list[int]] = []
    black: list[list[int]] = []
    stm: list[int] = []
    teacher_wdl: list[list[float]] = []
    cp_norm: list[float] = []
    cp_raw: list[int] = []
    cp_target: list[int] = []
    result_class: list[int] = []
    category_values: dict[str, list[str]] = {field: [] for field in CATEGORY_FIELDS}
    position_ids: set[str] = set()
    observation_keys: set[str] = set()
    game_ids: set[str] = set()

    with gzip.open(input_path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("split") != expected_split:
                raise ValueError(f"split mismatch at line {line_number}")
            if row.get("teacher_pov") != "side-to-move":
                raise ValueError(f"teacher POV mismatch at line {line_number}")
            fen = str(row["fen"])
            wf, bf, white_to_move = encode_fen(fen)
            if len(wf) != MAX_ACTIVE_FEATURES or len(bf) != MAX_ACTIVE_FEATURES:
                raise AssertionError("feature encoder produced wrong width")
            row_stm = str(row["side_to_move"])
            if white_to_move != (row_stm == "w"):
                raise ValueError(f"FEN/side_to_move mismatch at line {line_number}")
            pid = str(row["position_id"])
            obs = str(row["nnue_observation_key"])
            gid = str(row["game_id"])
            if pid in position_ids or obs in observation_keys:
                raise ValueError(f"duplicate identity within split at line {line_number}")
            position_ids.add(pid)
            observation_keys.add(obs)
            game_ids.add(gid)
            cp = int(row["teacher_cp"])
            clipped_cp = max(-CP_TARGET_CLIP, min(CP_TARGET_CLIP, cp))
            wdl = _strict_wdl(row["teacher_wdl"])
            raw_result = row.get("game_result_for_side_to_move")
            if raw_result is None:
                rclass = -1
            else:
                numeric = int(raw_result)
                if numeric not in (-1, 0, 1):
                    raise ValueError(f"invalid game result at line {line_number}")
                rclass = {1: 0, 0: 1, -1: 2}[numeric]
            white.append(wf)
            black.append(bf)
            stm.append(int(white_to_move))
            teacher_wdl.append(wdl)
            cp_raw.append(cp)
            cp_target.append(clipped_cp)
            cp_norm.append(math.tanh(clipped_cp / 600.0))
            result_class.append(rclass)
            for field in CATEGORY_FIELDS:
                category_values[field].append(str(row.get(field, "unknown")))

    if not white:
        raise ValueError(f"no records in {input_path}")
    arrays: dict[str, np.ndarray] = {
        "white": np.asarray(white, dtype=np.int32),
        "black": np.asarray(black, dtype=np.int32),
        "stm": np.asarray(stm, dtype=np.uint8),
        "teacher_wdl": np.asarray(teacher_wdl, dtype=np.float32),
        "teacher_cp_norm": np.asarray(cp_norm, dtype=np.float32),
        "teacher_cp": np.asarray(cp_target, dtype=np.int32),
        "teacher_cp_raw": np.asarray(cp_raw, dtype=np.int32),
        "result_class": np.asarray(result_class, dtype=np.int8),
    }
    category_maps: dict[str, dict[str, int]] = {}
    for field, values in category_values.items():
        mapping = {value: index for index, value in enumerate(sorted(set(values)))}
        category_maps[field] = mapping
        arrays[field] = np.asarray([mapping[value] for value in values], dtype=np.int16)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp.npz")
    np.savez_compressed(temporary, **arrays)
    temporary.replace(output_path)
    return {
        "status": "PASS",
        "split": expected_split,
        "rows": len(white),
        "unique_position_ids": len(position_ids),
        "unique_observation_keys": len(observation_keys),
        "unique_game_ids": len(game_ids),
        "input_sha256": sha256_file(input_path),
        "output_sha256": sha256_file(output_path),
        "category_maps": category_maps,
        "result_available": int(sum(x >= 0 for x in result_class)),
        "cp_target_clip": CP_TARGET_CLIP,
        "cp_clipped_rows": int(sum(abs(x) > CP_TARGET_CLIP for x in cp_raw)),
    }


def audit_raw_identity_isolation(paths: dict[str, Path]) -> dict[str, object]:
    """Re-prove global identity uniqueness and whole-game split isolation before training."""
    position_owner: dict[str, str] = {}
    observation_owner: dict[str, str] = {}
    game_owner: dict[str, str] = {}
    counts: dict[str, int] = {}
    for split in ("train", "development", "reserve"):
        path = paths[split]
        count = 0
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("split") != split:
                    raise ValueError(f"raw split mismatch for {split} at line {line_number}")
                for field, owners in (("position_id", position_owner), ("nnue_observation_key", observation_owner)):
                    value = str(row[field])
                    previous = owners.get(value)
                    if previous is not None:
                        raise ValueError(f"global duplicate {field}: {value} in {previous} and {split}")
                    owners[value] = split
                gid = str(row["game_id"])
                previous_game_split = game_owner.get(gid)
                if previous_game_split is not None and previous_game_split != split:
                    raise ValueError(f"whole-game split isolation failure: {gid} in {previous_game_split} and {split}")
                game_owner[gid] = split
                count += 1
        counts[split] = count
    return {
        "status": "PASS",
        "rows": counts,
        "unique_position_ids": len(position_owner),
        "unique_observation_keys": len(observation_owner),
        "unique_game_ids": len(game_owner),
        "whole_game_split_isolation": True,
    }


class PreparedDataset:
    def __init__(self, path: Path):
        self.path = path
        with np.load(path, allow_pickle=False) as data:
            self.tensors = {
                "white": torch.from_numpy(data["white"].astype(np.int64, copy=True)),
                "black": torch.from_numpy(data["black"].astype(np.int64, copy=True)),
                "stm": torch.from_numpy(data["stm"].astype(np.bool_, copy=True)),
                "teacher_wdl": torch.from_numpy(data["teacher_wdl"].astype(np.float32, copy=True)),
                "teacher_cp_norm": torch.from_numpy(data["teacher_cp_norm"].astype(np.float32, copy=True)),
                "teacher_cp": torch.from_numpy(data["teacher_cp"].astype(np.int32, copy=True)),
                "teacher_cp_raw": torch.from_numpy(data["teacher_cp_raw"].astype(np.int32, copy=True)),
                "result_class": torch.from_numpy(data["result_class"].astype(np.int64, copy=True)),
            }
            for field in CATEGORY_FIELDS:
                self.tensors[field] = torch.from_numpy(data[field].astype(np.int64, copy=True))
        self.length = int(self.tensors["white"].shape[0])

    def __len__(self) -> int:
        return self.length

    def batch_indices(self, batch_size: int, *, shuffle_seed: int | None) -> list[np.ndarray]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if shuffle_seed is None:
            order = np.arange(self.length, dtype=np.int64)
        else:
            order = np.random.default_rng(shuffle_seed).permutation(self.length).astype(np.int64)
        batches = []
        for start in range(0, self.length, batch_size):
            chunk = order[start : start + batch_size]
            if len(chunk) < batch_size:
                pad = np.resize(order[: max(1, batch_size - len(chunk))], batch_size - len(chunk))
                chunk = np.concatenate((chunk, pad))
            batches.append(chunk)
        return batches

    def get_batch(self, indices: np.ndarray, valid_count: int | None = None) -> dict[str, torch.Tensor]:
        index_tensor = torch.from_numpy(indices)
        result = {name: values.index_select(0, index_tensor) for name, values in self.tensors.items()}
        mask = torch.ones(len(indices), dtype=torch.bool)
        if valid_count is not None and valid_count < len(indices):
            mask[valid_count:] = False
        result["sample_mask"] = mask
        return result
