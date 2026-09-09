# 基准测试方法（BENCHMARK_METHOD）

本文档定义「mmap 零拷贝 CSI 管道」与「openwifi 社区基线 side_ch」之间的对比
测试方法，产出四大指标（对应论文的三张图）：

| 指标 | 含义 | 图 |
|---|---|---|
| 帧率/吞吐率 | 端到端实际交付的 CSI 帧速率（frames/s） | Fig.1 柱状 |
| CPU 占用 | 采集进程的 CPU 使用率（%） | Fig.2 柱状 |
| 时间戳抖动 | 相邻帧 802.11 TSF 时间戳差值的分布（μs） | Fig.3 CDF |
| 丢包率 | CSI 帧丢失比例（%），两种口径：显式 / TSF 估计 | 表 + Fig.1 标注 |

## 1. 基线（baseline）

- 驱动：openwifi `driver/side_ch/side_ch.c`（`side_ch.ko`）
- 用户态：`side_ch_ctl g1`（每 1 ms 经 netlink 拉取一次 CSI）
- 路径：`PL → AXI DMA（每次 dma_map_single）→ 内核 kmalloc 缓冲 → nlmsg_unicast
  拷贝 → UDP(192.168.10.1:4000) → 远端`
- CPU 拷贝次数 ≥2 次（netlink 拷贝 + 协议栈/UDP 处理），每次拉取都做 DMA
  map/unmap，且轮询周期 1 ms 引入量化抖动。
- 运行：`scripts/run_baseline.py` 自动并行启动 `csi_udp_recv.py`（本机绑定
  `0.0.0.0:4000`，接收 side_ch_ctl 转发到 192.168.10.1:4000 的数据流），
  产出 `baseline.json`：
  - 帧率 / 抖动：来自 UDP 接收端实际解析到的帧（真实交付帧，而非 netlink 应答数）
  - CPU%：对 `side_ch_ctl` 进程按 `/proc/<pid>/stat` 采样（utime+stime）

## 2. mmap 零拷贝（本工程）

- 驱动：`driver/csi_dma.c`（`csi_dma.ko`，V0）
- 用户态：`csi_bench -d 60 -n 8`（mmap 环形缓冲 + poll，0 CPU 拷贝）
- 路径：`PL → AXI DMA → coherent 环形缓冲（持续，无 map/unmap）→ mmap 直接读`
- 帧率 / 抖动：本地直接解析 TSF；CPU%：`getrusage(RUSAGE_SELF)` 前后差分
- 运行：`scripts/run_csi_bench.sh` 产出 `mmap.json`

## 3. 测试条件（必须固定，否则不可比）

- 同一块板（RK-ZYNQ7020-F，Zynq-7020，ARCH=32，无 SMMU）
- 同一 RF 场景 / 流量源：固定 AP 发包（帧长、MCS、速率固定），保证 CSI 产生率稳定
- 相同 `num_eq`（默认 8）与相同时长（默认 60 s）
- 交替加载互斥驱动：基线用 `side_ch.ko`，mmap 用 `csi_dma.ko`（二者共用
  `sdr,side_ch` DT 节点，物理上互斥）
- 运行前 `rmmod`/`insmod` 干净切换；记录 `dmesg` 确认无 DMA 报错

## 4. 执行步骤（板上）

```bash
# A. 基线
insmod side_ch.ko
python3 scripts/run_baseline.py --duration 60 --json baseline.json

# B. mmap
rmmod side_ch
insmod csi_dma.ko        # num_eq 默认 8
scripts/run_csi_bench.sh 60 8 mmap.json

# C. 图表（在开发机）
python3 scripts/plot_compare.py --baseline baseline.json --mmap mmap.json --outdir charts
```

## 5. 已排除 / 已知限制（写论文时引用）

- CSI 产生率受无线帧到达率上限约束，帧率天花板来自 RF 场景而非采集路径；
  对比的是「同场景下两条采集路径谁丢得少、谁 CPU 低、谁抖动小」。
- 基线轮询为 1 ms，抖动统计包含轮询量化分量；mmap 路径由 DMA 完成中断驱动，
  理论上更接近硬件真值。
- 若 `side_ch_ctl g1` 的 UDP 目的不可达（非 192.168.10.1:4000），基线帧率退化为
  解析 side_ch_ctl 日志的近似值（见 `run_baseline.py --recv-json` 说明）。
- mmap 路径的 CPU% 仅统计 `csi_bench` 进程（不含转发 UDP 的开销）；如需
  端到端公平对比，可在 csi_bench 开启 `-u` 转发后再测。

## 6. 与论文三图的关系

ComEX 规格（~1500 词 + 3 图）：Fig.1 帧率、Fig.2 CPU%、Fig.3 抖动，正好覆盖
零拷贝改造的三个收益面，结论叙事为「去掉内核拷贝 + 持续 DMA 映射后，
帧率提升 X%、CPU 下降 Y%、抖动下降 Z%」。
