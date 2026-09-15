#!/usr/bin/env python3
"""API 稳定性套件：vendor/rmw 公共函数签名静态检查。

对应 LLM 评估里的「工具调用正确性」——DDS 的"工具"就是 rmw_* 函数族，
调用方（dimos_bridge / rclcpp）按这些签名链接，签名一变就是 ABI/API break。

策略：
    1. 从 vendor/rmw/rmw/include/rmw/rmw.h 抽取关键函数原型；
    2. 用 ``git show HEAD:<file>`` 取基线版本的同一份文件；
    3. 逐函数对比参数列表，任何变化都报 fail；
    4. git 不可用或文件不在 HEAD 里时，退化为 blocked（说明无法对比）。

本套件**只读** vendor/，绝不修改。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from _common import (  # type: ignore
    CheckResult,
    bool_result,
    blocked,
    repo_root,
)


RMW_H_REL = Path("vendor/rmw/rmw/include/rmw/rmw.h")

# 关键公共函数。只看名字，参数列表通过正则抓。
# 注意：rmw_init / rmw_shutdown 在 rmw/init.h 里，不在 rmw.h，
# 这里只盯 rmw.h 里真正会被 publish/take/wait 路径触达的函数。
WATCHED_FUNCS = (
    "rmw_publish",
    "rmw_take",
    "rmw_wait",
    "rmw_get_implementation_identifier",
    "rmw_create_publisher",
    "rmw_create_subscription",
    "rmw_create_node",
)


def _extract_prototype(text: str, func: str) -> str | None:
    """在头文件文本里抓 ``func(...)`` 原型。

    C 原型跨多行，比如：
        rmw_ret_t
        rmw_publish(
          const rmw_publisher_t * publisher,
          ...
        );
    我们抓从 ``func`` 开始到第一个 ``);`` 为止的一段，归一化空白。
    """

    # 定位函数名第一次出现（排除注释里的同名引用）。
    pattern = re.compile(
        rf"(?:^|\n)([\w\s\*]+?)\b{re.escape(func)}\s*\(",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return None
    start = m.start(1)
    # 从函数名所在位置往后扫到 ");"
    fname_idx = text.find(func, m.start())
    depth = 0
    i = text.find("(", fname_idx)
    if i < 0:
        return None
    depth = 1
    i += 1
    while i < len(text) and depth > 0:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        i += 1
    proto = text[start:i]
    # 归一化空白
    proto = re.sub(r"\s+", " ", proto).strip()
    return proto or None


def _git_show(root: Path, rel: Path) -> str | None:
    """取 HEAD 版本的文件内容；失败返回 None。"""

    try:
        out = subprocess.check_output(
            ["git", "-C", str(root), "show", f"HEAD:{rel.as_posix()}"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return out
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None


def check_functions_present(root: Path) -> list[CheckResult]:
    """当前树里能找到所有 watched 函数原型。"""

    rel = RMW_H_REL
    path = root / rel
    if not path.is_file():
        return [bool_result(
            "api.rmw_h_exists", False, "", f"missing {rel}"
        )]
    text = path.read_text(encoding="utf-8", errors="replace")

    results: list[CheckResult] = []
    missing: list[str] = []
    for func in WATCHED_FUNCS:
        proto = _extract_prototype(text, func)
        if proto is None:
            missing.append(func)
            results.append(
                bool_result(
                    f"api.{func}_present",
                    False,
                    "",
                    f"{rel} 里找不到 {func} 的原型",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=f"api.{func}_present",
                    status="pass",
                    score=1.0,
                    summary=f"{func}: 原型存在",
                    details={"prototype": proto},
                )
            )
    return results


def check_signatures_unchanged(root: Path) -> CheckResult:
    """与 git HEAD 对比所有 watched 函数原型是否一致。"""

    rel = RMW_H_REL
    cur_path = root / rel
    if not cur_path.is_file():
        return bool_result("api.signature_diff", False, "", f"missing {rel}")
    cur_text = cur_path.read_text(encoding="utf-8", errors="replace")
    base_text = _git_show(root, rel)
    if base_text is None:
        return blocked(
            "api.signature_diff",
            f"无法从 git HEAD 取到 {rel}（不是 git 仓库或 HEAD 无此文件）",
            {"file": rel.as_posix()},
        )

    changes: list[dict[str, str]] = []
    for func in WATCHED_FUNCS:
        cur = _extract_prototype(cur_text, func) or ""
        base = _extract_prototype(base_text, func) or ""
        if cur != base:
            changes.append(
                {"func": func, "head": base, "worktree": cur}
            )

    ok = not changes
    return bool_result(
        "api.signature_diff",
        ok,
        f"所有 watched 函数签名与 HEAD 一致（{len(WATCHED_FUNCS)} 个）",
        f"{len(changes)} 个函数签名相对 HEAD 发生变化（ABI break 风险）",
        {"changes": changes},
    )


def run(chain: str = "both") -> list[CheckResult]:
    """运行 API 表面检查。``chain`` 与具体链无关。"""

    root = repo_root()
    results = check_functions_present(root)
    results.append(check_signatures_unchanged(root))
    return results


if __name__ == "__main__":  # pragma: no cover
    for item in run("both"):
        print(item.to_dict())
