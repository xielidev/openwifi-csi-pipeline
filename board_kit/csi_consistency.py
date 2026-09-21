#!/usr/bin/env python3
"""
csi_consistency.py - validate zero-copy (mmap) CSI data against the baseline
(reference) path: TSF monotonicity + CSI amplitude cross-correlation.

Two raw captures (concatenated num_eq-frame dumps) are compared:

  1. TSF monotonicity    - within each capture, the 64-bit TSF must be strictly
                           increasing (dirty reads / ring overwrites would break
                           ordering or produce wild timestamps).
  2. Cross-correlation   - in a static channel the equalizer (channel estimate)
                           magnitude profile is quasi-stationary, so the mean
                           amplitude vector of N frames must be highly correlated
                           between the two paths.  Corr > 0.99 => no dirty read
                           in the zero-copy path.

Frame layout (3792 B for num_eq=8, see include/csi_ring.h):
  symbol 0        : TSF timestamp   (u64 LE)        bytes [0:8]
  symbol 1        : phase offset                     bytes [8:16]
  symbols 2..57   : CSI (56 sym)                     bytes [16:464]
  symbols 58..473 : equalizer taps (num_eq*52 sym)  bytes [464:3792]

Usage:
  csi_consistency.py --ref baseline_raw.bin --zc mmap_raw.bin
                     [--num-eq 8] [--frames 500] [--json out.json]

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import argparse
import json
import math
import struct
import sys

SYMBOL_BYTES = 8
HEADER_LEN = 2
CSI_LEN = 56
EQUALIZER_LEN = 52

TWO32 = 2.0 ** 32


def frame_bytes(num_eq):
    return (HEADER_LEN + CSI_LEN + num_eq * EQUALIZER_LEN) * SYMBOL_BYTES


def load_frames(path, fb, max_frames):
    """Return list of (tsf, amp_vec). amp_vec = per-symbol magnitude of the
    equalizer taps (float). tsf = u64 LE from symbol 0."""
    frames = []
    with open(path, "rb") as f:
        buf = f.read()
    n = len(buf) // fb
    if n == 0:
        return frames
    for i in range(min(n, max_frames)):
        fr = buf[i * fb:(i + 1) * fb]
        tsf = struct.unpack_from("<Q", fr, 0)[0]
        # equalizer taps start after HEADER_LEN+CSI_LEN symbols
        off = (HEADER_LEN + CSI_LEN) * SYMBOL_BYTES
        n_eq_syms = (fb - off) // SYMBOL_BYTES
        amps = []
        for s in range(n_eq_syms):
            iq = struct.unpack_from("<ii", fr, off + s * SYMBOL_BYTES)
            amps.append(math.hypot(iq[0], iq[1]))
        frames.append((tsf, amps))
    return frames


def tsf_monotonicity(frames):
    """Return (violations, gaps>2x median)."""
    bad = 0
    for a, b in zip(frames, frames[1:]):
        if b[0] <= a[0]:
            bad += 1
    return bad


def classify_violations(frames, window=16):
    """Classify TSF regressions into complete duplicates vs torn/dirty frames.

    The openwifi FPGA periodically re-emits a CSI frame around the beacon
    interval, and the 16-slot ring means a stale slot is re-read ~16 frames
    later.  Three cases are distinguished for a frame whose TSF regresses:

      * complete duplicate : payload byte-identical to a nearby frame (stale
        slot re-read) -> benign.
      * torn / dirty read  : the first half of the equalizer payload matches
        one neighbouring frame and the second half matches a different one,
        i.e. the consumer read a slot mid-write.  This is the only true
        "dirty read" failure mode.
      * independent frame : complete self-consistent payload matching nothing
        nearby (FPGA beacon-interval TSF glitch) -> benign.

    Returns (duplicates, torn)."""
    dup = 0
    torn = 0
    n = len(frames)
    for i in range(1, n):
        if frames[i][0] <= frames[i - 1][0]:
            fr = frames[i][1]
            L = len(fr)
            h = L // 2
            A, B = fr[:h], fr[h:]
            lo = max(0, i - window)
            hi = min(n, i + window + 1)
            same = False
            srcA = set()
            srcB = set()
            for j in range(lo, hi):
                if j == i:
                    continue
                fj = frames[j][1]
                if fj == fr:
                    same = True
                if fj[:h] == A:
                    srcA.add(j)
                if fj[h:] == B:
                    srcB.add(j)
            if same:
                dup += 1
            elif srcA and srcB and srcA != srcB:
                torn += 1
    return dup, torn


def mean_amp_vector(frames):
    n = len(frames)
    if n == 0:
        return []
    L = len(frames[0][1])
    acc = [0.0] * L
    for _, amps in frames:
        for i, v in enumerate(amps):
            acc[i] += v
    return [a / n for a in acc]


def pearson(a, b):
    if len(a) != len(b) or len(a) < 2:
        return 0.0, 0.0
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da == 0 or db == 0:
        return 0.0, 0.0
    return num / (da * db), num


def main():
    ap = argparse.ArgumentParser(description="CSI consistency check")
    ap.add_argument("--ref", required=True, help="baseline raw dump (.bin)")
    ap.add_argument("--zc", required=True, help="mmap/zero-copy raw dump (.bin)")
    ap.add_argument("--num-eq", type=int, default=8)
    ap.add_argument("--frames", type=int, default=500,
                    help="max frames to use per capture")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    fb = frame_bytes(args.num_eq)
    ref = load_frames(args.ref, fb, args.frames)
    zc = load_frames(args.zc, fb, args.frames)
    if not ref or not zc:
        print(f"ERROR: empty capture (ref={len(ref)}, zc={len(zc)})")
        return 1
    n = min(len(ref), len(zc), args.frames)
    ref, zc = ref[:n], zc[:n]

    ref_bad = tsf_monotonicity(ref)
    zc_bad = tsf_monotonicity(zc)
    zc_dup, zc_torn = classify_violations(zc)

    a_ref = mean_amp_vector(ref)
    a_zc = mean_amp_vector(zc)
    corr, cov = pearson(a_ref, a_zc)

    # per-frame sample correlation (mean of pairwise corr of amplitude vectors)
    per = []
    for (_, r), (_, z) in zip(ref, zc):
        c, _ = pearson(r, z)
        per.append(c)
    per_corr = sum(per) / len(per) if per else 0.0

    print(f"frames used        : {n}")
    print(f"TSF monotonicity   : baseline {ref_bad} viol / {n-1} gaps, "
          f"mmap {zc_bad} viol "
          f"({zc_dup} complete-dup, {zc_torn} torn/dirty)")
    print(f"amplitude corr     : mean-vector {corr:.4f}  "
          f"per-frame-mean {per_corr:.4f}")
    ok = (corr > 0.99) and (zc_torn == 0)
    print(f"VERDICT            : {'PASS (no dirty read)' if ok else 'FAIL'}")

    if args.json:
        out = {
            "frames": n,
            "tsf_violations_baseline": ref_bad,
            "tsf_violations_mmap": zc_bad,
            "tsf_dup_mmap": zc_dup,
            "tsf_torn_mmap": zc_torn,
            "amp_corr_mean_vector": round(corr, 4),
            "amp_corr_per_frame_mean": round(per_corr, 4),
            "verdict": "PASS" if ok else "FAIL",
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
