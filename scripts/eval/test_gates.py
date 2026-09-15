#!/usr/bin/env python3
"""闸门套件：封装运行 scripts/ 下所有 check_*.py + prove_rmw.py。

每个闸门脚本自身约定：
    exit 0 = 通过（文档/契约 marker 齐全）
    exit 1 = 失败（缺文件或缺 marker）
    其他    = 脚本自身出错，按 fail 处理

本套件只负责：
    1. 逐个拉起这些脚本；
    2. 解析退出码，汇总通过率；
    3. 抓首行 stdout 作为 summary，避免重复实现闸门逻辑。

不修改任何被调脚本，也不解析它们的 markdown 细节。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _common import CheckResult, bool_result, repo_root  # type: ignore


# 与 AGENTS.md 列出的闸门清单保持一致。新增闸门时在这里加一行。
GATE_SCRIPTS: tuple[tuple[str, str], ...] = (
    ("prove_rmw", "scripts/prove_rmw.py"),
    ("source_map", "scripts/check_source_map.py"),
    ("bench_gates", "scripts/print_bench_gates.py"),
    ("risk_matrix", "scripts/check_risk_matrix.py"),
    ("executor_map", "scripts/check_executor_map.py"),
    ("runtime_provenance", "scripts/check_runtime_provenance.py"),
    ("unitree_cyclone_swap", "scripts/check_unitree_cyclone_swap.py"),
    ("three_chain_repro", "scripts/check_three_chain_repro.py"),
    ("sink_layers", "scripts/check_sink_layers.py"),
    ("dual_chain_baseline", "scripts/check_dual_chain_baseline.py"),
    ("dod_evidence", "scripts/check_dod_evidence.py"),
    ("cega_bridge_hold", "scripts/check_cega_bridge_hold.py"),
)


def _run_gate(root: Path, rel: str) -> tuple[int, str]:
    """运行单个闸门脚本，返回 (returncode, 截断后的 stdout 首行)。"""

    script = root / rel
    if not script.is_file():
        return 127, f"missing script: {rel}"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timeout: {rel}"
    except OSError as exc:
        return 126, f"os error running {rel}: {exc}"

    head_line = ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            head_line = line
            break
    return proc.returncode, head_line


def run(chain: str = "both") -> list[CheckResult]:
    """运行全部闸门。``chain`` 参数本套件不使用（闸门与具体链无关）。"""

    root = repo_root()
    results: list[CheckResult] = []

    passed = 0
    for name, rel in GATE_SCRIPTS:
        rc, head = _run_gate(root, rel)
        ok = rc == 0
        if ok:
            passed += 1
        results.append(
            bool_result(
                name=f"gate.{name}",
                ok=ok,
                summary_ok=f"{rel}: exit 0 — {head}",
                summary_fail=f"{rel}: exit {rc} — {head}",
                details={"exit_code": rc, "stdout_head": head, "script": rel},
            )
        )

    total = len(GATE_SCRIPTS)
    results.append(
        CheckResult(
            name="gate.summary",
            status="pass" if passed == total else "fail",
            score=passed / total if total else 0.0,
            summary=f"闸门通过率 {passed}/{total}",
            details={"passed": passed, "total": total},
        )
    )
    return results


if __name__ == "__main__":  # pragma: no cover - 手动调试用
    for item in run("both"):
        print(item.to_dict())
