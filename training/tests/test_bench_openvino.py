"""Tests for the OpenVINO benchmark/optimization harness.

The pure helpers (stats) run anywhere; the build/quantize/bench path is gated on openvino+nncf
being installed (absent in CI, present on the deploy boxes), mirroring the lazy-import design.
"""
import numpy as np
import pytest

from src.inference import bench_openvino as bench


def test_summarize_orders_percentiles_and_reports_fps():
    lat = np.linspace(1.0, 2.0, 100)  # ms
    s = bench.summarize(lat, target_hz=30.0)
    assert s["iters"] == 100
    assert s["min_ms"] <= s["p50_ms"] <= s["p95_ms"] <= s["p99_ms"] <= s["max_ms"]
    assert s["mean_fps"] == pytest.approx(1000.0 / lat.mean(), rel=1e-6)
    assert s["holds_target_hz"] is True  # ~1.5 ms p95 -> ~660 fps >> 30


def test_summarize_flags_when_target_missed():
    lat = np.full(50, 100.0)  # 100 ms -> 10 fps, below 30 Hz
    s = bench.summarize(lat, target_hz=30.0)
    assert s["holds_target_hz"] is False


def test_module_imports_without_openvino():
    # Lazy imports: the module and its arg parser must work with no openvino installed.
    assert hasattr(bench, "run_benchmark") and hasattr(bench, "build_synthetic_policy_model")


def test_synthetic_build_quantize_and_fold():
    pytest.importorskip("openvino")
    pytest.importorskip("nncf")
    from openvino import Core
    core = Core()
    model = bench.build_synthetic_policy_model(image_size=48, conv_channels=8, hidden=64)
    assert {i.get_any_name() for i in model.inputs} == {bench.IMAGE_INPUT_NAME, bench.STATE_INPUT_NAME}

    compiled, compile_s = bench.compile_optimized(core, model, "CPU")
    assert compile_s >= 0
    lat = bench.bench_compiled(compiled, warmup=2, iters=5)
    assert lat.shape == (5,) and np.all(lat > 0)

    folded = bench.fold_image_preprocess(model)
    assert any(i.get_element_type().to_string() == "u8" for i in folded.inputs)

    q = bench.quantize_int8(model, calib_size=8)
    cq, _ = bench.compile_optimized(core, q, "CPU")
    out = cq(bench.random_inputs_for_compiled(cq))
    assert np.isfinite(list(out.values())[0]).all()
