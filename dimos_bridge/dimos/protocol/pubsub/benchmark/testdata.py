# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np

from dimos.msgs.sensor_msgs.Image import Image, ImageFormat
from dimos.protocol.pubsub.benchmark.type import Case

try:
    import cyclonedds as _cyclonedds  # noqa: F401

    DDS_AVAILABLE = True
except ImportError:
    DDS_AVAILABLE = False
from dimos.protocol.pubsub.impl.lcmpubsub import LCM, LCMPubSubBase, Topic as LCMTopic
from dimos.protocol.pubsub.impl.memory import Memory
from dimos.protocol.pubsub.impl.shmpubsub import (
    BytesSharedMemory,
    LCMSharedMemory,
    PickleSharedMemory,
)


def make_data_bytes(size: int) -> bytes:
    """Generate random bytes of given size."""
    return bytes(i % 256 for i in range(size))


def _rgb_image_geometry(size: int) -> tuple[int, int, bytes]:
    """Compute (height, width, packed RGB bytes) for an approx-``size`` RGB image.

    Pads the byte buffer to a multiple of 3, lays it out as a near-square
    RGB image, and returns the pixel geometry plus the trimmed byte payload.
    """
    raw_data = np.frombuffer(make_data_bytes(size), dtype=np.uint8)
    padded_size = ((len(raw_data) + 2) // 3) * 3
    padded_data = np.pad(raw_data, (0, padded_size - len(raw_data)))
    pixels = len(padded_data) // 3
    height = max(1, int(pixels**0.5))
    width = pixels // height
    data = padded_data[: height * width * 3]
    return height, width, bytes(data)


def make_data_image(size: int) -> Image:
    """Generate an RGB Image with approximately `size` bytes of data."""
    height, width, data = _rgb_image_geometry(size)
    return Image(data=np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3), format=ImageFormat.RGB)


testcases: list[Case[Any, Any]] = []


@contextmanager
def lcm_pubsub_channel() -> Generator[LCM, None, None]:
    lcm_pubsub = LCM()
    lcm_pubsub.start()
    yield lcm_pubsub
    lcm_pubsub.stop()


def lcm_msggen(size: int) -> tuple[LCMTopic, Image]:
    topic = LCMTopic(topic="benchmark/lcm", lcm_type=Image)
    return (topic, make_data_image(size))


testcases.append(
    Case(
        pubsub_context=lcm_pubsub_channel,
        msg_gen=lcm_msggen,
    )
)


@contextmanager
def udp_bytes_pubsub_channel() -> Generator[LCMPubSubBase, None, None]:
    """LCM with raw bytes - no encoding overhead."""
    lcm_pubsub = LCMPubSubBase()
    lcm_pubsub.start()
    yield lcm_pubsub
    lcm_pubsub.stop()


def udp_bytes_msggen(size: int) -> tuple[LCMTopic, bytes]:
    """Generate raw bytes for LCM transport benchmark."""
    topic = LCMTopic(topic="benchmark/lcm_raw")
    return (topic, make_data_bytes(size))


testcases.append(
    Case(
        pubsub_context=udp_bytes_pubsub_channel,
        msg_gen=udp_bytes_msggen,
    )
)


@contextmanager
def memory_pubsub_channel() -> Generator[Memory, None, None]:
    """Context manager for Memory PubSub implementation."""
    yield Memory()


def memory_msggen(size: int) -> tuple[str, Any]:
    return ("benchmark/memory", make_data_image(size))


# testcases.append(
#     Case(
#         pubsub_context=memory_pubsub_channel,
#         msg_gen=memory_msggen,
#     )
# )


@contextmanager
def shm_pickle_pubsub_channel() -> Generator[PickleSharedMemory, None, None]:
    # 12MB capacity to handle benchmark sizes up to 10MB
    shm_pubsub = PickleSharedMemory(prefer="cpu", default_capacity=12 * 1024 * 1024)
    shm_pubsub.start()
    yield shm_pubsub
    shm_pubsub.stop()


def shm_msggen(size: int) -> tuple[str, Any]:
    """Generate message for SharedMemory pubsub benchmark."""
    return ("benchmark/shm", make_data_image(size))


testcases.append(
    Case(
        pubsub_context=shm_pickle_pubsub_channel,
        msg_gen=shm_msggen,
    )
)


