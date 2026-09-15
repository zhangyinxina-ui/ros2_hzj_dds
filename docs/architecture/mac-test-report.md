# macOS 系统测试报告：ROS 2 DDS 双链（修改后 vendor C++）

- 测试日期：2026-09-16（周三，JST）
- 测试分支 / 基线：`main` @ `c1f95eac`（对比基线 `06794bbf..HEAD`）
- 测试范围：《3》在 `vendor/` + `config/` 的 5 个改动 + 《5》评估套件
- 纪律声明：本机无 ROS 2 运行时，所有节点级 pingpong 一律标 **BLOCKED**，未编造任何 RTT 分位数。历史分位数仅作为"已有基线指针"引用，来源是 Linux VM 上的 iter9，**不是本机实测**。

---

## 1. 测试环境摘要

| 项 | 值 | 来源 |
|----|----|------|
| OS | macOS 26.5.1（Build 25F80，Darwin） | `sw_vers` |
| Python | 3.14.7（沙箱运行时内置，非系统 Python） | `python3 --version` |
| ROS 2 | **未安装**：无 `/opt/ros`、`ros2`/`colcon` 不在 PATH、`import rclpy` 失败 | `prove_rmw.py` + 手动确认 |
| rmw 运行时 | `(not loaded)`，无 `librmw_*.so` | `scripts/prove_rmw.py` |
| Docker | 二进制在 `/usr/local/bin/docker`，但 **守护进程未运行** | `docker info` 失败 |
| 编译器 | `/usr/bin/clang++`、`/usr/bin/g++`（clang shim）、`/opt/homebrew/bin/cmake` | `which` |
| brew 依赖 | 有 cmake、openssl@3；**缺** asio、tinyxml2、poco、foonathan_memory | `brew list` |
| cyclonedds python | **未安装**（`import cyclonedds` 失败） | 手动确认 |
| dimos / Unitree SDK | 本机无 dimos 包、无 Unitree SDK | 任务前提 |

环境结论：本机只具备"文档/闸门/静态分析"能力，不具备"节点级 DDS 通信"能力。

---

## 2. 三个用例的测试结果

### 用例 1：链 A Fast-DDS 同机 pub/sub pingpong（域 42）

| 子项 | 执行了什么 | 结果 |
|------|-----------|------|
| 环境身份 | `python3 scripts/prove_rmw.py` | exit 0；确认 `ROS_DISTRO/RMW/ROS_DOMAIN_ID` 全 unset、rclpy 不可导入、无 librmw |
| 闸门套件 | `run_evals.py --suite gate` | **13/13 pass, 0 fail, 0 blocked**（含 12/12 闸门 + prove_rmw） |
| 配置套件 | `run_evals.py --suite config` | **13/13 pass**：fastdds.xml well-formed、domainId=42、SHM 描述符在、socket buffer 2MiB、chain_a.sh 锚定 `rmw_fastrtps_cpp`+`ROS_DOMAIN_ID=42` |
| 静态审查 rmw_wait.cpp | 通读 diff + 上下文 | 见 §4 审查结论：API 链真实、掩码前提成立、无逻辑回归 |
| 静态审查 rmw_publish.cpp | 通读 diff + 头文件核对 | 见 §4：`Time_t(0,0)`/`now()`/`to_ns()` 均存在，默认路径字节级不变 |
| C++ 语法检查 | `clang++ -std=c++17 -fsyntax-only` | **BLOCKED**：预处理期即缺 `rcutils/macros.h` → `rcutils/error_handling.h` 等整条 ROS 2 核心头闭包（未 vendor、brew 不可装） |
| Docker 构建 Humble | `docker build docker/ros/` | **BLOCKED**：Docker 守护进程未运行（需用户启动 Docker Desktop） |
| 节点级 pingpong（p50/p95/p99） | 未执行 | **STATUS: blocked (no ROS 2 runtime on macOS)** |

**用例 1 结论**：可静态验证的部分全部通过；节点级 RTT 分位数无法在本机测量。

### 用例 2：链 B CycloneDDS 同机 pingpong / Unitree 兼容性（域 0）

