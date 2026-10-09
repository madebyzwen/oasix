from __future__ import annotations

import traceback
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml


@pytest.fixture
def valid_config_data() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "active_worker": "worker-primary",
        "workers": {
            "worker-primary": {
                "connection": {
                    "host": "compute-worker.example.invalid",
                    "ssh": {
                        "port": 22,
                        "user": "worker-service",
                        "private_key": {"source": "file", "name": "worker_ssh_key"},
                        "known_hosts": {"source": "file", "name": "worker_known_hosts"},
                    },
                },
                "power": {
                    "wake": {
                        "method": "wol",
                        "mac_address": "02:00:00:00:00:01",
                        "broadcast_host": "wake-network.example.invalid",
                        "port": 9,
                    },
                    "sleep": {
                        "method": "ssh_command",
                        "command": ["systemctl", "suspend"],
                    },
                },
                "services": {
                    "llm": {
                        "kind": "llm",
                        "enabled": True,
                        "endpoint": "http://llm.compute-worker.example.invalid:8080",
                        "auth": {
                            "method": "bearer",
                            "secret": {"source": "file", "name": "llm_api_token"},
                        },
                        "readiness": {
                            "type": "http",
                            "method": "GET",
                            "path": "/v1/models",
                            "expected_status_codes": [200],
                        },
                    }
                },
            }
        },
        "policies": {
            "idle_timeout_seconds": 3600,
            "readiness_timeout_seconds": 300,
            "force_sleep_grace_period_seconds": 60,
            "retry": {
                "job": {
                    "max_attempts": 3,
                    "initial_delay_seconds": 5.0,
                    "multiplier": 2.0,
                    "max_delay_seconds": 60.0,
                    "jitter": {"ratio": 0.2},
                },
                "wake": {
                    "max_attempts": 2,
                    "initial_delay_seconds": 10.0,
                    "multiplier": 2.0,
                    "max_delay_seconds": 120.0,
                    "jitter": None,
                },
            },
            "concurrency": {
                "max_concurrent_jobs": 1,
                "max_concurrent_inference_requests": 2,
            },
        },
    }


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[[dict[str, Any]], Path]:
    def _write(data: dict[str, Any]) -> Path:
        path = tmp_path / "oasix.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def secret_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "secrets"
    directory.mkdir()
    (directory / "worker_ssh_key").write_text("private-key-value\n", encoding="utf-8")
    (directory / "worker_known_hosts").write_text("known-host-value\n", encoding="utf-8")
    (directory / "llm_api_token").write_text("api-token-value\n", encoding="utf-8")
    return directory


@pytest.fixture
def clone_config() -> Callable[[dict[str, Any]], dict[str, Any]]:
    return deepcopy


@pytest.fixture
def production_traceback_locals() -> Callable[[BaseException], str]:
    source_root = (Path(__file__).resolve().parents[1] / "src" / "oasix").resolve()

    def _capture(error: BaseException) -> str:
        captured = traceback.TracebackException.from_exception(error, capture_locals=True)
        return "\n".join(
            repr(frame.locals)
            for frame in captured.stack
            if Path(frame.filename).resolve().is_relative_to(source_root)
        )

    return _capture
