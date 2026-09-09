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