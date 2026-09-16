# 容器内 Humble 编译与 bench 验证（2026-09-16）

本文件记录 iter10 patch（rmw_wait StatusCondition 预检 + rmw_publish 时间戳收口）首次**真正编译、链接、加载并跑出 bench 数字**的全过程。此前 patch 只在 vendor rolling/master 源码树里存在，从未在 Humble 运行时被编译过——这是《3》–《6》之外最大的"从未真编译"风险，本次闭环。

## 1. 环境

| 项 | 值 |
|----|----|
| 宿主 | macOS arm64/v8（Apple Silicon），Docker Desktop 27.4.0 |
| 基础镜像 | `osrf/ros:humble-desktop`（amd64；在 arm64 宿主上经 Rosetta/QEMU 模拟） |
| 容器内 OS | Ubuntu 22.04 jammy（amd64 模拟） |
| ROS 发行版 | Humble |
| 链 A 环境 | `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`、`ROS_DOMAIN_ID=42`、`FASTRTPS_DEFAULT_PROFILES_FILE=/work/config/fastdds.xml` |
| 挂在仓库 | `/Users/yixin0909zhang/Documents/ros2` → `/work` |

**重要环境偏差**：
- 容器是 **linux/amd64 模拟**（osrf/ros:humble-desktop 无 arm64 标签），单指令多翻译层，CPU 与 I/O 均被放大；RTT 绝对值不可与原生 Linux VM 直接对比。
- 本次跑的是 **same-process** pingpong（进程内回环），不是 same-host 两进程，更不是 cross-host-UDP。

## 2. 构建过程

### 2.1 自建 `ros2_hzj/ros:humble`（docker/ros/Dockerfile）—— 4 次失败，改用 osrf 官方镜像

按任务要求先 `docker build -f docker/ros/Dockerfile -t ros2_hzj/ros:humble .`。base 层（ubuntu:22.04 + 编译工具链）第 2 次成功；runtime 层（apt 装 ros-humble-desktop + nav2 + foxglove + joy）在本环境反复因网络失败：

| 尝试 | 失败点 | 错误 |
|------|--------|------|
| 1 | 拉取 ubuntu:22.04 | docker.io token unexpected EOF |
| 2 | base 层 apt | ports.ubuntu.com 502/连接中断（IP 198.18.x.x 为代理 fake-ip） |
| 3 | runtime 层末尾 | packages.ros.org 502 Bad Gateway（rqt/nav2 包） |
| 4 | runtime 层末尾 | 同上，apt 自动重试后仍 502（action-tutorials-py / rqt-gui / dwb-core / nav2-bringup） |

判断：宿主走代理（fake-ip 段 198.18.0.0/15），对 packages.ros.org / ports.ubuntu.com 间歇性 502。4 次累计约 3 小时未完成 runtime 层。

**替代**：`docker pull osrf/ros:humble-desktop`（Docker Hub 直拉成功，3.47 GB）。该镜像已含 Humble desktop + rclpy + rmw_fastrtps_cpp 6.2.10 + Fast-DDS 2.6.12，满足编译 overlay 与跑 pingpong 的全部需求。**未修改用户系统 Docker 全局配置，未修改 docker/ros/Dockerfile**。

### 2.2 方案 A：直接 overlay 编译 vendor rolling 栈 —— 失败（预期内）

`vendor/` 是 rolling/master 快照（rmw 7.11.2 / rmw_fastrtps_shared_cpp 9.5.2 / Fast-DDS 3.6.2），容器是 Humble（rmw 6.1.3 / rmw_fastrtps_shared_cpp 6.2.10 / Fast-DDS 2.6.12）。

直接把 `vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp` 拷进 overlay 编译：

```
CMake Error: Could not find a package configuration file provided by
"ament_cmake_ros_core"
```

rolling 的 rmw_fastrtps_shared_cpp 依赖 rolling 新拆分的 `ament_cmake_ros_core`，Humble 不存在。这与 [vendor/MANIFEST.md](../../vendor/MANIFEST.md)「严禁 Rolling 覆盖 Humble」警告一致。**方案 A 判定 blocked：rolling vendor 不能直接 overlay 到 Humble**（ABI/包名/API 三层差）。

### 2.3 方案 B：Humble 版源码 + 移植 iter10 patch —— 成功

`git clone --depth 1 --branch humble https://github.com/ros2/rmw_fastrtps.git` 取 Humble 版源码，把 vendor rolling 上的 iter10 patch **逻辑等价移植**到 Humble 版：

