"""Rack manifest helpers for a phone farm.

A rack manifest is a small JSON document describing every phone in one rack::

    {
      "rack": "rack-01",
      "host": "192.168.1.20",
      "devices": [
        {"udid": "R58M0001", "appium_port": 4723},
        {"udid": "R58M0002", "appium_port": 4724, "device_name": "slot-02"}
      ]
    }

Every key besides ``udid``/``appium_port`` is forwarded to
:meth:`social_media_bot.SocialMediaBot.connect_device` as a keyword argument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Union

DEFAULT_FIRST_PORT = 4723

#: Keyword arguments accepted by ``SocialMediaBot.connect_device``.
CONNECT_OPTION_KEYS = frozenset(
    {
        "app_activity",
        "app_package",
        "device_name",
        "extra_capabilities",
        "implicit_wait",
        "new_command_timeout",
        "no_reset",
        "platform_version",
        "server_path",
    }
)


@dataclass
class DeviceSpec:
    """One phone in a rack: UDID, Appium port and per-device options."""

    udid: str
    appium_port: int
    connect_options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.udid = self.udid.strip() if isinstance(self.udid, str) else ""
        if not self.udid:
            raise ValueError("device udid must be a non-empty string")

        if isinstance(self.appium_port, bool) or not isinstance(self.appium_port, int):
            raise ValueError(
                f"appium_port for {self.udid} must be an int, "
                f"got {type(self.appium_port).__name__}"
            )
        if not 1 <= self.appium_port <= 65535:
            raise ValueError(
                f"appium_port for {self.udid} must be between 1 and 65535, "
                f"got {self.appium_port}"
            )

        self.connect_options = dict(self.connect_options or {})
        unknown = set(self.connect_options) - CONNECT_OPTION_KEYS
        if unknown:
            raise ValueError(
                f"unknown connect option(s) for {self.udid}: {sorted(unknown)}"
            )


@dataclass
class Rack:
    """A named set of phones that share one Appium host."""

    name: str
    devices: List[DeviceSpec]
    host: Optional[str] = None
    allow_empty: bool = False

    def __post_init__(self) -> None:
        if not self.devices and not self.allow_empty:
            raise ValueError("rack must contain at least one device")

        udids = [device.udid for device in self.devices]
        if len(set(udids)) != len(udids):
            raise ValueError(f"duplicate udid in rack {self.name!r}")

        ports = [device.appium_port for device in self.devices]
        if len(set(ports)) != len(ports):
            raise ValueError(f"duplicate appium_port in rack {self.name!r}")

        if self.host is not None and not str(self.host).strip():
            raise ValueError("rack host must be a non-empty string when provided")

    @property
    def device_map(self) -> Dict[str, int]:
        """Convenience mapping ``udid -> appium_port``."""
        return {device.udid: device.appium_port for device in self.devices}

    def __len__(self) -> int:
        return len(self.devices)

    def __iter__(self) -> Iterator[DeviceSpec]:
        return iter(self.devices)


def load_rack(source: Union[str, Path, Mapping[str, Any]]) -> Rack:
    """Load a rack from a JSON file path or an already-parsed mapping.

    Raises:
        ValueError: The manifest is malformed or contains duplicate slots.
        OSError: The file cannot be read.
    """
    if isinstance(source, Mapping):
        payload: Any = source
    elif isinstance(source, (str, Path)):
        raw = Path(source).read_text(encoding="utf-8-sig")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid rack manifest JSON: {exc}") from exc
    else:
        raise ValueError("rack manifest must be a mapping, a path or a Path")

    if not isinstance(payload, Mapping):
        raise ValueError("rack manifest must be a JSON object")

    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise ValueError("rack manifest must define a non-empty 'devices' list")

    specs: List[DeviceSpec] = []
    for index, entry in enumerate(raw_devices):
        if not isinstance(entry, Mapping):
            raise ValueError(f"device #{index + 1} must be a JSON object")

        udid = entry.get("udid")
        if not isinstance(udid, str) or not udid.strip():
            raise ValueError(f"device #{index + 1} is missing a valid 'udid'")

        has_port = "appium_port" in entry
        has_alias = "port" in entry
        if has_port and has_alias:
            raise ValueError(f"device {udid} defines both 'port' and 'appium_port'")
        port = entry.get("appium_port", entry.get("port"))
        if port is None:
            raise ValueError(f"device {udid} is missing 'appium_port'")

        options = {
            key: value
            for key, value in entry.items()
            if key not in {"udid", "appium_port", "port"}
        }
        specs.append(
            DeviceSpec(udid=udid, appium_port=port, connect_options=options)
        )

    name = str(payload.get("rack") or payload.get("name") or "rack")
    host = payload.get("host")
    return Rack(name=name, devices=specs, host=str(host) if host else None)


def sequential_rack(
    name: str,
    udids: Iterable[str],
    *,
    first_port: int = DEFAULT_FIRST_PORT,
    host: Optional[str] = None,
) -> Rack:
    """Build a rack assigning consecutive ports starting at *first_port*.

    Handy for a 20-phone rack wired as ``4723 .. 4742``::

        rack = sequential_rack("rack-01", [f"phone-{i:02d}" for i in range(1, 21)])
    """
    specs = [
        DeviceSpec(udid=udid, appium_port=first_port + offset)
        for offset, udid in enumerate(udids)
    ]
    return Rack(name=name, devices=specs, host=host)
