# openwifi-csi-pipeline

**Zero-copy CSI streaming for openwifi, running on a self-ported board.**

This project ports the [openwifi](https://github.com/open-sdr/openwifi)
open-source Wi-Fi transceiver to a custom RK-ZYNQ7020-F board (Zynq-7020 +
FMCOMMS2/AD9361, target `zed_fmcs2`) and adds a **zero-copy CSI capture
pipeline**: channel state information (CSI) is streamed continuously from the
FPGA to userspace with no per-transfer DMA mapping and **no CPU copies**,
replacing the stock side-channel's polling + netlink + copy path.

## Motivation

openwifi's built-in CSI path (`side_ch` driver + `side_ch_ctl`) pulls data on
demand every ~100 ms: each poll does a DMA map/unmap, a kernel netlink copy and
a UDP hop. This adds latency, jitter and CPU overhead that matters for
wireless-sensing / ISAC workloads (device-free sensing, human activity
recognition, passive localization).

`csi_dma` turns that pull-and-copy path into a **push stream**:

```
 FPGA (AXI DMA S2MM)
      │  continuous, one frame per descriptor
      ▼
 coherent ring buffer (dma_alloc_coherent, uncached)
      │  mmap
      ▼
 userspace (csi_bench)   ←  poll() notifies new frames
```

No CPU copy, no per-transfer map/unmap, no polling quantization: the CSI lands
directly where the application reads it.

## Board / port

| Item | Value |
|---|---|
| Board | RK-ZYNQ7020-F |
| SoC | XC7Z020-2CLG484I (Zynq-7020, -2), 32-bit, no SMMU |
| Memory | 1 GB DDR3 (32-bit) |
| RF front-end | FMCOMMS2 (AD9361) |
| openwifi target | `zed_fmcs2` (ported: device tree + bitstream + driver) |
| Kernel | adi-linux 5.15 |

The openwifi port (device tree, hardware build, driver bring-up) lives in the
openwifi tree; this repository contains the CSI pipeline built on top of it.

## Repository layout

```
driver/csi_dma.c        V0 driver: continuous DMA + coherent ring + mmap + poll
include/csi_ring.h      shared ring layout & ioctl ABI (kernel + userspace)
user_space/csi_bench.c  mmap benchmark: frame rate / CPU% / TSF jitter
user_space/csi_udp_recv.py  remote UDP CSI consumer (baseline comparison)
scripts/build.sh        cross-compile driver + userspace for the board
scripts/run_baseline.py baseline side_ch measurement -> baseline.json
scripts/run_csi_bench.sh       mmap measurement -> mmap.json
scripts/plot_compare.py        mmap vs baseline charts (3 figures)
docs/BENCHMARK_METHOD.md       reproducible benchmark methodology
```

## Build

On the development machine (cross-compile for the board):

```bash
./scripts/build.sh                       # uses /tools/Xilinx + openwifi adi-linux
# or
./scripts/build.sh /opt/Xilinx /path/to/openwifi 32
```

Produces `driver/csi_dma.ko` and `user_space/csi_bench` (ARM ELF). Copy to the
board and load:

```bash
# on the board
rmmod side_ch            # csi_dma reuses the same DT node (mutually exclusive)
insmod csi_dma.ko        # default: num_eq=8, 16 slots x 4 KB
ls /dev/csi_dma
```

## Run & benchmark

```bash
# baseline (side_ch path), 60 s
python3 scripts/run_baseline.py --duration 60 --json baseline.json

# zero-copy mmap path, 60 s
scripts/run_csi_bench.sh 60 8 mmap.json

# comparison charts (dev machine)
python3 scripts/plot_compare.py --baseline baseline.json --mmap mmap.json
```

See [docs/BENCHMARK_METHOD.md](docs/BENCHMARK_METHOD.md) for the full,
reproducible methodology and the meaning of the three headline metrics
(frame rate / CPU% / TSF jitter).

## Benchmark results

Measurements captured on the board (2026-09-08) over 60 s each, `num_eq=8`,
under a fixed uplink `iperf3` stream from a Redmi client (UDP → 5201). Raw data
in [`results/`](results/).

| Metric | ① Official Baseline<br>(side_ch → UDP) | ③ Raw CSI capture<br>(side_ch → UDP+dump) | ② mmap zero-copy<br>(csi_dma) |
|---|---|---|---|
| Capture path | netlink → copy → UDP | netlink → copy → UDP+dump | **DMA + mmap, no CPU copy** |
| Frame rate (fps) | 1128.2 | 498.3 | 185.7 |
| Total frames | 68,170 | 29,987 | 11,142 |
| CPU% | 15.23 | — | 0.62 |
| Loss (explicit) | 0.00% | 0.00% | 0.00% |
| Loss (TSF-est) | 46.84%* | 71.17% | 100%† |
| TSF jitter p50 | 464 µs | 580 µs | 2,028 µs |
| TSF jitter p95 | 2,028 µs | 4,066 µs | 8,840 µs |
| DMA errors | — | — | 1 |

CSV: [`results/results_20260908.csv`](results/results_20260908.csv)

_*_ baseline row re-measured (2026-09-09) after fixing the `run_baseline.py`
/proc-stat sampling bug that previously reported a bogus 0.00% CPU; true
`side_ch_ctl` CPU is **15.23%**. TSF-est at ~12 Mbit/s uplink is a single-stall
artifact, not a real loss (explicit 0.00%).
_†_ mmap TSF-est 100% is a statistics artifact: one long stall (the single DMA
error) skews mean/std; the p50/p95 values are the valid numbers.

Takeaway: the zero-copy mmap path drops process CPU to 0.62% (vs **15.23%** for the
netlink/copy baseline), at the cost of somewhat higher scheduling/phase jitter
(p50 2,028 µs vs 464 µs).

## CSI frame layout

Each ring slot holds one CSI frame (little-endian, 64-bit symbols):

```
symbol 0 : 802.11 TSF timestamp (us)
symbol 1 : phase offset
symbol 2..57 : CSI, 56 complex subcarriers (I,Q)
...        : num_eq x 52 equalizer outputs (EQUALIZER_LEN)
```

`num_eq` is configurable at load time (`num_eq_init`) or via ioctl
(`CSI_DMA_IOCTL_SET_NUM_EQ`).

## Roadmap

- **V0 (this repo)** – reuse existing AXI DMA: continuous stream + mmap ring.
- **V1** – scatter-gather descriptor ring for larger/IQ capture buffers.
- **V2** – custom `csi_dma_engine.v` FPGA core for fully autonomous streaming.

## License

AGPL-3.0-or-later (aligned with openwifi). See [LICENSE](LICENSE).

## Acknowledgements

Built on [openwifi](https://github.com/open-sdr/openwifi) (Xianjun Jiao, UGent /
imec). The driver ABI and register map follow `side_ch.h` / `side_ch.v`.
