from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from .features import MAX_ACTIVE_FEATURES, NUM_FEATURES
from .model import CandidateSpec, Te1Nnue

MAGIC = b"TE1NN001"
FORMAT_VERSION = 1
DTYPE_INT16 = 1


@dataclass(frozen=True)
class QuantizedTensor:
    name: str
    shape: tuple[int, ...]
    scale: float
    values: np.ndarray


def _quantize(name: str, tensor: torch.Tensor) -> QuantizedTensor:
    array = tensor.detach().float().cpu().numpy().astype(np.float32, copy=False)
    maximum = float(np.max(np.abs(array))) if array.size else 0.0
    scale = maximum / 32767.0 if maximum > 0.0 else 1.0 / 32767.0
    quantized = np.rint(array / scale).clip(-32767, 32767).astype("<i2")
    return QuantizedTensor(name, tuple(int(x) for x in array.shape), float(scale), quantized)


def tensors_for_export(model: Te1Nnue) -> list[QuantizedTensor]:
    return [
        _quantize("feature.weight", model.feature.weight[:NUM_FEATURES]),
        _quantize("feature_bias", model.feature_bias),
        _quantize("hidden.weight", model.hidden.weight),
        _quantize("hidden.bias", model.hidden.bias),
        _quantize("wdl_head.weight", model.wdl_head.weight),
        _quantize("wdl_head.bias", model.wdl_head.bias),
        _quantize("cp_head.weight", model.cp_head.weight),
        _quantize("cp_head.bias", model.cp_head.bias),
    ]


