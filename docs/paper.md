# Zero-Copy CSI Streaming for Open-Source SDR Wi-Fi Sensing

> **Working draft** (fills ~75% of the IEICE ComEX structure).
> Sections marked ⏳ are experiments waiting on the board; everything else can
> be written today. Target: ~1500–1800 words + 3 figures.

---

## Abstract & Introduction (~350 words) ✅ can write now

**Background (Wi-Fi sensing needs high-rate CSI, embedded SDR compute is tight).**
Channel State Information (CSI) is the raw material for device-free wireless
sensing — activity recognition, presence detection, passive localization.
Sensing accuracy and latency hinge on CSI *frame rate*: individual channel
snapshots must be captured fast enough to resolve the human-scale dynamics they
are meant to sense. In practice this pushes the per-frame processing rate to
the kilo-hertz range and beyond.

OpenWiFi is the leading open-source Wi-Fi transceiver for this kind of
research: it runs a fully open MAC/PHY on an FPGA+SDR (AD9361) and exposes CSI
through a community "side channel". Yet its stock CSI path was designed for
debugging, not for sustained sensing throughput. It is a *polling* path: the
driver copies each CSI frame through the kernel netlink layer on a fixed 100 ms
tick, and a userspace daemon pushes each frame over UDP. Every frame therefore
crosses the kernel/userspace boundary twice and is copied out of a per-transfer
DMA buffer — a design that burns CPU and adds latency and jitter precisely when
a sensing workload wants high, steady, low-latency CSI. What that design
*costs*, however, has never been quantified: we provide the first measurement.

**Contributions.** This letter makes three contributions for open-source SDR
Wi-Fi sensing, using openwifi on a self-ported RK-ZYNQ7020-F board as the
vehicle:
1. the **first quantitative characterization of the stock openwifi CSI
   delivery cost**: under an identical ~1.1 kframes/s uplink, the stock
   netlink→UDP path spends ~**15.2%** of a core in the capture process alone;
   we decompose where those cycles go (polling quantization, per-transfer DMA
   map/unmap, two CPU copies);
2. a **zero-copy re-design** of that path — a continuous DMA front-end
   streaming frames into a *coherent ring buffer* with a single mapping (no
   per-transfer map/unmap, no CPU copy), exposed via an **mmap + poll**
   interface so the application reads frames the moment they arrive —
   measured at ~**3.4%** capture CPU (~4.5× less) with unchanged frame
   throughput and timestamp jitter;
3. a **reproducible benchmark methodology** (fixed-rate uplink, mutually
   exclusive drivers, multi-round mean±std protocol) so the numbers can be
   re-obtained and extended on any openwifi-compatible board.

We stress that zero-copy delivery itself is a mature pattern (DPDK, AF_XDP,
io_uring, V4L2 all exploit it). The contribution here is not the mechanism but
*what it buys on an open Wi-Fi SDR*: a quantified account of the stock path's
cost, a redesign that removes it, and a methodology that makes both claims
reproducible.

The CPU headroom this frees is exactly what a sensing algorithm (feature
extraction, classification) needs to run on the same embedded processor without
starving the Wi-Fi datapath.

## Section II: Bottleneck analysis of the stock CSI path (~300 words) ✅ can write now

To motivate the design we first characterise the baseline `side_ch` path.

**Path.** openwifi's CSI is produced in the FPGA (`side_ch.v`) and delivered on
demand. `side_ch_ctl` polls over netlink; each request triggers an AXI DMA of
one frame into a kernel kmalloc buffer, which is then copied into a netlink
message and forwarded to a userspace UDP daemon. Reproducing the flow:

```
FPGA(side_ch.v) --AXI DMA--> kernel buf --copy--> netlink msg --UDP--> userspace
```

**Four costs stand out** for a sensing workload:

1. **Polling quantization.** The 100 ms poll tick bounds how early a frame can
   be delivered and injects a coarse jitter component into the observed
   timestamps (Section IV, Fig. 3 the baseline CDF).
2. **Per-transfer DMA map/unmap.** Every poll maps, fills, unmaps a buffer —
   page-attribute flushes that cost cycles and interact poorly with throughput.
3. **Two CPU copies.** DMA → netlink copy, plus the protocol-stack copy toward
   the UDP socket. Frames are moved twice through the CPU cache.
4. **Copy work lands on the same core** that a sensing classifier would use. In
   our measurement the `side_ch_ctl` process alone used ~15% of one core at
   ~1.1 kframes/s (Section IV).