| 子项 | 执行了什么 | 结果 |
|------|-----------|------|
| 链 B 环境变量 | `python3 config/env/load.py print-b` | exit 0；输出 `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`、`ROS_DOMAIN_ID=0` |
| 双链隔离 | `run_evals.py --suite isolation` | **7/7 pass**：域 42 vs 0 不同、RMW 不同、fastdds.xml 只挂链 A、chain_b.sh 显式 `unset CYCLONEDDS_URI`、topics 无交集、load.py 顶层不写 `os.environ` |
| vendor 关键路径 | 通读 `check_executor_map`/`check_source_map` 引用 | CycloneDDS `dds_waitset_wait`/`dds_take`、rmw_cyclonedds `rmw_wait`@L4511 等符号在盘上、allowlist 0 warning |
| Unitree 兼容性文档 | 读 `docs/architecture/unitree-sdk2-dds-swap.md` | 已知结论：**drop-in FAIL / wire UNPROVEN**（Unitree bundled Cyclone 0.10.2 ≠ vendor 11.0.1，主版本/ABI 不同）；合法换库路径是独立仓 `unitree_sdk2_hzj` + opt-in `UNITREE_DDS_PROVIDER=external`，默认仍 bundled |
| 链 B 节点级 pingpong | 未执行 | **STATUS: blocked (no cyclonedds runtime / no dimos / no Unitree SDK)** |

**用例 2 结论**：双链配置/隔离静态健康；Unitree 兼容性维持既有"drop-in FAIL / wire UNPROVEN"裁决，未被本测试改变；节点级实测 blocked。

### Hero 用例 3：大包（1–8MB）+ IMU 高频小包（64B/200Hz jitter）

| 子项 | 执行了什么 | 结果 |
|------|-----------|------|
| 测试方法 | 读 `scripts/bench/run_large_packet.sh`、`run_imu_hf.sh` | 大包：100KiB/256KiB/1MiB、80 样本、10Hz；IMU：64B、400 样本、5ms 间隔（200Hz）、主指标是 jitter（RTT 尾 + 间隔 \|I−5ms\|） |
| 历史基线 | 读 `docs/artifacts/bench/2026-09-11-iter9/chain_a_same_host/summary.md` | 已有 Linux VM 基线（见下，**非本机实测**） |
| SHM 配置影响 | 读 `config/fastdds.xml` | `maxMessageSize=280000`（~273KiB）、`segment_size=2MiB`、`useBuiltinTransports=true`。100/256KiB 走 SHM；**1MiB 超过 maxMessageSize，按设计回退 builtin UDP loopback**（不强制走 SHM）。SCOREBOARD 也记录"exclusive/oversized SHM 曾擦除 1MiB BestEffort"，故刻意不让 SHM 扛 1MiB |
| rmw_wait 优化对 jitter 预期收益 | 静态分析 | 见下：预检在"被 timer/guard/超时唤醒"的常见路径上省去 `get_first_untaken_info()` 的读锁 + SampleInfo 拷贝，预期降低尾延迟，**但需真机复测才能量化** |
| 实测 | 未执行 | **STATUS: blocked (requires ROS 2 runtime + real LiDAR/IMU data)**；且脚本本身依赖 Docker（`osrf/ros:humble-desktop`） |

**历史基线指针（iter9，Linux VM，64B/200Hz same-host，0/400 timeout）**：

| QoS | RTT p50 | RTT p95 | RTT p99 | 到达 \|I−5ms\| p95 | 到达 \|I−5ms\| p99 |
|-----|---------|---------|---------|--------------------|--------------------|
| high_throughput (BestEffort/Kl1/Volatile) | 925.01 µs | 1148.3 µs | 1252.6 µs | 542.90 µs | 674.64 µs |
| reliable (Reliable/Kl5000/Volatile) | 941.24 µs | 1196.7 µs | 1312.2 µs | 587.49 µs | 750.56 µs |

> 上表来自 `2026-09-11-iter9/chain_a_same_host/summary.md`，是过去在另一台 Linux 机器上跑的产物，**本机未复跑**，仅作基线指针引用。

---

## 3. Bug 列表

本轮**未发现新的代码 bug**。静态审查（见 §4）确认《3》的两个 C++ 改动 API 真实、默认行为不变、关键前提成立；10 个闸门与 45 项评估全部 0 fail。

> 说明：由于节点级运行时缺失，"运行时才会暴露的 bug"（如并发竞态、特定 QoS 下的边界行为）**无法排除**，状态记为"静态未发现，运行时待验证"，而非"无 bug"。

---

## 4. 代码改动静态审查记录（`06794bbf..HEAD -- vendor/ config/`）

