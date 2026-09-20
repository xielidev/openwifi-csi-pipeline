#!/usr/bin/env python3
"""
plot_num_eq_scan.py - paper-grade error-bar figures from summary_mean_std.json.

Reads the aggregate produced by summarize_runs.py (5 rounds -> mean ± std) and
renders:

  num_eq_scan.png/pdf      CPU% and frame rate vs num_eq (the scan: capture
                           overhead vs CSI frame size, mmap path)
  headline_mean_std.png/pdf  baseline vs mmap(ne=8) bars with std error bars
  load_gradient.png/pdf    (optional, only if load0/load5/... runs exist)
                           CPU% and frame rate vs offered uplink load

Usage:
  python3 plot_num_eq_scan.py [--summary summary_mean_std.json]
                              [--outdir docs/figures]

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import argparse
import json
import os
import re

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

C_BASE = "#bbbbbb"
C_MMAP = "#2a7de1"

# frame bytes for a given num_eq: (2 header + 56 CSI + ne*52 EQ) symbols * 8 B
def frame_bytes(ne):
    return (2 + 56 + ne * 52) * 8


def save(fig, outdir, name):
    png = os.path.join(outdir, name + ".png")
    pdf = os.path.join(outdir, name + ".pdf")
    fig.savefig(png, dpi=150)
    fig.savefig(pdf, format="pdf")
    plt.close(fig)
    print(f"  {png}  +  {pdf}")


def eb(ax, x, mean, std, color, label=None, width=0.0):
    """Error-bar scatter (or bars if width>0) with mean±std."""
    if width > 0:
        ax.bar(x, mean, width=width, yerr=std, capsize=4, color=color,
               label=label, error_kw={"lw": 1.2})
        for xi, mi, si in zip(x, mean, std):
            ax.annotate(f"{mi:.1f}", (xi, mi + si), textcoords="offset points",
                        xytext=(0, 3), ha="center", fontsize=8)
    else:
        ax.errorbar(x, mean, yerr=std, capsize=4, color=color, marker="o",
                    lw=1.8, label=label)
        for xi, mi in zip(x, mean):
            ax.annotate(f"{mi:.1f}", (xi, mi), textcoords="offset points",
                        xytext=(0, 7), ha="center", fontsize=8)


def main():
    ap = argparse.ArgumentParser(description="num_eq scan / headline error-bar figures")
    ap.add_argument("--summary", default="summary_mean_std.json")
    ap.add_argument("--outdir", default="docs/figures")
    args = ap.parse_args()

    with open(args.summary) as f:
        s = json.load(f)
    groups = s.get("groups", {})
    scan = s.get("num_eq_scan")
    loads = s.get("load_gradient")
    os.makedirs(args.outdir, exist_ok=True)

    if plt is None:
        print("WARNING: matplotlib not installed; cannot draw figures.")
        return 0

    # ---- Fig: num_eq scan (CPU% + fps vs num_eq, mean±std) -------------------
    if scan:
        ne = [int(v) for v in scan["x"]]
        xlab = [f"{n}\n({frame_bytes(n)} B)" for n in ne]
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
        eb(axes[0], ne, scan["cpu_percent"]["mean"], scan["cpu_percent"]["std"], C_MMAP)
        axes[0].set_xticks(ne, xlab)
        axes[0].set_xlabel("num_eq (CSI frame bytes)")
        axes[0].set_ylabel("capture CPU %")
        axes[0].set_title("CPU vs CSI frame size")
        axes[0].grid(True, ls=":", alpha=0.5)

        eb(axes[1], ne, scan["frame_rate_hz"]["mean"], scan["frame_rate_hz"]["std"], C_MMAP)
        axes[1].set_xticks(ne, xlab)
        axes[1].set_xlabel("num_eq (CSI frame bytes)")
        axes[1].set_ylabel("CSI frames/s")
        axes[1].set_title("throughput vs CSI frame size")
        axes[1].grid(True, ls=":", alpha=0.5)

        n = groups.get("mmap_ne8", {}).get("n", "?")
        fig.suptitle(f"num_eq gradient scan, mmap zero-copy (mean±std, {n} rounds/point)",
                     fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        save(fig, args.outdir, "num_eq_scan")
    else:
        print("  (no num_eq_scan groups found - skip num_eq_scan figure)")

    # ---- Fig: headline baseline vs mmap(ne=8) with error bars ----------------
    base = groups.get("baseline")
    m8 = next((groups[g] for g in sorted(groups) if re.match(r"^mmap_ne8$", g)), None)
    if base and m8:
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
        labels = ["baseline\n(side_ch_ctl g1)", "mmap\n(csi_bench)"]
        for ax, metric, unit in (
            (axes[0], "cpu_percent", "CPU %"),
            (axes[1], "frame_rate_hz", "CSI frames/s"),
        ):
            bm = base["metrics"][metric]
            mm = m8["metrics"][metric]
            eb(ax, [0, 1], [bm["mean"], mm["mean"]], [bm["std"], mm["std"]],
               [C_BASE, C_MMAP], width=0.5)
            ax.set_xticks([0, 1], labels)
            ax.set_ylabel(unit)
            ax.set_title(f"{unit} (mean±std, {base['n']}/{m8['n']} rounds)")
            ax.grid(True, ls=":", alpha=0.5, axis="y")
        fig.tight_layout()
        save(fig, args.outdir, "headline_mean_std")
    else:
        print("  (baseline or mmap_ne8 group missing - skip headline figure)")

    # ---- Fig: uplink load gradient (optional bonus) ---------------------------
    if loads:
        load = [int(v) for v in loads["x"]]
        fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
        eb(axes[0], load, loads["cpu_percent"]["mean"], loads["cpu_percent"]["std"], C_MMAP)
        axes[0].set_xlabel("offered uplink load (Mbit/s)")
        axes[0].set_ylabel("capture CPU %")
        axes[0].set_title("CPU vs offered load")
        axes[0].grid(True, ls=":", alpha=0.5)
        eb(axes[1], load, loads["frame_rate_hz"]["mean"], loads["frame_rate_hz"]["std"], C_MMAP)
        axes[1].set_xlabel("offered uplink load (Mbit/s)")
        axes[1].set_ylabel("CSI frames/s")
        axes[1].set_title("captured frame rate vs offered load")
        axes[1].grid(True, ls=":", alpha=0.5)
        fig.suptitle("uplink load gradient, mmap zero-copy (mean±std)", fontsize=10)
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        save(fig, args.outdir, "load_gradient")
    else:
        print("  (no load0/load* groups found - skip load_gradient figure)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
