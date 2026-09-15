# Vendor 上游版本钉扎

本文件是本仓**唯一**的 vendor SHA / 标签钉扎表。其他文档只链到这里，不要再抄一份 SHA 表。

Humble Docker vs rolling / master 快照的**语义**（不要把 rolling 文件直接铺进 Humble）见 [MANIFEST.md](MANIFEST.md)，不在本表重复。

本目录是**整树文件拷贝**，不是 git submodule，也不是 subtree 远端跟踪。
源码以本仓 `vendor/` 为准。上游 URL 只用于追溯，不是运行时源。

拷贝日期：2026-09-10。策略与链 A 相同：浅克隆后去掉 `.git`，禁止 gitlink。

未使用 git LFS。未发明自定义 RMW。

| 链 | 本仓 vendor 树 |
|----|----------------|
| **A — nav FastDDS** | `vendor/rmw`、`vendor/rmw_implementation`、`vendor/rmw_fastrtps`、`vendor/Fast-DDS` |
| **B — DimOS Cyclone** | `vendor/rmw_cyclonedds`、`vendor/CycloneDDS` |

## 公开 ROS 2 / Fast-DDS 栈（链 A）

| 目录 | 上游 | 分支 | 标签（若 HEAD 正好是 tag） | 完整 SHA |
|------|------|------|------------------------------|----------|
| `vendor/rmw/` | https://github.com/ros2/rmw | `rolling` | `7.11.2` | `1e58706ed978ff8a9066f17dc11c61d3a644bf76` |
| `vendor/rmw_implementation/` | https://github.com/ros2/rmw_implementation | `rolling` | （HEAD 无 tag；近旁 tag `3.2.1`） | `ff8818df2328396011543db07e8ddca99b54345a` |
| `vendor/rmw_fastrtps/` | https://github.com/ros2/rmw_fastrtps | `rolling` | （HEAD 无 tag；近旁 tag `9.5.2`） | `83471d45c448dfc7f4d408bc36cff593df14eb90` |
| `vendor/Fast-DDS/` | https://github.com/eProsima/Fast-DDS | `master` | （HEAD 无 tag；近旁稳定 tag `v3.6.2`） | `343f155c36b561db3d9f047a65d866ce0f3da300` |

`rmw_fastrtps` 含 `rmw_fastrtps_cpp` / `rmw_fastrtps_dynamic_cpp` / `rmw_fastrtps_shared_cpp`。链 A 契约 RMW 名仍是 **`rmw_fastrtps_cpp`**。

## 公开 ROS 2 / CycloneDDS 栈（链 B）

策略：`rmw_cyclonedds` 取 `rolling` 当前 HEAD（与链 A 的 rmw 树一致）；CycloneDDS 取**最近稳定发行标签** `11.0.1`（与 rolling `rmw_cyclonedds` 4.2.x「Cyclone > 0.10」兼容声明匹配，而不是跟踪 `master` 日构建）。

| 目录 | 上游 | 分支 / 标签 | 标签 | 完整 SHA |
|------|------|-------------|------|----------|
| `vendor/rmw_cyclonedds/` | https://github.com/ros2/rmw_cyclonedds | `rolling` | `4.2.1` | `19478b0a9aa523af62023812d05bfcb4295e8efb` |
| `vendor/CycloneDDS/` | https://github.com/eclipse-cyclonedds/cyclonedds | tag `11.0.1` | `11.0.1` | `e54e991f75a3e67f8e628da3171122e36ea5b872` |

链 B 契约：DimOS 原生 `DDSConfig.domain_id` / Unitree `ChannelFactoryInitialize(0)` 为 **域 0**。若走 ROS 2 RMW，实现名是 **`rmw_cyclonedds_cpp`**。本仓**没有**自定义 RMW。

拷贝当日上游 `cyclonedds` `master` HEAD 为 `2f0d07d241f62f7121749b46721049e4dea5c58b`（**未**采用；本仓钉扎发行标签 `11.0.1`）。

