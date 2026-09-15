#!/usr/bin/env python3
"""配置验证套件：检查 config/ 下静态配置的合法性与取值合理性。

对应 LLM 评估里的「检索依据充分性」——DDS 的"依据"就是 XML / YAML / env，
这些错了，再漂亮的 RTT 数字也是错的环境。

检查项：
    1. config/fastdds.xml 是良构 XML；
    2. fastdds.xml 里 domainId == 42；
    3. SHM transport 声明齐全（type=SHM / maxMessageSize / segment_size）；
    4. socket buffer 等关键旋钮在合理范围（> 64 KiB）；
    5. config/topics.yaml 是本套件可解析的子集（discovery + nav_path）；
    6. chain_a.sh / chain_b.sh 的关键变量值正确且不互相覆盖。

本套件只读 config/，绝不写回。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from _common import (  # type: ignore
    CheckResult,
    bool_result,
    repo_root,
)


XML_REL = Path("config/fastdds.xml")
TOPICS_YAML_REL = Path("config/topics.yaml")
CHAIN_A_SH_REL = Path("config/env/chain_a.sh")
CHAIN_B_SH_REL = Path("config/env/chain_b.sh")

# 关键旋钮下限。iter2 落地是 2 MiB；64 KiB 是"比 Linux 默认 ~212 KiB 更糟就报警"的地板。
SOCKET_BUFFER_MIN_BYTES = 64 * 1024
SHM_MAX_MESSAGE_MIN = 64 * 1024
SHM_SEGMENT_MIN = 256 * 1024


def _read(root: Path, rel: Path) -> str:
    return (root / rel).read_text(encoding="utf-8", errors="replace")


def _strip_ns(tag: str) -> str:
    """``{http://www.eprosima.com}domainId`` -> ``domainId``。"""

    return tag.split("}", 1)[-1] if "}" in tag else tag


def _xml_child_text(elem: ET.Element, child_name: str) -> str | None:
    for child in elem:
        if _strip_ns(child.tag) == child_name:
            return (child.text or "").strip()
    return None


def check_xml_wellformed(root: Path) -> CheckResult:
    """fastdds.xml 能被 stdlib XML 解析器读成树。"""

    rel = XML_REL
    path = root / rel
    if not path.is_file():
        return bool_result("config.xml_exists", False, "", f"missing {rel}")
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return bool_result(
            "config.xml_wellformed", False, "",
            f"{rel} 不是合法 XML: {exc}",
            {"error": str(exc)},
        )
    return bool_result(
        "config.xml_wellformed", True,
        f"{rel}: well-formed",
        "",
        {"root_tag": _strip_ns(tree.getroot().tag)},
    )


def check_xml_domain_and_transport(root: Path) -> list[CheckResult]:
    """解析 XML 节点，断言 domainId / SHM transport / socket buffer 取值。"""

    results: list[CheckResult] = []
    path = root / XML_REL
    if not path.is_file():
        return [bool_result("config.xml_parsable", False, "", f"missing {XML_REL}")]

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        return [
            bool_result(
                "config.xml_parsable", False, "",
                f"{XML_REL} 解析失败: {exc}",
            )
        ]
    root_elem = tree.getroot()

    # 1) domainId == 42
    domain_id_text = None
    send_sock = None
    listen_sock = None
    shm_nodes: list[dict[str, str]] = []
    for participant in root_elem.iter():
        if _strip_ns(participant.tag) != "participant":
            continue
        domain_id_text = _xml_child_text(participant, "domainId")
        rtps = None
        for child in participant:
            if _strip_ns(child.tag) == "rtps":
                rtps = child
                break
        if rtps is None:
            continue
        send_sock = _xml_child_text(rtps, "sendSocketBufferSize")
        listen_sock = _xml_child_text(rtps, "listenSocketBufferSize")

    # 2) SHM transport descriptor
    for desc in root_elem.iter():
        if _strip_ns(desc.tag) != "transport_descriptor":
            continue
        kind = _xml_child_text(desc, "type") or ""
        if kind.upper() != "SHM":
            continue
        shm_nodes.append(
            {
                "transport_id": _xml_child_text(desc, "transport_id") or "",
                "maxMessageSize": _xml_child_text(desc, "maxMessageSize") or "",
                "segment_size": _xml_child_text(desc, "segment_size") or "",
            }
        )

    results.append(
        bool_result(
            "config.domain_id",
            domain_id_text == "42",
            f"fastdds.xml domainId={domain_id_text}（期望 42）",
            f"fastdds.xml domainId={domain_id_text!r}（期望 '42'）",
            {"value": domain_id_text},
        )
    )

    results.append(
        bool_result(
            "config.shm_transport_present",
            len(shm_nodes) >= 1,
            f"发现 {len(shm_nodes)} 个 SHM transport descriptor",
            "fastdds.xml 未声明 type=SHM 的 transport_descriptor",
            {"shm": shm_nodes},
        )
    )

    # 3) socket buffer 范围
    def _int(text: str | None) -> int | None:
        try:
            return int(text) if text is not None else None
        except ValueError:
            return None

    send_int = _int(send_sock)
    listen_int = _int(listen_sock)
    send_ok = send_int is not None and send_int >= SOCKET_BUFFER_MIN_BYTES
    listen_ok = listen_int is not None and listen_int >= SOCKET_BUFFER_MIN_BYTES
    results.append(
        bool_result(
            "config.socket_buffer_size",
            send_ok and listen_ok,
            f"send={send_sock} listen={listen_sock}（>= {SOCKET_BUFFER_MIN_BYTES} B）",
            f"socket buffer 过小或缺失: send={send_sock} listen={listen_sock}",
            {"send_bytes": send_int, "listen_bytes": listen_int},
        )
    )

    # 4) SHM maxMessageSize / segment_size 范围
    shm_ok = True
    shm_detail: dict[str, object] = {}
    for node in shm_nodes:
        try:
            mms = int(node["maxMessageSize"])
            seg = int(node["segment_size"])
        except (TypeError, ValueError):
            shm_ok = False
            shm_detail[node["transport_id"]] = "non-integer value"
            continue
        ok = mms >= SHM_MAX_MESSAGE_MIN and seg >= SHM_SEGMENT_MIN
        shm_ok = shm_ok and ok
        shm_detail[node["transport_id"]] = {
            "maxMessageSize": mms,
            "segment_size": seg,
            "ok": ok,
        }
    results.append(
        bool_result(
            "config.shm_transport_sane",
            shm_ok and bool(shm_nodes),
            f"SHM transport 参数在合理范围: {shm_detail}",
            f"SHM transport 参数不合理: {shm_detail}",
            {"thresholds": {"maxMessageSize_min": SHM_MAX_MESSAGE_MIN,
                            "segment_size_min": SHM_SEGMENT_MIN}},
        )
    )
    return results


def check_topics_yaml(root: Path) -> CheckResult:
    """topics.yaml 是本套件可解析的"子集"。

    本环境没有 pyyaml，只做轻量结构检查：
      - 文件存在；
      - 出现 ``discovery:`` / ``nav_path:`` 顶层键；
      - ``chain_a_ros_domain_id: 42`` / ``chain_b_cyclone_domain_id: 0``。
    不做完整 YAML 语义解析；真要加载 YAML 请在装了 pyyaml 的 CI 容器里做。
    """

    rel = TOPICS_YAML_REL
    path = root / rel
    if not path.is_file():
        return bool_result("config.topics_yaml_exists", False, "", f"missing {rel}")
    text = _read(root, rel)

    missing = []
    for needle in ("discovery:", "nav_path:",
                   "chain_a_ros_domain_id: 42",
                   "chain_b_cyclone_domain_id: 0"):
        if needle not in text:
            missing.append(needle)

    ok = not missing
    return bool_result(
        "config.topics_yaml_shape",
        ok,
        f"{rel}: discovery/nav_path/域常量齐全",
        f"{rel}: 缺关键字: {missing}",
        {"missing": missing},
    )


def check_env_scripts(root: Path) -> list[CheckResult]:
    """chain_a.sh / chain_b.sh 的 export 值与契约一致，且不会互相污染。"""

    results: list[CheckResult] = []
    a_text = _read(root, CHAIN_A_SH_REL)
    b_text = _read(root, CHAIN_B_SH_REL)

    a_expect = {
        "RMW_IMPLEMENTATION=rmw_fastrtps_cpp": True,
        "ROS_DOMAIN_ID=42": True,
        "FASTRTPS_DEFAULT_PROFILES_FILE=": True,
    }
    b_expect = {
        "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp": True,
        "ROS_DOMAIN_ID=0": True,
        "unset CYCLONEDDS_URI": True,
    }

    for needle, should in a_expect.items():
        present = needle in a_text
        results.append(
            bool_result(
                f"config.chain_a_{needle.replace(' ', '_').replace('=', '_')}",
                present == should,
                f"chain_a.sh 含 `{needle}`",
                f"chain_a.sh 应包含 `{needle}`",
                {"file": CHAIN_A_SH_REL.as_posix(), "needle": needle},
            )
        )

    for needle, should in b_expect.items():
        present = needle in b_text
        results.append(
            bool_result(
                f"config.chain_b_{re.sub(r'[^a-zA-Z0-9]+', '_', needle)}",
                present == should,
                f"chain_b.sh 含 `{needle}`",
                f"chain_b.sh 应包含 `{needle}`",
                {"file": CHAIN_B_SH_REL.as_posix(), "needle": needle},
            )
        )

    # 交叉污染：链 A 脚本不得 unset CYCLONEDDS_URI（那是 B 的事），
    # 链 B 脚本不得 export FASTRTPS_DEFAULT_PROFILES_FILE。
    results.append(
        bool_result(
            "config.no_cross_pollution",
            "CYCLONEDDS_URI" not in a_text
            and "FASTRTPS_DEFAULT_PROFILES_FILE" not in b_text,
            "chain_a.sh 不碰 CYCLONEDDS_URI；chain_b.sh 不碰 FASTRTPS_DEFAULT_PROFILES_FILE",
            "env 脚本存在跨链变量污染",
        )
    )
    return results


def run(chain: str = "both") -> list[CheckResult]:
    """运行全部配置检查。``chain`` 目前只影响报告，不跳过检查项。"""

    root = repo_root()
    results: list[CheckResult] = []
    results.append(check_xml_wellformed(root))
    results.extend(check_xml_domain_and_transport(root))
    results.append(check_topics_yaml(root))
    results.extend(check_env_scripts(root))
    return results


if __name__ == "__main__":  # pragma: no cover
    for item in run("both"):
        print(item.to_dict())
