"""bench_openvino.py — OpenVINO inference benchmark + optimization harness (Intel Pantherlake bonus).

Why this exists
---------------
The C1 deploy runs the policy through OpenVINO on the Intel Pantherlake box and must hold the
control rate (default 30 Hz) — i.e. single-inference latency has to fit inside the loop budget
(camera read + inference + UDP send). This tool measures that latency and exercises the OpenVINO
optimization knobs that buy it back:

  * PERFORMANCE_HINT=LATENCY  — tune for one-shot latency, not batch throughput (real-time control).
  * model caching (CACHE_DIR) — cut cold-start compile time on repeat launches.
  * NNCF INT8 post-training quantization — smaller/faster graph on CPU/iGPU/NPU.
  * preprocess folding (PrePostProcessor) — push u8 NHWC -> f32 NCHW + normalize INTO the graph,
    so the host loop hands the model the raw camera frame and does zero per-step numpy work.

It benches a real export when given ``--model <ir.xml|model.onnx>`` (the Pantherlake path), and
otherwise builds a representative *synthetic* vision-encoder + state-MLP stand-in so the whole
optimize -> quantize -> measure pipeline is verifiable now, before the trained policy exists.
Synthetic numbers validate the pipeline only — re-measure on Pantherlake with the real export.

Heavy deps (openvino, nncf) are imported lazily so this module imports anywhere; the structural
``--self-test`` and the benches require ``pip install openvino nncf``.

Usage
-----
    # pipeline check on this machine (builds synthetic model, CPU, FP32+INT8):
    python -m src.inference.bench_openvino --self-test
    python -m src.inference.bench_openvino --devices CPU --iters 300

    # real export on Pantherlake, sweep iGPU + NPU, hold 30 Hz, write a report:
    python -m src.inference.bench_openvino --model ./exports/c1_smolvla_ov/openvino_model.xml \
        --devices GPU,NPU,CPU --target-hz 30 --json bench_results.json
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Optional

import numpy as np

# Defaults for the synthetic stand-in (a vision encoder + state MLP -> action[8]).
DEFAULT_IMAGE_SIZE = 96
DEFAULT_STATE_DIM = 8
DEFAULT_ACTION_DIM = 8
DEFAULT_CONV_CHANNELS = 32
DEFAULT_HIDDEN = 512
IMAGE_INPUT_NAME = "image"
STATE_INPUT_NAME = "state"


# --- numpy/openvino type glue -----------------------------------------------
_OV_TO_NP = {"f32": np.float32, "f16": np.float16, "u8": np.uint8, "i8": np.int8,
             "i32": np.int32, "i64": np.int64, "boolean": np.bool_}


def _np_dtype(ov_type) -> np.dtype:
    return np.dtype(_OV_TO_NP.get(ov_type.to_string(), np.float32))


def _static_shape(port) -> list[int]:
    """Static dims of a model port; dynamic dims (e.g. batch) collapse to 1."""
    dims = []
    for d in port.get_partial_shape():
        dims.append(d.get_length() if d.is_static else 1)
    return dims


def random_inputs_for_model(model) -> dict[str, np.ndarray]:
    """One random input per model input, keyed by name (used for calibration + dynamic input gen)."""
    rng = np.random.default_rng(0)
    out = {}
    for port in model.inputs:
        name = port.get_any_name()
        shape = _static_shape(port)
        dtype = _np_dtype(port.get_element_type())
        if np.issubdtype(dtype, np.integer):
            out[name] = rng.integers(0, 256, size=shape, dtype=dtype)
        else:
            out[name] = rng.standard_normal(size=shape).astype(dtype)
    return out


def random_inputs_for_compiled(compiled) -> list[np.ndarray]:
    """Positional random inputs matching a compiled model's input ports."""
    rng = np.random.default_rng(1)
    arrays = []
    for port in compiled.inputs:
        shape = _static_shape(port)
        dtype = _np_dtype(port.get_element_type())
        if np.issubdtype(dtype, np.integer):
            arrays.append(rng.integers(0, 256, size=shape, dtype=dtype))
        else:
            arrays.append(rng.standard_normal(size=shape).astype(dtype))
    return arrays


