# scripts/eval/ — DDS 自动化评估套件

本目录是为 C++ DDS/RMW 中间件写的**等价**评估基础设施。
本仓不是 LLM 应用，没有 prompt / provider，因此不接 promptfoo；
这里提供的是「bench + 静态断言 + 闸门」组合，覆盖 DDS 通信路径上
用户真正会触达的质量维度。取舍说明见
[`docs/architecture/eval-suite.md`](../../docs/architecture/eval-suite.md)。

## 快速开始

```bash
# 仓库根目录下
python3 scripts/eval/run_evals.py --suite all --chain both

# 只跑配置 + 双链隔离
python3 scripts/eval/run_evals.py --suite config,isolation --chain a

# 把 JSON 报告落盘
python3 scripts/eval/run_evals.py --suite all -o docs/artifacts/eval/baseline.json
```

退出码：

| 退出码 | 含义 |
|--------|------|
| 0 | 所有评估项 pass |
| 1 | 至少一项 fail（生产代码 / 配置 / 文档契约有问题） |
| 2 | 没有 fail，但有 blocked（环境缺东西，不是被测对象的错） |

## 套件清单

| 套件 | 文件 | 对应 LLM 评估维度 | 做什么 |
|------|------|-------------------|--------|
| `gate` | `test_gates.py` | 工具调用正确性 | 跑全部 `scripts/check_*.py` 闸门，汇总通过率。 |
| `config` | `test_config.py` | 检索依据充分性 | 校验 `config/fastdds.xml` / `topics.yaml` / `chain_*.sh` 的合法性与取值范围。 |
| `isolation` | `test_chain_isolation.py` | 业务规则 | 静态断言链 A / 链 B 域 ID、RMW、XML、topic 不互串。 |
| `api` | `test_api_surface.py` | 工具调用正确性 | 抓 `vendor/rmw/rmw/include/rmw/rmw.h` 关键函数原型，与 `git HEAD` 对比。 |
| `bench` | `test_bench_wrapper.py` | 回答质量 | 检测 ROS 2 是否可用；解析最新 iter 的 `raw.json` 里的 p50/p95/p99。 |

## 关于 blocked

本机是 macOS，未装 ROS 2 Humble，所以以下项会稳定地 `blocked`：

- `bench.ros_available` —— 找不到 `ros2` CLI / `rclpy`；
- `bench.latest_raw` 在没有新 iter 时会去读历史 `docs/artifacts/bench/2026-09-11-iter9/...`，这一步是静态解析，不依赖 ROS。

解决 blocked 的办法：

```bash
# 方案 A：用仓库自带的 docker/ros/
bash scripts/bench/docker_chain_a.sh

# 方案 B：在 Ubuntu 22.04 + Humble 上手动 source
source /opt/ros/humble/setup.bash
source config/env/chain_a.sh
BENCH_SKIP_PYTEST=1 bash scripts/bench/run_chain_a.sh
```

## 新增一个评估项

1. 在对应 `test_*.py` 里写一个 `def check_xxx(root: Path) -> CheckResult`；
2. 在同文件的 `run(chain=...)` 里把它的返回值 append 到结果列表；
3. 名字用 `<suite>.<short_name>` 形式，便于在 JSON 报告里分组；
4. 跑一次 `python3 scripts/eval/run_evals.py --suite <suite>` 确认能跑通；
5. 如果新项依赖外部环境（ROS / 网络 / GPU），环境缺失时返回 `blocked()`，
   不要返回 `fail`——`fail` 只用于"被测对象本身错了"。

## 不要做的事

- 不要 `import yaml` —— 本机和 CI 容器默认不带 pyyaml。
- 不要修改 `vendor/` —— 那是《3》冻结范围。
- 不要修改 `config/fastdds.xml` / `docs/artifacts/bench/SCOREBOARD.md` ——
  它们在 CI `boundary` job 里被冻结。
- 不要在评估脚本里起真正的 ROS 节点做长时压测——那是
  `scripts/bench/` 的事，本套件只做静态 + 解析。
