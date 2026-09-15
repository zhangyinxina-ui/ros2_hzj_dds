# 评估驱动的 DDS 改进循环（eval-driven loop）

Status: **Iter10 已落地，当前最佳。** 日期：2026-09-16（JST）。
范围：`vendor/rmw_fastrtps/`、`vendor/Fast-DDS/`（只读分析）、`config/fastdds.xml`。
前置：[dds-request-flow.md](dds-request-flow.md)、[refactor-plan.md](refactor-plan.md)。

> 本仓不是 LLM 应用，没有 prompt/provider，也没有"LLM 平均分"。本文档用
> `scripts/eval/run_evals.py`（45 项静态+解析断言）+ 仓库原有 12 个
> `check_*.py` 闸门作为可重复的评分口径，**不编造分位数**。

---

## 1. 评分口径定义（迭代前先钉死）

| 维度 | 权重 | 测量方式 | 本机可测？ |
|------|------|----------|-----------|
| ① 闸门通过 | 30% | 12 个 `scripts/check_*.py` + `prove_rmw.py` + `print_bench_gates.py` 全部 exit 0 | ✅ |
| ② bench 时延（p50/p95/p99） | 40% | `scripts/eval/run_evals.py` 的 bench 套件：检测 ROS 可用性 + 解析最新 iter `raw.json` | ❌ **blocked-on-runtime**（无 `/opt/ros/humble`、无 `rclpy`） |
| ③ 代码健康 | 30% | `api.signature_diff`（7 个公共 rmw 函数签名与 HEAD 一致）、死代码只减不增、公共 RMW API 稳定 | ✅ |

**综合分**：只对①③两个可测维度计权（60 分制）；② 单独标注 `STATUS:
blocked-on-runtime`，不折算成假分。任何端到端 p50/p95/p99 数字必须来自
真实 `raw.json`，本机一律不造。

基线（iter9 之后、本循环开始前）：`docs/artifacts/eval/baseline.json`
= **45 项：44 pass / 0 fail / 1 blocked**。闸门 12/12。
bench 历史参考（非本次测量，仅作参照，来自
`docs/artifacts/bench/2026-09-11-iter9/chain_a_same_host/raw.json`）：

| case | p50 µs | p95 µs | p99 µs | timeouts |
|------|--------|--------|--------|----------|
| ros_high_throughput | 925.0 | 1148.3 | 1252.6 | 0 |
| ros_reliable | 941.2 | 1196.7 | 1312.2 | 0 |

> 这是 iter9 的历史数字，**不是本次改动后测出来的**。本次改动后重测
> STATUS: blocked。

---

## 2. 基线建立（Iter0）

- 跑全部 12 个 `check_*.py`：全 exit 0。
- 跑 `scripts/eval/run_evals.py --suite all`：44 pass / 0 fail / 1 blocked。
- 读 `config/fastdds.xml`：生效配置为 iter7 状态（iter8 leaseAnnouncement、
  iter9 WLP 探针已回退）。
- 读 `docs/artifacts/bench/iter7..iter9` 作为历史参照。
- 诚实记录：本机 macOS 无 ROS 2，`rclpy` 不可导入，无 `librmw_*.so`。

**Iter0 综合分（可测维度）**：① 30/30，③ 30/30 → **60/60 = 100%**（可测
维度满分；② 40 分 blocked 不计入）。

---

## 3. 迭代记录

### Iter10-a — `perf(rmw_wait)`: StatusCondition 预检（commit `57896f1d`）

**改了什么**（`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp`）：
- 新增 `reader_status_active()`：纯读 `DataReader::get_statuscondition()`
  `.get_trigger_value()`，**无锁**，只读一个 bool。
- `subscription_has_data()` 改为显式判空短路，避免对空 `cpu_data_reader_`
  /`accel_data_reader_` 的函数调用帧。
- 在 `__rmw_wait` 的订阅者标记阶段（原 254-267 行），对每个订阅者先用
  `reader_status_active()` 做预检：三个 reader 的 StatusCondition 都未触发
  ⇒ 该订阅者必无未读样本，直接置 0，**跳过** `get_first_untaken_info()`
  （它要持 reader 的样本锁并拷贝一个 `SampleInfo`）。

**为什么改**（依据《1》请求流、《2》重构计划 R2）：
- 原实现无论 wait 被谁唤醒，都对每个订阅者调一次 `get_first_untaken_info()`。
- 当 executor 被 timer / guard_condition 唤醒、或 wait 超时、或多数订阅者
  本就无数据时，这是纯浪费的持锁读取。
- DDS 契约保证：StatusCondition 未触发 ⇒ mask 内所有 status（含
  `data_available`）均不 active ⇒ 一定没有未读样本。故预检可安全跳过。
