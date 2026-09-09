#!/usr/bin/env python3
"""
plot_compare.py - mmap vs baseline comparison charts (paper-grade).

Reads baseline.json and mmap.json (produced by run_baseline.py / csi_bench)
plus the raw inter-frame interval files for the jitter CDF, and generates
three figures (each saved as both PNG and PDF/vector):

  Fig.1 frame_rate  - delivered CSI frame throughput (frames/s, bar)
  Fig.2 cpu_percent - capture process CPU usage (%)
  Fig.3 jitter      - TSF timestamp jitter CDF (cumulative distribution)

Usage:
  plot_compare.py [--baseline baseline.json] [--mmap mmap.json]
                  [--baseline-intervals baseline_intervals.txt]
                  [--mmap-intervals mmap_intervals.txt]
                  [--outdir charts]

If the raw interval files are not available, Fig.3 falls back to a p50/p95 bar.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import argparse
import json
import os

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None


def load(path):
    with open(path) as f:
        return json.load(f)


def load_intervals(path):
    if not path or not os.path.exists(path):
        return None
    vals = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                vals.append(float(line))
    return sorted(vals)


def main():
    ap = argparse.ArgumentParser(description="mmap vs baseline comparison charts")
    ap.add_argument("--baseline", default="baseline.json")
    ap.add_argument("--mmap", default="mmap.json")
    ap.add_argument("--baseline-intervals", default="baseline_intervals.txt")
    ap.add_argument("--mmap-intervals", default="mmap_intervals.txt")
    ap.add_argument("--outdir", default="charts")
    args = ap.parse_args()

    base = load(args.baseline)
    mm = load(args.mmap)
    os.makedirs(args.outdir, exist_ok=True)

    b_iv = load_intervals(args.baseline_intervals)
    m_iv = load_intervals(args.mmap_intervals)

    labels = ["baseline\n(side_ch_ctl g1)", "mmap\n(csi_bench)"]
    if plt is None:
        print("WARNING: matplotlib not installed; printing numbers only.")
        for name, d in (("baseline", base), ("mmap", mm)):
            j = d.get("jitter_us", {})
            print(f"[{name}] frames/s={d.get('frame_rate_hz')} "
                  f"cpu%={d.get('cpu_percent')} "
                  f"loss%={d.get('loss_percent')} "
                  f"jitter std={j.get('std')} us")
        return

    def save(fig, name):
        png = os.path.join(args.outdir, name + ".png")
        pdf = os.path.join(args.outdir, name + ".pdf")
        fig.savefig(png, dpi=150)
        fig.savefig(pdf, format="pdf")
        plt.close(fig)
        return png, pdf

    # Fig.1 frame throughput (frames/s) + MB/s annotation
    fb = mm.get("num_eq", 8)
    fig, ax = plt.subplots(figsize=(4, 3.2))
    vals = [base.get("frame_rate_hz", 0), mm.get("frame_rate_hz", 0)]
    ax.bar(labels, vals, color=["#bbbbbb", "#2a7de1"], width=0.55)
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:.1f}", ha="center", va="bottom")
    ax.set_ylabel("CSI frames/s")
    ax.set_title("CSI frame throughput (frames/s)")
    fig.tight_layout()
    save(fig, "frame_rate")

    # Fig.2 CPU%
    fig, ax = plt.subplots(figsize=(4, 3.2))
    vals = [base.get("cpu_percent", 0), mm.get("cpu_percent", 0)]
    ax.bar(labels, vals, color=["#bbbbbb", "#2a7de1"], width=0.55)
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:.2f}%", ha="center", va="bottom")
    ax.set_ylabel("CPU %")
    ax.set_title("Capture process CPU usage")
    fig.tight_layout()
    save(fig, "cpu_percent")

    # Fig.3 jitter CDF (or p50/p95 bar fallback)
    if b_iv and m_iv:
        fig, ax = plt.subplots(figsize=(4.6, 3.2))
        ax.plot(b_iv, [i / (len(b_iv) - 1) for i in range(len(b_iv))],
                label="baseline", color="#bbbbbb", lw=1.8)
        ax.plot(m_iv, [i / (len(m_iv) - 1) for i in range(len(m_iv))],
                label="mmap", color="#2a7de1", lw=1.8)
        ax.set_xlabel("inter-frame TSF interval (us)")
        ax.set_ylabel("CDF")
        ax.set_title("TSF timestamp jitter CDF")
        ax.legend()
        ax.grid(True, ls=":", alpha=0.5)
        fig.tight_layout()
        save(fig, "jitter_cdf")
    else:
        bj = base.get("jitter_us", {})
        mj = mm.get("jitter_us", {})
        fig, ax = plt.subplots(figsize=(4, 3.2))
        x = [0, 1]
        p50 = [bj.get("p50", 0), mj.get("p50", 0)]
        p95 = [bj.get("p95", 0), mj.get("p95", 0)]
        w = 0.35
        ax.bar([i - w / 2 for i in x], p50, w, label="p50", color="#7fb2e8")
        ax.bar([i + w / 2 for i in x], p95, w, label="p95", color="#2a7de1")
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_ylabel("us")
        ax.set_title("TSF timestamp jitter (p50/p95)")
        ax.legend()
        fig.tight_layout()
        save(fig, "jitter")

    print(f"charts written to {args.outdir}/: "
          f"frame_rate.{{png,pdf}}, cpu_percent.{{png,pdf}}, "
          f"jitter{{,_cdf}}.{{png,pdf}}")


if __name__ == "__main__":
    main()