改动共 5 文件、+125/−22：

1. **`rmw_fastrtps_shared_cpp/src/rmw_wait.cpp`（+54）— StatusCondition 预检**
   - 新增 `reader_status_active()`：`data_reader && data_reader->get_statuscondition().get_trigger_value()`。
   - **API 真实性已核对**：`DataReader : public DomainEntity`（`DataReader.hpp:84`），`get_statuscondition()` 来自 `Entity.hpp:130` 返回 `StatusCondition&`，`get_trigger_value()` 在 `StatusCondition.hpp:69`。
   - **正确性前提已核对**：`rmw_subscription.cpp:187-188` 在创建 reader 后显式 `get_statuscondition().set_enabled_statuses(StatusMask::data_available())`，即 StatusCondition 始终监听 data_available。因此 `trigger_value==false ⇒ data_available 未激活 ⇒ 无未取样本` 的早退出成立，不会漏数据。
   - `subscription_has_data()` 重构为带显式 nullptr 短路的三段式，与原三段 `||` 语义等价；cpu/accel reader 本就是 buffer-aware 订阅才有。
   - 结论：**无语法/逻辑回归迹象**。

2. **`rmw_fastrtps_shared_cpp/src/rmw_publish.cpp`（+44）— Time_t::now() 收口 + 环境开关**
   - 新增匿名命名空间内 `source_ts_zero_mode()`（magic-static 一次性读 `RMW_FASTRTPS_SOURCE_TS`，C++11 静态初始化线程安全）与 `fill_source_timestamp()`。
   - 三处 `Time_t::now(stamp)` 改走 helper。默认无 env 时走 `Time_t::now(stamp)`，**与原行字节级等价**；`=zero` 时写 `Time_t(0,0)`。
   - **API 真实性已核对**（`fastdds/dds/core/Time_t.hpp`）：`Time_t(int32_t, uint32_t)`@L50、`static void now(Time_t&)`@L80、`int64_t to_ns() const`@L68 均存在。
   - 小注意（非 bug）：env 在进程启动后改不生效（by design，注释已写）；仅建议 BestEffort 高频遥测用 `=zero`。
   - 结论：**无语法/逻辑回归迹象**。

3. **`custom_subscriber_info.hpp`（−16）— 删除注释死代码**
   - 仅删除一段被注释的 `on_type_discovery` 声明块，无任何活动代码变动。结论：无风险。

4. **`config/fastdds.xml`（+15）**
   - 主体是注释（iter10 说明）；唯一生效行是显式 `<port_queue_capacity>512</port_queue_capacity>`，注释标明为 Humble 2.6 默认值（iter6 探 64 曾回退）。XML 已通过 well-formed 校验。
   - 结论：文档化改动，不改行为。

5. **`vendor/VERSIONS.md`（+18）**：版本表追加，纯文档。

---

## 5. 分诊总结

| 维度 | 结果 |
|------|------|
| 全量闸门（10 个 `check_*.py`） | **全部 PASS**（每个 exit 0） |
| 评估套件 `--suite all` | **44 pass / 0 fail / 1 blocked**（blocked=`bench.ros_available`，预期内），JSON 已落 `/tmp/mac-test-eval.json` |
| gate / config / isolation 子套件 | 13/13、13/13、7/7 全过 |
| XML 校验 | well-formed |
| 代码静态审查 | 2 个 C++ 改动 API 真实、默认行为不变、关键前提成立；**未发现 bug** |
| C++ 语法检查（`clang++ -fsyntax-only`） | **BLOCKED**：缺 ROS 2 核心头闭包（rcutils/rosidl_runtime_c 未 vendor、brew 不可装） |
| 用例 1 节点级 pingpong | **BLOCKED**（无 ROS 2 运行时） |
| 用例 2 节点级 + Unitree 实测 | **BLOCKED**（无 cyclonedds runtime / dimos / Unitree SDK）；既有"drop-in FAIL / wire UNPROVEN"裁决维持 |
| Hero 用例 3 实测 | **BLOCKED**（依赖 ROS 2 运行时 + 真机数据；bench 脚本另依赖 Docker） |
| `dimos_bridge/` | 工作区有 2 个并行改动（`transport.py`、`rospubsub_conversion.py`），**非本次任务，未 stage/commit** |