- 注意：`has_triggered_condition()` 的检查顺序（guard→events→subs→clients
  →services）**上游已经优化过**（文件 71-75 行注释自承），本次不动它；
  `has_triggered_condition()` 仍保持精确语义，只在 wait 后标记阶段加快速路径。

**评估**：
- 12 个 `check_*.py` 全绿。
- `api.signature_diff`：7 个公共函数签名未变。
- 行为等价性审查：
  - StatusCondition 触发 ⇒ 仍走 `subscription_has_data()` 精确查；
  - StatusCondition 未触发 ⇒ 直接置 0，与原"get_first_untaken_info 返回
    RETCODE_NO_DATA ⇒ 置 0"等价；
  - `skip_wait=true`（wait 前已有触发）路径不受影响：有数据的订阅者其
    `data_available` status 仍 active，预检通过后进精确查。

**分数变化**：① 30/30，③ 30/30。② blocked。**预期收益（代码级分析，
未实测）**：timer/guard 唤醒或超时唤醒时，每个无数据订阅者省一次持样本锁
的 SampleInfo 读取；订阅者越多、多数无数据时收益越大。
**STATUS: blocked-on-runtime**（无 ROS 跑 executor pingpong）。

---

### Iter10-b — `refactor(rmw_publish)`: 时间戳收口 + opt-out（commit `79c20cb1`）

**改了什么**（`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp`）：
- 把散在 `__rmw_publish` / `__rmw_publish_serialized_message` /
  `__rmw_publish_loaned_message` 的 3 处 `Time_t::now(stamp)` 收口到
  `fill_source_timestamp(stamp)`。
- 新增运行期开关 `RMW_FASTRTPS_SOURCE_TS=zero`（**默认 off**）：
  - 默认 strict：行为与之前逐字节一致，仍 `clock_gettime`，接收端
    `source_timestamp` 有效、可算单向延迟；
  - zero 模式：热路径跳过 `clock_gettime`、写零值 timestamp，仅供纯
    BestEffort 高频遥测（如 200 Hz IMU）且接收端从不测延迟时用。
  - `getenv` 用 magic-static 只读一次，热路径零额外系统调用。

**为什么改**（依据《2》R1）：发布时间戳可注入，5 处 now() 收口到一处。

**诚实分析（不夸大）**：
- 默认路径**性能零变化**（仍调 now()）。
- zero 模式每次 publish 省一次 `clock_gettime`（约 20-50 ns），200 Hz 下
  约 4-10 µs/s，**收益微小**，且牺牲 `source_timestamp` 语义，故默认关闭。
- 注意：Fast-DDS 的 `write()`（不带 timestamp）内部也会打 ts，所以
  "跳过 now()"本身省不掉系统调用，只是把它从 RMW 层挪到 Fast-DDS 内部；
  本开关的真正价值是把"是否需要 source ts"显式化、为高频链留接口。

**评估**：12 闸门全绿；`api.signature_diff` 未变。
**分数变化**：① 30/30，③ 30/30。② blocked。

---

### Iter10-c — `refactor(custom_subscriber_info)`: 删注释死代码（commit `6c5b4829`）

**改了什么**：删除 `custom_subscriber_info.hpp` 原 192-206 行整段被注释
掉的 `on_type_discovery()` 声明（注释自承 dynamic type deferred case NOT
SUPPORTED）。

**为什么改**（依据《2》§1.1）：全树 grep 无任何调用点，纯死代码。

**评估**：
- `grep -rn on_type_discovery vendor/rmw_fastrtps/` 零结果。
- 12 闸门全绿。
- 净减 16 行。

**分数变化**：③ 代码健康 +1（死代码只减不增）。① 30/30。

---

### Iter10-d — `docs(fastdds.xml)`: iter10 注释 + 显式默认值（commit `6450044a`）

**改了什么**（`config/fastdds.xml`）：
- 头部注释增补 iter10 说明：本轮不再新增生效 XML 参数，iter1-9 已穷尽
  socket buffers / send_buffers / SHM size / healthy_check /
  leaseAnnouncement / WLP；剩余候选（publication_mode、thread settings、
  per-topic reader profile）在 Humble 2.6 上要么 inert（profile 不绑
  topic 不生效）要么无真机不敢盲改。
- `shm_midsize` transport descriptor 显式写 `<port_queue_capacity>512`
  （Humble 2.6 默认值），让 iter6 失败教训（512→64 使 arrival jitter
  变宽被回退）在文件里可见。**运行时行为不变**。

**为什么改**：iter1-9 的历史表明盲调 XML 参数风险高（iter6/8/9 均回退），
本机又无法跑 Fast-DDS 2.6 真机解析验证，故只做文档自固化，不赌新参数。

**评估**：`xmllint`/`xml.etree` wellformed；12 闸门全绿。
**分数变化**：① 30/30。

---

### 未单独提交的分析项（诚实记录）