# --- model construction / loading -------------------------------------------
def build_synthetic_policy_model(image_size: int = DEFAULT_IMAGE_SIZE, channels: int = 3,
                                 state_dim: int = DEFAULT_STATE_DIM,
                                 action_dim: int = DEFAULT_ACTION_DIM,
                                 conv_channels: int = DEFAULT_CONV_CHANNELS,
                                 hidden: int = DEFAULT_HIDDEN, seed: int = 0):
    """A representative vision-encoder + state-MLP stand-in for the real VLA policy.

    image[1,C,H,W] -> conv/relu x2 -> global-avg-pool -> concat(state[1,D]) -> FC/relu -> action[1,A].
    Not the real policy — a proxy whose FLOPs are tunable so the optimization pipeline is exercised
    end to end. Scale ``conv_channels``/``hidden``/``image_size`` to approximate the real model.
    """
    import openvino as ov
    import openvino.opset15 as ops
    rs = np.random.RandomState(seed)

    img = ops.parameter([1, channels, image_size, image_size], ov.Type.f32, name=IMAGE_INPUT_NAME)
    st = ops.parameter([1, state_dim], ov.Type.f32, name=STATE_INPUT_NAME)

    k1 = ops.constant((rs.randn(conv_channels, channels, 3, 3) * 0.1).astype(np.float32))
    c1 = ops.relu(ops.convolution(img, k1, [2, 2], [1, 1], [1, 1], [1, 1]))
    k2 = ops.constant((rs.randn(conv_channels, conv_channels, 3, 3) * 0.1).astype(np.float32))
    c2 = ops.relu(ops.convolution(c1, k2, [2, 2], [1, 1], [1, 1], [1, 1]))
    pooled = ops.reduce_mean(c2, ops.constant(np.array([2, 3], dtype=np.int64)), False)  # [1,conv_channels]

    feat = ops.concat([pooled, st], 1)  # [1, conv_channels + state_dim]
    w1 = ops.constant((rs.randn(conv_channels + state_dim, hidden) * 0.05).astype(np.float32))
    h = ops.relu(ops.matmul(feat, w1, False, False))
    w2 = ops.constant((rs.randn(hidden, action_dim) * 0.05).astype(np.float32))
    y = ops.matmul(h, w2, False, False)

    model = ov.Model([y], [img, st], "synthetic_policy_proxy")
    return model


def load_model(core, path: str):
    return core.read_model(path)


def fold_image_preprocess(model, image_input_name: str = IMAGE_INPUT_NAME):
    """Fold u8 NHWC -> f32 NCHW + /255 normalize into the graph for the image input.

    The deploy loop reads HWC uint8 RGB straight off the D405; folding the type/layout/scale into
    the model means the host does no per-step conversion. Returns a new model accepting u8 NHWC.
    If the named image input is absent (e.g. a real export with different names), returns the model
    unchanged so callers can stay generic.
    """
    import openvino as ov
    from openvino.preprocess import PrePostProcessor
    if not any(i.get_any_name() == image_input_name for i in model.inputs):
        return model
    ppp = PrePostProcessor(model)
    ppp.input(image_input_name).tensor().set_element_type(ov.Type.u8).set_layout(ov.Layout("NHWC"))
    ppp.input(image_input_name).model().set_layout(ov.Layout("NCHW"))
    ppp.input(image_input_name).preprocess().convert_element_type(ov.Type.f32).scale(255.0)
    return ppp.build()


# --- quantization -----------------------------------------------------------
def quantize_int8(model, calib_size: int = 64):
    """NNCF post-training INT8 quantization with a synthetic calibration set."""
    import nncf
    items = [random_inputs_for_model(model) for _ in range(calib_size)]
    dataset = nncf.Dataset(items, lambda item: item)
    return nncf.quantize(model, dataset, subset_size=calib_size)


