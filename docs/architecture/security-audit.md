# ROS 2 / DDS 公共包只读安全审计

- **Status**: DRAFT（只读审计，未执行任何修复；仅列建议）
- **审计日期**: 2026-09-16（Asia/Tokyo）
- **审计范围**: `vendor/` 内 Fast-DDS / Fast-CDR / CycloneDDS / rmw / rmw_fastrtps / rmw_cyclonedds / rmw_implementation 对照公开安全公告；`.github/workflows/ci.yml`、`docker/`、`scripts/`、`config/` 的供应链与脚本面。
- **约束**: 全程只读。未安装包、未运行构建/安装/postinstall、未执行任何不受信代码、未轮换凭证、未清理或修改任何文件（本文件除外）。
- **审计方法**: 静态文件阅读 + grep + `find` 枚举 + 对照 GitHub Security Advisory / NVD / 上游发行说明。

---

## 1. 总结

### 1.1 本仓 vendor 包及版本

版本来源以 `vendor/VERSIONS.md`（唯一钉扎表）为准，并与各 `package.xml` / `CMakeLists.txt` 交叉核对一致：

| 链 | 目录 | 包名 | 版本 | 钉扎来源 | 证据 |
|----|------|------|------|----------|------|
| A | `vendor/Fast-DDS/` | `fastdds` | **3.6.2**（master 快照，近 v3.6.2，SHA `343f155c…`） | VERSIONS.md:26 | `vendor/Fast-DDS/package.xml`（`<version>3.6.2</version>`）；`vendor/Fast-DDS/CMakeLists.txt:32` `project(fastdds VERSION "3.6.2.0" …)` |
| A | `vendor/Fast-DDS/thirdparty/fastcdr/` | `fastcdr` | **2.3.5** | VERSIONS.md:55 | `vendor/Fast-DDS/thirdparty/fastcdr/CMakeLists.txt:38` `project(fastcdr VERSION 2.3.5 …)` |
| A | `vendor/rmw/rmw/` | `rmw` | **7.11.2**（rolling） | VERSIONS.md:23 | `vendor/rmw/rmw/package.xml` |
| A | `vendor/rmw_implementation/rmw_implementation/` | `rmw_implementation` | **3.2.1**（rolling HEAD，近旁 tag） | VERSIONS.md:24 | `vendor/rmw_implementation/rmw_implementation/package.xml` |
| A | `vendor/rmw_fastrtps/rmw_fastrtps_{cpp,dynamic_cpp,shared_cpp}/` | rmw_fastrtps 三模块 | **9.5.2**（rolling，近旁 tag） | VERSIONS.md:25 | 三个 `package.xml` 均 `<version>9.5.2</version>` |
| B | `vendor/CycloneDDS/` | `cyclonedds` | **11.0.1**（发行标签） | VERSIONS.md:37 | `vendor/CycloneDDS/package.xml` `<version>11.0.1</version>` |
| B | `vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/` | `rmw_cyclonedds_cpp` | **4.2.1**（rolling） | VERSIONS.md:36 | `vendor/rmw_cyclonedds/rmw_cyclonedds_cpp/package.xml` |

**关键语义（决定暴露面）**：`vendor/` 是**整树文件拷贝的对照阅读快照，不是运行时**。

- 目标运行时是链 A Docker 里的 **Ubuntu 22.04 + ROS 2 Humble**（`docker/ros/Dockerfile:11,16` `FROM ubuntu:22.04` / `ENV ROS_DISTRO=humble`），即 apt 安装的发行版 Fast-DDS **2.6.x LTS**，而非 vendor 的 3.6.2 源码。见 `vendor/MANIFEST.md:17,20,28-30`。
- **CI 不编译任何 vendor 树**：`vendor/MANIFEST.md:22` 明确“CI 不编译这些树”；`.github/workflows/ci.yml:88`（注释“Do not build Fast-DDS or CycloneDDS in this job.”）、`:143`、`:345` 反复重申“Do not compile vendor.”。
- 因此 vendor 3.6.2 / 11.0.1 源码本身**不进入产物**；真正可能受 CVE 影响的是 Humble apt 的 2.6.x 运行时。

