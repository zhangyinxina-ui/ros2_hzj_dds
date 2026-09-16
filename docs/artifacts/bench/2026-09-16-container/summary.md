# Chain A same-process (patched Humble overlay, container)

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
| `ros_high_throughput` | 64 B | 0.0 | 400 | 0 | 351.23 | 478.17 | 549.69 | 297.00 | 695.29 |
| `ros_high_throughput` | 1024 B | 0.0 | 400 | 0 | 684.19 | 1140.3 | 1550.1 | 626.04 | 2695.7 |
| `ros_high_throughput` | 16384 B | 0.0 | 400 | 0 | 5597.7 | 5945.1 | 6049.1 | 5247.3 | 6258.0 |
| `ros_high_throughput` | 65536 B | 0.0 | 400 | 0 | 21153.9 | 22561.0 | 23713.3 | 20422.0 | 27790.3 |
| `ros_reliable` | 64 B | 0.0 | 400 | 0 | 356.69 | 521.29 | 710.94 | 324.92 | 849.88 |
| `ros_reliable` | 1024 B | 0.0 | 400 | 0 | 673.75 | 1077.9 | 1464.9 | 610.83 | 1781.5 |
| `ros_reliable` | 16384 B | 0.0 | 400 | 0 | 5811.6 | 6859.8 | 13371.9 | 5314.5 | 43496.1 |
| `ros_reliable` | 65536 B | 0.0 | 400 | 0 | 21114.8 | 22176.2 | 23764.8 | 20256.9 | 25934.8 |

## Jitter（主指标：RTT 尾 + inter-message interval）

Do **not** read p50/mean as success. `pub_interval` = consecutive post-warmup publish times (includes timed-out publishes). `arrival_interval` = consecutive successful pong times. `jitter_abs` = |interval − target gap|. `rfc3550` = running mean of |Δinterval|.

| case | payload (B) | RTT p95−p50 | RTT p99−p50 | RTT stdev | pub I p50 | pub I stdev | pub |I−tgt| p95 | pub |I−tgt| p99 | pub rfc3550 | arr I stdev | arr |I−tgt| p95 | arr |I−tgt| p99 |
|------|-------------|-------------|-------------|-----------|-----------|-------------|---------------|---------------|-------------|-------------|---------------|---------------|
| `ros_high_throughput` | 64 B | 126.94 | 198.46 | 49.36 | 576.08 | 62.74 | 154.48 | 241.08 | 33.39 | 65.03 | 167.03 | 241.95 |
| `ros_high_throughput` | 1024 B | 456.11 | 865.92 | 219.56 | 924.00 | 270.38 | 620.80 | 1083.8 | 33.42 | 268.12 | 603.87 | 1178.0 |
| `ros_high_throughput` | 16384 B | 347.43 | 451.39 | 171.82 | 5995.3 | 192.24 | 390.95 | 563.16 | 172.41 | 185.58 | 376.78 | 475.03 |
| `ros_high_throughput` | 65536 B | 1407.1 | 2559.4 | 673.37 | 22165.1 | 756.92 | 1574.6 | 2619.3 | 462.48 | 742.80 | 1505.1 | 2723.0 |
| `ros_reliable` | 64 B | 164.60 | 354.25 | 77.64 | 582.29 | 110.29 | 256.91 | 557.25 | 55.95 | 110.88 | 217.08 | 525.33 |
| `ros_reliable` | 1024 B | 404.12 | 791.19 | 165.01 | 910.71 | 214.14 | 547.15 | 1006.6 | 44.77 | 219.80 | 555.43 | 1076.6 |
| `ros_reliable` | 16384 B | 1048.1 | 7560.3 | 2314.2 | 6236.5 | 2445.6 | 1195.6 | 8241.4 | 253.59 | 2415.5 | 1187.9 | 7856.2 |
| `ros_reliable` | 65536 B | 1061.4 | 2650.1 | 605.01 | 22118.5 | 676.87 | 1303.9 | 2643.8 | 380.50 | 665.47 | 1201.7 | 3016.5 |

QoS 名称对齐 `testdata.py` 预设：`high_throughput` = BestEffort/KeepLast(1)/Volatile；
`reliable` = Reliable(max_blocking_time=0)/KeepLast(5000)/Volatile。
这不是冻结表导航 QoS，也没有改 `ddspubsub` / `rospubsub` 默认值。

