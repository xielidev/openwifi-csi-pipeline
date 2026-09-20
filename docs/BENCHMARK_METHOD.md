# Benchmark methodology

This document defines the comparison methodology between the **mmap zero-copy
CSI pipeline** (this project) and the **openwifi community baseline `side_ch`**,
producing four headline metrics corresponding to the three figures of the
paper:

| Metric | Meaning | Figure |
|---|---|---|
| Frame rate / throughput | End-to-end CSI frames actually delivered (frames/s) | Fig.1 bar chart |
| CPU usage | CPU utilization of the capture process (%) | Fig.2 bar chart |
| Timestamp jitter | Distribution of the difference between consecutive 802.11 TSF timestamps (μs) | Fig.3 CDF |
| Packet loss | Fraction of CSI frames lost (%), two definitions: explicit / TSF-estimated | Table + Fig.1 annotation |

## 1. Baseline (`side_ch`)

- Driver: openwifi `driver/side_ch/side_ch.c` (`side_ch.ko`).
- Userspace: `side_ch_ctl g1` (polls CSI via netlink every 1 ms).
- Path: `PL → AXI DMA (a `dma_map_single` per transfer) → kernel kmalloc buffer
  → nlmsg_unicast copy → UDP(192.168.10.1:4000) → remote`.
- CPU copies: ≥ 2 (netlink copy + protocol-stack/UDP processing); every poll
  performs a DMA map/unmap, and the 1 ms polling period introduces quantization
  jitter.
- Run: `scripts/run_baseline.py` starts `csi_udp_recv.py` in parallel (bound to
  `0.0.0.0:4000`, receiving the stream `side_ch_ctl` forwards to
  192.168.10.1:4000) and produces `baseline.json`:
  - Frame rate / jitter: derived from the frames actually parsed at the UDP
    receiver (real delivered frames, not netlink reply counts).
  - CPU%: sampled from `/proc/<pid>/stat` of the `side_ch_ctl` process
    (utime + stime).

## 2. mmap zero-copy (this project)

- Driver: `driver/csi_dma.c` (`csi_dma.ko`, V0).
- Userspace: `csi_bench -d 60 -n 8` (mmap ring buffer + poll, 0 CPU copies).
- Path: `PL → AXI DMA → coherent ring buffer (continuous, no map/unmap) →
  mmap direct read`.
- Frame rate / jitter: parsed directly from the local TSF; CPU%: `getrusage(
  RUSAGE_SELF)` before/after delta.
- Run: `scripts/run_csi_bench.sh` produces `mmap.json`.

## 3. Test conditions (must be fixed, otherwise results are incomparable)

- The same board (RK-ZYNQ7020-F, Zynq-7020, ARCH=32, no SMMU).
- The same RF scenario / traffic source: fixed AP packet injection (fixed frame
  length, MCS, rate) so the CSI generation rate is stable.
- The same `num_eq` (default 8) and the same duration (default 60 s).
- Alternating mutually-exclusive drivers: baseline uses `side_ch.ko`, mmap uses
  `csi_dma.ko` (both share the `sdr,side_ch` DT node, physically exclusive).
- Clean `rmmod`/`insmod` switch before each run; record `dmesg` to confirm no
  DMA errors.

## 4. Execution steps (on the board)

```bash
# A. Baseline
insmod side_ch.ko
python3 scripts/run_baseline.py --duration 60 --json baseline.json

# B. mmap
rmmod side_ch
insmod csi_dma.ko        # num_eq defaults to 8
scripts/run_csi_bench.sh 60 8 mmap.json

# C. Charts (on the development machine)
python3 scripts/plot_compare.py --baseline baseline.json --mmap mmap.json --outdir charts
```

## 5. Excluded cases / known limitations (cite when writing the paper)

- CSI generation rate is bounded by the wireless frame arrival rate; the frame
  rate ceiling comes from the RF scenario, not the capture path. What is
  compared is, *in the same scenario*, which capture path loses fewer frames,
  uses less CPU, and has smaller jitter.
- The baseline polls at 1 ms, so its jitter statistics include the polling
  quantization component; the mmap path is driven by the DMA-completion
  interrupt and is theoretically closer to the hardware truth.
- If `side_ch_ctl g1`'s UDP destination is unreachable (not
  192.168.10.1:4000), the baseline frame rate degrades to an approximation
  parsed from the `side_ch_ctl` log (see `run_baseline.py --recv-json`).
- The mmap path's CPU% accounts only for the `csi_bench` process (excluding the
  UDP forward overhead); for a fair end-to-end comparison, enable `-u`
  forwarding in `csi_bench` and re-measure.

## 6. Relationship to the paper's three figures

ComEX spec (~1500 words + 3 figures): Fig.1 frame rate, Fig.2 CPU%, Fig.3
jitter exactly cover the three benefits of the zero-copy refactor. The narrative
is: after removing the kernel copy and keeping the DMA mapped continuously,
frame rate improves by X%, CPU drops by Y%, and jitter decreases by Z%.

## 7. Statistical protocol (5 rounds, mean ± std)

Single-run numbers are not publishable; every reported data point is the
aggregate of **R = 5 independent rounds** (default; was 3 in the earlier
protocol) under identical traffic. Per round one JSON is produced with a
fixed naming scheme:

