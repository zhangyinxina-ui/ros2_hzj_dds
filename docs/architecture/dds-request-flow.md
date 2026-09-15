# DDS 请求流经文档（publish 发布链 + wait/take 接收链）

Status: **分析文档（只读源码对照，不改任何 vendor 源码）**。
日期：2026-09-16。
上游源码地图见 [ros2-source-map.md](ros2-source-map.md)（它只标"文件存在"，本文以实际行号深化为"一个请求怎么走完"）。版本钉扎见 [`../../vendor/VERSIONS.md`](../../vendor/VERSIONS.md)。

> **本仓没有 vendor `rcl` / `rclcpp` / `rclpy`。** `rcl_publish`、`Executor::spin`、`rcl_wait` 都在 Humble 发行版（`docker/ros/`，`ROS_DISTRO=humble`），不在 `vendor/`。本文凡涉及这一层，一律标注"发行版，不在本仓"，**不要拿 rolling 的 `rmw_*.h` 去覆盖 `/opt/ros/humble`**。

---

## 0. 两条链、两个域、两个 RMW

| | 链 A（nav） | 链 B（DimOS / Unitree） |
|---|---|---|
| RMW 实现名 | `rmw_fastrtps_cpp` | `rmw_cyclonedds_cpp` |
| DDS 栈 | Fast-DDS（`vendor/Fast-DDS/`，master 近 v3.6.2） | CycloneDDS（`vendor/CycloneDDS/`，tag **11.0.1**） |
| 域 ID | `ROS_DOMAIN_ID=42`（[`config/env/chain_a.sh:10`](../../config/env/chain_a.sh)、[`config/fastdds.xml:108`](../../config/fastdds.xml) `<domainId>42</domainId>`） | `ROS_DOMAIN_ID=0`（[`config/env/chain_b.sh:10`](../../config/env/chain_b.sh)） |
| 标识符字符串出处 | [`vendor/rmw_fastrtps/rmw_fastrtps_cpp/src/identifier.cpp`](../../vendor/rmw_fastrtps/rmw_fastrtps_cpp/src/identifier.cpp) | [`vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp:134`](../../vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp) `eclipse_cyclonedds_identifier = "rmw_cyclonedds_cpp"` |

**两条链不共享域、不共享 RMW 实例、QoS 也不假定互通**（见 §4 陷阱 6）。

---

## 1. 请求如何流经 DDS

### 1.1 发布链（一个 `publish()` 调用在本进程内走多远）

```
app publisher.publish(msg)
  └─ rclcpp/rclpy/rcl            [发行版，不在本仓]
     └─ rcl_publish               [发行版，不在本仓]
        └─ rmw_publish            声明 vendor/rmw/.../rmw.h:546
           └─ rmw_implementation  dlopen 分派到具体 .so
              ├─ 链A: rmw_fastrtps_cpp/src/rmw_publish.cpp:242
              │        └─ shared_cpp/src/rmw_publish.cpp:34  __rmw_publish
              │           └─ DataWriterImpl::write_w_timestamp   Fast-DDS
              └─ 链B: rmw_cyclonedds_cpp/src/rmw_node.cpp:2037 rmw_publish
                       └─ dds_write_ts                          Cyclone
```

**关键事实：`rmw_publish` 返回 `RMW_RET_OK` ≠ 对端已投递、已入对端 History、已跑 callback。** 它只代表"本进程把样本交给了 DataWriter / `dds_write_ts`"，并在 publish 线程上**同步**完成了序列化与入 Writer History（见下文 perform_create_new_change）。端到端成功要另走接收链。

#### 步骤 P0 —— RMW 声明与实现分派

- 声明：[`vendor/rmw/rmw/include/rmw/rmw.h:546`](../../vendor/rmw/rmw/include/rmw/rmw.h)
  ```c
  rmw_ret_t rmw_publish(const rmw_publisher_t * publisher,
                        const void * ros_message,
                        rmw_publisher_allocation_t * allocation);
  ```
  头注释（同文件 535–545 行）写明返回约定：NULL → `RMW_RET_INVALID_ARGUMENT`；标识符不匹配 → `RMW_RET_INCORRECT_RMW_IMPLEMENTATION`；其余异常 → `RMW_RET_ERROR`。
- 动态分派：[`vendor/rmw_implementation/rmw_implementation/src/functions.cpp:77`](../../vendor/rmw_implementation/rmw_implementation/src/functions.cpp) `load_library()`，优先级：
  1. 环境变量 `RMW_IMPLEMENTATION`（同文件 `:89` `rcpputils::get_env_var("RMW_IMPLEMENTATION")`）；
  2. `DEFAULT_RMW_IMPLEMENTATION`（`:105` `attempt_to_load_one_rmw(STRINGIFY(DEFAULT_RMW_IMPLEMENTATION))`）；
  3. ament 目录里其它 `rmw_typesupport`。
  真正 `dlopen` 发生在 `attempt_to_load_one_rmw`，装载结果挂到全局 `g_rmw_lib`（`:136`）。卸载在 `unload_library()`（`:940`）。