### 1.2 证据状态总览

| 包 | 证据状态 | 一句话结论 |
|----|----------|------------|
| CycloneDDS 11.0.1（vendor） | **已排除**（对已知公告） | 已知 CVE 全部修复在 ≤0.10.5 / ≤0.8.0，11.0.1 远超修复线 |
| Fast-CDR 2.3.5（vendor，Fast-DDS 依赖） | **已排除 / 未见独立公告** | 未检索到针对 Fast-CDR 2.3.5 的独立 GHSA/CVE |
| rmw / rmw_fastrtps 9.5.2 / rmw_cyclonedds 4.2.1（vendor） | **已排除**（对本仓范围） | 仅为 API/适配层，无独立可利用 CVE；底层风险随 Fast-DDS/CycloneDDS 版本 |
| Fast-DDS 3.6.2（vendor master 快照） | **需验证**（仅对 2026-09 最新一批 CVE） | 早于 3.4.1/3.6.0 的 CVE 均已修复；最新一批 CVE 是否含在该 master SHA 内需对照上游确认 |
| Fast-DDS 2.6.x（**运行时** Humble apt） | **需验证**（取决于镜像构建时点的 apt 解析版本） | Dockerfile 未钉小版本；若 ≥2.6.12 则最新批次已修复 |

### 1.3 权威来源 vs 第三方来源

- **官方权威**：eProsima 发行说明与受支持版本页（Fast-DDS 版本时间线的权威）；Eclipse CycloneDDS / eProsima 的 GitHub Security Advisory；NVD/CVE 官方编号与 affected 区间。
- **第三方/一方称**：Snyk、Breach & Build、Debian/Ubuntu tracker、Rapid7 等聚合页用于交叉印证，其“Fixed/affected”字段在本报告中均与 NVD 或上游 release tag 复核后采用。
- 本报告对每条 CVE 同时给出「NVD affected 区间」与「上游修复版本」两个事实，避免仅凭第三方博客下结论。

---

## 2. 检查清单（逐项执行并记录）

### A. 包清单与锁定文件

- 已读取全部 10 个 `package.xml`（见 §1.1 表）。Fast-DDS 版本以 `CMakeLists.txt:32` 的 `project(... VERSION "3.6.2.0")` 为准，与 `package.xml` 一致。
- **锁定文件**：仓库根无 `package-lock.json` / `Cargo.lock` / `poetry.lock` / `Pipfile.lock`；唯一依赖清单是 `scripts/bench/requirements-chain-b.txt`（见 §C）。
- **预编译产物**：`find vendor \( -name "*.so" -o -name "*.a" -o -name "*.dylib" \)` 结果为 **0**。vendor 树为纯源码拷贝，无二进制 lib 混入。
- **`docs/artifacts/`**：仅 `.md/.json/.txt/.xml` 文本日志（bench 环境与结果），无可执行文件；`docs/artifacts/bench/README.md` 经 `file` 确认为 UTF-8 文本。
- **Docker 基础镜像**：`docker/ros/Dockerfile:11` `ARG FROM_IMAGE=ubuntu:22.04`（未 digest 钉扎，仅 tag）。

### B. CI 工作流与权限（`.github/workflows/ci.yml`）

| 检查项 | 结果 | 证据 |
|--------|------|------|
| 第三方 action | 仅 `actions/checkout@v4`（`:22, :220, :351`），**按 tag 而非 SHA 钉扎** | 见 §4 建议 |
| `permissions` | 窄：`contents: read`、`pull-requests: read`（`:9-11`），**无 `write-all`** | 良好 |
| `pull_request_target` + checkout + 脚本执行 | **未使用**；触发仅 `push` + `pull_request`（`:3-7`） | 良好，无经典高危组合 |
| secrets 泄露 | 全文件无 `secrets.*`、无 `echo $SECRET`、无 `upload-artifact`、无 `GITHUB_TOKEN` 特殊用法 | 良好 |
| 缓存敏感数据 | **无 `actions/cache`**、无 `~/.npm`/`~/.cache`/pip cache 上传 | 良好 |
| 跨 PR diff | `boundary` job 用 `git fetch origin <base.sha>`（`:357-360`）做只读路径 diff，不执行 PR 代码 | 良好 |
| 构建 vendor | 明确“不编译”（`:88, :143, :345`） | 良好，缩小暴露面 |