**改进3 — TypeSupport 序列化/锁**（`TypeSupport_impl.cpp`）：
- `compute_key()` 第 79 行 `is_compute_key_provided=false` 时**在 `mtx_`
  锁之前就 return**；绝大多数 ROS 消息（IMU/sensor_msgs 无 key）根本不进
  锁，"减少锁粒度"在链 A 热点上**零收益**。
- `serialize()` 的 `FastBuffer`/`Cdr` 全是栈对象，buffer 本体由 Fast-DDS
  传入的 `payload.data` 提供，RMW 层无重复堆分配。
- DYNAMIC_MESSAGE 分支每次 `std::make_shared<DynamicPubSubType>()` 是堆
  分配，但链 A 走 `ROS_MESSAGE` introspection 分支，**不经过这里**。
- 结论：该路径在链 A 无热点，**未改逻辑**，避免无收益风险改动。

**改进4 — DataWriter 内存池**（`Fast-DDS/.../DataWriterImpl.cpp`）：
- 第 1080 行 `get_free_payload_from_pool()` 证明 Fast-DDS **已有 payload
  池**，RMW 层无需另造。
- 第 1068 行 `fixed_payload_size_` 只在
  `memory_policy == PREALLOCATED_MEMORY_MODE` 时才非零；rmw_fastrtps 默认
  `PREALLOCATED_WITH_REALLOC_MEMORY_MODE`（《2》§1.3 列了 15 处硬编码），
  故每次 write 都跑 `calculate_serialized_size_ctx`（introspection 遍历）。
- 可优化方向：对**已知定长**消息类型把 writer memory_policy 改成
  `PREALLOCATED_MEMORY_MODE`，Fast-DDS 会一次性预算 payload，跳过每次
  introspection。但这需要 per-topic 消息大小知识，变长消息（string/
  sequence）超大小会丢包——本机无法验证哪些 topic 定长，**未改**，
  留作真机验证项。

---

## 4. 当前最佳分数

`docs/artifacts/eval/iter10.json`：**45 项 = 44 pass / 0 fail / 1 blocked**。

| 维度 | 权重 | 得分 |
|------|------|------|
| ① 闸门通过 | 30% | 30/30（12/12 全绿） |
| ② bench p50/p95/p99 | 40% | **BLOCKED**（无 ROS 运行时，未折算） |
| ③ 代码健康 | 30% | 30/30（签名 7/7 未变；死代码 -16 行） |

**可测维度综合分：60/60 = 100%**。
**真实端到端延迟改善：STATUS: blocked-on-runtime**（无 `/opt/ros/humble`、
无 `rclpy`、无跨机第二台），用代码审查 + 静态等价性分析替代，不编造数字。

---

## 5. 剩余风险 / 薄弱环节

1. **改了不跑的代码**：vendor 是整树拷贝、rolling/master 快照，运行时是
   Humble 2.6.x；本机不编译，所有改动只过静态检查 + check 闸门，**未跑过
   一次真编译**。这是本循环最大的结构性风险（《2》§3.0 已标）。
2. **② bench 维度 40 分始终 blocked**：要真闭环，必须在 Ubuntu 22.04 +
   Humble 上跑 `scripts/bench/run_chain_a.sh`，对比改动前后 p95 抖动。
3. **StatusCondition 预检的边界**：依赖 Fast-DDS 对 `data_available`
   status 的契约。若未来 Fast-DDS 版本改变 status 重置时机，需回归。
4. **buffer-aware 重复序列化**（《2》R4）未动：每 endpoint 序列化是设计
   使然（`subscriber_endpoint_info` 不同），不是 bug。
5. **fixed_payload_size_ 机会**（改进4）未利用，待真机确认哪些 topic 定长。

## 6. 因环境无法测量的项（BLOCKED 清单）

| 项 | 原因 | 替代手段 |
|----|------|----------|
| executor pingpong p50/p95/p99 | 无 `/opt/ros/humble`、`rclpy` 不可导入 | 代码审查 + DDS 契约推理 |
| 跨机 RTT / UDP 抖动 | 无第二台机器 | 历史 iter9 数字仅作参照 |
| Fast-DDS 2.6 XML 解析校验 | 本机无 Fast-DDS 2.6 runtime | `xml.etree` wellformed + Humble schema 注释 |
| 编译警告 / ASan / 运行时回归 | 无 colcon build | `api.signature_diff` 静态签名对比 |

## 7. 纪律遵守确认

- 全程简体中文；数字均来自 `eval/*.json` 实测或 iter9 历史 `raw.json`。
- 公共 RMW API 签名未变（`api.signature_diff` 7/7）。
- `dimos_bridge/` 未改（工作区另有并行定时任务的改动，本循环未 stage）。
- 每迭代一个 commit，conventional commits 风格。
