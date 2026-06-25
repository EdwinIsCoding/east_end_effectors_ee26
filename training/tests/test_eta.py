"""Unit tests for the training time-keeper / ETA reporter (src/training/eta.py)."""

from __future__ import annotations

import io

from src.training.eta import ETAReporter, expand_big_number


def test_expand_big_number_inverts_format_big_number():
    assert expand_big_number("50", "") == 50
    assert expand_big_number("1", "K") == 1000
    assert expand_big_number("1.5", "K") == 1500
    assert expand_big_number("2", "M") == 2_000_000
    assert expand_big_number("3", "B") == 3_000_000_000


def test_consume_line_parses_total_and_tracker_step():
    out = io.StringIO()
    r = ETAReporter(total_steps=999, out=out)
    # lerobot prints the exact total once at startup.
    r.consume_line("INFO ... cfg.steps=20000 (20K)")
    assert r.total == 20000
    # Metrics line uses format_big_number for the step field.
    r.consume_line("INFO ... step:2K smpl:8K ep:1 epch:0.40 loss:1.230")
    assert r.current == 2000


def test_consume_line_exact_step_anchor_from_checkpoint():
    out = io.StringIO()
    r = ETAReporter(total_steps=20000, out=out)
    r.consume_line("INFO ... Checkpoint policy after step 5000")
    assert r.current == 5000
    r.consume_line("INFO ... Eval policy at step 7000")
    assert r.current == 7000


def test_step_never_goes_backwards():
    out = io.StringIO()
    r = ETAReporter(total_steps=20000, out=out)
    r.update(5000, force=True)
    # A coarser/rounded later reading must not regress the tracked step.
    r.update(4000, force=True)
    assert r.current == 5000


def test_emit_writes_grepable_eta_line():
    out = io.StringIO()
    r = ETAReporter(total_steps=20000, print_every_s=0.0, out=out)
    r.update(5000, force=True)
    text = out.getvalue()
    assert "[ETA]" in text
    assert "step 5000/20000" in text
    assert "25.0%" in text
    assert "remaining" in text


def test_no_eta_emitted_before_any_progress():
    out = io.StringIO()
    r = ETAReporter(total_steps=20000, out=out)
    # A line with no recognizable step pattern should not produce output.
    r.consume_line("INFO ... Creating dataset")
    assert out.getvalue() == ""
