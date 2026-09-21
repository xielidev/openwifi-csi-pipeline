#!/bin/bash
# run_sweep_fixed_rate.sh - ONE fixed-rate sweep point for paper Fig. 3(a).
#
# Measures the capture path at ONE offered uplink rate: R rounds x DUR s,
# while the client runs a constant-rate UDP uplink:
#   iperf3 -c 192.168.13.1 -u -b <RATE>M -l 1400 -t <pilot+rounds*DUR+60>
# (iperf3 must cover the pilot + all rounds; start it BEFORE this script.)
#
# Output naming feeds summarize_runs.py directly (same outdir for ALL points):
#   mmap     -> sweep_mmap_<rate>mbps_r<N>.json   (csi_bench, num_eq=8)
#   baseline -> sweep_base_<rate>mbps_r<N>.json   (side_ch_ctl + UDP recv)
#
# Usage: ./run_sweep_fixed_rate.sh <rate_mbps> [mmap|baseline] [rounds] [duration_s] [outdir]
#   rate_mbps : offered uplink rate in Mbit/s, e.g. 1 5 10 20 (REQUIRED)
#   kind      : mmap (default) | baseline
#   rounds    : repetitions per point (default 3)
#   duration_s: seconds per round (default 60)
#   outdir    : output dir (default ./sweep; keep ONE dir for the whole sweep)
#
# Environment overrides:
#   MIN_FPS : pilot threshold (default RATE*44 = ~50% of ~89 fps per Mbit/s
#             at 1400 B payloads; idle floor is beacons-only ~25 fps)
#   FORCE=1 : warn instead of abort on the traffic check
#   WAIT=1  : wait for the uplink to come up instead of aborting (poll the
#             pilot every 10 s up to WAIT_TIMEOUT=600 s) - lets the board sit
#             idle until the client starts iperf3
#
# Preconditions (aborts if violated):
#   mmap     : csi_dma.ko loaded (auto_start=0), side_ch.ko NOT loaded
#   baseline : side_ch.ko loaded, csi_dma.ko NOT loaded (mutually exclusive)
#   AP up, client associated, uplink running for the WHOLE point
#
# Full sweep recipe (one path fully, then switch drivers for the other):
#   # mmap path (csi_dma already loaded by bench_up.sh):
#   for R in 1 5 10 20; do
#       # client: iperf3 -c 192.168.13.1 -u -b ${R}M -l 1400 -t 260
#       ./run_sweep_fixed_rate.sh $R mmap
#   done
#   rmmod csi_dma && insmod side_ch.ko
#   for R in 1 5 10 20; do
#       # client: iperf3 -c 192.168.13.1 -u -b ${R}M -l 1400 -t 260
#       ./run_sweep_fixed_rate.sh $R baseline
#   done
#   python3 summarize_runs.py --dir sweep
#   (dev machine) python3 scripts/plot_sweep.py --summary sweep/summary_mean_std.json
#
# SPDX-License-Identifier: AGPL-3.0-or-later
set -u
RATE=${1:?usage: run_sweep_fixed_rate.sh <rate_mbps> [mmap|baseline] [rounds] [duration_s] [outdir]}
KIND=${2:-mmap}
ROUNDS=${3:-3}
DUR=${4:-60}
OUTDIR=${5:-sweep}
MIN_FPS=${MIN_FPS:-$(( RATE * 44 ))}
FORCE=${FORCE:-0}
WAIT=${WAIT:-0}
WAIT_TIMEOUT=${WAIT_TIMEOUT:-600}

case "$KIND" in
    mmap|baseline) ;;
    *) echo "ERROR: kind must be 'mmap' or 'baseline' (got '$KIND')" >&2; exit 1 ;;
esac
case "$RATE" in
    ''|*[!0-9]*) echo "ERROR: rate_mbps must be a positive integer (got '$RATE')" >&2; exit 1 ;;
esac

SCRIPT_DIR=$(dirname "$0")
fail() { echo "ERROR: $*" >&2; exit 1; }

