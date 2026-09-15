# ROS 2 DDS 现代化改造与重构计划

Status: **Draft 计划 — 本文档不改动任何源码；所有行号均经 Read/Grep 实测于 2026-09-16。**
日期：2026-09-16（JST）。
范围：`vendor/rmw_fastrtps/`、`vendor/Fast-DDS/`、`vendor/CycloneDDS/`、`config/`、`scripts/`。
前置文档：[latency-attribution.md](latency-attribution.md)、[feishu-risk-matrix.md](feishu-risk-matrix.md)、[cn-jp-ros2-absorb.md](cn-jp-ros2-absorb.md)、[ros2-source-map.md](ros2-source-map.md)、[vendor/MANIFEST.md](../../vendor/MANIFEST.md)、[vendor/VERSIONS.md](../../vendor/VERSIONS.md)。

> 与 AGENTS.md Hold 的关系：原 Hold（不改 `fastdds.xml` / SCOREBOARD、不接 zenoh、不改 vendor 源码）是**只读观察期**约束。本文档是用户授权进入**实际重构期**的产物；本文件本身只写计划、不改代码。凡涉及突破原 Hold 的条目，均在第 3 节单独拆为迁移任务并给出回滚方案。

---

## 0. 为什么要重构：五个症状的代码归因

智能纪要列出的五个核心问题，在本仓代码里都能定位到具体位置：

| 症状 | 代码层归因（已验证） |
|------|----------------------|
| 1. 通信延迟波动大（jitter 高） | 每次 `rmw_publish` 都在热路径调 `Time_t::now()` 系统调用（见 §1.5、步骤 R1）；`rmw_wait` 每轮对每个订阅者轮询 3 个 DataReader 的 `get_first_untaken_info()`（`rmw_fastrtps_shared_cpp/src/rmw_wait.cpp:32-50`），且 wait 前后各查一次（`:102` 与 `:259`） |
| 2. CPU 占用高 | buffer-aware 路径按订阅者数量**重复序列化**：`publish_to_buffer_endpoints()` 对每个 endpoint 重新走一遍 CDR 序列化（`rmw_fastrtps_cpp/src/rmw_publish.cpp:186-234`）；`PREALLOCATED_WITH_REALLOC_MEMORY_MODE` 在 15 处硬编码（见 §1.3） |
| 3. 偶现消息追击 / 丢包 | buffer-aware 降级回退逻辑依赖「matched 数 vs buffer-aware 数」的锁内计数比较（`rmw_fastrtps_cpp/src/rmw_publish.cpp:264-276`），matched 回调与 publish 线程之间存在竞态窗口；`data_sharing()` 被显式 `off()`（`rmw_publish.cpp:91`），大包走 UDP 分片 |
| 4. 未在真实业务场景测试 | bench 入口已有 `run_large_packet.sh`（激光雷达大包）、`run_imu_hf.sh`（IMU 高频），但尚无**针对 vendor 改动**的回归闸门；`scripts/` 13 个 `check_*.py` 全是文档/身份一致性检查，没有一个覆盖序列化路径或 QoS 兼容性 |
| 5. 代码量大、改动难（rolling 快照 ≠ Humble 运行时） | `vendor/` 是整树拷贝（非 submodule，见 [VERSIONS.md](../../vendor/VERSIONS.md)），且**混入了本地 buffer-aware 分叉补丁**（`buffer_backend_*.{hpp,cpp}`、`cpu/accel_data_reader_`），与上游 rolling 基线纠缠在一起；`MANIFEST.md` 明确「CI 不编译这些树」——改 vendor 本身不改变运行时，这是本计划最大的结构性风险，见 §3.0 |

---

## 1. 现状诊断：拖慢变更速度的问题

### 1.1 死代码与近死代码

以下函数/文件在 vendor 树内**没有被任何翻译单元引用**（已用 grep 验证调用点）：

