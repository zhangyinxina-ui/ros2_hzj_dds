#!/usr/bin/env python3
"""双链隔离静态检查套件。

对应 LLM 评估里的「业务规则」：本仓的业务规则就是两条 DDS 链**必须不互通**——
    链 A：rmw_fastrtps_cpp，ROS_DOMAIN_ID=42，读 config/fastdds.xml；
    链 B：rmw_cyclonedds_cpp，ROS_DOMAIN_ID=0，不读 FastDDS XML。

如果这些静态约束被破坏，A 上的 /cmd_vel 可能被 B 订阅到，或反之——
那是机器人层的事故，不是性能问题。

本套件只做静态分析，不真起两个 participant。真正的"发得出去收不到"
要靠 bench 套件在有 ROS 的机器上跑。
"""

from __future__ import annotations

import re
from pathlib import Path

from _common import CheckResult, bool_result, repo_root  # type: ignore


CHAIN_A_SH = Path("config/env/chain_a.sh")
CHAIN_B_SH = Path("config/env/chain_b.sh")
FASTDDS_XML = Path("config/fastdds.xml")
TOPICS_YAML = Path("config/topics.yaml")
LOAD_PY = Path("config/env/load.py")


def _read(root: Path, rel: Path) -> str:
    return (root / rel).read_text(encoding="utf-8", errors="replace")


