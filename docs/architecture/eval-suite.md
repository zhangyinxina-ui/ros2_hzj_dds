# 评估套件（eval-suite）设计文档

## 1. 为什么不用 promptfoo

用户原始诉求是"用 `$promptfoo-evals` 给修改后的 DDS 加一套评估"。
但本仓是 **C++ DDS/RMW 中间件**，不是 LLM 应用：

| promptfoo 假设 | 本仓实际 |
|----------------|----------|
| 有 prompt 模板 | 没有 prompt；配置是 `config/fastdds.xml` / `topics.yaml` |
| 有 LLM provider | 没有 provider；运行时是 rmw_fastrtps_cpp / rmw_cyclonedds_cpp |
| 评估"回答质量" | 评估的是 RTT / jitter / 丢包 / 域隔离 |
| 评估"工具调用正确性" | 评估的是 `rmw_publish` / `rmw_take` / `rmw_wait` 签名与返回值 |
| 评估"检索依据充分性" | 评估的是 XML / QoS / 域 ID 配置是否合法 |
| 评估"代理任务完成" | 评估的是 pub/sub 端到端消息投递 |

强行套 promptfoo 等于为一个 C++ 中间件写一层 LLM 调用壳，既测不到 DDS 的真实行为，
也增加了一个本仓不需要的 Python 依赖链。务实做法是提供**等价**的评估基础设施：

- **自动化**：一条命令跑全部；
- **结构化输出**：JSON 报告 + 终端摘要；
- **分级状态**：`pass` / `fail` / `blocked`（blocked = 环境缺失，不是被测对象的错）；
- **可扩展**：新评估项就是一个 `test_*.py` 里的 `check_xxx()` 函数。

如果未来真要在 DDS 上层加一个 LLM agent（例如"自动调优 fastdds.xml 的 agent"），
那时再用 promptfoo 评估那个 agent 的 prompt 即可——评估对象变了，工具才换。

## 2. 评估套件架构

```
scripts/eval/
├── run_evals.py          # 主入口：--suite / --chain / --output
├── _common.py            # CheckResult 数据结构 + repo root 解析
├── test_gates.py         # 封装 scripts/check_*.py 闸门
├── test_config.py         # config/ 下 XML / YAML / env 合法性
├── test_chain_isolation.py  # 双链隔离静态断言
├── test_api_surface.py    # vendor/rmw 公共函数签名 vs git HEAD
├── test_bench_wrapper.py  # pingpong raw.json 解析 + ROS 可用性检测
├── README.md              # 使用说明
└── fixtures/              # 最小公开夹具（无机密）
    ├── minimal_fastdds.xml
    ├── minimal_topics.yaml
    └── README.md
```

数据流：

```
run_evals.py
  ├─ 按 --suite 动态 import test_*.py
  ├─ 每个 test_*.py 暴露 run(chain) -> list[CheckResult]
  ├─ 汇总 45 项结果
  ├─ 终端打印 [PASS]/[FAIL]/[BLKD] 表
  └─ 写 docs/artifacts/eval/<name>.json
       └─ exit code: 0=全 pass, 1=有 fail, 2=无 fail 有 blocked
```

## 3. 评估维度映射

| LLM 评估维度 | DDS 等价物 | 套件 | 具体项 |
|--------------|-----------|------|--------|
| 回答质量 | 通信质量（RTT / jitter / 丢包） | `bench` | `bench.latest_raw`（解析历史 raw.json 的 p50/p95/p99）；`bench.ros_available`（真跑前置） |
| 工具调用正确性 | API 正确性（publish/take/wait） | `gate` + `api` | 12 个 `check_*.py` 闸门；`api.*_present` / `api.signature_diff` |
| 检索依据充分性 | 配置正确性（XML / QoS / 域） | `config` | `config.xml_wellformed` / `domain_id` / `socket_buffer_size` / `shm_transport_*` / `topics_yaml_shape` |
| 业务规则 | 双链隔离（域不互通、RMW 不串） | `isolation` | `isolation.domain_ids_distinct` / `rmw_distinct` / `fastdds_xml_only_chain_a` / `topics_no_cross_list` |
| 代理任务完成 | 端到端消息投递 | `bench` | 历史 `docs/artifacts/bench/2026-09-*/chain_a_*/raw.json` 里的 `recorded_samples / timeouts` |