| 位置 | 证据 | 结论 |
|------|------|------|
| `rmw_fastrtps_shared_cpp/include/.../custom_subscriber_info.hpp:192-206` | 整段 `on_type_discovery` 被注释掉，注释自承「NOT SUPPORTED」 | 注释死代码，可删，无行为影响 |
| `rmw_fastrtps_cpp/src/rmw_get_serialization_format.cpp`、`rmw_fastrtps_dynamic_cpp/src/rmw_get_serialization_format.cpp` | 两链各一份，仅返回同一格式字符串；`rmw_serialize.cpp` 与 `rmw_get_serialization_format.cpp` 互为平行拷贝 | 两条链各存一份同一逻辑，见 §1.2 |
| `rmw_fastrtps_cpp/src/rmw_wait_set.cpp`、`rmw_fastrtps_dynamic_cpp/src/rmw_wait_set.cpp` | 与 `rmw_wait.cpp` 同构的 37 行薄封装，仅 identifier 不同（已 diff 验证：两文件仅 `#include` 的 identifier 头不同） | 重复薄封装，见步骤 R6 |
| `custom_subscriber_info.hpp:140-156` 的 buffer-aware 字段（`cpu_data_reader_`、`accel_data_reader_`、`my_backend_types_`…） | 仅当 `is_buffer_aware_=true` 才用；链 A 运行时若未注册任何 backend（`buffer_backend_loader.cpp` 里 `get_backend_names()` 为空），这些字段全部空置 | 条件死代码，应下沉到独立子类或编译开关后清理 |

> 说明：本仓**未**接入编译，无法用 linker 符号表证明零引用；上表是「grep 全树无调用点」级别的证据，删除前须在 CI 里加 `scripts/check_dead_code.py`（见 §4.2）做符号扫描兜底。

### 1.2 重复路径：rmw_fastrtps_cpp 与 dynamic_cpp 的平行分叉

两目录同名文件实测（`diff` 已跑）：

| 文件 | `rmw_fastrtps_cpp` | `rmw_fastrtps_dynamic_cpp` | 关系 |
|------|--------------------|----------------------------|------|
| `rmw_wait.cpp` | 37 行 | 37 行 | 逻辑逐行相同，仅 identifier 头不同 |
| `rmw_client.cpp` | 558 行 | 605 行 | 平行实现，各含 16 处 `RMW_CHECK*` |
| `rmw_service.cpp` | 550 行 | 596 行 | 平行实现 |
| `subscription.cpp` | **957 行** | 383 行 | cpp 版被 buffer-aware 补丁大幅扩写 |
| `publisher.cpp` | 427 行 | 347 行 | 平行实现 |
| `rmw_publish.cpp` | 302 行 | 57 行 | cpp 版自带 buffer-aware publish，dynamic 版直接转调 shared |

关键事实：真正的实现已经在 `rmw_fastrtps_shared_cpp/`（`__rmw_publish`、`__rmw_wait`、`__rmw_take`），但 `rmw_fastrtps_cpp/src/rmw_client.cpp`、`rmw_service.cpp`、`subscription.cpp` 并没有把逻辑下沉到 shared，而是各自维护一份带 buffer-aware 分支的完整副本——**这就是「链 A 与链 B 之间功能重复封装」的代码形态**：链 A 的 cpp 版与 shared 版之间，又是一套半平行实现。

### 1.3 过大的模块（行数实测）

| 文件 | 行数 | 问题 |
|------|------|------|
| `rmw_fastrtps_dynamic_cpp/src/TypeSupport_impl.hpp` | **1264** | 单个头文件 1264 行，模板实现与序列化逻辑混在一起 |
| `rmw_fastrtps_cpp/src/subscription.cpp` | **957** | 接近 1000 行；主构造函数与 buffer-aware 分支、QoS 转换、listener 挂载全在一个函数族里 |
| `rmw_fastrtps_shared_cpp/src/TypeSupport_impl.cpp` | **729** | 序列化 + key 计算 + DynamicType 三路 switch（`:85-117`） |
| `rmw_fastrtps_shared_cpp/src/rmw_take.cpp` | **680** | take / take_loaned / return_loaned / deserialization 四合一 |
| `rmw_fastrtps_shared_cpp/include/.../rmw_common.hpp` | **617** | 全部 `__rmw_*` 内联声明堆在一个头里 |
| `rmw_fastrtps_shared_cpp/src/custom_subscriber_info.cpp` | **596** | listener 事件状态机 |
| `rmw_fastrtps_cpp/src/rmw_client.cpp` / `rmw_service.cpp` | 558 / 550 | 与 dynamic_cpp 平行 |
| `rmw_fastrtps_dynamic_cpp/src/rmw_client.cpp` / `rmw_service.cpp` | 605 / 596 | 同上 |

