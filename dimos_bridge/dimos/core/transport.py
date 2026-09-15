# Copyright 2025-2026 Dimensional Inc.
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

from __future__ import annotations

import threading
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)

from dimos.core.stream import Out, Stream, Transport
from dimos.msgs.protocol import DimosMsg
from dimos.utils import colors

try:
    import cyclonedds as _cyclonedds  # noqa: F401

    DDS_AVAILABLE = True
except ImportError:
    DDS_AVAILABLE = False

from dimos.protocol.pubsub.impl.lcmpubsub import LCM, PickleLCM, Topic as LCMTopic
from dimos.protocol.pubsub.impl.rospubsub import DimosROS, ROSTopic
from dimos.protocol.pubsub.impl.shmpubsub import BytesSharedMemory, PickleSharedMemory

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

# TODO
# Transports need to be rewritten and simplified,
#
# there is no need for them to get a reference to "a stream" on publish/subscribe calls
# this is a legacy from dask transports.
#
# new transport should literally have 2 functions (next to start/stop)
# "send(msg)" and "receive(callback)" and that's all
#
# we can also consider pubsubs conforming directly to Transport specs
# and removing PubSubTransport glue entirely
#
# Why not ONLY pubsubs without Transport abstraction?
#
# General idea for transports (and why they exist at all)
# is that they can be * anything * like
#
# a web camera rtsp stream for Image, audio stream from mic, etc
# http binary streams, tcp connections etc


class PubSubTransport(Transport[T]):
    topic: Any
    _started: bool = False
    _backend: Any = None

    def __init__(self, topic: Any) -> None:
        self.topic = topic

    def _ensure_started(self) -> None:
        """Lazily start the transport on first use, matching legacy semantics."""
        if not self._started:
            self.start()

    def start(self) -> None:
        """Start the underlying backend (LCM/SHM family default)."""
        self._backend.start()
        self._started = True

    def stop(self) -> None:
        """Stop the underlying backend (LCM/SHM family default)."""
        self._backend.stop()
        self._started = False

    def subscribe(
        self, callback: Callable[[T], Any], selfstream: Stream[T] | None = None
    ) -> Callable[[], None] | None:
        """Subscribe to the underlying backend (LCM/SHM family default)."""
        self._ensure_started()
        return self._backend.subscribe(self.topic, lambda msg, topic: callback(msg))

    def broadcast(self, _: Out[T] | None, msg: T) -> None:
        """Publish a message through the underlying backend (LCM/SHM family default)."""
        self._ensure_started()
        self._backend.publish(self.topic, msg)

    def __str__(self) -> str:
        return (
            colors.green(f"{self.__class__.__name__}(")
            + colors.blue(self.topic)
            + colors.green(")")
        )


class pLCMTransport(PubSubTransport[T]):
    def __init__(self, topic: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(topic)
        self.lcm = PickleLCM(**kwargs)
        self._backend = self.lcm

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (pLCMTransport, (self.topic,))

    def subscribe(
        self, callback: Callable[[T], Any], selfstream: Stream[T] | None = None
    ) -> Callable[[], None]:
        self._ensure_started()
        return self.lcm.subscribe(LCMTopic(self.topic), lambda msg, topic: callback(msg))


class LCMTransport(PubSubTransport[T]):
    def __init__(self, topic: str, type: type, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(LCMTopic(topic, type))
        if not hasattr(self, "lcm"):
            self.lcm = LCM(**kwargs)
        self._backend = self.lcm

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (LCMTransport, (self.topic.topic, self.topic.lcm_type))


class JpegLcmTransport(LCMTransport):  # type: ignore[type-arg]
    def __init__(self, topic: str, type: type, **kwargs) -> None:  # type: ignore[no-untyped-def]
        from dimos.protocol.pubsub.impl.jpeg_lcm import (
            JpegLCM,
        )  # ~330ms: deferred to avoid pulling in Image/cv2/rerun

        self.lcm = JpegLCM(**kwargs)  # type: ignore[assignment]
        super().__init__(topic, type)

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (JpegLcmTransport, (self.topic.topic, self.topic.lcm_type))


class pSHMTransport(PubSubTransport[T]):
    def __init__(self, topic: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(topic)
        self.shm = PickleSharedMemory(**kwargs)
        self._backend = self.shm

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (pSHMTransport, (self.topic,))


class SHMTransport(PubSubTransport[T]):
    def __init__(self, topic: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(topic)
        self.shm = BytesSharedMemory(**kwargs)
        self._backend = self.shm

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (SHMTransport, (self.topic,))


class JpegShmTransport(PubSubTransport[T]):
    def __init__(self, topic: str, quality: int = 75, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(topic)
        from dimos.protocol.pubsub.impl.jpeg_shm import (
            JpegSharedMemory,
        )  # deferred to avoid pulling in Image/cv2/rerun

        self.shm = JpegSharedMemory(quality=quality, **kwargs)
        self._backend = self.shm
        self.quality = quality

    def __reduce__(self):  # type: ignore[no-untyped-def]
        return (JpegShmTransport, (self.topic, self.quality))


class ROSTransport(PubSubTransport[DimosMsg]):
    _ros: DimosROS | None = None

    def __init__(self, topic: str, msg_type: type[DimosMsg], **kwargs: Any) -> None:
        super().__init__(ROSTopic(topic, msg_type))
        self._kwargs = kwargs

    def __reduce__(self) -> tuple[Any, ...]:
        return (ROSTransport, (self.topic.topic, self.topic.msg_type))

    def broadcast(self, _: Out[DimosMsg], msg: DimosMsg) -> None:
        if self._ros is None:
            self.start()
            assert self._ros is not None  # for type narrowing
        self._ros.publish(self.topic, msg)

    def subscribe(
        self, callback: Callable[[DimosMsg], Any], selfstream: Stream[DimosMsg] | None = None
    ) -> Callable[[], None]:
        if self._ros is None:
            self.start()
            assert self._ros is not None  # for type narrowing
        return self._ros.subscribe(self.topic, lambda msg, topic: callback(msg))

    def start(self) -> None:
        if self._ros is None:
            self._ros = DimosROS(**self._kwargs)
            self._ros.start()

    def stop(self) -> None:
        if self._ros is not None:
            self._ros.stop()
            self._ros = None


if DDS_AVAILABLE:
    from dimos.protocol.pubsub.impl.ddspubsub import DDS, Topic as DDSTopic

    class DDSTransport(PubSubTransport[T]):
        def __init__(self, topic: str, type: type, **kwargs) -> None:  # type: ignore[no-untyped-def]
            super().__init__(DDSTopic(topic, type))
            self.dds = DDS(**kwargs)
            self._started: bool = False
            self._start_lock = threading.RLock()

        def start(self) -> None:
            with self._start_lock:
                if not self._started:
                    self.dds.start()
                    self._started = True

        def stop(self) -> None:
            with self._start_lock:
                if self._started:
                    self.dds.stop()
                    self._started = False

        def broadcast(self, _, msg) -> None:  # type: ignore[no-untyped-def]
            with self._start_lock:
                if not self._started:
                    self.start()
                self.dds.publish(self.topic, msg)

        def subscribe(
            self, callback: Callable[[T], None], selfstream: Stream[T] | None = None
        ) -> Callable[[], None]:
            with self._start_lock:
                if not self._started:
                    self.start()
                return self.dds.subscribe(self.topic, lambda msg, topic: callback(msg))


class ZenohTransport(PubSubTransport[T]): ...