@contextmanager
def shm_bytes_pubsub_channel() -> Generator[BytesSharedMemory, None, None]:
    """SharedMemory with raw bytes - no pickle overhead."""
    shm_pubsub = BytesSharedMemory(prefer="cpu", default_capacity=12 * 1024 * 1024)
    shm_pubsub.start()
    yield shm_pubsub
    shm_pubsub.stop()


def shm_bytes_msggen(size: int) -> tuple[str, bytes]:
    """Generate raw bytes for SharedMemory transport benchmark."""
    return ("benchmark/shm_bytes", make_data_bytes(size))


testcases.append(
    Case(
        pubsub_context=shm_bytes_pubsub_channel,
        msg_gen=shm_bytes_msggen,
    )
)


@contextmanager
def shm_lcm_pubsub_channel() -> Generator[LCMSharedMemory, None, None]:
    """SharedMemory with LCM binary encoding - no pickle overhead."""
    shm_pubsub = LCMSharedMemory(prefer="cpu", default_capacity=12 * 1024 * 1024)
    shm_pubsub.start()
    yield shm_pubsub
    shm_pubsub.stop()


testcases.append(
    Case(
        pubsub_context=shm_lcm_pubsub_channel,
        msg_gen=lcm_msggen,  # Reuse the LCM message generator
    )
)

if DDS_AVAILABLE:
    from cyclonedds.idl import IdlStruct
    from cyclonedds.idl.types import sequence, uint8
    from cyclonedds.qos import Policy, Qos

    from dimos.protocol.pubsub.impl.ddspubsub import (
        DDS,
        Topic as DDSTopic,
    )

    @dataclass
    class DDSBenchmarkData(IdlStruct):  # type: ignore[misc]
        """DDS message type for benchmarking with variable-size byte payload."""

        data: sequence[uint8]  # type: ignore[type-arg]

    @contextmanager
    def _dds_pubsub_channel(qos: Any) -> Generator[DDS, None, None]:
        dds_pubsub = DDS(qos=qos)
        dds_pubsub.start()
        yield dds_pubsub
        dds_pubsub.stop()

    @contextmanager
    def dds_high_throughput_pubsub_channel() -> Generator[DDS, None, None]:
        """DDS with high-throughput QoS preset."""
        HIGH_THROUGHPUT_QOS = Qos(
            Policy.Reliability.BestEffort,
            Policy.History.KeepLast(depth=1),
            Policy.Durability.Volatile,
        )
        yield from _dds_pubsub_channel(HIGH_THROUGHPUT_QOS)

    @contextmanager
    def dds_reliable_pubsub_channel() -> Generator[DDS, None, None]:
        """DDS with reliable QoS preset."""
        RELIABLE_QOS = Qos(
            Policy.Reliability.Reliable(max_blocking_time=0),
            Policy.History.KeepLast(depth=5000),
            Policy.Durability.Volatile,
        )
        yield from _dds_pubsub_channel(RELIABLE_QOS)

    def dds_msggen(size: int) -> tuple[DDSTopic, DDSBenchmarkData]:
        """Generate DDS message for benchmark."""
        topic = DDSTopic(name="benchmark/dds", data_type=DDSBenchmarkData)
        return (topic, DDSBenchmarkData(data=list(make_data_bytes(size))))  # type: ignore[arg-type]

    testcases.append(
        Case(
            pubsub_context=dds_high_throughput_pubsub_channel,
            msg_gen=dds_msggen,
        )
    )

    testcases.append(
        Case(
            pubsub_context=dds_reliable_pubsub_channel,
            msg_gen=dds_msggen,
        )
    )


try:
    from dimos.protocol.pubsub.impl.redispubsub import Redis

    @contextmanager
    def redis_pubsub_channel() -> Generator[Redis, None, None]:
        redis_pubsub = Redis()
        redis_pubsub.start()
        yield redis_pubsub
        redis_pubsub.stop()

    def redis_msggen(size: int) -> tuple[str, Any]:
        # Redis uses JSON serialization, so use a simple dict with base64-encoded data
        import base64

        data = base64.b64encode(make_data_bytes(size)).decode("ascii")
        return ("benchmark/redis", {"data": data, "size": size})

    testcases.append(
        Case(
            pubsub_context=redis_pubsub_channel,
            msg_gen=redis_msggen,
        )
    )