#### 步骤 P1 —— 链 A：rmw_fastrtps_cpp 入口

[`vendor/rmw_fastrtps/rmw_fastrtps_cpp/src/rmw_publish.cpp:242`](../../vendor/rmw_fastrtps/rmw_fastrtps_cpp/src/rmw_publish.cpp)：

```cpp
242  extern "C" rmw_ret_t rmw_publish(const rmw_publisher_t * publisher,
243        const void * ros_message, rmw_publisher_allocation_t * allocation) {
248    RMW_CHECK_FOR_NULL_WITH_MSG(publisher, ...);
251    RMW_CHECK_TYPE_IDENTIFIERS_MATCH(publisher, publisher->implementation_identifier,
252        rmw_fastrtps_cpp__identifier(), ...);
254    RMW_CHECK_FOR_NULL_WITH_MSG(ros_message, ...);
279    return rmw_fastrtps_shared_cpp::__rmw_publish(
280        rmw_fastrtps_cpp__identifier(), publisher, ros_message, allocation);
```

#### 步骤 P2 —— 链 A：shared_cpp 取时间戳 + 调 DataWriter

[`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp:34`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp) `__rmw_publish`：

```cpp
45   RMW_CHECK_FOR_NULL_WITH_MSG(publisher, "publisher handle is null", ...);
48   RMW_CHECK_TYPE_IDENTIFIERS_MATCH(publisher, publisher->implementation_identifier, identifier, ...);
51   RMW_CHECK_FOR_NULL_WITH_MSG(ros_message, "ros message handle is null", ...);
58   rmw_fastrtps_shared_cpp::SerializedData data;
62   eprosima::fastdds::dds::Time_t stamp;
63   eprosima::fastdds::dds::Time_t::now(stamp);          // ← 每次 publish 都取一次墙钟
65   if (RETCODE_OK != info->data_writer_->write_w_timestamp(&data, HANDLE_NIL, stamp)) {
68     RMW_SET_ERROR_MSG("cannot publish data");
70     return RMW_RET_ERROR;
```

`Time_t::now` 本身只是一次系统时钟读取：[`vendor/Fast-DDS/src/cpp/fastdds/core/Time_t.cpp:85`](../../vendor/Fast-DDS/src/cpp/fastdds/core/Time_t.cpp) → `current_time_since_unix_epoch(...)`（`:88`）。**这是 publish 热路径上的系统调用**（见 §4 陷阱 3）。

#### 步骤 P3 —— 链 A：Fast-DDS 序列化 + 入 History

[`vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp:750`](../../vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp) `write_w_timestamp` → 预条件检查 `check_write_preconditions`（`:764`）→ `create_new_change_with_params`（`:772`）→ [`:1044`](../../vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp) `perform_create_new_change`：

```cpp
1068   uint32_t payload_size = fixed_payload_size_ ? fixed_payload_size_
1070       : type_->calculate_serialized_size_ctx(type_support_context_, data, data_representation_);  // 算序列化长度
1080   if (!get_free_payload_from_pool(payload_size, payload)) return RETCODE_OUT_OF_RESOURCES;
1086   if (!type_->serialize_ctx(type_support_context_, data, payload, data_representation_)) {       // ← 同步序列化
1090     return RETCODE_ERROR;
1101   CacheChange_t* ch = history_->create_change(change_kind, handle);
1119   added = history_->add_pub_change(ch, wparams, lock, max_blocking_time);                        // ← 入 Writer History
```

History 落两层：
- [`vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterHistory.cpp:273`](../../vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterHistory.cpp) `add_pub_change` → `add_change_`（`:285` / `:287`）；
- RTPS 层 [`vendor/Fast-DDS/src/cpp/rtps/history/WriterHistory.cpp:208`](../../vendor/Fast-DDS/src/cpp/rtps/history/WriterHistory.cpp) `add_change_`（另有 `:121`/`:128` 两个重载入口）。
- 入 History 之后才由 RTPS 层异步把 change 写到线上（UDP / SHM）。**publish 返回时线上不一定发出去了。**

#### 步骤 P1' —— 链 B：rmw_cyclonedds_cpp 入口

[`vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp:2037`](../../vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp)：

```cpp
2042  RMW_CHECK_FOR_NULL_WITH_MSG(publisher, "publisher handle is null", ...);
2045  RMW_CHECK_TYPE_IDENTIFIERS_MATCH(publisher, publisher->implementation_identifier,
2046      eclipse_cyclonedds_identifier, ...);
2051  auto pub = static_cast<CddsPublisher *>(publisher->data);
2053  const dds_time_t tstamp = dds_time();
2055  if (dds_write_ts(pub->enth, ros_message, tstamp) >= 0) return RMW_RET_OK;
```