**通过**：全部文档/闸门/配置/隔离类检查；两个 C++ 改动的静态 API 正确性。
**Blocked**：所有需要 ROS 2 运行时的节点级通信与分位数测量；C++ 完整编译；Docker Humble 构建。
**失败**：无（0 fail）。
**新 bug**：无（静态层面）。

### 下一步建议
1. 在 Linux 或 `docker/ros/`（需先启动 Docker Desktop）内用 Humble underlay 做一次真正的 `colcon build`，把本轮"静态 API 核对"升级为编译验证；这也是 `clang++ -fsyntax-only` 真正能跑通的环境。
2. 同一环境复跑 `run_evals.py --suite all`，重点观察 `bench.ros_available` 从 blocked 转 pass，并在 same-host 复测 64B/200Hz，验证 rmw_wait 预检对 RTT 尾（p95/p99、到达 \|I−5ms\|）的收益。
3. 大包用例在同一环境跑 `run_large_packet.sh`，确认 100/256KiB 走 SHM、1MiB 走 UDP loopback 且不丢包。
4. Unitree 兼容性走 `unitree_sdk2_hzj` + `UNITREE_DDS_PROVIDER=external` 独立路径验证 wire 互通，**不要**把 vendor 11.0.1 就地覆盖 bundled 0.10.2。
5. 本轮报告的"运行时待验证"项（并发/竞态）需在真机环境另行设计用例。

---

## 6. 已运行的命令清单（本机）

```bash
# 环境探查
sw_vers; python3 --version
ls /opt/ros; which ros2 colcon
python3 -c "import rclpy"            # ModuleNotFoundError
which docker; docker info            # daemon not running
which clang++ g++ cmake brew
python3 -c "import cyclonedds"      # ModuleNotFoundError

# 闸门（全部 exit 0）
for f in scripts/check_*.py; do python3 "$f"; done
#   check_cega_bridge_hold / dod_evidence / dual_chain_baseline / executor_map /
#   risk_matrix / runtime_provenance / sink_layers / source_map /
#   three_chain_repro / unitree_cyclone_swap  — 共 10 个，全部 exit 0

# 身份与评估
python3 scripts/prove_rmw.py                                          # exit 0
python3 config/env/load.py print-b                                   # exit 0 -> RMW=rmw_cyclonedds_cpp, DOMAIN=0
python3 scripts/eval/run_evals.py --suite gate                       # 13/13 pass
python3 scripts/eval/run_evals.py --suite config                     # 13/13 pass
python3 scripts/eval/run_evals.py --suite isolation                  # 7/7 pass
python3 scripts/eval/run_evals.py --suite all -o /tmp/mac-test-eval.json
#   total=45 pass=44 fail=0 blocked=1  (blocked=bench.ros_available)

# 代码审查
git diff --stat 06794bbf..HEAD -- vendor/ config/
git diff 06794bbf..HEAD -- vendor/.../rmw_wait.cpp custom_subscriber_info.hpp rmw_publish.cpp config/fastdds.xml
grep get_statuscondition / get_trigger_value / Time_t(...)  # 核对 vendored 头文件
Read vendor/.../rmw_subscription.cpp:187-188               # 核对 StatusCondition 掩码

# 语法检查尝试（BLOCKED）
clang++ -std=c++17 -fsyntax-only -I vendor/Fast-DDS/include -I vendor/rmw/rmw/include \
  -I vendor/rmw_fastrtps/.../include -I vendor/rmw_fastrtps/.../src rmw_wait.cpp
#   fatal error: 'rcutils/macros.h' file not found
#   建 /tmp 桩头后重试，级联缺 rcutils/error_handling.h 等，停止并记录 blocked

# XML / 仓库状态
python3 -c "import xml.etree.ElementTree as ET; ET.parse('config/fastdds.xml')"   # XML wellformed
git status --short dimos_bridge/    # M transport.py / rospubsub_conversion.py （非本次，未动）

# 静态材料阅读
cat scripts/bench/run_large_packet.sh scripts/bench/run_imu_hf.sh
sed -n 1,60p docs/architecture/unitree-sdk2-dds-swap.md
cat docs/artifacts/bench/2026-09-11-iter9/chain_a_same_host/summary.md
sed -n 108,140p config/fastdds.xml
```

**未安装任何 brew 包**（语法检查在确认依赖为 ROS 2 核心头、brew 无法解决后即停止，未安装 asio/tinyxml2 等）。