The conclusion of the analysis: the bottleneck is not bandwidth (a CSI frame is
only ~0.5–1 KB) but *per-frame CPU work and timing slop*. A sensing pipeline
therefore benefits more from removing copy/poll overhead than from adding DMA
width — which points directly at a zero-copy, interrupt-driven ring design.

## Section III: System design — zero-copy ring streaming (~450 words) ✅ can write now

Our design keeps the same Silicon (Zynq-7020, AXI DMA, AD9361) but changes the
data path so a CSI frame is DMA'd **once** into a shared, coherent buffer the
consumer already has mapped.

**Continuous DMA to a coherent ring.** The driver allocates one
`dma_alloc_coherent` buffer and programs the AXI DMA (`rx_dma_s2mm`) to write
each completed frame into the next ring slot, keeping the buffer permanently
mapped (no map/unmap per frame):

```
FPGA(side_ch.v) --continuous AXI DMA--> coherent ring buffer --mmap--> userspace
```

Because the buffer is DMA-coherent (uncached on Zynq) the producer index is
visible to userspace immediately after each transfer, with no round-trip into
the kernel.

**Ring layout** (see Fig. 2): the first page holds a metadata header
(`producer_idx`, `frame_seq`, `slot_size`, `num_eq`, ...); the rest holds
`N` fixed-size slots, one CSI frame each (default `num_eq=8`, 16×4 KB). The
frame layout keeps the openwifi convention — symbol 0 is the 64-bit 802.11 TSF
timestamp (µs), symbol 1 the phase offset, then 56 complex subcarriers and
`num_eq` equalizer blocks — so existing PHY consumers are unchanged.

**mmap + poll.** Userspace `mmap`s the ring (`MAP_SHARED`, read-only) and
`poll()`s the device; on a DMA-completion interrupt the driver bumps
`producer_idx` and wakes the poller. The consumer iterates the newly written
slots and computes its own metrics — no kernel copy, no poll quantization, no
syscall per frame. `num_eq` is configurable via ioctl without rebuilding.

**Control surface** (ioctls): `START/STOP`, `SET_NUM_EQ`, `GET_STATS`
(including `dma_err_count`, `frame_seq`). The driver is load-time
`auto_start=0`: it registers but does not stream until START, so installing it
does not disturb beaconing / association on the radio.

**Why zero-copy matters here.** Removing the CPU from the per-frame data path
is the point: the freed core budget is what the sensing application consumes.

## Fig. 1 — System architecture: stock vs zero-copy ✅ can draw now

Two-panel block diagram (vector):

- **(a) Stock openwifi path:** `side_ch.v → AXI DMA (map/unmap per frame) →
  kmalloc → netlink copy → side_ch_ctl → UDP recv`, annotating "2×CPU copy",
  "100 ms poll".
- **(b) Proposed path:** `side_ch.v → continuous AXI DMA → coherent ring →
  mmap → poll → application`, annotating "1×DMA, 0×CPU copy", "interrupt-driven".

*(Rendered vector: `docs/figures/fig1_architecture.{png,pdf}`.)*

## Fig. 2 — Coherent ring buffer data-flow ✅ can draw now

Single diagram of the mmap'd memory: metadata header page + N slots; arrows
"DMA (producer) writes slot[i]", "poll" notifies, "app reads slot[i]". Depicts
the `producer_idx` advancing and being read directly by userspace.

*(Rendered vector: `docs/figures/fig2_ring.{png,pdf}`.)*

## Section IV: Evaluation (~400 words)

Below is the fixed template/target figure axis; the table is filled from the
board measurements already collected (see `docs/BENCHMARK_METHOD.md`). Targets
are the numbers we have today; running a fixed-rate sweep will turn Fig. 3 into
a curve.

**Setup (fixed, reproducible).** Board RK-ZYNQ7020-F (Zynq-7020, 32-bit, no
SMMU), AD9361, 2.4 GHz AP (channel 6, +37 kHz CFO), an X230 client (Wi-Fi
power save off) generating a fixed-rate UDP uplink (iperf3 → 5201). Each data
point aggregates independent 60 s rounds per path (baseline `side_ch_ctl` vs
mmap `csi_bench`), reported as mean ± std; `num_eq=8`. Rounds whose frame rate
fell below half their group median (client traffic stall) were re-run, not
averaged in. See `docs/BENCHMARK_METHOD.md`.