函数级：`__rmw_wait`（`rmw_wait.cpp:136-360`）单函数 224 行；`publish_to_buffer_endpoints`（`rmw_publish.cpp:139-235`）96 行且含双重循环 + 序列化；`create_pending_buffer_writers`（同文件 `:44-132`）88 行。

### 1.4 陈旧的抽象

- **TypeSupport 序列化方式**：`TypeSupport_impl.cpp:65-68` 每次 `create_data()` 都 `new eprosima::fastcdr::FastBuffer()`、`delete_data()` 里 `delete`（`:59-63`）——裸 `new/delete` 配对，无异常安全；序列化路径走 `SerializedData.type` 三路 union（`FASTDDS_SERIALIZED_DATA_TYPE_ROS_MESSAGE / CDR_BUFFER / DYNAMIC_MESSAGE`），靠 `void*` + enum 区分类型，是典型的 C 风格 tagged union。
- **CustomPublisherInfo / CustomSubscriberInfo 数据组织**：`custom_subscriber_info.hpp:117-161` 一个 struct 塞了 20+ 字段——上游 DDS 字段（`data_reader_`、`type_support_`、`topic_`…）与本仓 buffer-aware 补丁字段（`is_buffer_aware_`、`cpu_data_reader_`、`accel_data_reader_`、`buffer_state_`）混在同一个 POD 里；buffer-aware 字段被所有订阅者无条件构造。
- **`rmw_common.hpp` 617 行头**：所有 `__rmw_*` 声明集中暴露，cpp 与 dynamic_cpp 两个实现各 include 一遍，抽象边界形同虚设。

### 1.5 遗留模式（裸指针 / 手动内存 / 热路径系统调用）

grep 实测的裸 `new`（节选）：

| 文件:行 | 代码 |
|---------|------|
| `rmw_fastrtps_shared_cpp/src/participant.cpp:66,85` | `participant_info = new CustomParticipantInfo();` / `new ParticipantListener(...)` |
| `rmw_fastrtps_shared_cpp/src/custom_participant_info.cpp:85` | `uct->topic_listener = new CustomTopicListener(event_listener);` |
| `rmw_fastrtps_shared_cpp/src/rmw_guard_condition.cpp:29,31` | `new rmw_guard_condition_t;` + `new GuardCondition()` |
| `rmw_fastrtps_cpp/src/publisher.cpp:163,184,204,210` | `new (std::nothrow) CustomPublisherInfo / MessageTypeSupport_cpp / RMWPublisherEvent / CustomDataWriterListener` |
| `rmw_fastrtps_cpp/src/rmw_init.cpp:109,126,133` | `new (std::nothrow) rmw_context_impl_t / BufferBackendContext / BufferEndpointRegistry` |
| `rmw_fastrtps_shared_cpp/src/TypeSupport_impl.cpp:67` | `new eprosima::fastcdr::FastBuffer()` |

热路径上的系统调用：`Time_t::now()` 共 5 处（`rmw_fastrtps_shared_cpp/src/rmw_publish.cpp:63,115,151`、`rmw_response.cpp:170`、`rmw_fastrtps_cpp/src/rmw_publish.cpp:153`），每次 publish 必走一次 `clock_gettime`。

异常安全：`publish_to_buffer_endpoints`（`rmw_publish.cpp:202-212`）是全树唯一对序列化做 `try/catch std::exception` 的地方；其余热路径（shared `__rmw_publish`）序列化发生在 Fast-DDS 内部，无 C++ 层兜底。

---

## 2. 重构步骤（12 个，每个可独立审查）

> 验证列里的 `check_*.py` 指 scripts/ 下现有闸门；标注「新增」的为本计划要求新建（见 §4.2）。
> 所有步骤默认**不改变公共 API**（`rmw_*` C 接口签名不变）；唯一例外在步骤 R10 与 §3。

