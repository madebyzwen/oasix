"""Configured Wake-on-LAN adapters for the active compute worker."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable

from oasix.config.models import RuntimeConfig, WakeOnLan
from oasix.worker.contracts import WorkerId
from oasix.worker.errors import WorkerCommunicationError, WorkerConfigurationError
from oasix.worker.resolution import ActiveWorkerTarget, resolve_active_worker

type DatagramSender = Callable[[bytes, str, int], Awaitable[None]]


class NoWakeController:
    """A bounded no-op for worker profiles which need no wake mechanism."""

    __slots__ = ("_target",)

    def __init__(self, target: ActiveWorkerTarget) -> None:
        self._target = target

    def __repr__(self) -> str:
        return f"NoWakeController(worker_id={self.worker_id!r})"

    @property
    def worker_id(self) -> WorkerId:
        return self._target.worker_id

    async def wake(self) -> None:
        return None


class WakeOnLanController:
    """Send the validated magic packet without implying worker readiness."""

    __slots__ = ("_sender", "_settings", "_target")

    def __init__(
        self,
        target: ActiveWorkerTarget,
        settings: WakeOnLan,
        *,
        sender: DatagramSender | None = None,
    ) -> None:
        self._target = target
        self._settings = settings
        self._sender = sender if sender is not None else _send_udp_broadcast

    def __repr__(self) -> str:
        return f"WakeOnLanController(worker_id={self.worker_id!r}, destination=<configured>)"

    @property
    def worker_id(self) -> WorkerId:
        return self._target.worker_id

    async def wake(self) -> None:
        packet = _magic_packet(self._settings.mac_address)
        delivery = _deliver_magic_packet(
            self._sender,
            packet,
            self._settings.broadcast_host,
            self._settings.port,
        )
        packet = b""
        delivered = await delivery
        if not delivered:
            raise WorkerCommunicationError from None


def create_worker_wake_controller(
    runtime: RuntimeConfig,
    *,
    sender: DatagramSender | None = None,
) -> NoWakeController | WakeOnLanController:
    """Create the configured wake adapter for exactly the active worker."""

    target = resolve_active_worker(runtime)
    settings = target.profile.power.wake
    if settings.method == "none":
        if sender is not None:
            raise WorkerConfigurationError from None
        return NoWakeController(target)
    return WakeOnLanController(target, settings, sender=sender)


def _magic_packet(mac_address: str) -> bytes:
    address = bytes.fromhex(mac_address.replace(":", ""))
    return b"\xff" * 6 + address * 16


async def _deliver_magic_packet(
    sender: DatagramSender,
    packet: bytes,
    host: str,
    port: int,
) -> bool:
    try:
        await sender(packet, host, port)
    except Exception:
        return False
    return True


async def _send_udp_broadcast(packet: bytes, host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(
        host,
        port,
        family=socket.AF_INET,
        type=socket.SOCK_DGRAM,
    )
    if not addresses:
        raise OSError("wake destination could not be resolved")

    datagram_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        datagram_socket.setblocking(False)
        datagram_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        await loop.sock_sendto(datagram_socket, packet, addresses[0][4])
    finally:
        datagram_socket.close()