# ---- path-specific tooling + driver preconditions ----------------------------
if [ "$KIND" = "mmap" ]; then
    if [ -x "$SCRIPT_DIR/csi_bench" ]; then
        BENCH="$SCRIPT_DIR/csi_bench"                 # flat board_kit layout
    else
        BENCH="$SCRIPT_DIR/../user_space/csi_bench"   # project tree layout
    fi
    [ -x "$BENCH" ] || fail "csi_bench not found next to this script"
    grep -q '^side_ch ' /proc/modules 2>/dev/null \
        && fail "side_ch.ko is loaded - rmmod side_ch first (mutually exclusive DT node)"
    grep -q '^csi_dma ' /proc/modules 2>/dev/null \
        || fail "csi_dma.ko not loaded (insmod csi_dma.ko auto_start=0)"
    [ -e /dev/csi_dma ] || fail "/dev/csi_dma missing"
    AUTO=$(cat /sys/module/csi_dma/parameters/auto_start 2>/dev/null || echo '?')
    [ "$AUTO" = "0" ] \
        || echo "WARNING: csi_dma auto_start=$AUTO (expected 0; auto_start=1 disturbs TX beacons)"
    # Same data exit as baseline: forward frames over UDP to the loopback
    # receiver (192.168.10.1:4000) so the only difference vs baseline is the
    # eliminated driver->user copy, not the whole UDP leg.
    UDP_FWD=${UDP_FWD:-1}
    if [ "$UDP_FWD" = "1" ]; then
        if [ -f "$SCRIPT_DIR/csi_udp_recv.py" ]; then
            RECV_PY="$SCRIPT_DIR/csi_udp_recv.py"
        else
            RECV_PY="$SCRIPT_DIR/../board_kit/csi_udp_recv.py"
        fi
        run_point() { # json dur
            local recv_json="${1%.json}_udp.json"
            python3 "$RECV_PY" --port 4000 --num-eq 8 --duration "$2" \
                --json "$recv_json" >/dev/null 2>&1 &
            local rpid=$!
            "$BENCH" -d "$2" -n 8 -u 192.168.10.1 -j "$1"
            wait $rpid
        }
        run_pilot() { # json
            "$BENCH" -d 5 -n 8 -u 192.168.10.1 -j "$1"
        }
    else
        run_point() { # json dur
            "$BENCH" -d "$2" -n 8 -j "$1"
        }
        run_pilot() { # json
            "$BENCH" -d 5 -n 8 -j "$1"
        }
    fi
else
    if [ -x "$SCRIPT_DIR/run_baseline.py" ]; then
        RUN_BASELINE="$SCRIPT_DIR/run_baseline.py"
    else
        RUN_BASELINE="$SCRIPT_DIR/../scripts/run_baseline.py"
    fi
    [ -f "$RUN_BASELINE" ] || fail "run_baseline.py not found next to this script"
    command -v python3 >/dev/null || fail "python3 required on the board"
    if [ -x "$SCRIPT_DIR/side_ch_ctl" ]; then
        export PATH="$SCRIPT_DIR:$PATH"
    fi
    command -v side_ch_ctl >/dev/null || fail "side_ch_ctl not found in PATH or next to this script"
    grep -q '^csi_dma ' /proc/modules 2>/dev/null \
        && fail "csi_dma.ko is loaded - rmmod csi_dma && insmod side_ch.ko first (mutually exclusive)"
    grep -q '^side_ch ' /proc/modules 2>/dev/null || fail "side_ch.ko not loaded"
    run_point() { # json dur
        python3 "$RUN_BASELINE" --duration "$2" --json "$1"
    }
    run_pilot() { # json
        python3 "$RUN_BASELINE" --duration 5 --json "$1"
    }
fi

mkdir -p "$OUTDIR"

