#!/usr/bin/env python3
"""
run_baseline.py - measure the baseline side_ch path.

Runs `side_ch_ctl g1` (netlink pull every 1 ms) for <duration> seconds and, in
parallel, a local `csi_udp_recv.py` that receives the CSI stream the baseline
forwards to UDP 192.168.10.1:4000 (the board itself, openwifi default IP).

Produces baseline.json with:
  - frame rate + TSF jitter   (from the UDP receiver, i.e. real delivered frames)
  - process CPU%              (side_ch_ctl, sampled via /proc/<pid>/stat)

Usage:
  run_baseline.py [--duration 60] [--side-ch-ctl /path/to/side_ch_ctl]
                  [--num-eq 8] [--json baseline.json]

Requires (on the board):
  - side_ch.ko loaded (baseline driver), NOT csi_dma.ko
  - side_ch_ctl built, board reachable at 192.168.10.1
  - python3 on the board (or run csi_udp_recv.py on the UDP target and pass
    its --json output here via --recv-json)

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

HZ = os.sysconf("SC_CLK_TCK")


def cpu_percent_of(pid, t0_ticks, t1_ticks, elapsed):
    return (t1_ticks - t0_ticks) / HZ / elapsed * 100.0


def read_proc_ticks(pid):
    with open(f"/proc/{pid}/stat") as f:
        parts = f.read().rsplit(")", 1)[1].split()  # skip comm (may contain spaces)
    utime = int(parts[11])   # field 14 -> index 11 after dropping 3 leading
    stime = int(parts[12])   # field 15
    return utime + stime


def main():
    ap = argparse.ArgumentParser(description="Baseline side_ch measurement")
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--side-ch-ctl", default="side_ch_ctl")
    ap.add_argument("--num-eq", type=int, default=8)
    ap.add_argument("--json", default="baseline.json")
    ap.add_argument("--recv-json", default=None,
                    help="pre-generated csi_udp_recv.py JSON (skip local UDP receiver)")
    ap.add_argument("--expected-rate", type=float, default=0.0,
                    help="expected source frame rate in frames/s for explicit loss%%")
    args = ap.parse_args()

    proc = subprocess.Popen([args.side_ch_ctl, "g1"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[baseline] side_ch_ctl g1 pid={proc.pid}, {args.duration:.0f}s")

    recv = None
    recv_json = args.recv_json
    if recv_json is None:
        recv_json = args.json.replace(".json", "_udp.json")
        here = os.path.dirname(os.path.abspath(__file__))
        recv_py = os.path.join(here, "csi_udp_recv.py")
        if not os.path.exists(recv_py):  # project tree layout
            recv_py = os.path.join(here, "..", "user_space", "csi_udp_recv.py")
        recv_cmd = [
            sys.executable, recv_py,
            "--port", "4000", "--num-eq", str(args.num_eq),
            "--duration", str(args.duration), "--json", recv_json,
        ]
        if args.expected_rate > 0:
            recv_cmd += ["--expected-rate", str(args.expected_rate)]
            recv_cmd += ["--intervals-out",
                         args.json.replace(".json", "_intervals.txt")]
        recv = subprocess.Popen(recv_cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    t0 = read_proc_ticks(proc.pid)
    wall0 = time.monotonic()
    try:
        proc.wait(timeout=args.duration)
        # Exited on its own before the window elapsed -> stat already reaped,
        # CPU can't be recovered. side_ch_ctl g1 normally runs until SIGINT.
        t1 = t0
        wall1 = time.monotonic()
    except subprocess.TimeoutExpired:
        # Child still alive at window end: sample its final CPU ticks NOW,
        # while /proc/<pid>/stat still exists, BEFORE signaling it to exit.
        t1 = read_proc_ticks(proc.pid)
        wall1 = time.monotonic()
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    elapsed = wall1 - wall0
    cpu = cpu_percent_of(proc.pid, t0, t1, elapsed)

    if recv is not None:
        recv.wait()

    recv_data = {}
    if os.path.exists(recv_json):
        with open(recv_json) as f:
            recv_data = json.load(f)

    out = {
        "mode": "baseline",
        "duration_s": round(elapsed, 2),
        "num_eq": args.num_eq,
        "total_frames": recv_data.get("total_frames", 0),
        "frame_rate_hz": recv_data.get("frame_rate_hz", 0.0),
        "cpu_percent": round(cpu, 2),
        "loss_percent": recv_data.get("loss_percent", 0.0),
        "tsf_loss_est_percent": recv_data.get("tsf_loss_est_percent", 0.0),
        "jitter_us": recv_data.get("jitter_us", {"mean": 0, "std": 0, "p50": 0, "p95": 0}),
        "note": "frame rate/jitter/loss from UDP receiver; CPU% of side_ch_ctl",
    }
    with open(args.json, "w") as f:
        json.dump(out, f, indent=2)

    print("[baseline] done")
    print(f"  duration   : {elapsed:.2f} s")
    print(f"  frame rate : {out['frame_rate_hz']:.1f} frames/s  ({out['total_frames']} frames)")
    print(f"  loss%      : explicit {out['loss_percent']:.2f}%  "
          f"(TSF-est {out['tsf_loss_est_percent']:.2f}%)")
    print(f"  CPU%       : {out['cpu_percent']:.2f}%")
    j = out["jitter_us"]
    print(f"  TSF jitter : mean {j['mean']:.1f} us  std {j['std']:.1f} us  "
          f"p50 {j['p50']:.1f} us  p95 {j['p95']:.1f} us")
    print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
