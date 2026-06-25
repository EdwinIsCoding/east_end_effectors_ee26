"""Training time-keeper / ETA reporter.

lerobot's `train()` does not print a wall-clock ETA we can rely on in piped
(CloudWatch / nohup) logs — it logs a metrics line via Python `logging` (stderr)
plus a tqdm bar (also stderr). This module wraps the training subprocess, streams
its output through unchanged (so the tqdm bar still renders), and parses the
metrics lines to emit an explicit, grep-able ETA line on stdout:

    [ETA] step 2500/20000 ( 12.5%) | elapsed 0:05:10 | 0.124 s/step | remaining 0:36:12 | finish ~17:58:22

Parsing targets (lerobot @ 05a52238, src/lerobot/utils/logging_utils.py +
scripts/lerobot_train.py):
  - metrics line:  "step:1K smpl:8K ep:1 epch:0.50 loss:1.234 ..."  (step uses
    format_big_number, so "1K"/"1.5K"/"2M" — we invert the suffix).
  - exact anchors: "Checkpoint policy after step 1000", "Eval policy at step 1000".
  - total steps:   "cfg.steps=20000 (20K)".

ETA uses the self-correcting elapsed/fraction estimate (total_est = elapsed/frac;
remaining = total_est - elapsed), which is robust to the coarse step granularity
that format_big_number introduces near round thousands.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Sequence, TextIO

# "step:1K smpl:8K ..." — capture the number and optional magnitude suffix.
_TRACKER_RE = re.compile(r"step:([0-9]+(?:\.[0-9]+)?)([KMBTQ]?)\s+smpl:")
# Exact integer step from checkpoint / eval log lines.
_EXACT_STEP_RE = re.compile(r"(?:after step|at step)\s+([0-9]+)")
# Total step budget: "cfg.steps=20000 (20K)".
_TOTAL_RE = re.compile(r"cfg\.steps=([0-9]+)")

_SUFFIX = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "Q": 1e15}


def expand_big_number(num_str: str, suffix: str) -> int:
    """Invert lerobot's format_big_number, e.g. ('1.5', 'K') -> 1500."""
    return int(round(float(num_str) * _SUFFIX.get(suffix, 1)))


def _fmt_hms(seconds: float) -> str:
    if seconds is None or math.isnan(seconds) or seconds < 0 or math.isinf(seconds):
        return "?"
    return str(timedelta(seconds=int(round(seconds))))


class ETAReporter:
    """Tracks training progress and prints an ETA line, throttled in time."""

    def __init__(self, total_steps: int, *, print_every_s: float = 0.0,
                 out: TextIO | None = None) -> None:
        self.total = max(int(total_steps), 1)
        self.print_every_s = print_every_s
        self.out = out if out is not None else sys.stdout
        self._start: float | None = None
        self._last_print = 0.0
        self.current = 0

    def set_total(self, total: int) -> None:
        if total and total > 0:
            self.total = int(total)

    def update(self, step: int, *, force: bool = False) -> None:
        """Record the latest step and (throttled) print an ETA line."""
        if self._start is None:
            self._start = time.monotonic()
        # Steps are monotonic; never let a coarse/rounded value go backwards.
        self.current = max(self.current, int(step))
        now = time.monotonic()
        if not force and (now - self._last_print) < self.print_every_s:
            return
        self._last_print = now
        self._emit(now)

    def finish(self) -> None:
        if self._start is not None:
            self._emit(time.monotonic())

    def _emit(self, now: float) -> None:
        assert self._start is not None
        elapsed = now - self._start
        step = min(self.current, self.total)
        frac = step / self.total if self.total else 0.0
        if step > 0 and frac > 0.0:
            total_est = elapsed / frac
            remaining = max(total_est - elapsed, 0.0)
            s_per_step = elapsed / step
            finish_str = (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M:%S")
        else:
            remaining = float("nan")
            s_per_step = float("nan")
            finish_str = "?"
        sps = f"{s_per_step:.3f}" if not math.isnan(s_per_step) else "?"
        print(
            f"[ETA] step {step}/{self.total} ({frac * 100:5.1f}%) | "
            f"elapsed {_fmt_hms(elapsed)} | {sps} s/step | "
            f"remaining {_fmt_hms(remaining)} | finish ~{finish_str}",
            file=self.out,
            flush=True,
        )

    def consume_line(self, line: str) -> None:
        """Feed one finished log line; update state from any recognized pattern."""
        m_total = _TOTAL_RE.search(line)
        if m_total:
            self.set_total(int(m_total.group(1)))
        m_exact = _EXACT_STEP_RE.search(line)
        if m_exact:
            self.update(int(m_exact.group(1)), force=True)
            return
        m = _TRACKER_RE.search(line)
        if m:
            self.update(expand_big_number(m.group(1), m.group(2)))


def stream_with_eta(
    cmd: Sequence[str],
    *,
    cwd: str | None,
    total_steps: int,
    print_every_s: float = 30.0,
) -> int:
    """Run `cmd`, pass its stderr through verbatim (tqdm stays live) while
    parsing it for progress, and print ETA lines to stdout. Returns the exit code.

    We tap stderr because lerobot's logging StreamHandler + tqdm both write there.
    Reading char-by-char and echoing immediately preserves tqdm's `\\r` redraws;
    we only parse complete `\\n`-terminated lines (a `\\r` clears the line buffer,
    matching terminal behaviour, so the in-progress tqdm bar never false-matches).
    """
    reporter = ETAReporter(total_steps, print_every_s=print_every_s)
    proc = subprocess.Popen(
        list(cmd),
        cwd=cwd,
        stdout=None,                 # inherit: train_phase's rich output stays on stdout
        stderr=subprocess.PIPE,      # capture logging + tqdm for parsing
        text=True,
        bufsize=1,
    )
    line_buf: list[str] = []
    assert proc.stderr is not None
    try:
        for ch in iter(lambda: proc.stderr.read(1), ""):
            sys.stderr.write(ch)     # echo verbatim so tqdm renders normally
            sys.stderr.flush()
            if ch == "\n":
                reporter.consume_line("".join(line_buf))
                line_buf.clear()
            elif ch == "\r":
                line_buf.clear()     # tqdm redraw: discard the in-progress visual line
            else:
                line_buf.append(ch)
    finally:
        proc.wait()
    if line_buf:
        reporter.consume_line("".join(line_buf))
    reporter.finish()
    return proc.returncode
