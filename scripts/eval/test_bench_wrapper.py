#!/usr/bin/env python3
"""bench 封装套件：pingpong RTT 的静态解析 + 环境检测。

对应 LLM 评估里的「回答质量」——DDS 的"质量"就是 RTT p50/p95/p99 + jitter。

本套件**不在基线阶段真跑** pingpong.py（那需要 Humble + rclpy，本机 macOS 没有）。
它做三件事：
    1. 检测本机 ROS 2 / rclpy 是否可用；不可用则 blocked；
    2. 解析 docs/artifacts/bench/ 下最新一次 iter 的 raw.json，
       提取 p50/p95/p99 与 jitter，作为"历史记录"展示；
    3. 校验 SCOREBOARD.md 与 README.md 的指针仍然存在。

真要跑一次新的 pingpong，请在有 ROS 的机器上：
    source /opt/ros/humble/setup.bash
    source config/env/chain_a.sh
    BENCH_SKIP_PYTEST=1 bash scripts/bench/run_chain_a.sh
然后再跑本套件，它会自动 pick up 新的 raw.json。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

from _common import (  # type: ignore
    CheckResult,
    bool_result,
    blocked,
    repo_root,
)


BENCH_ROOT = Path("docs/artifacts/bench")
SCOREBOARD = BENCH_ROOT / "SCOREBOARD.md"
README = BENCH_ROOT / "README.md"


def _rclpy_available() -> bool:
    """真正尝试 import rclpy，而不是只看 find_spec。

    沙箱/部分环境下 find_spec 会返回非 None 但 import 时才炸，
    与 scripts/prove_rmw.py 保持一致：真的 import 一下才作数。
    """

    try:
        import rclpy  # type: ignore  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — ImportError / OSError / 缺 .so 都算不可用
        return False


def check_ros_available() -> CheckResult:
    """ROS 2 / rclpy 是否在本机可用。"""

    ros2_cli = shutil.which("ros2")
    rclpy = _rclpy_available()
    detail = {"ros2_cli": ros2_cli, "rclpy_importable": rclpy}
    if ros2_cli is None and not rclpy:
        return blocked(
            "bench.ros_available",
            "未找到 ros2 CLI / rclpy（本机 macOS，按 AGENTS.md 应在 docker/ros/ 内跑）",
            detail,
        )
    return CheckResult(
        name="bench.ros_available",
        status="pass",
        score=1.0,
        summary=f"ros2 CLI={ros2_cli}，rclpy={rclpy}",
        details=detail,
    )


def _find_latest_iter_dir(root: Path) -> Path | None:
    """按目录名日期排序，取最新一个包含 raw.json 的 iter 目录。"""

    bench = root / BENCH_ROOT
    if not bench.is_dir():
        return None
    candidates = [
        p for p in bench.iterdir()
        if p.is_dir() and (p / "chain_a_same_host" / "raw.json").is_file()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name)
    return candidates[-1]


def check_latest_raw(root: Path) -> CheckResult:
    """解析最新 iter 的 chain_a_same_host/raw.json，列出 p50/p95/p99。"""

    latest = _find_latest_iter_dir(root)
    if latest is None:
        return blocked(
            "bench.latest_raw",
            "docs/artifacts/bench/ 下没有任何 chain_a_same_host/raw.json",
        )
    raw_path = latest / "chain_a_same_host" / "raw.json"
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return bool_result(
            "bench.latest_raw",
            False,
            "",
            f"{raw_path} 读/解析失败: {exc}",
            {"file": raw_path.as_posix()},
        )

    cases = data.get("cases") or []
    summary = []
    for case in cases:
        summary.append(
            {
                "name": case.get("name"),
                "p50_us": case.get("p50_us"),
                "p95_us": case.get("p95_us"),
                "p99_us": case.get("p99_us"),
                "timeouts": case.get("timeouts"),
            }
        )
    return CheckResult(
        name="bench.latest_raw",
        status="pass",
        score=1.0,
        summary=f"解析 {latest.name}/chain_a_same_host/raw.json：{len(cases)} 个 case",
        details={
            "iter_dir": latest.name,
            "raw_path": raw_path.as_posix(),
            "status": data.get("status"),
            "cases": summary,
        },
    )


def check_scoreboard_pointer(root: Path) -> CheckResult:
    """SCOREBOARD.md / README.md 指针文件还在（与 print_bench_gates.py 一致）。"""

    missing = [
        rel.as_posix()
        for rel in (SCOREBOARD, README)
        if not (root / rel).is_file()
    ]
    return bool_result(
        "bench.scoreboard_pointer",
        not missing,
        f"{BENCH_ROOT}/ 下 SCOREBOARD.md 与 README.md 都在",
        f"缺指针文件: {missing}",
        {"missing": missing},
    )


def run(chain: str = "both") -> list[CheckResult]:
    """运行 bench 封装检查。``chain`` 选 a 只看链 A 产物，b 暂只看 blocked 提示。"""

    root = repo_root()
    results = [
        check_ros_available(),
        check_latest_raw(root),
        check_scoreboard_pointer(root),
    ]
    if chain in ("a", "both"):
        # 链 A 的 raw.json 已在 check_latest_raw 里。
        pass
    if chain in ("b", "both"):
        # 链 B 的历史产物在 2026-09-10/chain_b_*；本套件暂不重复解析，
        # 只提示一下：真要跑链 B 请用 scripts/bench/run_chain_b.sh。
        chain_b_raw = root / BENCH_ROOT / "2026-09-10"
        results.append(
            bool_result(
                "bench.chain_b_history",
                chain_b_raw.is_dir(),
                "2026-09-10/ 下有链 B 历史 same-process/same-host 记录",
                "2026-09-10/ 下没找到链 B 历史目录",
                {"expected_dir": chain_b_raw.as_posix()},
            )
        )
    return results


if __name__ == "__main__":  # pragma: no cover
    for item in run("both"):
        print(item.to_dict())
