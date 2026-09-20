#!/usr/bin/env python3
"""
summarize_runs.py - aggregate repeated benchmark runs into mean ± std.

Turns the per-round JSONs produced by the board scripts into a statistical
summary (5 rounds per config -> mean ± sample std), so single-point numbers
become credible:

  mmap_ne<ne>_r<N>.json    <- run_scan_num_eq.sh   (num_eq gradient, mmap)
  mmap_<tag>_r<N>.json     <- run_repeats.sh       (e.g. load0/load5/load20)
  baseline_r<N>.json       <- run_repeat_baseline.sh (side_ch path)
  sweep_{mmap,base}_<rate>mbps_r<N>.json <- run_sweep_fixed_rate.sh
                             (fixed-rate sweep, one group per rate x path)

Outputs:
  summary_mean_std.json  - per-group stats + num_eq_scan / load_gradient /
                           sweep (Fig. 3a) views
  summary_mean_std.csv   - long format: group, metric, n, mean, std, rounds

Stdlib only (runs on the board and on the dev machine). Rounds whose frame
rate is < 50% of the group median are flagged as suspected traffic stalls
(known Redmi iperf3 stall issue) - re-run those rounds before publishing.

Usage:
  python3 summarize_runs.py [--dir <json_dir>] [--out <summary.json>]

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys

METRICS = [
    "frame_rate_hz",
    "cpu_percent",
    "loss_percent",
    "tsf_loss_est_percent",
    "jitter_p50_us",
    "jitter_p95_us",
    "total_frames",
    "duration_s",
]


def extract(round_json):
    """Flatten the per-round JSON into the metrics we aggregate."""
    j = round_json
    out = {k: j.get(k, 0.0) for k in METRICS}
    jit = j.get("jitter_us") or {}
    out["jitter_p50_us"] = jit.get("p50", 0.0)
    out["jitter_p95_us"] = jit.get("p95", 0.0)
    out["num_eq"] = j.get("num_eq")
    out["dma_err_count"] = j.get("dma_err_count", 0)
    return out


def stats(values):
    n = len(values)
    mean = statistics.fmean(values) if n else 0.0
    std = statistics.stdev(values) if n >= 2 else 0.0
    return {"n": n, "rounds": values, "mean": round(mean, 3), "std": round(std, 3)}


def discover(json_dir):
    """Return {group: {round: filepath}} for the naming conventions above."""
    groups = {}
    pats = [
        (re.compile(r"^mmap_([A-Za-z0-9]+)_r(\d+)\.json$"), "mmap_{}"),
        (re.compile(r"^sweep_mmap_(\d+)mbps_r(\d+)\.json$"), "sweep_mmap_{}mbps"),
        (re.compile(r"^sweep_base_(\d+)mbps_r(\d+)\.json$"), "sweep_base_{}mbps"),
        (re.compile(r"^baseline_r(\d+)\.json$"), "baseline"),
    ]
    for fname in sorted(os.listdir(json_dir)):
        path = os.path.join(json_dir, fname)
        if not os.path.isfile(path):
            continue
        for pat, gname in pats:
            m = pat.match(fname)
            if m:
                if gname == "baseline":
                    group, rnd = "baseline", int(m.group(1))
                else:
                    group, rnd = gname.format(m.group(1)), int(m.group(2))
                groups.setdefault(group, {})[rnd] = path
                break
    return groups


def main():
    ap = argparse.ArgumentParser(description="Aggregate repeated runs -> mean±std")
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="directory holding the per-round JSONs (default: this script's dir)")
    ap.add_argument("--out", default=None,
                    help="summary JSON path (default: <dir>/summary_mean_std.json)")
    args = ap.parse_args()

    d = args.dir
    if not os.path.isdir(d):
        print(f"ERROR: not a directory: {d}", file=sys.stderr)
        return 1
    groups = discover(d)
    if not groups:
        print(f"ERROR: no per-round JSONs (mmap_<tag>_r<N>.json / baseline_r<N>.json / "
              f"sweep_*_<rate>mbps_r<N>.json) in {d}",
              file=sys.stderr)
        return 1

    summary = {"groups": {}}
    print(f"{'group':<16} {'n':>2}  " + "  ".join(f"{m:>14}" for m in
          ("frame_rate_hz", "cpu_percent", "jitter_p50", "jitter_p95")))
    for group in sorted(groups):
        rounds = sorted(groups[group])
        rows = []
        for r in rounds:
            with open(groups[group][r]) as f:
                rows.append(extract(json.load(f)))

        g = {
            "n": len(rows),
            "rounds_files": [os.path.basename(groups[group][r]) for r in rounds],
            "num_eq": next((x.get("num_eq") for x in rows if x.get("num_eq") is not None),
                           None),
            "dma_err_count": sum(x.get("dma_err_count", 0) for x in rows),
            "metrics": {m: stats([x[m] for x in rows]) for m in METRICS},
        }

        # flag suspected traffic stalls: fps < 50% of group median
        fps = g["metrics"]["frame_rate_hz"]["rounds"]
        med = sorted(fps)[len(fps) // 2] if fps else 0.0
        flagged = [f"{i + 1}" for i, v in enumerate(fps) if med > 0 and v < 0.5 * med]
        g["suspect_rounds"] = flagged
        if flagged:
            print(f"WARNING: {group}: round(s) {','.join(flagged)} < 50% of median fps "
                  f"({med:.1f}) - traffic stall? re-run those rounds")

        summary["groups"][group] = g
        print(f"{group:<16} {g['n']:>2}  "
              + "  ".join(f"{g['metrics'][m]['mean']:>7.1f}±{g['metrics'][m]['std']:<5.1f}"
                          for m in ("frame_rate_hz", "cpu_percent",
                                    "jitter_p50_us", "jitter_p95_us")))

    # ---- convenience views: num_eq scan / load gradient ---------------------
    def view(prefix):
        tags = []
        for gname in summary["groups"]:
            m = re.match(rf"^{prefix}(\d+)$", gname)
            if m:
                tags.append((int(m.group(1)), gname))
        if not tags:
            return None
        tags.sort()
        vals = [float(t[0]) for t in tags]
        view = {"tags": [t[1] for t in tags], "x": vals}
        for metric in ("frame_rate_hz", "cpu_percent", "jitter_p50_us",
                       "jitter_p95_us", "loss_percent"):
            view[metric] = {
                "mean": [summary["groups"][t[1]]["metrics"][metric]["mean"] for t in tags],
                "std": [summary["groups"][t[1]]["metrics"][metric]["std"] for t in tags],
            }
        return view

    def sweep_view(prefix):
        """Fixed-rate sweep view: groups sweep_<prefix>_<rate>mbps sorted by rate."""
        pts = []
        for gname in summary["groups"]:
            m = re.match(rf"^{prefix}_(\d+)mbps$", gname)
            if m:
                pts.append((int(m.group(1)), gname))
        if not pts:
            return None
        pts.sort()
        view = {"tags": [t[1] for t in pts], "x": [t[0] for t in pts]}
        for metric in ("frame_rate_hz", "cpu_percent", "jitter_p50_us",
                       "jitter_p95_us", "loss_percent"):
            view[metric] = {
                "mean": [summary["groups"][t[1]]["metrics"][metric]["mean"] for t in pts],
                "std": [summary["groups"][t[1]]["metrics"][metric]["std"] for t in pts],
            }
        return view

    summary["num_eq_scan"] = view("mmap_ne")
    summary["load_gradient"] = view("mmap_load")
    summary["sweep"] = {
        "mmap": sweep_view("sweep_mmap"),
        "baseline": sweep_view("sweep_base"),
    }

    out_path = args.out or os.path.join(d, "summary_mean_std.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    csv_path = os.path.splitext(out_path)[0] + ".csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "metric", "n", "mean", "std", "rounds"])
        for gname, g in sorted(summary["groups"].items()):
            for metric, s in g["metrics"].items():
                w.writerow([gname, metric, s["n"], s["mean"], s["std"],
                            " ".join(f"{v:g}" for v in s["rounds"])])

    print(f"\nwrote {out_path}\nwrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