## 4. 每个评估项说明

### 4.1 gate（13 项）

逐个拉起 `scripts/check_*.py` + `prove_rmw.py`，按退出码判 pass/fail。
这些闸门本身是"文档/契约 marker 是否还在"的静态检查，与 CI `structure` job 完全对齐。
`gate.summary` 是汇总通过率。

### 4.2 config（14 项）

- `config.xml_wellformed`：`config/fastdds.xml` 能被 `xml.etree.ElementTree` 解析。
- `config.domain_id`：XML 里 `<domainId> == 42`。
- `config.shm_transport_present`：至少有一个 `type=SHM` 的 `transport_descriptor`。
- `config.socket_buffer_size`：send/listen socket buffer ≥ 64 KiB（iter2 落地是 2 MiB）。
- `config.shm_transport_sane`：SHM `maxMessageSize` ≥ 64 KiB，`segment_size` ≥ 256 KiB。
- `config.topics_yaml_shape`：`topics.yaml` 含 `discovery:` / `nav_path:` / 域常量 42/0。
- `config.chain_a_*` / `config.chain_b_*`：env 脚本里 export 的关键变量值正确。
- `config.no_cross_pollution`：链 A 脚本不碰 `CYCLONEDDS_URI`；链 B 脚本不碰 `FASTRTPS_DEFAULT_PROFILES_FILE`。

### 4.3 isolation（7 项）

- `isolation.domain_ids_distinct`：链 A 域 42 ≠ 链 B 域 0。
- `isolation.rmw_distinct`：`rmw_fastrtps_cpp` ≠ `rmw_cyclonedds_cpp`。
- `isolation.fastdds_xml_only_chain_a`：`FASTRTPS_DEFAULT_PROFILES_FILE` 只在链 A 导出。
- `isolation.chain_b_unset_cyclonedds_uri`：链 B 显式 `unset CYCLONEDDS_URI`。
- `isolation.xml_domain_matches_chain_a`：XML 里的 `<domainId>` 与 `chain_a.sh` 一致。
- `isolation.topics_no_cross_list`：`topics.yaml` 里 `nav_path` 与 `go2_ros_bridge` 两段 topic 字符串无交集。
- `isolation.load_py_no_top_level_env_write`：`config/env/load.py` 顶层不写 `os.environ`。

### 4.4 api（8 项）

- `api.<func>_present`：`vendor/rmw/rmw/include/rmw/rmw.h` 里能抓到 7 个关键函数原型
  （`rmw_publish` / `rmw_take` / `rmw_wait` / `rmw_get_implementation_identifier` /
  `rmw_create_publisher` / `rmw_create_subscription` / `rmw_create_node`）。
- `api.signature_diff`：与 `git HEAD:vendor/rmw/rmw/include/rmw/rmw.h` 对比，
  任何一个函数原型归一化后不一致即 fail（ABI break 风险）。

### 4.5 bench（4 项）

- `bench.ros_available`：真的 `import rclpy` 试一下；macOS 无 ROS 时 blocked。
- `bench.latest_raw`：找 `docs/artifacts/bench/` 下最新一个含
  `chain_a_same_host/raw.json` 的 iter 目录，解析 p50/p95/p99 / timeouts。
- `bench.scoreboard_pointer`：`SCOREBOARD.md` 与 `README.md` 还在。
- `bench.chain_b_history`：链 B 历史 same-process/same-host 记录还在 `2026-09-10/`。

## 5. 基线评估结果

命令：

```bash
cd /Users/yixin0909zhang/Documents/ros2
python3 scripts/eval/run_evals.py --suite all --chain both \
    -o docs/artifacts/eval/baseline.json
```

结果：**total=45, pass=44, fail=0, blocked=1, exit_code=2**。

### 5.1 blocked（1 项）

| 项 | 原因 | 解决办法 |
|----|------|----------|
| `bench.ros_available` | 本机 macOS 未装 ROS 2 Humble；`rclpy` import 失败；`ros2` CLI 不在 PATH | `bash scripts/bench/docker_chain_a.sh`，或在 Ubuntu 22.04 + Humble 机器上 `source /opt/ros/humble/setup.bash` |

