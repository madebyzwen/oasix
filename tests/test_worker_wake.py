from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from oasix.config import RuntimeConfig
from oasix.worker import (
    NoWakeController,
    WakeOnLanController,
    WorkerCommunicationError,
    WorkerWakeController,
    create_worker_wake_controller,
)


def test_wake_on_lan_uses_only_active_worker_and_builds_valid_magic_packet(
    valid_config_data: dict[str, Any],
) -> None:
    second_worker = deepcopy(valid_config_data["workers"]["worker-primary"])
    second_worker["power"]["wake"].update(
        mac_address="02:00:00:00:00:02",
        broadcast_host="second-network.example.invalid",
        port=7,
    )
    valid_config_data["workers"]["worker-secondary"] = second_worker
    valid_config_data["active_worker"] = "worker-secondary"
    runtime = RuntimeConfig.model_validate(valid_config_data)
    deliveries: list[tuple[bytes, str, int]] = []

    async def sender(packet: bytes, host: str, port: int) -> None:
        deliveries.append((packet, host, port))

    controller = create_worker_wake_controller(runtime, sender=sender)
    assert isinstance(controller, WakeOnLanController)
    assert isinstance(controller, WorkerWakeController)

    asyncio.run(controller.wake())

    expected_mac = bytes.fromhex("020000000002")
    assert deliveries == [
        (
            b"\xff" * 6 + expected_mac * 16,
            "second-network.example.invalid",
            7,
        )
    ]
    assert len(deliveries[0][0]) == 102


def test_no_wake_profile_uses_noop_controller(valid_config_data: dict[str, Any]) -> None:
    valid_config_data["workers"]["worker-primary"]["power"]["wake"] = {"method": "none"}
    runtime = RuntimeConfig.model_validate(valid_config_data)
    controller = create_worker_wake_controller(runtime)

    assert isinstance(controller, NoWakeController)
    asyncio.run(controller.wake())


def test_wake_transport_failure_is_controlled_and_redacted(
    valid_config_data: dict[str, Any],
    production_traceback_locals: Any,
) -> None:
    destination = "sentinel-private-network.example.invalid"
    valid_config_data["workers"]["worker-primary"]["power"]["wake"]["broadcast_host"] = destination
    runtime = RuntimeConfig.model_validate(valid_config_data)

    async def failing_sender(packet: bytes, host: str, port: int) -> None:
        raise OSError("SENTINEL-WAKE-TRANSPORT")

    controller = create_worker_wake_controller(runtime, sender=failing_sender)

    with pytest.raises(WorkerCommunicationError) as captured:
        asyncio.run(controller.wake())

    exposed = "\n".join(
        (
            str(captured.value),
            repr(captured.value),
            production_traceback_locals(captured.value),
        )
    )
    assert destination not in exposed
    assert "SENTINEL-WAKE-TRANSPORT" not in exposed
    assert "destination=<configured>" in repr(controller)