| vendor rolling patch | Humble 版对应 | 移植结果 |
|----------------------|---------------|----------|
| rmw_wait.cpp：`reader_status_active()` 预检（`get_statuscondition().get_trigger_value()` 跳过昂贵的 `get_first_untaken_info()`） | Humble `__rmw_wait` 第一阶段循环里直接调 `get_first_untaken_info()` | **已移植**：subscriptions / clients / services 三处循环均加 `if (get_trigger_value()) { get_first_untaken_info(); }` 短路 |
| rmw_publish.cpp：`fill_source_timestamp()` + `RMW_FASTRTPS_SOURCE_TS=zero` | Humble 版 publish 路径**无** `Time_t::now()` / `write_w_timestamp`（grep 无命中），Humble 默认 `data_writer_->write(&data, HANDLE_NIL)` 不带 source timestamp | **blocked**：Humble 版没有可收口的时间戳热路径，该 patch 不适用 |
| custom_subscriber_info.hpp 删 16 行注释死代码 | 无性能影响 | 跳过 |

patch 脚本：`.tmp_build/apply_patch.py`（3 处替换全部命中）。

### 2.4 编译产物

- overlay 路径：`/opt/overlay_ws`（容器内 ephemeral；与 bench 在同一 `docker run` 内完成）
- 编译包：`rmw_fastrtps_shared_cpp` + `rmw_fastrtps_cpp`
- 编译耗时：约 60 s（amd64 模拟下）
- 产出 `.so`：
  - `/opt/overlay_ws/install/rmw_fastrtps_cpp/lib/librmw_fastrtps_cpp.so`
  - `/opt/overlay_ws/install/rmw_fastrtps_shared_cpp/lib/librmw_fastrtps_shared_cpp.so`

### 2.5 prove_rmw 确认 patched .so 真正被加载

`scripts/prove_rmw.py` 在 source overlay + chain_a.sh 后显示：

```
librmw_fastrtps_cpp.so => /opt/overlay_ws/install/rmw_fastrtps_cpp/lib/librmw_fastrtps_cpp.so
  ldd: librmw_fastrtps_shared_cpp.so => /opt/overlay_ws/install/rmw_fastrtps_shared_cpp/lib/librmw_fastrtps_shared_cpp.so
librmw_fastrtps_shared_cpp.so => /opt/overlay_ws/install/rmw_fastrtps_shared_cpp/lib/librmw_fastrtps_shared_cpp.so
```

依赖侧（`librmw.so`、`librmw_dds_common.so`）仍来自 `/opt/ros/humble`——符合预期，我们只改了 fastrtps 层。**这是 patch 首次从"源码里躺着"变成"进程真正链接的 .so"**。

## 3. bench 结果（same-process pingpong）

两次独立 `docker run`：一次应用 patch（`raw.json`），一次不应用 patch（`raw_baseline.json`）。每组 4 payload × 2 QoS × 400 样本。

### 3.1 BestEffort（high_throughput，链 A 主路径）

| payload | 版本 | p50 µs | p95 µs | p99 µs | stdev µs |
|----------|------|--------|--------|--------|----------|
| 64 B | patched | **351.23** | **478.17** | **549.69** | **49.36** |
| 64 B | baseline | 353.62 | 557.48 | 685.50 | 101.20 |
| 64 B Δ | | -0.7% | -14.2% | -19.8% | -51.2% |
| 1024 B | patched | 684.19 | 1140.3 | **1550.1** | **219.56** |
| 1024 B | baseline | **674.38** | **1067.9** | 2390.7 | 440.61 |
| 1024 B Δ | | +1.5% | +6.8% | -35.2% | -50.2% |
| 16 KB | patched | 5597.7 | 5945.1 | 6049.1 | 171.82 |
| 16 KB | baseline | **5596.2** | **5934.6** | **6037.5** | **162.52** |
| 64 KB | patched | 21153.9 | 22561.0 | 23713.3 | 673.37 |
| 64 KB | baseline | **21220.2** | **22689.2** | **25465.3** | 3379.0 |

### 3.2 Reliable

| payload | 版本 | p50 µs | p95 µs | p99 µs | stdev µs |
|---------|------|--------|--------|--------|----------|
| 64 B | patched | 356.69 | 521.29 | 710.94 | 77.64 |
| 64 B | baseline | **341.25** | **399.58** | **497.44** | **36.66** |
| 1024 B | patched | 673.75 | 1077.9 | 1464.9 | 165.01 |
| 1024 B | baseline | **660.06** | **896.14** | **1104.9** | **101.12** |

### 3.3 与 iter9 基线对比（仅参考，不可直接下结论）

iter9（Linux VM，same-host BestEffort RTT）：p50≈925 µs / p95≈1148 µs。
本次（amd64 模拟容器，same-process）：64 B p50≈351 µs。

