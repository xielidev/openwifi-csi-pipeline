#!/usr/bin/env python3
"""
csi_udp_recv.py - remote CSI consumer / UDP receiver.

Receives CSI frames sent by the baseline `side_ch_ctl g1` (to 192.168.10.1:4000
by default) or by `csi_bench -u <ip>` over UDP, splits datagrams into
fixed-size CSI frames, and reports the same three headline numbers used in the
benchmark: frame rate, TSF timestamp jitter (and optionally CPU usage of this
process).

This is the "remote side" of the end-to-end CSI path. For the baseline
(netlink -> copy -> UDP -> remote), run this on the UDP target and use its
JSON output for the jitter number in the comparison.

Usage:
  csi_udp_recv.py [--port 4000] [--num-eq 8] [--duration 60] [--json out.json]
                  [--dump raw.bin]

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import argparse
import json
import signal
import socket
import sys
import time

CSI_SYMBOL_BYTES = 8
CSI_HEADER_LEN = 2
CSI_CSI_LEN = 56
CSI_EQUALIZER_LEN = 56 - 4


def frame_bytes(num_eq):
    return (CSI_HEADER_LEN + CSI_CSI_LEN + num_eq * CSI_EQUALIZER_LEN) * CSI_SYMBOL_BYTES


def percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    idx = int((len(sorted_vals) - 1) * p)
    return sorted_vals[idx]


def main():
    ap = argparse.ArgumentParser(description="CSI UDP receiver / consumer")
    ap.add_argument("--port", type=int, default=4000)
    ap.add_argument("--num-eq", type=int, default=8)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--dump", default=None)
    ap.add_argument("--expected-rate", type=float, default=0.0,
                    help="expected source frame rate in frames/s; enables explicit loss%%")
    ap.add_argument("--intervals-out", default=None,
                    help="dump raw inter-frame TSF deltas (us), one per line (for CDF)")
    args = ap.parse_args()

    fb = frame_bytes(args.num_eq)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(0.5)

    dump_fp = open(args.dump, "wb") if args.dump else None
    tsfs = []
    total = 0
    stop = False

    def on_int(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_int)
    signal.signal(signal.SIGTERM, on_int)

    t0 = time.monotonic()
    print(f"[csi_udp_recv] listening on :{args.port}, num_eq={args.num_eq}, "
          f"frame={fb}B, duration={args.duration:.0f}s (Ctrl-C to stop early)")
    while not stop:
        elapsed = time.monotonic() - t0
        if elapsed >= args.duration:
            break
        try:
            data, _addr = sock.recvfrom(65536)
        except socket.timeout:
            continue
        n = len(data) // fb
        if n == 0:
            continue
        for i in range(n):
            frame = data[i * fb:(i + 1) * fb]
            tsf = int.from_bytes(frame[0:8], "little")
            tsfs.append(tsf)
            total += 1
            if dump_fp:
                dump_fp.write(frame)

    elapsed = time.monotonic() - t0
    if dump_fp:
        dump_fp.close()

    # jitter from inter-frame TSF deltas
    diffs = sorted(b - a for a, b in zip(tsfs, tsfs[1:])) if len(tsfs) > 1 else []
    if diffs:
        jit_mean = sum(diffs) / len(diffs)
        jit_std = (sum((d - jit_mean) ** 2 for d in diffs) / len(diffs)) ** 0.5
        jit_p50 = percentile(diffs, 0.50)
        jit_p95 = percentile(diffs, 0.95)
    else:
        jit_mean = jit_std = jit_p50 = jit_p95 = 0.0

    # loss estimates
    loss_percent = 0.0
    if args.expected_rate > 0 and elapsed > 0:
        expected = args.expected_rate * elapsed
        loss_percent = (1.0 - total / expected) * 100.0 if expected > 0 else 0.0
    tsf_loss_est_percent = 0.0
    if diffs:
        nominal = percentile(diffs, 0.50)  # median inter-frame interval
        if nominal > 0:
            lost = sum(max(0, round(d / nominal) - 1)
                       for d in diffs if d > 1.5 * nominal)
            tsf_loss_est_percent = lost / (total + lost) * 100.0 if (total + lost) else 0.0

    if args.intervals_out:
        with open(args.intervals_out, "w") as f:
            for d in diffs:
                f.write(f"{d}\n")

    rate = total / elapsed if elapsed > 0 else 0.0
    print("[csi_udp_recv] done")
    print(f"  duration   : {elapsed:.2f} s")
    print(f"  total      : {total} frames")
    print(f"  frame rate : {rate:.1f} frames/s")
    print(f"  loss%%      : explicit {loss_percent:.2f}%  (TSF-est {tsf_loss_est_percent:.2f}%)")
    print(f"  TSF jitter : mean {jit_mean:.1f} us  std {jit_std:.1f} us  "
          f"p50 {jit_p50:.1f} us  p95 {jit_p95:.1f} us")

    if args.json:
        out = {
            "mode": "udp_baseline",
            "duration_s": round(elapsed, 2),
            "num_eq": args.num_eq,
            "total_frames": total,
            "frame_rate_hz": round(rate, 1),
            "loss_percent": round(loss_percent, 2),
            "tsf_loss_est_percent": round(tsf_loss_est_percent, 2),
            "jitter_us": {
                "mean": round(jit_mean, 1),
                "std": round(jit_std, 1),
                "p50": round(jit_p50, 1),
                "p95": round(jit_p95, 1),
            },
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"  wrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