结论：CI 配置整体健康；唯一改进点是 `actions/checkout@v4` 应改 commit SHA 钉扎。

### C. install / 构建 / postinstall 脚本

| 检查项 | 结果 | 证据 |
|--------|------|------|
| `curl | bash` / `wget | sh` | **未发现**（`grep` 全仓 scripts/docker/config/dimos_bridge 无命中） | — |
| 从非 HTTPS 源下载 | 仅 `docker/ros/install-base.sh:41` 的 apt 源写成 `http://packages.ros.org/ros2/ubuntu …`（明文 HTTP）；签名 key 走 HTTPS（`:40` `curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key`） | 见 §4 建议 |
| 硬编码 token/密码/密钥 | `grep -rniE "password|secret|api_key|token|private_key"` 在非 vendor 代码中的命中**全部是** `scripts/check_*.py` 里“文本匹配 token / 分位数 token”的注释与变量，**无真实凭证**；`.env` 已被 `.gitignore` 忽略（`.gitignore` `.env`/`.env.*`，仅放行 `.env.example`） | 良好 |
| postinstall 钩子执行任意代码 | 无 npm/pip postinstall；Docker 内 `rosdep init/update`（`install-runtime.sh:39-40`）为官方工具 | — |
| CMake `ExternalProject` / 不安全 URL | Fast-DDS `CMakeLists.txt` 无 `ExternalProject`/`GIT_REPOSITORY`；仅有 `file(DOWNLOAD …)` 抓取文档 zip（`:552, :562`，均为 HTTPS `fast-dds.docs.eprosima.com`），且仅在构建 docs 时触发，CI 不编译 vendor | 低风险 |
| Python 依赖钉扎 | `scripts/bench/requirements-chain-b.txt`：`cyclonedds>=0.10.5`、`rerun-sdk>=0.20.0` 为范围版本，仅 `sortedcontainers==2.4.0` 精确钉；**未使用 `--require-hashes`** | 见 §4 建议 |

### D. 供应商制品 / 容器 / 捆绑包

- `docker/` 仅 `ros/` 一个子目录，含 `Dockerfile`、`install-base.sh`、`install-runtime.sh`、`NOTES.md`，无额外预编译层。
- vendor 内预编译库 **0 个**（见 §A）。
- `docs/artifacts/` 无可执行文件。
- 结论：无第三方二进制捆绑包混入仓库。

### E. CI 缓存 / 令牌暴露路径

- 无 `actions/cache`；无凭证上传；`GITHUB_TOKEN` 未在任何 step 显式声明（默认继承的 read 权限即 `permissions` 块所限）。
- 该专项**未发现** CI/发布侧令牌暴露面。

---

## 3. 已知 CVE / 安全公告核查

> 版本对比口径：vendor 源码版本 vs NVD affected 区间；同时区分「是否需要启用 DDS-Security 才可触发」。

### 3.1 Fast-DDS（eProsima）

时间线依据：eProsima 官方「Supported versions」页（最新维护分支）与 v3.6.2 previous-versions 页交叉核对。

