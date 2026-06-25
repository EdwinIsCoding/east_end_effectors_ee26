#!/usr/bin/env python
"""Extract the training loss history from a main.py train log and export a curve + CSV.

The periodic metric line lerobot prints (`step:.. loss:.. grdn:.. lr:..`) is appended
to the end of a tqdm progress line and the step is abbreviated (1K/5K/20K) past 1000,
so we read the exact step from the tqdm "<step>/<total>" counter on the same fragment.

Usage:
    python scripts/plot_loss_curve.py outputs/<run>/../<run>.log --out-dir outputs/<run>
    python scripts/plot_loss_curve.py outputs/c1_insertion_smolvla.log     # out-dir defaults next to log
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_log(text: str) -> list[tuple[int, float, float, float]]:
    frag_re = re.compile(r"(\d+)/\d+.*?loss:([\d.]+)\s+grdn:([\d.]+)\s+lr:([\d.eE+-]+)")
    seen: dict[int, tuple[float, float, float]] = {}
    for frag in re.split(r"[\r\n]", text):
        m = frag_re.search(frag)
        if m:
            seen[int(m.group(1))] = (float(m.group(2)), float(m.group(3)), float(m.group(4)))
    return [(s, *seen[s]) for s in sorted(seen)]


def ema(x: list[float], a: float = 0.1) -> list[float]:
    out, m = [], x[0]
    for v in x:
        m = a * v + (1 - a) * m
        out.append(m)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path, help="Path to the training .log file.")
    ap.add_argument("--out-dir", type=Path, default=None, help="Where to write loss_curve.png + loss_history.csv.")
    ap.add_argument("--title", type=str, default=None)
    args = ap.parse_args()

    out_dir = args.out_dir or args.log.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = parse_log(args.log.read_text(errors="replace"))
    if not rows:
        raise SystemExit(f"No loss points parsed from {args.log}")
    steps = [r[0] for r in rows]
    loss = [r[1] for r in rows]
    grdn = [r[2] for r in rows]
    print(f"parsed {len(rows)} points: step {steps[0]}..{steps[-1]}, "
          f"loss {loss[0]:.3f}->{loss[-1]:.3f}, min {min(loss):.4f}")

    csv_path = out_dir / "loss_history.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["step", "loss", "grad_norm", "lr"])
        w.writerows(rows)

    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax1.plot(steps, loss, color="#bcd", lw=1, alpha=0.6, label="loss (raw)")
    ax1.plot(steps, ema(loss), color="#1f6feb", lw=2.2, label="loss (EMA 0.1)")
    ax1.set_xlabel("training step")
    ax1.set_ylabel("loss", color="#1f6feb")
    ax1.set_yscale("log")
    ax1.tick_params(axis="y", labelcolor="#1f6feb")
    ax1.grid(True, which="both", ls=":", alpha=0.4)
    ax1.set_xlim(0, steps[-1])

    ax2 = ax1.twinx()
    ax2.plot(steps, grdn, color="#d29922", lw=1, alpha=0.5, label="grad norm")
    ax2.set_ylabel("grad norm", color="#d29922")
    ax2.tick_params(axis="y", labelcolor="#d29922")

    ax1.set_title(args.title or f"{args.log.stem} — final loss {loss[-1]:.3f} @ {steps[-1]} steps")
    lines = ax1.get_lines() + ax2.get_lines()
    ax1.legend(lines, [l.get_label() for l in lines], loc="upper right", fontsize=9)
    fig.tight_layout()
    png_path = out_dir / "loss_curve.png"
    fig.savefig(png_path, dpi=130)
    print("wrote", png_path)
    print("wrote", csv_path)


if __name__ == "__main__":
    main()