| # | 标题 | 当前行为（文件:行号） | 结构性改进 | 行为稳定验证 | 风险 | 影响公共 API |
|---|------|------------------------|------------|--------------|------|--------------|
| **R1** | 发布时间戳可注入 | `__rmw_publish` 每次 `Time_t::now(stamp)`（`rmw_fastrtps_shared_cpp/src/rmw_publish.cpp:62-64`、`:114-116`、`:150-152`；cpp 版 `rmw_publish.cpp:152-154`） | 抽出 `TimeSource` 接口：默认实现仍调 `Time_t::now()`，但允许测试/高频链注入预取时间戳或 RDTSC 时钟；5 处 now() 收口到一个函数 | 编译通过；`check_source_map.py`（符号 `__rmw_publish` 仍在）；bench `pingpong.py` p50/p95 对比不劣化 | 低 | 否（内部接口） |
| **R2** | 优化 `rmw_wait` 轮询策略 | `has_triggered_condition` 对每个订阅者查 3 个 reader（`rmw_wait.cpp:44-50`），wait 后再全量查一遍（`:254-267`）；`get_first_untaken_info` 每次都持锁读 SampleInfo | 用 `on_data_available` 回调维护一个无锁计数器（`std::atomic<size_t>`），wait 只看计数；保留 `get_first_untaken_info` 作为兜底校验。WaitSet attach/detach 每轮重复（`:231-250`）改为一次 attach、长期挂载 | `check_executor_map.py` 仍通过；`run_imu_hf.sh` 高频场景 p95 抖动收敛 | 中 | 否 |
| **R3** | 统一 cpp / dynamic_cpp 的 TypeSupport 序列化路径 | 两链各自维护 `rmw_serialize.cpp`、`rmw_get_serialization_format.cpp`、TypeSupport 平行实现（§1.2 表） | 把序列化纯函数下沉到 `rmw_fastrtps_shared_cpp`，cpp 与 dynamic_cpp 只留 identifier 薄封装（对标 `rmw_wait.cpp` 已有的 37 行形态） | 新增 `check_serialization_path.py`：断言两链 serialize 入口指向同一份 shared 实现；`prove_rmw.py` 身份不变 | 低 | 否 |
| **R4** | 大消息异步序列化选项 | `publish_to_buffer_endpoints` 对每个 endpoint 同步重序列化（`rmw_publish.cpp:186-234`）；主路径 `__rmw_publish` 同步 write | 为 `is_buffer_aware_` 发布者增加「一次序列化、多 endpoint 投递」：先把 ROS 消息序列化一次到可复用 `FastBuffer`，再对各 endpoint 做零拷贝/浅拷贝投递；超阈值（如 >256 KiB）走异步队列 | `run_large_packet.sh` 大包 RTT 对比；新增 `check_no_duplicate_serialize.py`（grep 热路径序列化调用点数） | 中 | 否 |
| **R5** | WriterHistory 内存预分配 | 15 处硬编码 `PREALLOCATED_WITH_REALLOC_MEMORY_MODE`（`participant.cpp:334,336`、`publisher.cpp:252`、`subscription.cpp:413,685,909`、`rmw_client.cpp:316,384`、`rmw_service.cpp:312,384`、`rmw_publish.cpp:89`） | 抽 `get_writer_qos_template()` / `get_reader_qos_template()`，按 topic 深度一次性预分配深度槽位，消除运行时 realloc；历史策略从散点收敛到一处 | 编译通过；`run_large_packet.sh` 大包不出现 realloc 导致的偶发尾延迟 | 低 | 否 |
| **R6** | 提取重复 null / identifier 检查为辅助函数 | 全树 100 处 `RMW_CHECK_TYPE_IDENTIFIERS_MATCH` + 179 处 null 检查（§1.5 前 grep 计数）；`rmw_wait_set.cpp` 两链各 37 行重复 | 提供 `RMW_CHECK_PUBLISHER_IDENTIFIER(pub, id)` 之类的单行辅助宏/inline，cpp 与 dynamic_cpp 共用；删除两链逐字相同的 wait_set 薄封装，合并为模板化单文件 | 新增 `check_null_check_dedup.py`：断言重复模式数量单调下降；全部现有 `check_*.py` 通过 | 低 | 否 |
| **R7** | fastdds.xml 增加 data sharing 配置选项 | 当前 iter7 种子有 `shm_midsize` SHM 传输（`config/fastdds.xml`），但 buffer-aware writer 显式 `data_sharing().off()`（`rmw_publish.cpp:91`）；XML 无 `<data_sharing>` 段 | 在 XML 增加可选 `data_sharing` profile（`on`/`auto`），RMW 侧按 topic 尺寸选择：小包（IMU 64B）开 data sharing，大包（激光雷达 >1MB）保持 off 走 SHM 分片 | 先跑 `run_imu_hf.sh` 开/关对比；新增 `check_datasharing_consistency.py` 校验 XML 与代码默认值一致 | 中 | 否（配置层） |
| **R8** | 订阅端反序列化缓存 | `__rmw_take` 每次 take 重新反序列化（`rmw_take.cpp:88,315,436` 三处 `SerializedData` 构造） | 对 loaned 路径已有 `LoanManager`（`:573-586`）做对齐：非 loan 路径复用 `FastBuffer`/反序列化临时对象，避免每次 take 分配；缓存 `Cdr` 解码上下文 | `check_dod_evidence.py` 通过；`run_imu_hf.sh` CPU 占用下降 | 中 | 否 |
| **R9** | 统一两条链的环境变量加载 | 链 A：`config/env/chain_a.sh` 硬编码 `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` 等三行；链 B：`chain_b.sh` 硬编码 `rmw_cyclonedds_cpp` + `unset CYCLONEDDS_URI`；另有 `load.py`（`import` 不写 `os.environ`） | 两个 `.sh` 改为 source 同一个 `_chain_common.sh`，参数化 RMW/域/XML 路径；把「import 不写 environ」这条陷阱写进唯一 README，删重复注释 | 新增 `check_env_chain_consistency.py`：断言两链脚本导出集合差集只有预期三项 | 低 | 否 |
| **R10** | 编译期 QoS 兼容性检查 | 运行时才发现 incompatible QoS（`on_requested_incompatible_qos`，`custom_subscriber_info.hpp:85-87`） | 写 `scripts/check_qos_compat.py` 静态扫描 publisher/subscription 构造处的 QoS 模板，在编译期/CI 标记 RELLIABLE vs BEST_EFFORT、DURABILITY 不匹配；配合 R5 的 QoS 集中点 | 新增 `check_qos_compat.py` 随 CI 跑 | 低 | **是**（若要把不匹配从运行时错误升级为编译错误，需改 CMake；先做静态扫描版，不改 API） |
| **R11** | 清理死代码与注释块 | §1.1 表全部条目 | 删 `custom_subscriber_info.hpp:192-206` 注释块；将 buffer-aware 字段从 `CustomSubscriberInfo` 主 struct 下沉到 `BufferAwareSubscriberInfo` 子类，普通订阅者零开销 | 编译通过；`check_source_map.py` 符号仍在；bench 基线不漂移 | 低 | 否 |
| **R12** | 裸指针管理 RAII 化 | §1.5 表 14 处裸 `new`（publisher/subscription/client/guard_condition 构造路径） | 逐处改 `std::make_unique` / `std::make_shared`，保持析构路径不变；`TypeSupport_impl.cpp:59-68` 的 create/delete data 对齐成 RAII | 编译 + 现有全部 `check_*.py`；valgrind/ASan 跑 `prove_rmw.py` 无泄漏新增 | 中 | 否 |