#### 步骤 P3' —— 链 B：Cyclone 写路径

[`vendor/CycloneDDS/src/core/ddsc/src/dds_write.c`](../../vendor/CycloneDDS/src/core/ddsc/src/dds_write.c)：

- `dds_write`（`:101`）/ `dds_write_ts`（`:158`）只是薄壳，都调 `dds_write_impl(...)`；
- `dds_write_impl`（`:808`）内部先 `dds_write_impl_make_serdata`（`:660`，**在 publish 线程上把样序序列化成 `ddsi_serdata`**），再走交付：
  - 经 PSMX（共享内存）：`dds_write_impl_deliver_via_psmx`（`:628`，调用点 `:905`/`:925`）；
  - 经 DDSI（网络）：`dds_write_impl_deliver_via_ddsi`（`:565`，调用点 `:935`）；
- Writer History（WHC）插入是内联函数：[`vendor/CycloneDDS/src/core/ddsi/src/ddsi__whc.h:67`](../../vendor/CycloneDDS/src/core/ddsi/src/ddsi__whc.h) `inline int ddsi_whc_insert(...)`（`.c` 文件 `:22` 仅为 extern 声明，真正函数体在头文件里）。

---

### 1.2 接收链（样本怎么走到用户 callback）

接收分两段、在**不同线程**：
1. **DDS 后台线程**：网络/本机投递 → 反序列化 → Reader History（早于任何 ROS callback）；
2. **ROS executor 线程**：`rmw_wait` 唤醒 → `rmw_take` → 用户 callback。

```
[DDS 线程] 网络/本机投递
  ├─ 链A: StatefulReader::process_data_msg / change_received
  │        → ReaderHistory::received_change → add_change（入 Reader History）
  └─ 链B: ddsi 收包 → rhc_store → 默认 RHC（dds_rhc_*.c）

[Executor 线程] rclcpp::Executor::spin  [发行版，不在本仓]
  └─ rcl_wait  [发行版]
     └─ rmw_wait  声明 rmw.h:2688
        ├─ 链A: shared_cpp/src/rmw_wait.cpp __rmw_wait
        │        WaitSet::wait → get_first_untaken_info 轮询就绪
        └─ 链B: rmw_node.cpp:4511 rmw_wait
                 dds_waitset_attach → dds_waitset_wait
        ↓ 就绪
     rmw_take  声明 rmw.h:1253
        ├─ 链A: __rmw_take_with_info → DataReader::take → 反序列化
        └─ 链B: rmw_take:3790 → dds_take:356（dds_read.c）
        ↓
     用户 callback（rclcpp 层，不在本仓）
```

#### 步骤 R1 —— DDS 线程入 Reader History（链 A）

- RTPS 收包分发：[`vendor/Fast-DDS/src/cpp/rtps/reader/StatefulReader.cpp:568`](../../vendor/Fast-DDS/src/cpp/rtps/reader/StatefulReader.cpp) `process_data_msg`；状态机判新 change：`:1091` `change_received`。
- 入 Reader History：[`vendor/Fast-DDS/src/cpp/rtps/history/ReaderHistory.cpp:81`](../../vendor/Fast-DDS/src/cpp/rtps/history/ReaderHistory.cpp) `received_change` → `:88` `add_change`。
- **注意：到这里样本只躺在 reader cache 里，用户一行 callback 都没跑。**

#### 步骤 R2 —— Executor wait（链 A）