| Path | Script (on the board) | Per-round files |
|---|---|---|
| mmap, num_eq scan | `run_scan_num_eq.sh [rounds] [dur] [outdir]` | `mmap_ne<ne>_r<N>.json` for `ne ∈ {0,2,4,8}` |
| mmap, single config | `run_repeats.sh <tag> <num_eq> [rounds] [dur] [outdir]` | `mmap_<tag>_r<N>.json` |
| baseline (side_ch) | `run_repeat_baseline.sh [rounds] [dur] [outdir]` | `baseline_r<N>.json` |

Aggregation (board or dev machine, stdlib only):

```bash
python3 summarize_runs.py --dir <outdir>     # -> summary_mean_std.json + .csv
python3 scripts/plot_num_eq_scan.py --summary <outdir>/summary_mean_std.json
```

- Metrics aggregated per group: frame rate, CPU%, loss, jitter p50/p95.
- `std` is the sample standard deviation (n−1); reported as `mean ± std`.
- **Suspect-round rule:** a round whose frame rate is < 50% of its group
  median is flagged (`suspect_rounds` in the summary) — this is the known
  Redmi iperf3 stall (client stops transmitting mid-run). Re-run flagged
  rounds before publishing; do not silently average dead rounds in.
- Preconditions enforced by the scripts: driver mutual exclusion
  (`csi_dma.ko` XOR `side_ch.ko`), `auto_start=0`, and a traffic pilot
  (5 s probe; aborts if < `MIN_FPS`=200 fps unless `FORCE=1`) so a 20-minute
  scan is never wasted on an idle channel.

## 8. num_eq gradient scan (capture overhead vs frame size)

Purpose: show the zero-copy path's cost **scales gracefully with the CSI
frame size**. `num_eq` appends `num_eq × 52` equalizer symbols to each frame
→ 464 / 1296 / 2128 / 3792 bytes for `num_eq ∈ {0,2,4,8}`. Zero new code:
`csi_bench -n` sets it per run and the driver forwards it to the FPGA
(`SIDE_CH_REG_NUM_EQ`).

Protocol: fixed uplink traffic (same rate for **all** points — the comparison
is across frame size at constant offered load), AP up, `csi_dma.ko` loaded
(`auto_start=0`):

```bash
# client: iperf3 -c 192.168.13.1 -u -b 12M -l 1400 -t <rounds*60*4+60>
./run_scan_num_eq.sh                 # 4 points × 5 rounds × 60 s ≈ 21 min
```

Expected shape: mmap CPU% stays low and grows only mildly with frame bytes
(no per-frame copy exists to multiply); frame rate is statistically flat
across points. The `ne=8` group doubles as the mmap side of the headline
comparison — the baseline repeats (`run_repeat_baseline.sh`) must run under
the *same* traffic, board state and CFO offset.

## 9. Fixed-rate sweep (paper Fig. 3(a): capture CPU% vs offered load)

Purpose: the Fig. 3(a) deliverable — capture CPU% (and delivered frame rate as
sanity) at several *fixed* offered uplink rates, for BOTH paths. Uplink only
(CSI frames come from RX at the AP). Uses the dedicated board script
`run_sweep_fixed_rate.sh <rate_mbps> [mmap|baseline] [rounds] [dur] [outdir]`,
which runs a 5 s traffic pilot (aborts below `MIN_FPS` unless `FORCE=1`,
default threshold ≈ rate×44 fps) then `R` rounds, writing
`sweep_{mmap,base}_<rate>mbps_r<N>.json` into ONE shared outdir.

Protocol (3 rounds × 60 s per point per path by default):

```bash
# --- mmap path first (csi_dma.ko already loaded by bench_up.sh) ---
for R in 1 5 10 20; do
    # client (X230): iperf3 -c 192.168.13.1 -u -b ${R}M -l 1400 -t 260
    ./run_sweep_fixed_rate.sh $R mmap
done
# --- switch drivers (mutually exclusive), then baseline path ---
rmmod csi_dma && insmod side_ch.ko
for R in 1 5 10 20; do
    # client: iperf3 -c 192.168.13.1 -u -b ${R}M -l 1400 -t 260
    ./run_sweep_fixed_rate.sh $R baseline
done
# --- aggregate + figure ---
python3 summarize_runs.py --dir sweep
python3 scripts/plot_sweep.py --summary sweep/summary_mean_std.json --outdir docs/figures
```

Notes:
- iperf3 must cover the pilot + all rounds of one point (~230 s); start it
  BEFORE running the script for that rate.
- The 1 Mbit/s point yields only ~90 fps of CSI (beacons add a small floor);
  the rate-scaled `MIN_FPS` avoids false aborts at low rates.
- `summarize_runs.py` groups by filename (`sweep_mmap_<rate>mbps` /
  `sweep_base_<rate>mbps`) and exposes the `sweep` view; `plot_sweep.py`
  renders `sweep_cpu_vs_rate` (Fig. 3(a)) plus `sweep_fps` (sanity).
- A load0/idle-floor point (no iperf) can be taken with `FORCE=1` if needed;
  by default the pilot check aborts idle points.