### 5.2 pass（44 项，按套件分组）

- gate：13/13（prove_rmw + 12 个 check_*.py + summary）
- config：14/14
- isolation：7/7
- api：8/8（7 个函数存在 + 签名与 HEAD 一致）
- bench：3/4（除 `ros_available` 外都 pass）

### 5.3 fail（0 项）

基线干净。后续修改生产代码后再跑一次，任何新增 fail 都应在合入前修掉。

## 6. 本次新增 / 变更文件

新增（未修改任何 vendor/ 或 config/ 生产文件）：

```
scripts/eval/
├── run_evals.py
├── _common.py
├── test_gates.py
├── test_config.py
├── test_chain_isolation.py
├── test_api_surface.py
├── test_bench_wrapper.py
├── README.md
└── fixtures/
    ├── minimal_fastdds.xml
    ├── minimal_topics.yaml
    └── README.md

docs/artifacts/eval/
└── baseline.json

docs/architecture/
└── eval-suite.md   （本文件）
```

未触碰：

- `vendor/` 任何文件（包括任务开始前已有的
  `vendor/rmw_fastrtps/.../rmw_wait.cpp` 修改，那不是本次引入）；
- `config/fastdds.xml` / `config/topics.yaml` / `config/env/*.sh`；
- `dimos_bridge/` 运行时代码。

## 7. Eval 命令运行结果（节选）

```
# eval report  suite=all chain=both
# total=45  pass=44  fail=0  blocked=1
[PASS] gate.prove_rmw: scripts/prove_rmw.py: exit 0
[PASS] gate.summary: 闸门通过率 12/12
[PASS] config.domain_id: fastdds.xml domainId=42（期望 42）
[PASS] config.socket_buffer_size: send=2097152 listen=2097152（>= 65536 B）
[PASS] isolation.domain_ids_distinct: 链 A domain=42，链 B domain=0（不同）
[PASS] isolation.rmw_distinct: 链 A RMW=rmw_fastrtps_cpp，链 B RMW=rmw_cyclonedds_cpp
[PASS] api.signature_diff: 所有 watched 函数签名与 HEAD 一致（7 个）
[BLKD] bench.ros_available: blocked: 未找到 ros2 CLI / rclpy
[PASS] bench.latest_raw: 解析 2026-09-11-iter9/chain_a_same_host/raw.json：2 个 case
# exit_code = 2
```

完整 JSON：[`docs/artifacts/eval/baseline.json`](../artifacts/eval/baseline.json)。

## 8. 建议接下来添加的 evals

按优先级从高到低：

1. **真实激光雷达数据回放测试** —— 录一段 `/lidar` (PointCloud2) 包，
   回放 10 分钟，统计 RTT p99 漂移与丢包率。需要 ROS 2 环境，目前 blocked。
2. **跨机网络模拟** —— 用 `tc netem` 在同机模拟 5 ms 延迟 + 0.1% 丢包，
   验证 Fast-DDS UDP 在跨机场景下的行为，补齐
   `docs/artifacts/bench/2026-09-11-cross-host/` 的 blocked 项。
3. **长时间稳定性测试（100 h soak）** —— 起一对 pub/sub 跑 100 小时，
   每小时采一次 p50/p95/p99，看是否有内存泄漏 / SHM segment 累积。
4. **QoS 协商不匹配检测** —— 静态对比 `topics.yaml` 里声明的 QoS 与
   `dimos_bridge/dimos/protocol/dds_topics.py` 实际代码里的 QoS，
   防止"契约表写 RELIABLE，代码里用 BestEffort"。
5. **链 B 历史 bench 自动对比** —— 现在只解析链 A 最新 iter；
   加一项把链 B 2026-09-10 的 raw.json 也解析出来，
   在 JSON 报告里并列展示（不混表，遵守 README 的"不混表"规则）。
6. **CI 集成** —— 在 `.github/workflows/ci.yml` 里加一个 `eval` job：
   `python3 scripts/eval/run_evals.py --suite all --chain both`，
   把 `exit_code != 0` 作为 CI 失败信号（blocked 在 CI 容器里应该是 pass，
   因为 CI 跑在 ubuntu 上可以装 ROS）。