[`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp)：

- `data_reader_has_data`（`:32`）——**逐个 reader 调 `get_first_untaken_info`**（`:41`）判断有没有未取样本；
- `has_triggered_condition`（`:64`）先查 GuardCondition / event（便宜），最后才查 subscription（`:98`）——源码注释 `:71` 明确写：**"`get_first_untaken_info` 比查 guard condition 贵，应尽量避免"**；
- `__rmw_wait`（`:137`）：把所有 Condition `attach_condition`（`:232`）→ `WaitSet::wait` 阻塞（`:240`）→ 醒来后再 detach（`:249`）；
- 醒来后**再对每个 subscription 调一次** `subscription_has_data`（`:259`）确认就绪，不就绪的 slot 置 0。

Fast-DDS WaitSet 本体：[`vendor/Fast-DDS/src/cpp/fastdds/core/condition/WaitSet.cpp:49`](../../vendor/Fast-DDS/src/cpp/fastdds/core/condition/WaitSet.cpp) `WaitSet::wait`。
`get_first_untaken_info` 本体：[`vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp:802`](../../vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp) → 转 `history_->get_first_untaken_info`（`:810`）。

#### 步骤 R2' —— Executor wait（链 B）

[`vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp:4511`](../../vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp) `rmw_wait`：

```cpp
4517  RMW_CHECK_TYPE_IDENTIFIERS_MATCH(wait_set, ..., eclipse_cyclonedds_identifier, ...);
4532  if (require_reattach(...)) {          // WaitSet 成员集合变化才重挂
4543    waitset_detach(ws);
4552    dds_waitset_attach(ws->waitseth, x->rdcondh, nelems++);   // subscription
4563    dds_waitset_attach(ws->waitseth, x->gcondh, ...);         // guard condition
4574    dds_waitset_attach(ws->waitseth, x->service.sub->rdcondh, ...);
4585    dds_waitset_attach(ws->waitseth, x->client.sub->rdcondh, ...);
4620  const dds_return_t ntrig = dds_waitset_wait(ws->waitseth, ws->trigs.data(),
4621                                              ws->trigs.size(), timeout);
```

- Cyclone 侧：[`vendor/CycloneDDS/src/core/ddsc/src/dds_waitset.c:289`](../../vendor/CycloneDDS/src/core/ddsc/src/dds_waitset.c) `dds_waitset_attach`（经 observer 注册 `dds_entity_observer_register` `:327`）；`dds_waitset_wait`（`:390`）→ 实现 `dds_waitset_wait_impl`（`:43`）。
- 与链 A 的差别：**Cyclone 是"事件驱动 attach"**——reader 变就绪会直接唤醒 waitset，不需要在 `rmw_wait` 里轮询每个 reader 的 SampleInfo；链 A 则依赖 WaitSet 唤醒后再用 `get_first_untaken_info` 二次确认。

#### 步骤 R3 —— take + 反序列化（链 A）

[`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_take.cpp`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_take.cpp)：

- `__rmw_take`（`:208`）/ `__rmw_take_with_info`（`:270`）做 NULL 与标识符检查后调 `_take`；
- take 循环（以 serialized 版本为例 `:324`）：
  ```cpp
  324  while (RETCODE_OK == info->data_reader_->take(data_values, info_seq, 1)) {
  332    if (info_seq[0].valid_data) { ... }   // ← SampleInfo 验证点
  ```
  本体 `DataReaderImpl::take`：[`vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp:644`](../../vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp)，由它把 CDR 反序列化回用户消息结构。

#### 步骤 R3' —— take（链 B）

- RMW 层 `rmw_take`：[`rmw_node.cpp:3790`](../../vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp)；
- DDS 层 `dds_take`：[`vendor/CycloneDDS/src/core/ddsc/src/dds_read.c:356`](../../vendor/CycloneDDS/src/core/ddsc/src/dds_read.c)（一脉变体 `dds_take_wl`/`_mask`/`_instance`/`_next` 都在 `:361`–`:406`），统一走 `dds_read_impl`（`:213`）→ `dds_read_impl_common`（`:152`）。

---

## 2. 模块职责划分

| 模块 | 在本仓位置 | 负责 | **不**负责 |
|---|---|---|---|
| **rcl / rclcpp / rclpy** | **不在本仓**（Humble `/opt/ros/humble`） | 应用 API、Executor 调度、callback 注册、`rcl_publish`/`rcl_wait` | 不做序列化、不碰 socket |
| **rmw（接口层）** | [`vendor/rmw/rmw/include/rmw/rmw.h`](../../vendor/rmw/rmw/include/rmw/rmw.h) | 纯 C 抽象接口声明（`rmw_publish`/`rmw_wait`/`rmw_take`）、返回码约定 | 无实现 |
| **rmw_implementation** | [`vendor/rmw_implementation/rmw_implementation/src/functions.cpp`](../../vendor/rmw_implementation/rmw_implementation/src/functions.cpp) | `dlopen` 加载具体 RMW `.so`，按 `RMW_IMPLEMENTATION` 选链 | 不做 DDS 业务 |
| **rmw_fastrtps_cpp** | `vendor/rmw_fastrtps/rmw_fastrtps_cpp/src/` | 链 A 的 `extern "C"` 入口、identifier 字符串 | 不直接写 DDS，转调 shared_cpp |
| **rmw_fastrtps_shared_cpp** | `vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/` | 链 A 的 `__rmw_publish`/`__rmw_wait`/`__rmw_take`、与 Fast-DDS 类型支持对接 | 不做 UDP |
| **Fast-DDS** | `vendor/Fast-DDS/` | DataWriter/DataReader、History、WaitSet、RTPS 协议、序列化池、UDP/SHM 传输 | 不懂 ROS 消息语义，只认 CDR 字节流 |
| **rmw_cyclonedds_cpp** | `vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp` | 链 B 的全部 RMW 入口、`dds_write_ts`/`dds_waitset_attach`/`dds_take` 封装 | 不实现 DDSI 协议本身 |
| **CycloneDDS** | `vendor/CycloneDDS/` | ddsc API、ddsi 协议、WHC/RHC、waitset、发现 | 不懂 ROS |
| **TypeSupport（序列化）** | fastcdr（Fast-DDS thirdparty）/ Cyclone `serdata` | 把 ROS 消息 ↔ CDR 字节流；算序列化大小、按 buffer 容量切 | 不含业务判断，只做字段级编解码 |
| **Executor** | 不在本仓（Humble rclcpp） | spin、wait → take → callback 的线程调度 | 不保证顺序外的任何业务语义 |

**哪个模块负责实际业务逻辑？——都不是。** 整条 DDS 栈（rcl 以下）是**纯传输层**：它搬运字节、管理 History、唤醒线程，但不理解"这条消息代表机器人要左转 0.1 rad"。业务逻辑只存在于**上层应用节点**（订阅 callback 里）。最接近"业务数据处理"的层是 **TypeSupport 的序列化/反序列化**——它逐字段读写字段类型和长度，但那也是机械编解码，不是业务判断。

---

## 3. 数据验证点（每层强制执行了哪些假设）

| 层 | 位置 | 验证了什么假设 | 失败时返回 |
|---|---|---|---|
| RMW 空指针 | 链 A [`shared rmw_publish.cpp:45`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp)、`:51`；链 B [`rmw_node.cpp:2042`](../../vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp)、`:2048` | `publisher`/`ros_message` 句柄非空 | `RMW_RET_INVALID_ARGUMENT` |
| RMW 实现标识符匹配 | 链 A `shared rmw_publish.cpp:48`（`RMW_CHECK_TYPE_IDENTIFIERS_MATCH`）；链 B `rmw_node.cpp:2045`；wait 路径 `shared rmw_wait.cpp:151`、`rmw_node.cpp:4517` | 这个 handle 确实属于"当前已加载的 RMW"（防止把 fastrtps 的 publisher 喂给 cyclonedds） | `RMW_RET_INCORRECT_RMW_IMPLEMENTATION` |
| TypeSupport 序列化大小 | [`DataWriterImpl.cpp:1068`](../../vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp) `calculate_serialized_size_ctx`；`:1080` `get_free_payload_from_pool(payload_size, ...)` | 序列化后字节数能从 payload 池拿到足够容量 | `RETCODE_OUT_OF_RESOURCES` |
| TypeSupport 序列化成功 | `DataWriterImpl.cpp:1086` `type_->serialize_ctx(...)` 返回 bool | 类型支持器能把对象写成合法 CDR | `RETCODE_ERROR` |
| DDS 时间戳合法性 | `DataWriterImpl.cpp:757` `timestamp.is_infinite() || timestamp.seconds < 0` | 调用方给的时间戳不是负/无穷 | `RETCODE_BAD_PARAMETER` |
| DDS 写预条件 | `DataWriterImpl.cpp:764` `check_write_preconditions`（writer 已 enable、data 非空、key 已算） | 实体已正确初始化 | `RETCODE_NOT_ENABLED` / `BAD_PARAMETER` |
| Writer History 深度 | `DataWriterHistory.cpp:273` `add_pub_change` → `add_change_`（受 QoS `history.depth` 约束，满了按 KEEP_LAST 丢弃最旧） | 历史不无限增长 | 写阻塞到 `max_blocking_time`，超时 `RETCODE_TIMEOUT`（见 `perform_create_new_change` `:1051`/`:1130`） |
| WaitSet 就绪二次确认 | 链 A `shared rmw_wait.cpp:259` `subscription_has_data` → `get_first_untaken_info`（`DataReaderImpl.cpp:802`） | 唤醒后该 reader 真有未取样本（防假唤醒） | 不就绪 slot 置 0，最终 `RMW_RET_TIMEOUT`（`shared rmw_wait.cpp:359`） |
| take 后 SampleInfo | 链 A `shared rmw_take.cpp:332` `if (info_seq[0].valid_data)`；`ignore_local_publications` 时比对 `publication_handle` GUID 前缀（`:335`–`:337`） | 样本是有效数据、且不是本进程自产自销 | 跳过该样本（`continue`） |
| 并发 wait 互斥 | 链 B `rmw_node.cpp:4524`–`:4529` `ws->inuse` 锁 | 同一个 WaitSet 不被两个线程同时 `rmw_wait` | `RMW_RET_ERROR` "concurrent calls ... not supported" |

---

## 4. 改动前最需要注意的陷阱

1. **`rmw_publish` 返回 `RMW_RET_OK` ≠ 对端收到。** 它只表示本进程已序列化并入了 Writer History（`DataWriterImpl.cpp:1119`）。线上何时发、对端何时入 History、callback 何时跑，都不在这次调用的返回值里。不要用它做"消息送达"判断。
2. **rolling vendor ≠ Humble 运行时。** `vendor/` 是 rolling/master 快照（`vendor/VERSIONS.md`：rmw 7.11.2、Fast-DDS master 近 v3.6.2、Cyclone tag 11.0.1），跑的 Docker 是 Humble。**不要把 vendor 里的文件直接覆盖 `/opt/ros/humble`**，行号对不上、ABI 也可能变。
3. **`Time_t::now()` 是 publish 热路径上的系统调用。** 链 A 每次 `__rmw_publish` 都在 `shared rmw_publish.cpp:63` 取一次墙钟 → `Time_t.cpp:88` `current_time_since_unix_epoch`。高频发布时这是可观测的延迟来源之一（对照 `latency-attribution.md`）。
4. **`get_first_untaken_info` 在 `rmw_wait` 里被反复调用，是 CPU 开销来源。** `shared rmw_wait.cpp:71` 源码注释自己承认它"相对贵"；`has_triggered_condition`（`:64`）和 wait 后二次过滤（`:259`）都会对每个 subscription 各调一次。订阅很多时这是自旋成本。
5. **序列化在 publish 线程上同步完成。** `perform_create_new_change` 在调用 `write_w_timestamp` 的线程内就跑完 `calculate_serialized_size_ctx`（`:1068`）+ `serialize_ctx`（`:1086`）。大消息（图像、点云）会**阻塞发布线程**，不是丢给后台。
6. **两条链不共享域 / RMW / QoS。** 链 A 域 42 + fastrtps，链 B 域 0 + cyclonedds（`config/env/chain_a.sh:10`、`chain_b.sh:10`）。**不能假设链 A 发的 topic 链 B 能收到**——跨链互通要另配发现/桥接，且 QoS（reliability/durability/deadline）不兼容时连同栈也建不上 reader。
7. **`dimos_bridge/` 是只读参照。** 它是 DimOS 双链对照代码（见 `dimos_bridge/SOURCE.md`），按 `AGENTS.md` 的 Hold 要求**不改它的 DDS 行为**，也不要把它当运行时补丁点。
8. **History 深度与 QoS 的交互是丢包的真因。** KEEP_LAST 满了不是报错，而是静默丢最旧（`WriterHistory`/`WHC` 内部）；再叠 reliability=best_effort、deadline、lifespan（`DataWriterImpl.cpp:1133`/`:1155` 的 timer 就是 lifespan/deadline 驱动），"消息偶发丢失"往往先去查 QoS 而不是网络。
9. **WaitSet 每次 wait 都 attach 再 detach。** `shared rmw_wait.cpp:232` attach、`:249` detach——这是 fastrtps 链 A 的设计，高频 spin 下有额外开销；链 B 只有成员变化才 `require_reattach` 重挂（`rmw_node.cpp:4532`）。别把两条链的 wait 成本混为一谈。
10. **publish 路径上还有 deadline/lifespan 定时器。** `perform_create_new_change:1136`/`:1160` 会在每次 ALIVE 写时重排 deadline timer、重启 lifespan timer。配了 deadline QoS 时，publish 不只是"写一条数据"。

---

## 5. 接下来应该阅读的文件清单（按优先级）

| # | 文件 | 为什么读 |
|---|---|---|
| 1 | [`vendor/rmw/rmw/include/rmw/rmw.h`](../../vendor/rmw/rmw/include/rmw/rmw.h) | RMW 全部接口契约与返回码，所有改动的"宪法"（`rmw_publish:546`/`rmw_wait:2688`/`rmw_take:1253`） |
| 2 | [`vendor/rmw_implementation/rmw_implementation/src/functions.cpp`](../../vendor/rmw_implementation/rmw_implementation/src/functions.cpp) | 理解"环境变量怎么决定走哪条链"（`load_library:77`） |
| 3 | [`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp) | 链 A publish 全貌，验证点与 `Time_t::now` 都在这 |
| 4 | [`vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp`](../../vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp) | `write_w_timestamp:750` → `perform_create_new_change:1044`，序列化与 History 插入的真正发生地 |
| 5 | [`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp) | 链 A 唤醒模型 + `get_first_untaken_info` 成本注释 |
| 6 | [`vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_take.cpp`](../../vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_take.cpp) | 链 A take 循环与 `valid_data`/ignore-local 过滤 |
| 7 | [`vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp`](../../vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp) | 链 B publish `:2037`、wait `:4511`、take `:3790` 全在这一个文件 |
| 8 | [`vendor/CycloneDDS/src/core/ddsc/src/dds_write.c`](../../vendor/CycloneDDS/src/core/ddsc/src/dds_write.c) | `dds_write_impl:808` 里 PSMX vs DDSI 两条交付分支 |
| 9 | [`vendor/CycloneDDS/src/core/ddsc/src/dds_read.c`](../../vendor/CycloneDDS/src/core/ddsc/src/dds_read.c) | `dds_take:356` 及 read/take 一脉变体 |
| 10 | [`vendor/CycloneDDS/src/core/ddsc/src/dds_waitset.c`](../../vendor/CycloneDDS/src/core/ddsc/src/dds_waitset.c) | 链 B 事件驱动唤醒（attach `:289` / wait `:390`） |
| 11 | [`vendor/Fast-DDS/src/cpp/rtps/reader/StatefulReader.cpp`](../../vendor/Fast-DDS/src/cpp/rtps/reader/StatefulReader.cpp) | 对端样本怎么进入 reader（`process_data_msg:568`/`change_received:1091`） |
| 12 | [`vendor/Fast-DDS/src/cpp/rtps/history/ReaderHistory.cpp`](../../vendor/Fast-DDS/src/cpp/rtps/history/ReaderHistory.cpp) | Reader History 的 `received_change:81`/`add_change:88` |
| 13 | [`vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp`](../../vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp) | `take:644` 与 `get_first_untaken_info:802` 的真正实现 |
| 14 | [`vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterHistory.cpp`](../../vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterHistory.cpp) | `add_pub_change:273`，理解 KEEP_LAST 丢弃行为 |
| 15 | [`vendor/Fast-DDS/src/cpp/fastdds/core/condition/WaitSet.cpp`](../../vendor/Fast-DDS/src/cpp/fastdds/core/condition/WaitSet.cpp) | `WaitSet::wait:49`，链 A 阻塞原语 |
| 16 | [`vendor/CycloneDDS/src/core/ddsi/src/ddsi__whc.h`](../../vendor/CycloneDDS/src/core/ddsi/src/ddsi__whc.h) | 链 B Writer History 内联插入 `:67` |
| 17 | [`docs/architecture/feishu-executor-waitset.md`](feishu-executor-waitset.md) | WaitSet → callback 的身份映射（Humble rclcpp 不在本仓的那部分） |
| 18 | [`vendor/MANIFEST.md`](../../vendor/MANIFEST.md) | Humble Docker vs rolling 快照的语义差异，改之前必读 |
| 19 | [`config/fastdds.xml`](../../config/fastdds.xml) + [`config/env/chain_a.sh`](../../config/env/chain_a.sh)/[`chain_b.sh`](../../config/env/chain_b.sh) | 域 ID、RMW 选择、发现配置的实际生效点 |

---

## 6. 改流程时容易被忽略的相关文件 / 后台任务

- **事件回调（listener）**：订阅就绪/匹配不是在你代码里轮询，而是 DDS 后台 listener。链 A 的 `data_available` / `subscription_matched` 走 `CustomEventInfo`（见 `shared rmw_wait.cpp:86`–`:96` 的 event guard 检查）；链 B 在 `rmw_wait` 里用 `gather_event_entities` 把 event entity 也 attach 进 waitset（`rmw_node.cpp:4592`–`:4600`）。改 wait 路径时事件回调会跟着变。
- **GuardCondition 唤醒机制**：链 A 在 `shared rmw_wait.cpp:344`–`:356` 里 `set_trigger_value(false)` 消费 guard；链 B attach 的是 `x->gcondh`（`rmw_node.cpp:4563`）。Cancel-tokens、interrupt executor、`rclcpp::shutdown()` 都靠它——只改 DataReader 路径容易漏掉 guard 分支。
- **静态发现 vs 动态发现**：链 A 的发现行为由 `config/fastdds.xml`（`<domainId>`、initial peers、transport）控制；链 B 走 Cyclone 内建发现，`config/env/chain_b.sh:13` 还特意 `unset CYCLONEDDS_URI`。"明明发布了却收不到"经常是发现没配通，而不是数据路径错。
- **内建 topic**：`rosout`（日志）、`parameter_events`（参数）是 RMW 层自动创建的 publisher/subscription。它们也在 `rmw_wait` 的 subscription 列表里——改 wait/take 遍历时别把它们当普通业务 topic 漏掉，也别假设它们的 QoS 和你业务 topic 一致。

---

## 7. 编辑后该跑的测试 / 检查

本仓是"无 ROS 的 vanilla box"，先跑不依赖 ROS 的闸门（`AGENTS.md` 列出）：

```bash
# 本文档自身
python3 scripts/check_source_map.py          # 校验 ros2-source-map.md 引用路径与符号仍在（本文不破坏它）

# RMW / 环境一致性
python3 scripts/prove_rmw.py                # 当前进程到底会加载哪个 RMW/.so（无 ROS 也退出 0）
python3 scripts/check_executor_map.py       # WaitSet→callback 地图
python3 scripts/check_runtime_provenance.py
python3 scripts/check_dual_chain_baseline.py
python3 scripts/check_three_chain_repro.py
python3 scripts/check_sink_layers.py
python3 scripts/check_risk_matrix.py
python3 scripts/check_dod_evidence.py
python3 scripts/check_cega_bridge_hold.py
python3 scripts/check_unitree_cyclone_swap.py

# 环境打印（不写 os.environ，需自己 source）
python3 config/env/load.py print-a          # 链 A：fastrtps / 域 42
python3 config/env/load.py print-b          # 链 B：cyclonedds / 域 0
```

bench 脚本在 [`scripts/bench/`](../../scripts/bench/)：`pingpong.py`（时延往返）、`run_chain_a.sh` / `run_chain_b.sh` / `run_cross_host_a.sh`、`run_large_packet.sh`（大消息阻塞验证，对应陷阱 5）、`hzj_dds_compat.py`（双链 QoS/兼容性探测，对应陷阱 6）。跑之前先看 `scripts/bench/README.md`。

**如果有 Humble ROS 环境**（`docker/ros/`，`ROS_DISTRO=humble`）：

- 用 `source` 加载对应链 env（`config/env/chain_a.sh` 或 `chain_b.sh`），确认 `RMW_IMPLEMENTATION` / `ROS_DOMAIN_ID` 生效；
- 跑 `rclcpp` 自带最小 pub/sub：`ros2 run demo_nodes_cpp talker` 与 `listener`，对照 `scripts/prove_rmw.py` 确认加载的 `.so` 与预期一致；
- 改的是 wait/take 路径时，用 `intra-process` 与跨进程两种模式各跑一遍，确认 guard condition 与内建 topic 行为没退化；
- **不要**在本机直接覆盖 `/opt/ros/humble` 的文件来"验证"vendor 里的改动——那是 rolling 快照（陷阱 2）。

---

## 附：本文引用的行号快照（vendor 2026-09-10 拷贝）

| 符号 | 文件:行 |
|---|---|
| `rmw_publish` 声明 | `vendor/rmw/rmw/include/rmw/rmw.h:546` |
| `rmw_wait` 声明 | `vendor/rmw/rmw/include/rmw/rmw.h:2688` |
| `rmw_take` 声明 | `vendor/rmw/rmw/include/rmw/rmw.h:1253` |
| `load_library` | `vendor/rmw_implementation/rmw_implementation/src/functions.cpp:77` |
| 链 A `rmw_publish` → `__rmw_publish` | `vendor/rmw_fastrtps/rmw_fastrtps_cpp/src/rmw_publish.cpp:242`、`:279` |
| `__rmw_publish` | `vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp:34`（`Time_t::now:63`，`write_w_timestamp:65`） |
| `write_w_timestamp` / `perform_create_new_change` | `vendor/Fast-DDS/src/cpp/fastdds/publisher/DataWriterImpl.cpp:750` / `:1044`（`serialize_ctx:1086`，`add_pub_change:1119`） |
| `Time_t::now` | `vendor/Fast-DDS/src/cpp/fastdds/core/Time_t.cpp:85` |
| `__rmw_wait` / `get_first_untaken_info` | `vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp:137` / `:41`（成本注释 `:71`） |
| `WaitSet::wait` | `vendor/Fast-DDS/src/cpp/fastdds/core/condition/WaitSet.cpp:49` |
| `__rmw_take_with_info` / take 循环 | `vendor/rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_take.cpp:270` / `:324` |
| `DataReaderImpl::take` / `get_first_untaken_info` | `vendor/Fast-DDS/src/cpp/fastdds/subscriber/DataReaderImpl.cpp:644` / `:802` |
| `StatefulReader` / `ReaderHistory` | `StatefulReader.cpp:568`/`:1091`；`ReaderHistory.cpp:81`/`:88` |
| 链 B identifier / `rmw_publish` / `rmw_wait` / `rmw_take` | `vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/src/rmw_node.cpp:134` / `:2037`（`dds_write_ts:2055`）/ `:4511`（`dds_waitset_wait:4620`）/ `:3790` |
| `dds_write_ts` / `dds_write_impl` | `vendor/CycloneDDS/src/core/ddsc/src/dds_write.c:158` / `:808` |
| `dds_waitset_attach` / `dds_waitset_wait` | `vendor/CycloneDDS/src/core/ddsc/src/dds_waitset.c:289` / `:390` |
| `dds_take` | `vendor/CycloneDDS/src/core/ddsc/src/dds_read.c:356` |
| `ddsi_whc_insert` | `vendor/CycloneDDS/src/core/ddsi/src/ddsi__whc.h:67` |
