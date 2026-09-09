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
a sensing workload wants high, steady, low-latency CSI.

**Contributions.** This letter reports a zero-copy re-design of that CSI path
for an open platform (openwifi on a self-ported RK-ZYNQ7020-F board):
1. a **continuous DMA** front-end that streams CSI frames into a *coherent
   ring buffer* with a single mapping (no per-transfer map/unmap, no CPU copy);
2. an **mmap ring** interface that exposes the frames directly to userspace, so
   the application reads channel data the moment it arrives;
3. a benchmark and reproducibility methodology, with measured evidence that the
   redesign releases the capture CPU to ~**3.4%** vs **15.2%** for the stock
   netlink path — an ~4.5× reduction — while keeping frame throughput and
   timestamp jitter essentially unchanged.

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

*(Generate with a TikZ/Python script; placeholder below.)*

## Fig. 2 — Coherent ring buffer data-flow ✅ can draw now

Single diagram of the mmap'd memory: metadata header page + N slots; arrows
"DMA (producer) writes slot[i]", "poll" notifies, "app reads slot[i]". Depicts
the `producer_idx` advancing and being read directly by userspace.

*(Placeholder.)*

## Section IV: Evaluation (~400 words) ⏳ only section needing experiments

Below is the fixed template/target figure axis; the table is filled from the
board measurements already collected (see `docs/BENCHMARK_METHOD.md`). Targets
are the numbers we have today; running a fixed-rate sweep will turn Fig. 3 into
a curve.

**Setup (fixed, reproducible).** Board RK-ZYNQ7020-F (Zynq-7020, 32-bit, no
SMMU), AD9361, 2.4 GHz AP (channel 6, +37 kHz CFO), a Redmi client generating a
fixed-rate UDP uplink (iperf3 → 5201). Each path (baseline `side_ch_ctl` vs mmap
`csi_bench`) ran 60 s, `num_eq=8`. See `docs/BENCHMARK_METHOD.md`.

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

**Fig. 3 (target sweep)** — two subplots (⏳ fill after fixed-rate runs):

- **3(a):** X = fixed uplink rate {1, 5, 10, 20} Mbit/s (≈ frames offered), Y =
  capture CPU% — line for baseline vs mmap.
- **3(b):** X = inter-frame TSF interval (µs), Y = CDF — empirical CDF curves;
  today's data are in `docs/figures/jitter_cdf.png`.

**Claim to verify.** The CPU saving grows with frame rate (equal per-frame copy
savings × rate) while frame rate and jitter track the baseline — i.e. the
reduction in CPU is *free* in terms of sensing-rate capability.

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

- [ ] Run the fixed-rate sweep (Fig. 3(a)): take CPU% at several uplink rates.
- [ ] Capture the second interval set at a matched rate so Fig. 3(b) uses the
      same traffic as the table (optional; current CDF is already valid).
- [ ] Render Fig. 1 / Fig. 2 as vector diagrams (TikZ or Python).
- [ ] Final word count and ComEX layout pass.