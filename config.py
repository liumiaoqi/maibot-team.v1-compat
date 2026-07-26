"""V1 兼容层配置。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CompatConfig:
    """兼容层完整配置。"""

    plugin_dirs: list[str] = field(default_factory=lambda: ["data/MaiMBot/plugins"])
    ipc_listen_address: str = "tcp://127.0.0.1:0"
    session_token: str = ""
    host_version: str = "compat-v1"
    runner_group: str = "compat"
    max_restart_attempts: int = 3
    restart_initial_delay_s: float = 1.0
    restart_max_delay_s: float = 10.0
    health_check_interval_s: float = 30.0