# ---- pilot: is the fixed-rate uplink actually flowing? ------------------------
PILOT_JSON="$OUTDIR/pilot_${KIND}_${RATE}mbps.json"
echo "[sweep] pilot run: ${KIND} 5 s (traffic sanity check, MIN_FPS=${MIN_FPS})"
run_pilot "$PILOT_JSON" >/dev/null 2>&1 || fail "pilot run failed"
PILOT_FPS=$(python3 -c "import json;print(json.load(open('$PILOT_JSON'))['frame_rate_hz'])" 2>/dev/null || echo 0)
echo "[sweep] pilot frame rate: ${PILOT_FPS} fps"
LOW=$(python3 -c "print(1 if float('$PILOT_FPS') < float('$MIN_FPS') else 0)")
if [ "$LOW" = "1" ] && [ "$WAIT" = "1" ]; then
    DEADLINE=$(( $(date +%s) + WAIT_TIMEOUT ))
    while [ "$LOW" = "1" ] && [ "$(date +%s)" -lt "$DEADLINE" ]; do
        echo "[sweep] $(date +%H:%M:%S) waiting for uplink: pilot ${PILOT_FPS} fps < MIN_FPS ${MIN_FPS} - start iperf3 on the client: iperf3 -c 192.168.13.1 -u -b ${RATE}M -l 1400 -t $((ROUNDS * DUR + 120))"
        sleep 10
        run_pilot "$PILOT_JSON" >/dev/null 2>&1 || { echo "WARNING: pilot rerun failed" >&2; continue; }
        PILOT_FPS=$(python3 -c "import json;print(json.load(open('$PILOT_JSON'))['frame_rate_hz'])" 2>/dev/null || echo 0)
        LOW=$(python3 -c "print(1 if float('$PILOT_FPS') < float('$MIN_FPS') else 0)")
    done
fi
if [ "$LOW" = "1" ]; then
    MSG="pilot ${PILOT_FPS} fps < MIN_FPS ${MIN_FPS} - uplink looks idle/off or rate too low.
       On the client: iperf3 -c 192.168.13.1 -u -b ${RATE}M -l 1400 -t $((ROUNDS * DUR + 120))
       (FORCE=1 to run anyway - numbers will reflect whatever traffic exists)"
    if [ "$FORCE" = "1" ]; then echo "WARNING: $MSG"; else fail "$MSG"; fi
fi

# ---- manifest -------------------------------------------------------------------
{
    echo "{"
    echo "  \"experiment\": \"fixed-rate sweep point (paper Fig. 3a)\","
    echo "  \"date\": \"$(date -Iseconds)\","
    echo "  \"board\": \"$(uname -n)\","
    echo "  \"kernel\": \"$(uname -r)\","
    echo "  \"kind\": \"$KIND\","
    echo "  \"rate_mbps\": $RATE,"
    echo "  \"rounds\": $ROUNDS,"
    echo "  \"duration_s\": $DUR,"
    echo "  \"min_fps\": $MIN_FPS,"
    echo "  \"pilot_fps\": $PILOT_FPS"
    echo "}"
} > "$OUTDIR/sweep_meta_${KIND}_${RATE}mbps.json"

# ---- the point: R rounds -------------------------------------------------------
TOTAL_START=$(date +%s)
for R in $(seq 1 "$ROUNDS"); do
    if [ "$KIND" = "mmap" ]; then
        OUT="$OUTDIR/sweep_mmap_${RATE}mbps_r${R}.json"
    else
        OUT="$OUTDIR/sweep_base_${RATE}mbps_r${R}.json"
    fi
    echo "[sweep] $(date +%H:%M:%S)  ${KIND}  rate=${RATE}M  round=$R/$ROUNDS  (${DUR}s)  -> $OUT"
    run_point "$OUT" "$DUR" || echo "WARNING: round failed (kind=$KIND rate=$RATE round=$R)" >&2
    sleep 2
done
echo "[sweep] point done in $(( $(date +%s) - TOTAL_START ))s"

# ---- quick sanity: flag rounds far from their group median ----------------------
python3 - "$OUTDIR" "${KIND}" "${RATE}" <<'EOF'
import glob, json, os, sys
d, kind, rate = sys.argv[1], sys.argv[2], sys.argv[3]
pref = "sweep_mmap" if kind == "mmap" else "sweep_base"
rates = []
for f in sorted(glob.glob(os.path.join(d, f"{pref}_{rate}mbps_r*.json"))):
    with open(f) as fh:
        rates.append((os.path.basename(f), json.load(fh).get("frame_rate_hz", 0.0)))
vals = sorted(r for _, r in rates)
med = vals[len(vals) // 2] if vals else 0.0
for f, r in rates:
    if med > 0 and r < 0.5 * med:
        print(f"[sweep] SANITY: {f} fps={r:.1f} < 50% of group median {med:.1f} "
              f"- traffic stall? re-run this round")
EOF
echo "[sweep] next point: start the next iperf3 rate, then ./run_sweep_fixed_rate.sh <rate> $KIND"
echo "[sweep] after ALL points: python3 summarize_runs.py --dir $OUTDIR"
