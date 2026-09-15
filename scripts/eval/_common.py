#!/usr/bin/env python3
"""评估套件共享工具：结果数据结构与仓库根解析。

本模块只依赖 Python 标准库（本机 macOS 未装 pyyaml，且 CI 也要求 stdlib-only）。
所有 test_*.py 都通过这里定义的 ``CheckResult`` 回传单项结果，
由 ``run_evals.py`` 汇总成 JSON 报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import sys
from typing import Any


# 状态常量。语义对齐用户需求：
#   pass    — 断言通过
#   fail    — 断言失败（生产代码/配置可能有问题）
#   blocked — 环境不具备（如未装 ROS 2），不是被测对象的错
STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_BLOCKED = "blocked"
_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED)


@dataclass
class CheckResult:
    """单个评估项的结果。

    属性:
        name: 机器可读名，例如 ``config.xml_valid``。
        status: ``pass`` / ``fail`` / ``blocked``。
        score: 0.0~1.0。blocked 一律记 0.5（未验证，不算通过也不算失败）。
        summary: 一句话人话结论。
        details: 结构化细节（文件路径、期望值、实测值、退出码等）。
    """

    name: str
    status: str
    score: float
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(f"unknown status: {self.status}")
        if not 0.0 <= self.score <= 1.0:
            # score 越界不是致命错误，夹一下即可，避免误写炸掉整套件。
            self.score = max(0.0, min(1.0, self.score))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def repo_root() -> Path:
    """定位仓库根目录。

    优先级：cwd 下有 ``config/fastdds.xml`` 即认为在仓库根；
    否则按 ``__file__`` 往上找两级（``scripts/eval/_common.py`` → 仓库根）。
    找不到直接退出，避免后续脚本在错误位置跑。
    """

    cwd = Path.cwd()
    if (cwd / "config" / "fastdds.xml").is_file():
        return cwd.resolve()
    here = Path(__file__).resolve()
    candidate = here.parents[2]  # scripts/eval/_common.py -> repo root
    if (candidate / "config" / "fastdds.xml").is_file():
        return candidate
    sys.exit(
        "eval: cannot locate repo root (looked in cwd and scripts/eval/). "
        "Run from the ros2 workspace."
    )


def bool_result(name: str, ok: bool, summary_ok: str, summary_fail: str,
                details: dict[str, Any] | None = None) -> CheckResult:
    """从一个布尔断言快速构造结果。"""

    if ok:
        return CheckResult(name, STATUS_PASS, 1.0, summary_ok, details or {})
    return CheckResult(name, STATUS_FAIL, 0.0, summary_fail, details or {})


def blocked(name: str, reason: str,
            details: dict[str, Any] | None = None) -> CheckResult:
    """构造一个 blocked 结果（环境缺失，不是被测对象的错）。"""

    return CheckResult(name, STATUS_BLOCKED, 0.5, f"blocked: {reason}", details or {})