except (ConnectionError, ImportError):
    # either redis is not installed or the server is not running
    print("Redis not available")


from dimos.protocol.pubsub.impl.rospubsub import (
    ROS_AVAILABLE,
    DimosROS,
    RawROS,
    RawROSTopic,
    ROSTopic,
)

if ROS_AVAILABLE:
    from rclpy.qos import (
        QoSDurabilityPolicy,
        QoSHistoryPolicy,
        QoSProfile,
        QoSReliabilityPolicy,
    )
    from sensor_msgs.msg import Image as ROSImage

    @contextmanager
    def _ros_pubsub_channel(
        node_name: str, reliability: QoSReliabilityPolicy
    ) -> Generator[RawROS, None, None]:
        qos = QoSProfile(  # type: ignore[no-untyped-call]
            reliability=reliability,
            history=QoSHistoryPolicy.KEEP_LAST,
            durability=QoSDurabilityPolicy.VOLATILE,
            depth=5000,
        )
        ros_pubsub = RawROS(node_name=node_name, qos=qos)
        ros_pubsub.start()
        yield ros_pubsub
        ros_pubsub.stop()

    @contextmanager
    def ros_best_effort_pubsub_channel() -> Generator[RawROS, None, None]:
        yield from _ros_pubsub_channel(
            "benchmark_ros_best_effort", QoSReliabilityPolicy.BEST_EFFORT
        )

    @contextmanager
    def ros_reliable_pubsub_channel() -> Generator[RawROS, None, None]:
        yield from _ros_pubsub_channel(
            "benchmark_ros_reliable", QoSReliabilityPolicy.RELIABLE
        )

    def ros_msggen(size: int) -> tuple[RawROSTopic, ROSImage]:
        height, width, data = _rgb_image_geometry(size)

        # Create ROS Image message
        msg = ROSImage()
        msg.height = height
        msg.width = width
        msg.encoding = "rgb8"
        msg.step = width * 3
        msg.data = data

        topic = RawROSTopic(topic="/benchmark/ros", ros_type=ROSImage)
        return (topic, msg)

    testcases.append(
        Case(
            pubsub_context=ros_best_effort_pubsub_channel,
            msg_gen=ros_msggen,
        )
    )

    testcases.append(
        Case(
            pubsub_context=ros_reliable_pubsub_channel,
            msg_gen=ros_msggen,
        )
    )

    @contextmanager
    def _dimos_ros_pubsub_channel(
        node_name: str, reliability: QoSReliabilityPolicy
    ) -> Generator[DimosROS, None, None]:
        qos = QoSProfile(  # type: ignore[no-untyped-call]
            reliability=reliability,
            history=QoSHistoryPolicy.KEEP_LAST,
            durability=QoSDurabilityPolicy.VOLATILE,
            depth=5000,
        )
        ros_pubsub = DimosROS(node_name=node_name, qos=qos)
        ros_pubsub.start()
        yield ros_pubsub
        ros_pubsub.stop()

    @contextmanager
    def dimos_ros_best_effort_pubsub_channel() -> Generator[DimosROS, None, None]:
        yield from _dimos_ros_pubsub_channel(
            "benchmark_dimos_ros_best_effort", QoSReliabilityPolicy.BEST_EFFORT
        )

    @contextmanager
    def dimos_ros_reliable_pubsub_channel() -> Generator[DimosROS, None, None]:
        yield from _dimos_ros_pubsub_channel(
            "benchmark_dimos_ros_reliable", QoSReliabilityPolicy.RELIABLE
        )

    def dimos_ros_msggen(size: int) -> tuple[ROSTopic, Image]:
        topic = ROSTopic(topic="/benchmark/dimos_ros", msg_type=Image)
        return (topic, make_data_image(size))

    # commented to save benchmarking time,
    # since reliable and best effort are very similar in performance for local pubsub
    # testcases.append(
    #     Case(
    #         pubsub_context=dimos_ros_best_effort_pubsub_channel,
    #         msg_gen=dimos_ros_msggen,
    #     )
    # )

    testcases.append(
        Case(
            pubsub_context=dimos_ros_reliable_pubsub_channel,
            msg_gen=dimos_ros_msggen,
        )
    )