| CVE | 公告摘要 | NVD affected 区间 | 修复版本 | 本仓 vendor 3.6.2 | 攻击向量 / 严重度 | 本仓证据 |
|-----|----------|-------------------|----------|-------------------|-------------------|----------|
| CVE-2025-64438 | RELIABLE 下处理 RTPS GAP 子消息远程 OOM | `<2.6.11`、`[3.0.0,3.3.1)`、`[3.4.0,3.4.1)` | 2.6.11 / 3.3.1 / 3.4.1 | **已排除**（3.6.2 > 3.4.1） | 网络相邻、无需安全模式、DoS | 非安全路径；运行时需 ≥2.6.11 |
| CVE-2025-62599~62603、64098、62799、65016、2026-22590 | 一批堆/整型/越界修复（含 62602：启用安全时篡改 SPDP DATA 子消息堆溢出） | `<2.6.11`、`[3.0.0,3.3.1)`、`[3.4.0,3.4.1)` | 2.6.11 / 3.3.1 / 3.4.1 | **已排除**（3.6.2 已含） | 多数需启用 DDS-Security | 本仓未启用安全（见下） |
| CVE-2026-22591 | SQL 内容过滤器实现不受控递归（CWE-400/674） | 2.7.0、3.0.0、3.4.0 等受影响；修复于 2.6.12/2.14.6/3.2.4/**3.6.0** | 3.6.0（及维护分支） | **已排除**（3.6.2 ≥ 3.6.0） | 网络相邻、无需安全、DoS | vendor 已修复；运行时需 ≥2.6.12 |
| CVE-2025-63829 / 65865 / 67108；CVE-2026-45092~45098；CVE-2026-49861~49863；CVE-2026-53588~53590；CVE-2026-55063 | 2026-09 前后发布的最新一批安全修复 | 修复进入维护分支 2.6.12 / 2.14.7 / 3.2.5 / 3.4.3 与 **Pro 3.6.2.1** | 上述维护版本 | **需验证** | 多为网络相邻 / 安全路径 | 见下方说明 |
| CVE-2023-50257 | 即使启用 SROS2，断开节点的 `p[UD]`/`guid` 未加密，可强制断开 Subscriber | 老版本（2.6.7 起修复） | 2.6.7 / 2.10.3 等 | **已排除** | 网络相邻 DoS | 3.6.2 早已修复 |
| CVE-2024-28231 / 2024-30258 / 2024-30259 / 2025-24807 | 更早一批（DATA_FRAG bad-free、ciphering、整型溢出等） | <2.6.x 对应补丁 | 2.6.8/2.6.10 等 | **已排除** | 网络相邻 DoS | 3.6.2 早已修复 |

**关于「需验证」批次的说明**：本仓 vendor 为 `master` HEAD 快照（拷贝日 2026-09-10，SHA `343f155c…`，`vendor/VERSIONS.md:26`）。上游社区 3.6 支持清单仅列到 3.6.0/3.6.1，而该最新批次在维护分支（2.6.12/2.14.7/3.2.5/3.4.3）与 **Pro** 3.6.2.1 中落地；社区 master 是否在该 SHA 之前合入同批 backport，仅凭文档页无法 100% 判定。建议后续对照该 SHA 的 git log 与对应 GHSA 修复 commit 核对（见 §4）。

**本仓侧的暴露/排除证据（Fast-DDS）**：
- 排除证据①：vendor 树**不编译、不进产物**（`.github/workflows/ci.yml:88,143,345`；`vendor/MANIFEST.md:22`）。
- 排除证据②：`config/fastdds.xml:96-132` 全程**无 `<security>`/DDS-Security 配置块**，未配置任何证书/治理文件；链 A 仅配置 SHM/UDP 传输与域 42。因此「启用安全才触发」的 CVE（如 CVE-2025-62602、CVE-2023-50257 的 SROS2 场景）在本仓配置下**不可达**。
- 真实暴露面：运行时为 Humble apt 的 Fast-DDS **2.6.x**。仓内注释观察到 2.6.12（`config/fastdds.xml:37` 「Humble Fast-DDS 2.6.12」）。2.6.12 已含最新批次修复；但 `Dockerfile`/`install-runtime.sh` 用 `apt-get install ros-humble-*` **未钉小版本**，实际版本取决于镜像构建日的 apt 解析，存在漂移风险。

### 3.2 CycloneDDS（Eclipse）

| CVE | 公告摘要 | NVD affected 区间 | 修复版本 | 本仓 11.0.1 | 说明 |
|-----|----------|-------------------|----------|-------------|------|
| CVE-2024-10838 | `DDS_Security_Deserialize_*` 整型下溢 → 堆越界读（CWE-191，GHSA-6jj6-w25p-jc42） | `>=0, <0.10.5` | **0.10.5** | **已排除**（11.0.1 ≫ 0.10.5） | 位于 DDS-Security 反序列化路径，本仓未启用安全 |
| CVE-2025-67109 | time certificate 校验不足，可绕过证书检查（CVSS 10.0 报道） | before **0.10.5** | 0.10.5 | **已排除** | 证书/TLS 路径；本仓未配置任何 Cyclone 安全证书 |
| CVE-2021-38441 / 38443 | XML 解析器 write-what-where / 任意写 | before **0.8.0** | 0.8.0 | **已排除** | 11.0.1 远高于此 |
| CVE-2026-27509 | Unitree Go2 EDU `actuator_manager.py` 暴露 DDS topic `rt/api/programming_actuator/request`，网络相邻未认证即可下发任意 Python | 非 CycloneDDS 库本身 | — | **不适用（库版本）** | 这是 Unitree 机器人应用层问题，与本仓 `vendor/CycloneDDS` 库版本无关；但与链 B Unitree 域 0 场景相关，见 §4 注意事项 |

**本仓侧证据（CycloneDDS）**：
- 排除证据：钉扎发行标签 **11.0.1**（`vendor/CycloneDDS/package.xml`；`vendor/VERSIONS.md:37`），高于全部已知修复线 0.10.5 / 0.8.0。
- 排除证据②：链 B 为原生 Cyclone **域 0**、无安全配置（`vendor/MANIFEST.md:19,39`）；`scripts/bench/cyclonedds_udp_lo.xml` 为环回调试用，未见证书配置。
- 运行时链 B 依赖 `cyclonedds>=0.10.5`（`scripts/bench/requirements-chain-b.txt:3`）——下界恰在 CVE-2024-10838 / CVE-2025-67109 修复版上，**满足**修复要求。

### 3.3 ROS 2 rmw / rmw_fastrtps / rmw_cyclonedds

- 未检索到针对 `rmw` 7.11.2、`rmw_fastrtps_*` 9.5.2、`rmw_cyclonedds_cpp` 4.2.1 的独立 GHSA/CVE。这三者是 **RMW 适配/API 层**，安全风险主要来自其封装的底层 Fast-DDS / CycloneDDS（见 §3.1/§3.2）。
- 历史上与 ROS 2 安全相关的公开问题（如 CVE-2023-24011 PKCS#7 证书校验、SROS2 单 CA 信任链、CVE-2019-19625/19627）均针对 **SROS2/DDS-Security 启用场景**；本仓未启用 DDS-Security（`config/fastdds.xml` 无安全块），这些路径不可达。
- **证据状态：已排除（对本仓未启用安全的配置）**。

### 3.4 Fast-CDR（Fast-DDS 依赖）

- vendor 钉扎 **2.3.5**（`vendor/Fast-DDS/thirdparty/fastcdr/CMakeLists.txt:38`），与上游 Fast-DDS 3.4.2+ 「Update fastcdr to 2.3.5」一致。
- 未检索到针对 Fast-CDR 2.3.5 的独立 GHSA/CVE；历史上的 Fast CDR 相关问题（如 BadParamException 未捕获导致远程崩溃）由 **Fast-DDS** 侧修复（CVE-2023-39948，2.6.5/2.10.0），非 Fast-CDR 库本身 CVE。
- **证据状态：已排除 / 未见独立公告**，建议随 Fast-DDS 升级一并跟踪。

---

## 4. 结论、注意事项与建议后续步骤

### 4.1 证据状态（按包）

| 包 | 状态 |
|----|------|
| CycloneDDS 11.0.1 | **已排除**（已知公告全部修复在 ≤0.10.5；本仓未启用安全） |
| Fast-CDR 2.3.5 | **已排除**（未见独立公告） |
| rmw / rmw_fastrtps 9.5.2 / rmw_cyclonedds 4.2.1 | **已排除**（适配层无独立 CVE；未启用 SROS2） |
| Fast-DDS 3.6.2（vendor master 快照） | **需验证**（仅 2026-09 最新一批；vendor 不编译不进产物） |
| Fast-DDS 2.6.x（Humble 运行时） | **需验证**（Dockerfile 未钉小版本；≥2.6.12 则已修复） |

### 4.2 严重程度与影响范围

- **最高潜在影响**来自 Fast-DDS **运行时**（Humble 2.6.x）在未启用安全的 UDP 网络上暴露的无认证 DoS 类 CVE（如 GAP 子消息 OOM、内容过滤递归）。攻击向量为**网络相邻**（同一 DDS 域），无需凭证；本仓链 A 域 42、链 B 域 0，均为无认证广播发现，若运行于不可信网络段需关注。
- **缓解因素（已具备）**：① vendor 树不编译不进产物；② CI 权限最小化、无 `pull_request_target`、无 secrets/缓存暴露；③ 无 `curl|bash`、无硬编码凭证、无预编译二进制入库；④ DDS-Security 未启用，大量“需安全模式”的 CVE 路径不可达。

### 4.3 建议（仅列出，未执行）

1. **确认运行时 Fast-DDS 小版本**：在目标镜像内 `dpkg -l ros-humble-fastrtps`（或 `apt policy ros-humble-fastrtps`）确认 ≥2.6.12；若低于此，`apt-get update && apt-get install --only-upgrade ros-humble-fastrtps*` 到最新维护版。这是消除 §3.1「需验证」批次对运行时影响的最直接动作。
2. **核对 vendor master SHA 是否含最新批次**：对照 `343f155c36b561db3d9f047a65d866ce0f3da300` 的 git log 与 eProsima 对应 GHSA 修复 commit；若该 master 快照落后于 backport，下次刷新 vendor 时取含修复的 tag（如维护分支最新）。注意 vendor 仅供对照，不阻塞运行时。
3. **CI action SHA 钉扎**：将 `actions/checkout@v4` 改为 `@<commit-sha>` 形式（`.github/workflows/ci.yml:22,220,351`），降低 tag 被移动的供应链风险。
4. **apt 源改 HTTPS**：`docker/ros/install-base.sh:41` 的 `http://packages.ros.org/...` 改为 `https://packages.ros.org/...`（官方支持 HTTPS 镜像），减小中间人面（keyring 签名已存在，但传输层仍建议加密）。
5. **Python 依赖加 hash**：`scripts/bench/requirements-chain-b.txt` 引入 `--require-hashes` 并钉死版本（当前仅 `sortedcontainers==2.4.0` 钉版，`cyclonedds`/`rerun-sdk` 为范围）。
6. **关注 Unitree 应用层 CVE-2026-27509**：这不是 CycloneDDS 库问题，而是 Go2 EDU 侧 `actuator_manager` 暴露可执行任意 Python 的 DDS topic；链 B 若直连 Unitree 域 0，建议在网络层隔离 DDS 域，不依赖 CycloneDDS 版本升级。
7. **网络面建议**：因两条链均为无认证 DDS 发现，生产/外网段应限制 DDS 多播与发现端口的可达范围，或在真正需要机密性/完整性时再评估启用 DDS-Security（届时需重新评估 §3.1/§3.2 安全路径 CVE）。

### 4.4 来源

- eProsima Fast-DDS 受支持版本（含各分支 CVE 修复表）: https://fast-dds.docs.eprosima.com/en/latest/notes/previous_versions/supported_versions.html
- eProsima Fast-DDS v3.6.2 历史版本说明: https://fast-dds.docs.eprosima.com/en/v3.6.2/notes/previous_versions/previous_versions.html
- NVD CVE-2026-22591（Fast-DDS 内容过滤递归）: https://nvd.nist.gov/vuln/detail/cve-2026-22591
- NVD CVE-2025-64438（Fast-DDS GAP OOM）: https://nvd.nist.gov/vuln/detail/cve-2025-64438
- NVD CVE-2025-62602（Fast-DDS 安全模式堆溢出）: https://nvd.nist.gov/vuln/detail/CVE-2025-62602
- CVE.org CVE-2024-10838（CycloneDDS 反序列化整型下溢，affected <0.10.5）: https://www.cve.org/CVERecord?id=CVE-2024-10838
- NVD CVE-2025-67109（CycloneDDS 证书校验，before 0.10.5）: https://nvd.nist.gov/vuln/detail/CVE-2025-67109
- NVD CVE-2026-27509（Unitree Go2 EDU 应用层 DDS topic）: https://nvd.nist.gov/vuln/detail/CVE-2026-27509

> 本报告为只读审计结论，未对仓库任何文件或运行时做改动。§4 的建议均待人工确认后另行执行。