---

## 3. 框架迁移 / 依赖升级 / API 变更 / 架构调整（独立迁移任务）

### 3.0 先决决策：vendor 树到底怎么进运行时（最高优先级，阻塞全部 Phase 2/3）

现状矛盾：[MANIFEST.md](../../vendor/MANIFEST.md) 写明「CI 不编译这些树」，运行时是 Humble 发行版 `/opt/ros/humble`，而 vendor 是 **rolling/master 快照**（Fast-DDS `master` `343f155c`，近旁 tag `v3.6.2`；运行时 Humble 实为 Fast-DDS **2.6.x**）。不改这一点，第 2 节所有重构都只是「改了不跑的代码」。

必须先二选一：

- **选项甲（overlay 编译）**：在 Docker/CI 里用 vendor 树构建 overlay 覆盖 Humble，把本仓 patch 真正编进去。需要解决 ABI 兼容（rolling `rmw 7.11.2` vs Humble `rmw 7.x`）。
- **选项乙（patch backport）**：vendor 树仅作设计参考，实际改动 backport 到 Humble 对应版本的 patch 系列，用 `quilt` / `git format-patch` 管理。

未决策前，Phase 2/3 全部冻结。本文档默认推荐**选项乙**（与 `feishu-runtime-provenance.md` 的 underlay/overlay/vendor 分层一致，ABI 风险最小）。

### 3.1 Fast-DDS 版本升级