`rmw_cyclonedds` 无 `.gitmodules` / gitlink。`CycloneDDS` 上游带空 `.gitmodules`（0 字节、无 submodule 条目），已原样保留作历史记录；**没有**需要摊平的 gitlink。Iceoryx 是 CMake 可选外部依赖，不是本树 submodule，本轮不 vendor。

**不要**把「已 vendor Cyclone」写成时延根因。双链对照与评测见 [docs/usage/benchmark-dds.md](../docs/usage/benchmark-dds.md)；数字未测之前只是假设。

## Fast-DDS thirdparty（原 gitlink，已摊成普通目录）

上游 `Fast-DDS/.gitmodules` 仍保留作历史记录，但本仓**没有** submodule。下列空 gitlink 已按 Fast-DDS 记录的 commit 整树拷入：

| 路径 | 上游 | SHA |
|------|------|-----|
| `vendor/Fast-DDS/thirdparty/android-ifaddrs/` | https://github.com/michalsrb/android-ifaddrs | `7b1ce82817226e481d3cda0a5d06b66ebcc211f8` |
| `vendor/Fast-DDS/thirdparty/asio/` | https://github.com/chriskohlhoff/asio | `ed6aa8a13d51dfc6c00ae453fc9fb7df5d6ea963` |
| `vendor/Fast-DDS/thirdparty/fastcdr/` | https://github.com/eProsima/Fast-CDR | `7d33a3b51a1585f5631b0a8d905bcc4f249d0f34` |
| `vendor/Fast-DDS/thirdparty/tinyxml2/` | https://github.com/leethomason/tinyxml2 | `8c8293ba8969a46947606a93ff0cb5a083aab47a` |
| `vendor/Fast-DDS/thirdparty/dds-types-test/` | https://github.com/eProsima/dds-types-test | `2d2cfd15e36b323a12889454ffb09f9dc9d3ebe9` |

`boost` / `filewatch` / `nlohmann-json` / `optionparser` / `taocpp-pegtl` 本来就是 Fast-DDS 树内普通目录，原样保留。

## DimOS 双链代码（不在本目录）

见 [`dimos_bridge/SOURCE.md`](../dimos_bridge/SOURCE.md)。上游 `topsun_dimos` `main` @ `a5259958db23c8ea6648544ed138eab19726ce93`。

## 许可

各 vendor 树保留上游 LICENSE（多为 Apache-2.0 / EPL）。本仓根 LICENSE 为 MIT，不覆盖这些拷贝。

## 本地修改记录（2026-09-16，iter10 评估驱动循环）

上游 SHA 见上表；下列文件是在拷贝之上做的**本地 patch**，与上游有 diff。
每个 patch 一个 commit，conventional commits 风格，详见
[docs/architecture/eval-driven-loop.md](../docs/architecture/eval-driven-loop.md)。

| 文件 | commit | 一句话 |
|------|--------|--------|
| `rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_wait.cpp` | `57896f1d` | `__rmw_wait` 标记阶段先查 StatusCondition trigger_value（无锁 bool），未触发则跳过 `get_first_untaken_info()`；`subscription_has_data` 显式判空短路 |
| `rmw_fastrtps/rmw_fastrtps_shared_cpp/src/rmw_publish.cpp` | `79c20cb1` | 3 处 `Time_t::now()` 收口到 `fill_source_timestamp()`；新增 `RMW_FASTRTPS_SOURCE_TS=zero` 运行期开关（默认 off） |
| `rmw_fastrtps/rmw_fastrtps_shared_cpp/include/rmw_fastrtps_shared_cpp/custom_subscriber_info.hpp` | `6c5b4829` | 删除整段注释掉的 `on_type_discovery()` 死代码（原 192-206 行） |

未改动 `vendor/Fast-DDS/` 源码（仅只读分析 `DataWriterImpl.cpp` 的
payload pool / `fixed_payload_size_` 机制）。本机 macOS 无 ROS 2 运行时，
上述 patch **未经真编译验证**，仅过 12 个 `check_*.py` 静态闸门 +
`api.signature_diff` 签名对比；端到端收益 STATUS: blocked-on-runtime。

