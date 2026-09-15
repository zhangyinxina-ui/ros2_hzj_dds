#!/usr/bin/env python3
"""DDS 自动化评估套件主入口。

本仓库是 C++ DDS/RMW 中间件（非 LLM 应用），没有 prompt / provider 可评，
因此不接入 promptfoo；本脚本提供**等价**的评估基础设施：

    python3 scripts/eval/run_evals.py --suite all --chain both
    python3 scripts/eval/run_evals.py --suite config --chain a --out report.json

套件拆分（对应 LLM 评估维度的 DDS 等价物，详见
``docs/architecture/eval-suite.md``）：

    gate      scripts/check_*.py 闸门集合（文档/契约 marker 是否齐全）
    config    config/ 下 XML / topics.yaml / env 脚本的合法性检查
    isolation 双链隔离（域 ID、RMW、env、topic 不串）
    api       vendor/rmw 公共函数签名与基线对比
    bench     pingpong RTT 封装（ROS 不可用时记 blocked）

退出码：
    0  全部评估项 pass
    1  至少一项 fail
    2  没有 fail，但至少一项 blocked
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

from _common import (  # type: ignore  # noqa: E402
    STATUS_BLOCKED,
    STATUS_FAIL,
    STATUS_PASS,
    CheckResult,
    repo_root,
)


# 套件名 -> (模块文件名, 中文说明)。新增套件时在这里加一行即可。
SUITES: dict[str, tuple[str, str]] = {
    "gate": ("test_gates.py", "scripts/check_*.py 文档/契约闸门"),
    "config": ("test_config.py", "config/ 下 XML / topics.yaml / env 脚本合法性"),
    "isolation": ("test_chain_isolation.py", "双链隔离静态检查"),
    "api": ("test_api_surface.py", "vendor/rmw 公共函数签名稳定性"),
    "bench": ("test_bench_wrapper.py", "pingpong RTT 封装（无 ROS 时 blocked）"),
}

ALL_SUITES = tuple(SUITES.keys())


def _load_module(module_path: Path):
    """按路径动态加载一个 test_*.py 模块。"""

    # test_*.py 内部用 ``from _common import ...`` 同目录导入，
    # 把 eval_dir 加进 sys.path 才能让动态加载的模块找到它。
    eval_dir = module_path.parent
    if str(eval_dir) not in sys.path:
        sys.path.insert(0, str(eval_dir))
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_suite(suite: str, chain: str, eval_dir: Path) -> list[CheckResult]:
    """运行单个套件，返回结果列表。套件自身抛异常时降级为一条 fail。"""

    filename, _desc = SUITES[suite]
    module_path = eval_dir / filename
    try:
        module = _load_module(module_path)
        runner = getattr(module, "run")
        results = runner(chain=chain)
    except Exception as exc:  # noqa: BLE001 — 评估套件本身不能把整个进程炸掉
        results = [
            CheckResult(
                name=f"{suite}.suite_error",
                status=STATUS_FAIL,
                score=0.0,
                summary=f"suite {suite!r} raised {type(exc).__name__}: {exc}",
                details={"exception": repr(exc)},
            )
        ]
    # 给每个结果的 name 加套件前缀，避免不同套件里同名冲突。
    for item in results:
        if not item.name.startswith(f"{suite}."):
            item.name = f"{suite}.{item.name}"
    return results


def _summarize(results: list[CheckResult]) -> dict[str, int]:
    counts = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_BLOCKED: 0}
    for item in results:
        counts[item.status] += 1
    return counts


def _overall_exit_code(counts: dict[str, int]) -> int:
    if counts[STATUS_FAIL] > 0:
        return 1
    if counts[STATUS_BLOCKED] > 0:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--suite",
        choices=("all", *ALL_SUITES),
        default="all",
        help="要运行的套件；all 表示全部。默认 all。",
    )
    parser.add_argument(
        "--chain",
        choices=("a", "b", "both"),
        default="both",
        help="只针对哪条链跑；both 表示两条链都跑。默认 both。",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="可选：把 JSON 报告写到该路径。",
    )
    args = parser.parse_args(argv)

    root = repo_root()
    eval_dir = Path(__file__).resolve().parent

    suites = list(ALL_SUITES) if args.suite == "all" else [args.suite]

    results: list[CheckResult] = []
    for suite in suites:
        results.extend(run_suite(suite, args.chain, eval_dir))

    counts = _summarize(results)
    exit_code = _overall_exit_code(counts)

    report: dict[str, Any] = {
        "suite": args.suite,
        "chain": args.chain,
        "repo_root": str(root),
        "summary": {
            "total": len(results),
            STATUS_PASS: counts[STATUS_PASS],
            STATUS_FAIL: counts[STATUS_FAIL],
            STATUS_BLOCKED: counts[STATUS_BLOCKED],
            "exit_code": exit_code,
        },
        "results": [item.to_dict() for item in results],
    }

    # 终端打印一份紧凑表，方便人肉看。
    print(f"# eval report  suite={args.suite} chain={args.chain}")
    print(
        f"# total={len(results)}  pass={counts[STATUS_PASS]}  "
        f"fail={counts[STATUS_FAIL]}  blocked={counts[STATUS_BLOCKED]}"
    )
    for item in results:
        badge = {
            STATUS_PASS: "PASS",
            STATUS_FAIL: "FAIL",
            STATUS_BLOCKED: "BLKD",
        }[item.status]
        print(f"[{badge}] {item.name}: {item.summary}")
    print()
    print(f"# exit_code = {exit_code}")

    if args.output is not None:
        out_path = args.output
        if not out_path.is_absolute():
            out_path = root / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"# wrote JSON report -> {out_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