| 项 | 内容 |
|----|------|
| 当前版本 | vendor = `master` `343f155c…`（近旁稳定 tag `v3.6.2`）；Humble 运行时 = 2.6.x |
| 目标版本 | 先钉到稳定 tag **v3.6.2**（vendor 树）；运行时升级单独评估（Humble → 新版 Fast-DDS 需跟 Rolling/Jazzy 对齐） |
| 迁移步骤 | ① vendor/Fast-DDS 整树替换为 v3.6.2；② 跑 `check_runtime_provenance.py`；③ 重跑 13 个 check 闸门；④ `run_chain_a.sh` pingpong 基线对比 |
| 回滚 | vendor 是整树拷贝，`git checkout vendor/Fast-DDS` 即回滚；不碰 Humble 运行时则零运行时风险 |
| 验证 | `prove_rmw.py`、`print_bench_gates.py`、大包/高频两脚本不劣化 |

### 3.2 CycloneDDS 版本升级

| 项 | 内容 |
|----|------|
| 当前版本 | vendor/CycloneDDS = 发行 tag **11.0.1**（`e54e991f…`，见 VERSIONS.md）；链 B 原生 DimOS 另有自带 0.10.2（见 unitree-sdk2-dds-swap.md，drop-in 已证 FAIL） |
| 目标版本 | 维持 11.0.1 不动；仅在出现明确 bug 修复需求时评估 11.x 补丁小版本 |
| 迁移步骤 | （本次不执行）如需升级：先 `check_unitree_cyclone_swap.py` 确认不影响 Unitree 自带栈 |
| 回滚 | 整树替换回 11.0.1 |
| 验证 | `run_chain_b.sh` 基线 |

### 3.3 RMW API 对齐（rolling → Humble）

| 项 | 内容 |
|----|------|
| 现状 | vendor `rmw` = rolling `7.11.2`；运行时 Humble 的 `rmw` API 版本更低；`rmw_fastrtps` vendor `9.5.2` 快照含 Humble 没有的符号 |
| 目标 | 不追求 vendor=Humble，而是建立 **API 差量表**：列出 rolling 用到、Humble 没有的符号，标记重构中允许用/不允许用 |
| 迁移步骤 | ① 写 `docs/architecture/rmw-api-diff.md`；② 新增 `check_rmw_api_usage.py` 扫描 vendor 代码里是否引用了 Humble 不存在的符号；③ 重构步骤一律只用两边共有符号 |
| 回滚 | 纯文档 + 静态检查脚本，无运行时回滚 |
| 验证 | 新 check 脚本 exit 0 |

### 3.4 引入共享内存传输（Fast-DDS SHM / iceoryx）

| 项 | 内容 |
|----|------|
| 现状 | `fastdds.xml` 已有 `shm_midsize`（maxMessageSize 280000，segment 2MiB，见 iter5/iter7 注释）；大包仍走 UDP 分片；iceoryx 未 vendor（CycloneDDS 的 CMake 可选外部依赖） |
| 目标 | 与 R7 联动：小包走 data sharing（零拷贝），中包走现有 SHM，大包显式分片策略 |
| 迁移步骤 | ① 先写 §4.1 的「共享内存设计文档」；② 在 XML 加 profile（不改默认）；③ `run_large_packet.sh` 1MiB 用例对比（注意 iter5 曾把 1MiB BestEffort 从 80/80 打回 1/80 的教训，不可照搬大段 SHM 实验） |
| 回滚 | XML profile 注释即回滚；`git checkout config/fastdds.xml` |
| 验证 | 大包不丢包 + 高频抖动 p95 不劣化；iter5 的失败模式必须复现并留档 |

### 3.5 引入 Zenoh 作为备选 RMW

| 项 | 内容 |
|----|------|
| 现状 | AGENTS.md 原 Hold：不接 zenoh、无 vendor 树、无 kmod、无 `rmw_zenoh` |
| 评估结论 | **本期仍不做。** 仅保留为「RMW 层备选方案」写在设计文档里；不 vendor、不编译、不进 CI。理由：当前首要矛盾是链路 A 内部 jitter 与 CPU，Zenoh 是换中间件而非修问题，且与 §3.0 的 overlay 决策正交 |
| 如未来启动 | 需独立 ADR + 独立 domain + 独立 bench 目录，禁止与链 A/B 混表 |

### 3.6 自研 RMW 层

