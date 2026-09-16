# Chain A same-process (BASELINE unpatched Humble, container)

- **STATUS:** `ok`
- **Chain:** `A`
- **Topology:** `same-process`
- **Metric:** round-trip time (ping-pong in scripts/bench/pingpong.py) （单位：microseconds）
- **Domain / RMW:** domain_id=`42` RMW=`rmw_fastrtps_cpp` ROS_DOMAIN_ID=`42`
- **CYCLONEDDS_URI / iceoryx:** `(unset)` / `default`
- **Payload sizes (bytes):** `[64, 1024, 16384, 65536]`
- **Inter-message gap:** `0.0` ms (closed-loop only)
- **Scale:** `(n/a)`
- **Primary metric:** jitter (RTT p95/p99 + inter-message interval variance). Do **not** claim success from p50/mean alone.
- **Chain A ros_msg:** `byte_multiarray`

数字是 **ping-pong RTT 与 inter-message interval**（本仓 `scripts/bench/pingpong.py` 里计时），不是中间件根因，也不是和另一条链可比的对照表。 **不是** 飞书现场 / 实机 / 跨机根因证明。

## p50 / p95 / p99（微秒，RTT）

| case | payload (B) | gap (ms) | samples | timeouts | p50 (µs) | p95 (µs) | p99 (µs) | min | max |
|------|-------------|----------|---------|----------|----------|----------|----------|-----|-----|
| `ros_high_throughput` | 64 B | 0.0 | 400 | 0 | 353.62 | 557.48 | 685.50 | 292.00 | 1526.4 |
| `ros_high_throughput` | 1024 B | 0.0 | 400 | 0 | 674.38 | 1067.9 | 2390.7 | 611.33 | 5982.7 |
| `ros_high_throughput` | 16384 B | 0.0 | 400 | 0 | 5596.2 | 5934.6 | 6037.5 | 5297.6 | 6265.1 |
| `ros_high_throughput` | 65536 B | 0.0 | 400 | 0 | 21220.2 | 22689.2 | 25465.3 | 20611.3 | 86748.2 |
| `ros_reliable` | 64 B | 0.0 | 400 | 0 | 341.25 | 399.58 | 497.44 | 316.17 | 696.12 |
| `ros_reliable` | 1024 B | 0.0 | 400 | 0 | 660.06 | 896.14 | 1104.9 | 613.08 | 1442.2 |
| `ros_reliable` | 16384 B | 0.0 | 400 | 0 | 5751.0 | 6726.3 | 8214.7 | 5365.6 | 10373.1 |
| `ros_reliable` | 65536 B | 0.0 | 400 | 0 | 21759.1 | 27427.5 | 47232.5 | 20675.5 | 133822.2 |

## Jitter（主指标：RTT 尾 + inter-message interval）

Do **not** read p50/mean as success. `pub_interval` = consecutive post-warmup publish times (includes timed-out publishes). `arrival_interval` = consecutive successful pong times. `jitter_abs` = |interval − target gap|. `rfc3550` = running mean of |Δinterval|.

| case | payload (B) | RTT p95−p50 | RTT p99−p50 | RTT stdev | pub I p50 | pub I stdev | pub |I−tgt| p95 | pub |I−tgt| p99 | pub rfc3550 | arr I stdev | arr |I−tgt| p95 | arr |I−tgt| p99 |
|------|-------------|-------------|-------------|-----------|-----------|-------------|---------------|---------------|-------------|-------------|---------------|---------------|
| `ros_high_throughput` | 64 B | 203.86 | 331.87 | 101.20 | 575.67 | 132.86 | 249.43 | 485.62 | 250.37 | 129.83 | 235.07 | 512.74 |
| `ros_high_throughput` | 1024 B | 393.56 | 1716.3 | 440.61 | 907.17 | 592.67 | 459.87 | 3916.6 | 47.28 | 595.54 | 450.40 | 3040.3 |
| `ros_high_throughput` | 16384 B | 338.36 | 441.28 | 162.52 | 5995.9 | 178.69 | 341.53 | 535.06 | 171.78 | 172.64 | 338.09 | 531.60 |
| `ros_high_throughput` | 65536 B | 1469.1 | 4245.2 | 3379.0 | 22227.0 | 3427.7 | 1636.4 | 4424.1 | 396.73 | 3410.9 | 1506.6 | 4618.0 |
| `ros_reliable` | 64 B | 58.33 | 156.19 | 36.66 | 559.17 | 42.79 | 87.86 | 182.07 | 14.72 | 42.60 | 86.76 | 174.59 |
| `ros_reliable` | 1024 B | 236.08 | 444.88 | 101.12 | 889.62 | 132.79 | 335.96 | 648.89 | 190.54 | 128.15 | 302.22 | 570.73 |
| `ros_reliable` | 16384 B | 975.37 | 2463.7 | 513.34 | 6157.7 | 582.47 | 1114.4 | 2876.8 | 390.22 | 576.65 | 1075.7 | 2558.9 |
| `ros_reliable` | 65536 B | 5668.4 | 25473.3 | 6708.9 | 22889.8 | 6798.5 | 6300.2 | 25952.7 | 2784.1 | 6836.5 | 6237.8 | 27156.2 |

QoS 名称对齐 `testdata.py` 预设：`high_throughput` = BestEffort/KeepLast(1)/Volatile；
`reliable` = Reliable(max_blocking_time=0)/KeepLast(5000)/Volatile。
这不是冻结表导航 QoS，也没有改 `ddspubsub` / `rospubsub` 默认值。