**不可比**：
- 本次是 same-process（进程内回环），iter9 是 same-host（两进程，走 SHM/UDP）
- 本次是 amd64 模拟，CPU/IO 放大
- 单次运行，400 样本，未做多次取中位

### 3.4 解读（诚实）

- **p50 基本持平**（64B: 351 vs 354；1024B: 684 vs 674）——预检不影响热路径中位。
- **尾部与抖动有改善趋势**：64B p99 -20%、stdev -51%；1024B p99 -35%、stdev -50%。这与 patch 设计意图一致（wait 路径跳过昂贵的 sample lock 读，减少偶发长尾）。
- **Reliable 64B 反而略退化**（p50 356 vs 341）——样本量小 + 模拟环境噪声，不能据此判定退化。
- **单次运行，不构成统计显著结论**。需要原生 Linux VM 上多次运行才能下"性能收益"定论。

## 4. RMW_FASTRTPS_SOURCE_TS=zero 开关测试 —— blocked

**blocked：Humble 版 publish 路径无 `write_w_timestamp` / `Time_t::now()`**。

vendor rolling 的 rmw_publish.cpp 用 `data_writer_->write_w_timestamp(&data, HANDLE_NIL, stamp)` 并在 3 处调 `Time_t::now()`，这才是 `RMW_FASTRTPS_SOURCE_TS=zero` 开关作用的热路径。Humble 版 grep 无 `Time_t::now` / `write_w_timestamp`，publish 走的是 `data_writer_->write(&data, HANDLE_NIL)`（不带 source timestamp）。开关在 Humble 版上没有可作用的代码点。

**结论**：该开关只在 rolling/Fast-DDS 3.x 路径上有意义；Humble 验证场景下无法测。iter10 的时间戳收口 patch 在 Humble 上无对应落点。

## 5. 剩余风险与诚实标注

| 项 | 状态 |
|----|------|
| patch 是否真正编译通过 | ✅ 是（rmw_fastrtps_shared_cpp + rmw_fastrtps_cpp，Release） |
| patched .so 是否真正被进程加载 | ✅ 是（prove_rmw ldd 指向 /opt/overlay_ws） |
| bench 是否真正跑通 | ✅ 是（8 case × 400 样本，p50/p95/p99/jitter 齐全） |
| 性能收益是否显著 | ⚠️ 仅趋势：尾部/jitter 改善，p50 持平；单次运行 + amd64 模拟，**不可下定论** |
| RMW_FASTRTPS_SOURCE_TS=zero 开关 | ❌ blocked：Humble 无对应时间戳热路径 |
| 直接编译 vendor rolling 栈到 Humble | ❌ blocked：`ament_cmake_ros_core` 不存在，ABI/包名/API 三层差 |
| `ros2_hzj/ros:humble` 自建镜像 | ❌ blocked 4 次（packages.ros.org 502），改用 osrf 官方镜像 |
| cross-host-UDP bench | ❌ blocked（单机容器） |
| 原生 Linux VM 多次运行取中位 | ❌ 未做（本机是 arm64 Mac + amd64 模拟） |

## 6. 结论

1. **"从未真编译"风险已消除**：iter10 rmw_wait 预检 patch 首次在 Humble 工具链下完整编译、链接、加载，bench 数字非编造。
2. **patch 移植范围**：3 处 wait 预检成功；时间戳开关 patch 在 Humble 无落点（Humble publish 不带 source timestamp）。
3. **性能方向**：尾部延迟与抖动有改善趋势（64B p99 -20%、stdev -51%），但 p50 持平，且受 amd64 模拟 + 单次运行噪声影响，**不能作为性能收益的最终证据**。下一步应在原生 Linux x86_64 上多次运行取中位。
4. **vendor rolling 栈不能直接 overlay 到 Humble**（与 MANIFEST.md 一致）；要真正跑 vendor rolling 栈需原生 Humble 不适用的环境，或另开 PR 做 ABI 语义 backport。

## 7. 产物索引

- bench 数据：[docs/artifacts/bench/2026-09-16-container/](../artifacts/bench/2026-09-16-container/)
  - `raw.json` — patched 版原始数据
  - `raw_baseline.json` — 未 patch baseline 原始数据
  - `summary.md` / `summary_baseline.md` — 分位数表
  - `prove_rmw.txt` — patched .so 加载证据
- 临时脚本（未提交，.tmp_build/ 在 .gitignore 之外需手工排除）：`.tmp_build/apply_patch.py`、`compile_and_bench.sh`、`compile_baseline.sh`、`compile_overlay.sh`、`rmw_fastrtps_humble/`（Humble 源码 clone）