def _extract_export(text: str, key: str) -> str | None:
    """从 shell 脚本里抽 ``export KEY=value`` 的 value。"""

    m = re.search(rf"^export\s+{re.escape(key)}=(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


def check_domain_ids_distinct(root: Path) -> CheckResult:
    """链 A 与链 B 的 ROS_DOMAIN_ID 必须不同。"""

    a = _read(root, CHAIN_A_SH)
    b = _read(root, CHAIN_B_SH)
    a_id = _extract_export(a, "ROS_DOMAIN_ID")
    b_id = _extract_export(b, "ROS_DOMAIN_ID")
    ok = a_id is not None and b_id is not None and a_id != b_id
    return bool_result(
        "isolation.domain_ids_distinct",
        ok,
        f"链 A domain={a_id}，链 B domain={b_id}（不同）",
        f"domain ID 未隔离: A={a_id!r} B={b_id!r}",
        {"chain_a": a_id, "chain_b": b_id},
    )


def check_rmw_distinct(root: Path) -> CheckResult:
    """两条链的 RMW 实现必须不同。"""

    a = _read(root, CHAIN_A_SH)
    b = _read(root, CHAIN_B_SH)
    a_rmw = _extract_export(a, "RMW_IMPLEMENTATION")
    b_rmw = _extract_export(b, "RMW_IMPLEMENTATION")
    ok = a_rmw is not None and b_rmw is not None and a_rmw != b_rmw
    return bool_result(
        "isolation.rmw_distinct",
        ok,
        f"链 A RMW={a_rmw}，链 B RMW={b_rmw}",
        f"RMW 未隔离: A={a_rmw!r} B={b_rmw!r}",
        {"chain_a": a_rmw, "chain_b": b_rmw},
    )


def check_fastdds_xml_bound_to_chain_a(root: Path) -> CheckResult:
    """FASTRTPS_DEFAULT_PROFILES_FILE 只能由链 A 导出，且指向 config/fastdds.xml。"""

    a = _read(root, CHAIN_A_SH)
    b = _read(root, CHAIN_B_SH)
    a_val = _extract_export(a, "FASTRTPS_DEFAULT_PROFILES_FILE") or ""
    b_val = _extract_export(b, "FASTRTPS_DEFAULT_PROFILES_FILE")
    # chain_a.sh 里写的是 "${_ROS2_HZJ_ROOT}/config/fastdds.xml"，
    # 用 endswith 会被 shell 变量前缀绊倒，改用子串包含。
    ok = (
        "config/fastdds.xml" in a_val
        and b_val is None
    )
    return bool_result(
        "isolation.fastdds_xml_only_chain_a",
        ok,
        f"链 A FASTRTPS_DEFAULT_PROFILES_FILE=.../config/fastdds.xml；链 B 未导出",
        f"FastDDS XML 绑定错: A={a_val!r} B={b_val!r}",
        {"chain_a_value": a_val, "chain_b_value": b_val},
    )


def check_chain_b_unset_cyclonedds_uri(root: Path) -> CheckResult:
    """链 B 必须 ``unset CYCLONEDDS_URI``，避免继承链 A / 环境里的 XML。"""

    b = _read(root, CHAIN_B_SH)
    ok = re.search(r"^\s*unset\s+CYCLONEDDS_URI\b", b, re.MULTILINE) is not None
    return bool_result(
        "isolation.chain_b_unset_cyclonedds_uri",
        ok,
        "chain_b.sh 显式 unset CYCLONEDDS_URI",
        "chain_b.sh 没有 unset CYCLONEDDS_URI（可能被链 A 的 XML 污染）",
    )


def check_xml_domain_matches_chain_a(root: Path) -> CheckResult:
    """fastdds.xml 里的 <domainId> 必须与 chain_a.sh 的 ROS_DOMAIN_ID 一致。"""

    xml_text = _read(root, FASTDDS_XML)
    a_text = _read(root, CHAIN_A_SH)
    m_xml = re.search(r"<domainId>\s*(\d+)\s*</domainId>", xml_text)
    a_id = _extract_export(a_text, "ROS_DOMAIN_ID")
    xml_id = m_xml.group(1) if m_xml else None
    ok = xml_id is not None and a_id is not None and xml_id == a_id
    return bool_result(
        "isolation.xml_domain_matches_chain_a",
        ok,
        f"fastdds.xml <domainId>={xml_id} == chain_a.sh ROS_DOMAIN_ID={a_id}",
        f"XML 域 ID ({xml_id!r}) 与 chain_a.sh ({a_id!r}) 不一致",
        {"xml_domain_id": xml_id, "chain_a_domain_id": a_id},
    )


def check_topics_not_cross_listed(root: Path) -> CheckResult:
    """topics.yaml 里 nav_path（链 A）与 go2_ros_bridge（链 B）topic 不重名。

    同名 topic 在不同 domain 下本来也不通信，但这里是"契约表"层——
    两个段引用同一字符串，等于工程师会误以为可以跨链 pub/sub。
    """

    text = _read(root, TOPICS_YAML)
    nav_topics = re.findall(r"^\s*topic:\s*([/\w]+)", text, re.MULTILINE)
    # 粗分两段：nav_path 在前，go2_ros_bridge 在后；按行号切。
    nav_start = text.find("nav_path:")
    bridge_start = text.find("go2_ros_bridge:")
    if nav_start < 0 or bridge_start < 0:
        return bool_result(
            "isolation.topics_sections",
            False,
            "",
            "topics.yaml 缺 nav_path: 或 go2_ros_bridge: 段",
        )
    nav_block = text[nav_start:bridge_start]
    bridge_block = text[bridge_start:]
    nav_topics = re.findall(r"^\s*topic:\s*([/\w]+)", nav_block, re.MULTILINE)
    bridge_topics = re.findall(r"^\s*topic:\s*([/\w]+)", bridge_block, re.MULTILINE)
    overlap = sorted(set(nav_topics) & set(bridge_topics))
    ok = not overlap
    return bool_result(
        "isolation.topics_no_cross_list",
        ok,
        f"nav_path topics={nav_topics} 与 go2_ros_bridge topics={bridge_topics} 无交集",
        f"以下 topic 同时出现在两段，疑似跨链引用: {overlap}",
        {"overlap": overlap, "nav": nav_topics, "bridge": bridge_topics},
    )


def check_load_py_does_not_pollute_env(root: Path) -> CheckResult:
    """config/env/load.py import 时不得写 os.environ（CI 已有 contract，这里再守一次）。"""

    text = _read(root, LOAD_PY)
    # apply() 函数里写 os.environ.update 是允许的，但模块顶层不许写。
    # 粗检：模块顶层（不在函数内）出现 os.environ.update / os.environ[ 即报警。
    lines = text.splitlines()
    in_function = False
    bad: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^def\s+\w+", line) or re.match(r"^class\s+\w+", line):
            in_function = True
            continue
        if line and not line.startswith((" ", "\t", "#")) and not stripped.startswith(
            ("from ", "import ")
        ):
            in_function = False
        if not in_function and re.search(r"os\.environ\s*(\.update|\[)", line):
            bad.append(line)
    ok = not bad
    return bool_result(
        "isolation.load_py_no_top_level_env_write",
        ok,
        "load.py 顶层不写 os.environ",
        f"load.py 顶层直接写 os.environ: {bad}",
    )


def run(chain: str = "both") -> list[CheckResult]:
    """运行全部隔离检查。``chain`` 参数目前不影响检查项。"""

    root = repo_root()
    return [
        check_domain_ids_distinct(root),
        check_rmw_distinct(root),
        check_fastdds_xml_bound_to_chain_a(root),
        check_chain_b_unset_cyclonedds_uri(root),
        check_xml_domain_matches_chain_a(root),
        check_topics_not_cross_listed(root),
        check_load_py_does_not_pollute_env(root),
    ]


if __name__ == "__main__":  # pragma: no cover
    for item in run("both"):
        print(item.to_dict())