| 项 | 内容 |
|----|------|
| 现状 | VERSIONS.md 明确「未发明自定义 RMW」；buffer-aware 补丁是**在 rmw_fastrtps_cpp 内部**的扩展，不是独立 RMW |
| 评估结论 | **本期不做。** 先把 §2 的 12 步做完并实测收益；只有当 R1-R12 + 3.4 之后 jitter/CPU 仍不达标，才启动自研 RMW 的 ADR |
| 如未来启动 | 必须先冻结 `rmw/rmw.h` 子集面，参考 `ros2-dds-r0-interface-freeze.md` 的冻结方法 |

---

## 4. 实施前应先创建的文档 / 规范 / 一致性检查

### 4.1 先写的设计文档（按顺序）

| 文档 | 目的 | 阻塞的步骤 |
|------|------|------------|
| `docs/architecture/overlay-or-backport-decision.md` | 拍板 §3.0 选项甲/乙 | 全部 Phase 2/3 |
| `docs/architecture/qos-spec.md` | 本仓 topic 的 QoS 规范（reliability/durability/history 深度 × 激光雷达/IMU/控制指令） | R5、R7、R10 |
| `docs/architecture/shared-memory-design.md` | 共享内存/data sharing 拓扑、大包分片策略、iter5 失败复盘 | R7、3.4 |
| `docs/architecture/vendor-patch-convention.md` | **本仓 vendor 代码的修改约定**：patch 用什么形式管理（quilt 系列 / git diff 目录 / overlay 目录）、每次改动必须附上下游 commit 对照、CI 怎么跑 | 全部 |
| `docs/architecture/rmw-api-diff.md` | rolling vs Humble API 差量表 | R3、R10 |

### 4.2 新增一致性检查脚本（scripts/，沿用 prove_rmw.py 风格：无第三方依赖、vanilla box 可跑）

| 脚本 | 检查什么 | 失败条件 |
|------|----------|----------|
| `check_dead_code.py` | 扫描 §1.1 清单中的符号，全树 grep 无调用点才允许删；防止误删仍在用的私有函数 | 符号仍被引用却出现在删除清单 |
| `check_serialization_path.py` | 断言 R3 之后 cpp/dynamic_cpp 的 serialize 入口都指向 shared 实现 | 某链仍自带平行实现 |
| `check_null_check_dedup.py` | 统计重复 `RMW_CHECK_TYPE_IDENTIFIERS_MATCH` 模式数，随 R6 单调下降 | 新增重复模式 |
| `check_qos_compat.py` | 静态扫 publisher/subscription 构造处的 QoS，标记 reliability/durability 不匹配 | 发现不匹配且无白名单注释 |
| `check_datasharing_consistency.py` | 校验 fastdds.xml 的 data sharing 段与代码默认值一致 | XML 与代码漂移 |
| `check_env_chain_consistency.py` | 校验链 A/B 脚本导出集合差集只有预期项 | 多出未登记的 export |
| `check_rmw_api_usage.py` | 扫描 vendor 代码是否引用 Humble 不存在的 rmw 符号 | 引用差量表外符号 |
| `check_no_duplicate_serialize.py` | R4 后热路径序列化调用点数量 | 出现每 endpoint 重序列化 |

### 4.3 编码规范（vendor 修改约定，草案）

1. **vendor 是整树拷贝**（非 submodule）：所有本地改动必须集中在 `vendor/` 内，禁止反向改 `/opt/ros/humble`。
2. **每个 patch 可追溯**：改动文件头部注释块写明「上游 commit 哈希 + 本地改动一句话 + 是否计划上游 PR」。
3. **公共 API 默认冻结**：`rmw_*` C 接口签名不改；内部 `__rmw_*` 与 `Custom*Info` struct 可重构但必须跑全部 check 闸门。
4. **一次一步**：每个 PR 只对应一个 R 步骤，禁止 R1+R2 混提。
5. **bench 红线**：改动前后必须跑 `pingpong.py` + `run_imu_hf.sh` + `run_large_packet.sh`，p95 抖动与大包成功率必须留档到 `docs/artifacts/bench/<date>/`（沿用现有 DoD 惯例，不抄 SCOREBOARD 数字）。
6. **不动 `dimos_bridge/`**（只读参照）与原 Hold 中未被本次授权覆盖的部分（Cega、Agnocast）。

---

## 5. 执行路线图

