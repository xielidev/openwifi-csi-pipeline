#!/usr/bin/env python3
"""
plot_sweep.py - paper 1 Fig. 3(a): capture CPU% vs offered uplink rate.

Reads the aggregate produced by summarize_runs.py (contains the "sweep" view
built from sweep_{mmap,base}_<rate>mbps_r<N>.json groups written by
run_sweep_fixed_rate.sh) and renders:

  sweep_cpu_vs_rate.png/pdf  baseline vs mmap capture CPU% over the rate sweep
                             (the Fig. 3(a) deliverable)
  sweep_fps.png/pdf          delivered CSI frame rate vs offered load
                             (sanity figure: both paths must track the load)

Usage (dev machine, after aggregating the board's sweep/ dir):
  python3 scripts/plot_sweep.py --summary sweep/summary_mean_std.json \
                                --outdir docs/figures

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

C_BASE = "#bbbbbb"
C_MMAP = "#2a7de1"


def save(fig, outdir, name):
    png = os.path.join(outdir, name + ".png")
    pdf = os.path.join(outdir, name + ".pdf")
    fig.savefig(png, dpi=150)
    fig.savefig(pdf, format="pdf")
    plt.close(fig)
    print(f"  {png}  +  {pdf}")


def eb(ax, x, mean, std, color, label):
    ax.errorbar(x, mean, yerr=std, capsize=4, color=color, marker="o",
                lw=1.8, label=label)
    for xi, mi in zip(x, mean):
        ax.annotate(f"{mi:.1f}", (xi, mi), textcoords="offset points",
                    xytext=(0, 7), ha="center", fontsize=8)


def main():
    ap = argparse.ArgumentParser(description="Fig. 3(a) fixed-rate sweep figure")
    ap.add_argument("--summary", default="summary_mean_std.json")
    ap.add_argument("--outdir", default="docs/figures")
    ap.add_argument("--base-ref", default=None,
                    help="side_ch reference JSON (e.g. baseline_v2.json) used as a "
                         "single reference point when no baseline sweep exists. "
                         "Requires frame_rate_hz + cpu_percent keys.")
    args = ap.parse_args()

    with open(args.summary) as f:
        s = json.load(f)
    sweep = s.get("sweep") or {}
    mmap_v = sweep.get("mmap")
    base_v = sweep.get("baseline")
    if not mmap_v and not base_v:
        print("ERROR: no sweep groups in summary - run run_sweep_fixed_rate.sh "
              "for at least one path, then summarize_runs.py --dir <sweep dir>")
        return 1

    if plt is None:
        print("WARNING: matplotlib not installed; cannot draw figures.")
        return 0
    os.makedirs(args.outdir, exist_ok=True)

    n_mmap = len(mmap_v["x"]) if mmap_v else 0
    n_base = len(base_v["x"]) if base_v else 0
    rounds = s.get("groups", {}).get(
        (mmap_v or base_v)["tags"][0], {}).get("n", "?")

    # ---- Fig. 3(a): capture CPU% vs offered rate ----------------------------
    fig, ax = plt.subplots(figsize=(4.4, 3.3))
    base_ref_xy = None
    if args.base_ref and os.path.isfile(args.base_ref):
        with open(args.base_ref) as rf:
            ref = json.load(rf)
        rfps = float(ref["frame_rate_hz"])
        rcpu = float(ref["cpu_percent"])
        # map reference fps onto the mmap fps-vs-rate scale => nominal load point
        if mmap_v and len(mmap_v["frame_rate_hz"]["mean"]) >= 2:
            mx = list(mmap_v["x"])
            mfps = list(mmap_v["frame_rate_hz"]["mean"])
            rate_per_fps = (max(mx) - min(mx)) / max(1e-9, (max(mfps) - min(mfps)))
            rx_load = min(mx) + (rfps - min(mfps)) * rate_per_fps
        else:
            rx_load = None
        base_ref_xy = (rx_load, rcpu)
    if base_v:
        eb(ax, base_v["x"], base_v["cpu_percent"]["mean"],
           base_v["cpu_percent"]["std"], C_BASE, "baseline (side_ch_ctl)")
    elif base_ref_xy and rx_load is not None:
        ax.scatter([rx_load], [base_ref_xy[1]], marker="D", s=45,
                   facecolor="none", edgecolor=C_BASE, zorder=3,
                   label="side_ch ref. (measured 15.2% @ ~13 Mbit/s)")
        ax.axhline(base_ref_xy[1], color=C_BASE, ls="--", lw=1)
        ax.annotate(f"{base_ref_xy[1]:.1f}",
                    (rx_load, base_ref_xy[1]), textcoords="offset points",
                    xytext=(8, -2), ha="left", fontsize=8, color=C_BASE)
    if mmap_v:
        eb(ax, mmap_v["x"], mmap_v["cpu_percent"]["mean"],
           mmap_v["cpu_percent"]["std"], C_MMAP, "mmap zero-copy (csi_bench)")
    ax.set_xlabel("offered uplink load (Mbit/s)")
    ax.set_ylabel("capture CPU %")
    ax.set_xticks(sorted({*base_v["x"], *mmap_v["x"]}) if mmap_v and base_v
                  else (mmap_v["x"] if mmap_v else base_v["x"]))
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(loc="upper left", fontsize=8)
    fig.suptitle(f"capture CPU vs offered load (mean±std, {rounds} rounds/point)",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    save(fig, args.outdir, "sweep_cpu_vs_rate")

    # ---- sanity figure: delivered frame rate vs offered load -----------------
    fig, ax = plt.subplots(figsize=(4.4, 3.3))
    if base_v:
        eb(ax, base_v["x"], base_v["frame_rate_hz"]["mean"],
           base_v["frame_rate_hz"]["std"], C_BASE, "baseline (side_ch_ctl)")
    if mmap_v:
        eb(ax, mmap_v["x"], mmap_v["frame_rate_hz"]["mean"],
           mmap_v["frame_rate_hz"]["std"], C_MMAP, "mmap zero-copy (csi_bench)")
    ax.set_xlabel("offered uplink load (Mbit/s)")
    ax.set_ylabel("delivered CSI frames/s")
    ax.set_xticks(sorted({*base_v["x"], *mmap_v["x"]}) if mmap_v and base_v
                  else (mmap_v["x"] if mmap_v else base_v["x"]))
    ax.grid(True, ls=":", alpha=0.5)
    ax.legend(loc="upper left", fontsize=8)
    fig.suptitle("delivered frame rate vs offered load (sanity: tracks load)",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    save(fig, args.outdir, "sweep_fps")

    print(f"done: mmap points={n_mmap}, baseline points={n_base}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