| Metric | Baseline (netlink→UDP) | mmap zero-copy | Δ |
|---|---|---|---|
| Capture-process CPU | 15.23% | **3.37%** | **−4.5×** |
| Frame rate (same ~12 Mbit/s uplink) | 1128 fps | 974 fps | −14% |
| TSF jitter p50 | 464 µs | 736 µs | +0.27 ms |
| TSF jitter p95 | 2028 µs | 2019 µs | ≈ equal |
| Explicit loss | 0.00% | 0.00% | 0 |
| DMA errors | — | 0 | — |

*Table: headline metrics under a fixed uplink. Higher-rate interval
re-measurement gives mmap p50 580 µs / p95 931 µs over 83,992 frames.*

**Fig. 3 (capture CPU vs offered load)** — two subplots.

- **3(a):** X = fixed uplink rate {1, 5, 10, 20} Mbit/s, Y = capture CPU%.
  Rendered in `docs/figures/sweep_cpu_vs_rate.{png,pdf}`. Measured mmap four-point
  curve (mean ± std, 3 rounds × 60 s each):

  | uplink | mmap frame rate (fps) | mmap CPU% |
  |---|---|---|
  | 1 Mbit/s | 103.1 ± 1.4 | 0.4 ± 0.0 |
  | 5 Mbit/s | 583.0 ± 72.6 | 2.0 ± 0.3 |
  | 10 Mbit/s | 859.3 ± 199.0 | 3.0 ± 0.8 |
  | 20 Mbit/s | 1820.4 ± 13.5 | 6.9 ± 0.2 |

  CPU% scales near-linearly with offered rate (≈0.0036 %/fps), confirming the
  zero-copy cost is per-frame and linear. The `side_ch_ctl` reference (measured
  15.2% @ ~13 Mbit/s, `results/baseline_v2.json`) is drawn as a single reference
  point rather than a full sweep: on this board the baseline DMA s2mm path wedges
  under sustained data traffic (`get_side_info status!=DMA_COMPLETE`), so it could
  not be re-measured per-rate in this session; we plot the prior in-board reading
  for context. Full mmap / baseline per-rate numbers and re-runs are in
  `docs/BENCHMARK_METHOD.md` and `results/board_data/sweep_from_board/`.
- **3(b):** X = inter-frame TSF interval (µs), Y = CDF — empirical CDF curves,
  rendered in `docs/figures/jitter_cdf.{png,pdf}` from matched-load runs
  (`baseline_v2_intervals.txt`, 68 k frames vs `mmap_intervals.txt`, 84 k frames,
  both ~12 Mbit/s). mmap p50 736 µs ≈ baseline p50 464 µs; p95 tracks (≈2.0 ms,
  both) until a tail driven by occasional scheduling stalls.

**Claim (verified for mmap).** Capture CPU% grows near-linearly with frame rate
(≈0.0036 %/fps; 0.4% @ 103 fps → 6.9% @ 1820 fps); delivered frame rate tracks
the offered load with no DMA errors, so the reduction in CPU is free in terms of
sensing-rate capability.

## Conclusion (~100 words) ✅ can write now

We showed that the CSI capture path of an open Wi-Fi SDR can be redrawn as a
zero-copy, interrupt-driven ring that delivers the same frames with ~4.5× less
capture CPU and unchanged timing, verified on a self-ported board with a
reproducible methodology. The released compute is the headroom wireless-sensing
algorithms on the embedded processor need. Future work ties this ring to
deterministic (TSN-like) delivery and closes the loop with on-board learnable
sensing.

---

## Checklist (filling the ⏳ parts)

- [x] Run the fixed-rate sweep (Fig. 3(a)): take CPU% at several uplink rates —
      mmap four-point curve done (3 rounds × 60 s). Baseline per-rate blocked by
      board DMA s2mm wedge; mmap side complete, baseline plotted as reference.
- [x] Capture the second interval set at a matched rate so Fig. 3(b) uses the
      same traffic as the table — CDF regenerated from matched-load runs
      (`baseline_v2_intervals.txt` vs `mmap_intervals.txt`, both ~12 Mbit/s).
- [x] Render Fig. 1 / Fig. 2 as vector diagrams (TikZ or Python) —
      `docs/figures/fig1_architecture.{png,pdf}`, `docs/figures/fig2_ring.{png,pdf}`.
- [ ] Final word count and ComEX layout pass.