### Phase 1：低风险行为保持型重构（先做，不要求 §3.0 决策）

| 步骤 | 预期收益 | 验证标准 |
|------|----------|----------|
| R11 死代码清理 | 订阅者 struct 体积下降，阅读成本下降 | 编译通过；`check_source_map.py` 符号仍在 |
| R6 辅助函数提取 | 179 处 null 检查收敛，新增重复被 check 脚本挡住 | `check_null_check_dedup.py` 数字下降；全部现有闸门绿 |
| R9 环境变量统一 | 操作员 source 错误率下降 | `check_env_chain_consistency.py` 绿 |
| R12 RAII 化（分批） | 消除异常安全隐患 | ASan 无新增泄漏；bench 零漂移 |
| R5 QoS 模板收敛 | 15 处散点策略收敛到一处 | `check_qos_compat.py` 上线 |
| R1 时间戳注入接口 | 为 Phase 2 的高频优化铺路（当前收益微小） | pingpong p50 不劣化 |

**Phase 1 出口标准**：12 个现有 `check_*.py` + §4.2 新增脚本全绿；`prove_rmw.py` 身份不变；大包/高频 bench 与基线同分布。

### Phase 2：中风险性能优化（依赖 §3.0 决策落地）

| 步骤 | 预期收益 | 验证标准 |
|------|----------|----------|
| R2 WaitSet 轮询优化 | 高频订阅（IMU 200Hz）下 CPU 占用下降、p95 抖动收敛 | `run_imu_hf.sh` p95 抖动下降且 CPU 占用下降；`check_executor_map.py` 通过 |
| R4 大消息一次序列化 | 多订阅者时 CPU 序列化成本从 O(N) 接近 O(1) | `run_large_packet.sh` 大包 RTT 下降；`check_no_duplicate_serialize.py` 绿 |
| R3 序列化路径统一 | 两链维护成本减半，改序列化只需改一处 | `check_serialization_path.py` 绿 |
| R8 反序列化缓存 | take 路径分配减少 | IMU 高频场景 CPU 下降 |
| R7 data sharing 选项 | 小包零拷贝、jitter 下降 | IMU 场景 p95 下降；大包不重蹈 iter5 覆辙 |
| R10 编译期 QoS 检查 | 消灭运行时 incompatible QoS 偶发 | `check_qos_compat.py` 对历史不匹配报警 |

**Phase 2 出口标准**：三个 bench 场景（pingpong / IMU 高频 / 大包）p95 与 CPU 均有可量化改善且无回退；新增 check 脚本全绿；行为对应用层完全透明。

### Phase 3：高风险架构调整（依赖 Phase 2 实测收益 + §3.0 + §4.1 设计文档）

| 任务 | 预期收益 | 验证标准 / 回滚 |
|------|----------|------------------|
| 3.1 Fast-DDS 钉到 v3.6.2 | vendor 从滚动快照变为可复现 | 整树替换可回滚；bench 基线不漂移 |
| 3.3 rolling↔Humble API 差量表 + 扫描 | 从根上解决「改了不跑的代码」 | `check_rmw_api_usage.py` 绿 |
| 3.4 共享内存/data sharing 落地 | 同机大包零拷贝 | iter5 失败模式复现留档；大包成功率 100% |
| 3.2 CycloneDDS（仅按需） | 无强制收益 | 链 B 基线不变 |
| 3.5 Zenoh / 3.6 自研 RMW | **本期不启动** | 仅在设计文档保留评估结论 |

**Phase 3 出口标准**：vendor 树版本可复现、与运行时的差异完全文档化、共享内存策略对大包/高频两个真实业务场景都有实测收益。

---

## 附：与既有纪律的一致性

- 本文档**不抄 SCOREBOARD 数字**，所有收益表述均为方向性；实测数字必须落到 `docs/artifacts/bench/<date>/`（沿用 latency-attribution.md 硬规则）。
- 跨机 UDP 若仍无第二台机器，相关步骤一律标 `STATUS: blocked`，不填假分位数。
- 链 A 与链 B 永不进同一张性能对照表。
- 本计划覆盖原 AGENTS.md 中「不改 vendor 源码」的 Hold（用户已授权进入重构期），但「不接 Agnocast / zenoh、不改 dimos_bridge、《3》-《6》out of scope」仍然有效，见 §3.5/3.6。