def export_te1nn(model: Te1Nnue, path: Path) -> dict[str, object]:
    spec = model.spec
    metadata = {
        "candidate": spec.to_dict(),
        "feature_set": "TE1-K32-RP11-v1",
        "max_active_features": MAX_ACTIVE_FEATURES,
        "num_features": NUM_FEATURES,
    }
    metadata_bytes = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
    tensors = tensors_for_export(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(MAGIC)
        handle.write(struct.pack("<I", FORMAT_VERSION))
        handle.write(struct.pack("<I", len(metadata_bytes)))
        handle.write(metadata_bytes)
        handle.write(struct.pack("<I", len(tensors)))
        for tensor in tensors:
            name = tensor.name.encode("utf-8")
            handle.write(struct.pack("<HBB", len(name), DTYPE_INT16, len(tensor.shape)))
            handle.write(name)
            for dimension in tensor.shape:
                handle.write(struct.pack("<I", dimension))
            handle.write(struct.pack("<fQ", tensor.scale, tensor.values.nbytes))
            handle.write(tensor.values.tobytes(order="C"))
    return {
        "candidate": spec.to_dict(),
        "metadata": metadata,
        "tensors": [
            {"name": t.name, "shape": list(t.shape), "scale": t.scale, "elements": int(t.values.size)}
            for t in tensors
        ],
    }


def load_te1nn_as_model(path: Path) -> Te1Nnue:
    raw = memoryview(path.read_bytes())
    offset = 0
    def take(n: int) -> memoryview:
        nonlocal offset
        if n < 0 or offset + n > len(raw):
            raise ValueError("truncated TE1NN file")
        value = raw[offset : offset + n]
        offset += n
        return value
    if bytes(take(8)) != MAGIC:
        raise ValueError("bad magic")
    version, = struct.unpack("<I", take(4))
    if version != FORMAT_VERSION:
        raise ValueError("unsupported version")
    metadata_len, = struct.unpack("<I", take(4))
    metadata = json.loads(bytes(take(metadata_len)))
    if metadata.get("feature_set") != "TE1-K32-RP11-v1" or int(metadata.get("max_active_features", -1)) != MAX_ACTIVE_FEATURES or int(metadata.get("num_features", -1)) != NUM_FEATURES:
        raise ValueError("incompatible TE1NN feature metadata")
    spec = CandidateSpec(**metadata["candidate"]); spec.validate()
    model = Te1Nnue(spec)
    tensor_count, = struct.unpack("<I", take(4))
    if tensor_count != 8:
        raise ValueError(f"unexpected tensor count: {tensor_count}")
    tensors: dict[str, np.ndarray] = {}
    for _ in range(tensor_count):
        name_len, dtype, rank = struct.unpack("<HBB", take(4))
        if dtype != DTYPE_INT16:
            raise ValueError("unsupported dtype")
        name = bytes(take(name_len)).decode("utf-8")
        if name in tensors:
            raise ValueError(f"duplicate tensor name: {name}")
        shape = tuple(struct.unpack("<I", take(4))[0] for _ in range(rank))
        scale, byte_len = struct.unpack("<fQ", take(12))
        if not math.isfinite(scale) or scale <= 0.0:
            raise ValueError(f"invalid quantization scale for {name}")
        count = math.prod(shape)
        if byte_len != count * 2:
            raise ValueError("tensor byte length mismatch")
        q = np.frombuffer(take(byte_len), dtype="<i2").astype(np.float32).reshape(shape)
        tensors[name] = q * float(scale)
    if offset != len(raw):
        raise ValueError("unexpected trailing bytes")
    expected = {
        "feature.weight", "feature_bias", "hidden.weight", "hidden.bias",
        "wdl_head.weight", "wdl_head.bias", "cp_head.weight", "cp_head.bias",
    }
    if set(tensors) != expected:
        raise ValueError("unexpected tensor set")
    expected_shapes = {
        "feature.weight": (NUM_FEATURES, spec.width),
        "feature_bias": (spec.width,),
        "hidden.weight": (spec.hidden, 2 * spec.width),
        "hidden.bias": (spec.hidden,),
        "wdl_head.weight": (3, spec.hidden),
        "wdl_head.bias": (3,),
        "cp_head.weight": (1, spec.hidden),
        "cp_head.bias": (1,),
    }
    for name, expected_shape in expected_shapes.items():
        if tuple(tensors[name].shape) != expected_shape:
            raise ValueError(f"shape mismatch for {name}: {tensors[name].shape} != {expected_shape}")
    state = model.state_dict()
    with torch.no_grad():
        state["feature.weight"][:NUM_FEATURES].copy_(torch.from_numpy(tensors["feature.weight"]))
        state["feature.weight"][NUM_FEATURES].zero_()
        state["feature_bias"].copy_(torch.from_numpy(tensors["feature_bias"]))
        state["hidden.weight"].copy_(torch.from_numpy(tensors["hidden.weight"]))
        state["hidden.bias"].copy_(torch.from_numpy(tensors["hidden.bias"]))
        state["wdl_head.weight"].copy_(torch.from_numpy(tensors["wdl_head.weight"]))
        state["wdl_head.bias"].copy_(torch.from_numpy(tensors["wdl_head.bias"]))
        state["cp_head.weight"].copy_(torch.from_numpy(tensors["cp_head.weight"]))
        state["cp_head.bias"].copy_(torch.from_numpy(tensors["cp_head.bias"]))
    model.load_state_dict(state)
    model.eval()
    return model


@torch.no_grad()
def compare_float_and_quantized(
    float_model: Te1Nnue,
    quantized_model: Te1Nnue,
    white: torch.Tensor,
    black: torch.Tensor,
    stm: torch.Tensor,
) -> dict[str, float]:
    float_model.eval(); quantized_model.eval()
    fl, fc = float_model(white, black, stm)
    ql, qc = quantized_model(white, black, stm)
    fp = F.softmax(fl.float(), dim=1)
    qp = F.softmax(ql.float(), dim=1)
    fcp = 600.0 * torch.atanh(fc.float().clamp(-0.999999, 0.999999))
    qcp = 600.0 * torch.atanh(qc.float().clamp(-0.999999, 0.999999))
    cp_error = (fcp - qcp).abs()
    return {
        "max_wdl_probability_error": float((fp - qp).abs().max().item()),
        "mean_wdl_probability_error": float((fp - qp).abs().mean().item()),
        "max_cp_error": float(cp_error.max().item()),
        "mean_cp_error": float(cp_error.mean().item()),
    }
