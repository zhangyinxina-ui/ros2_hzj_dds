# eval/fixtures/

评估套件用的最小夹具。**不是生产配置**，请勿被 `source` 或 `FASTRTPS_DEFAULT_PROFILES_FILE` 指向。

| 文件 | 用途 |
|------|------|
| `minimal_fastdds.xml` | 最小合法 Fast-DDS profile 样本，演示 domainId / SHM / socket buffer 字段。 |
| `minimal_topics.yaml` | 最小 topics.yaml 样本，演示 discovery / nav_path / go2_ros_bridge 三段。 |

夹具内容全部来自仓库公开契约（域 42 / 0、RMW 名、topic 字符串），不含任何机密、客户数据或个人信息。

新增夹具时：
1. 只放"可公开、可重放、无密钥"的样本；
2. 在本文件登记一行；
3. 如果夹具依赖了某个版本的 Fast-DDS / CycloneDDS，在文件名里标注（如 `fastdds_humble_2.6.xml`）。