# --- compile + benchmark ----------------------------------------------------
def compile_optimized(core, model, device: str, cache_dir: Optional[str] = None,
                      hint: str = "LATENCY") -> tuple[Any, float]:
    """Compile with a real-time-control config; returns (compiled, compile_seconds)."""
    config = {"PERFORMANCE_HINT": hint}
    if cache_dir:
        config["CACHE_DIR"] = cache_dir
    t0 = time.perf_counter()
    compiled = core.compile_model(model, device, config)
    return compiled, time.perf_counter() - t0


def bench_compiled(compiled, warmup: int, iters: int) -> np.ndarray:
    """Time `iters` synchronous single-inference calls (ms each) on a reused InferRequest."""
    inputs = random_inputs_for_compiled(compiled)
    request = compiled.create_infer_request()
    for _ in range(warmup):
        request.infer(inputs)
    latencies = np.empty(iters, dtype=np.float64)
    for i in range(iters):
        t0 = time.perf_counter_ns()
        request.infer(inputs)
        latencies[i] = (time.perf_counter_ns() - t0) / 1e6
    return latencies


def summarize(latencies: np.ndarray, target_hz: float) -> dict[str, Any]:
    """Latency percentiles + achievable rate. holds_target uses p95 (conservative)."""
    p95 = float(np.percentile(latencies, 95))
    return {
        "iters": int(latencies.size),
        "mean_ms": float(latencies.mean()),
        "p50_ms": float(np.percentile(latencies, 50)),
        "p90_ms": float(np.percentile(latencies, 90)),
        "p95_ms": p95,
        "p99_ms": float(np.percentile(latencies, 99)),
        "min_ms": float(latencies.min()),
        "max_ms": float(latencies.max()),
        "mean_fps": float(1000.0 / latencies.mean()),
        "target_hz": float(target_hz),
        "holds_target_hz": bool(p95 > 0 and (1000.0 / p95) >= target_hz),
    }


def _run_one(core, model, label: str, device: str, args, cache_dir: Optional[str]) -> Optional[dict]:
    try:
        compiled, compile_s = compile_optimized(core, model, device, cache_dir=cache_dir)
        latencies = bench_compiled(compiled, args.warmup, args.iters)
    except Exception as exc:  # device unavailable / unsupported op — report, don't abort the sweep
        print(f"  [{device:>3}] {label:<18} SKIPPED ({type(exc).__name__}: {exc})")
        return None
    stats = summarize(latencies, args.target_hz)
    stats.update(device=device, variant=label, compile_s=round(compile_s, 3))
    hold = "OK " if stats["holds_target_hz"] else "MISS"
    print(f"  [{device:>3}] {label:<18} p50={stats['p50_ms']:6.2f}ms p95={stats['p95_ms']:6.2f}ms "
          f"p99={stats['p99_ms']:6.2f}ms  {stats['mean_fps']:6.1f} fps  "
          f"{hold} @ {args.target_hz:g}Hz  (compile {compile_s:.2f}s)")
    return stats


def run_benchmark(args) -> list[dict]:
    import openvino as ov  # noqa: F401  (ensures a clear error if openvino is missing)
    from openvino import Core
    core = Core()
    available = core.available_devices

    if args.model:
        base = load_model(core, args.model)
        source = f"export: {args.model}"
    else:
        base = build_synthetic_policy_model(image_size=args.image_size, hidden=args.hidden,
                                            conv_channels=args.conv_channels)
        source = (f"SYNTHETIC proxy (image={args.image_size}px conv={args.conv_channels} "
                  f"hidden={args.hidden}) — pipeline check only, not the real policy")

    print(f"model: {source}")
    print(f"available devices: {available}")
    devices = [d.strip().upper() for d in args.devices.split(",") if d.strip()]

    int8_model = None
    if args.int8:
        print(f"quantizing INT8 (NNCF PTQ, calib={args.calib_size}) ...")
        try:
            int8_model = quantize_int8(base, calib_size=args.calib_size)
        except Exception as exc:
            print(f"  INT8 quantization SKIPPED ({type(exc).__name__}: {exc})")

    folded = fold_image_preprocess(base) if args.fold_preprocess else None

    results: list[dict] = []
    for device in devices:
        if device not in available:
            print(f"  [{device:>3}] not available — skipping")
            continue
        cache_dir = args.cache_dir or None
        r = _run_one(core, base, "FP32", device, args, cache_dir)
        if r:
            results.append(r)
        if folded is not None:
            r = _run_one(core, folded, "FP32+foldprep", device, args, cache_dir)
            if r:
                results.append(r)
        if int8_model is not None:
            r = _run_one(core, int8_model, "INT8", device, args, cache_dir)
            if r:
                results.append(r)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"source": source, "available_devices": available, "results": results}, fh, indent=2)
        print(f"wrote {args.json}")
    return results


def _self_test() -> int:
    """Build a tiny synthetic model, run a few FP32 + INT8 iters on CPU, assert sane stats."""
    try:
        import openvino  # noqa: F401
        import nncf  # noqa: F401
    except ImportError as exc:
        print(f"[self-test] SKIP — {exc} (pip install openvino nncf to run)")
        return 0
    from openvino import Core
    core = Core()
    model = build_synthetic_policy_model(image_size=48, conv_channels=8, hidden=64)
    compiled, _ = compile_optimized(core, model, "CPU")
    lat = bench_compiled(compiled, warmup=2, iters=10)
    stats = summarize(lat, target_hz=30.0)
    assert stats["iters"] == 10 and np.isfinite(stats["mean_ms"]) and stats["mean_ms"] > 0, stats
    q = quantize_int8(model, calib_size=8)
    cq, _ = compile_optimized(core, q, "CPU")
    out = cq(random_inputs_for_compiled(cq))
    assert np.isfinite(list(out.values())[0]).all()
    folded = fold_image_preprocess(model)
    assert any(i.get_element_type().to_string() == "u8" for i in folded.inputs), "fold did not apply"
    print(f"[self-test] FP32 mean={stats['mean_ms']:.2f}ms INT8+fold compiled, schema=OK -> PASS")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true", help="Tiny CPU FP32+INT8 pipeline check, then exit.")
    p.add_argument("--model", type=str, default=None, help="OpenVINO IR (.xml) or ONNX export; omit for synthetic.")
    p.add_argument("--devices", type=str, default="CPU", help="Comma list, e.g. CPU,GPU,NPU.")
    p.add_argument("--iters", type=int, default=200, help="Timed inferences per variant.")
    p.add_argument("--warmup", type=int, default=20, help="Warmup inferences (not timed).")
    p.add_argument("--target-hz", type=float, default=30.0, help="Control rate the loop must hold.")
    p.add_argument("--int8", dest="int8", action="store_true", default=True, help="Run NNCF INT8 (default on).")
    p.add_argument("--no-int8", dest="int8", action="store_false", help="Skip INT8 quantization.")
    p.add_argument("--calib-size", type=int, default=64, help="NNCF calibration samples.")
    p.add_argument("--fold-preprocess", action="store_true", help="Also bench a preprocess-folded variant.")
    p.add_argument("--cache-dir", type=str, default=None, help="OpenVINO CACHE_DIR for compiled-model caching.")
    p.add_argument("--image-size", type=int, default=DEFAULT_IMAGE_SIZE, help="Synthetic image side (px).")
    p.add_argument("--conv-channels", type=int, default=DEFAULT_CONV_CHANNELS, help="Synthetic conv width.")
    p.add_argument("--hidden", type=int, default=DEFAULT_HIDDEN, help="Synthetic MLP hidden width.")
    p.add_argument("--json", type=str, default=None, help="Write results JSON to this path.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return _self_test()
    run_benchmark(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
