#!/usr/bin/env python3

"""Local web dashboard for the SocialMediaBot phone farm.

Run it from the tool folder::

    python3 dashboard.py
    python3 dashboard.py --manifest rack-lab.json

If the manifest does not exist, the dashboard starts in setup mode; open the
dashboard and use **Setup rack → Scan ADB → Simpan & terapkan** to create it.

The server binds to ``127.0.0.1:8080`` by default and exposes a small JSON API
plus a static single-page UI (``dashboard_ui/``). Jobs (connect, disconnect and
Facebook actions) run on a background worker so HTTP requests stay responsive.
"""

from __future__ import annotations

import argparse
import hmac
import itertools
import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import datetime
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)
from urllib.parse import parse_qs, unquote, urlparse

try:  # Support both package and direct-script execution.
    from .console import configure_stdio
    from .errors import PlatformActionNotSupported
    from .facebook import comment_on_post, create_post, follow_user
    from .generate_rack import parse_adb_devices, run_adb
    from .platforms import PlatformAdapter, PlatformInfo, default_platforms
    from .rack import Rack, DeviceSpec, load_rack
    from .social_media_bot import DeviceNotConnectedError, SocialMediaBot
except ImportError:  # pragma: no cover - direct script execution
    from console import configure_stdio
    from errors import PlatformActionNotSupported
    from facebook import comment_on_post, create_post, follow_user
    from generate_rack import parse_adb_devices, run_adb
    from platforms import PlatformAdapter, PlatformInfo, default_platforms
    from rack import Rack, DeviceSpec, load_rack
    from social_media_bot import DeviceNotConnectedError, SocialMediaBot

__all__ = [
    "ACTIONS",
    "DashboardServer",
    "DashboardState",
    "main",
]

LOGGER_NAME = "social_media_bot.dashboard"
STATIC_DIR = Path(__file__).resolve().parent / "dashboard_ui"
MAX_BODY_BYTES = 1_000_000
MAX_JOBS = 30
MAX_LOGS = 500
MAX_BULK_ITEMS = 200
MAX_BULK_INTERVAL = 300.0
MAX_BULK_RETRIES = 5
MAX_RACK_DEVICES = 64
MAX_SCHEDULE_AHEAD = 365 * 24 * 60 * 60
AUTH_COOKIE = "gads_auth"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

#: Longest per-device error shown inside a connect job message.
CONNECT_ERROR_LIMIT = 180


def _short_error(message: Optional[str], limit: int = CONNECT_ERROR_LIMIT) -> str:
    """Collapse a multi-line Appium error into one short line for job messages."""

    if not message:
        return "unknown error"
    text = " ".join(str(message).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"

LOGIN_PAGE = """<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login · SocialMediaBot</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      display: grid;
      place-items: center;
      min-height: 100vh;
      margin: 0;
      background: #12100e;
      color: #f3eee5;
      font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    }
    main {
      width: min(360px, calc(100vw - 32px));
      padding: 22px;
      border: 1px solid #352f29;
      border-radius: 8px;
      background: #1b1815;
    }
    h1 { margin: 0 0 6px; font-size: 18px; }
    p { margin: 0 0 14px; color: #a79a89; }
    label { display: grid; gap: 6px; font-weight: 600; }
    input {
      width: 100%;
      padding: 9px 11px;
      border: 1px solid #5a4938;
      border-radius: 6px;
      background: #181410;
      color: inherit;
      font: inherit;
    }
    input:focus { outline: 2px solid rgba(217, 119, 46, 0.75); outline-offset: 1px; }
    button {
      width: 100%;
      margin-top: 12px;
      padding: 9px 11px;
      border: 1px solid #d9772e;
      border-radius: 6px;
      background: #d9772e;
      color: #1a120b;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    .error { min-height: 20px; margin: 10px 0 0; color: #e08b82; font-size: 12px; }
  </style>
</head>
<body>
  <main>
    <h1>SocialMediaBot</h1>
    <p>Dashboard ini dilindungi token akses.</p>
    <form method="post" action="/login">
      <label>Token akses
        <input name="token" type="password" autocomplete="current-password" autofocus required>
      </label>
      <button type="submit">Masuk</button>
    </form>
    <p class="error" role="alert">%ERROR%</p>
  </main>
</body>
</html>
"""
DEFAULT_DAILY_LIMITS = {
    "follow": 30,
    "post": 10,
    "comment": 30,
    "like": 50,
    "save": 50,
    "reply": 30,
}

#: Key names exposed by the live screen controls.
KEYCODES: Dict[str, int] = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "delete": 67,
}

#: Action name -> callable(driver, **params).
ACTIONS: Dict[str, Callable[..., Any]] = {
    "follow": follow_user,
    "post": create_post,
    "comment": comment_on_post,
}

ACTION_FIELDS: Dict[str, Sequence[str]] = {
    "follow": ("username",),
    "post": ("text",),
    "comment": ("url", "text"),
    "like": ("url",),
    "save": ("url",),
    "reply": ("url", "text"),
}

#: Dashboard action key -> platform adapter method name.
ACTION_METHODS: Dict[str, str] = {
    "follow": "follow_user",
    "post": "create_post",
    "comment": "comment_on_post",
    "like": "like_post",
    "save": "save_post",
    "reply": "reply_to_post",
}


class _LegacyPlatform(PlatformAdapter):
    """Adapter used when tests/embedders inject a plain action mapping."""

    def __init__(
        self,
        handlers: Mapping[str, Callable[..., Any]],
        key: str = "facebook",
        label: str = "Facebook",
    ) -> None:
        actions = {
            action: ACTION_FIELDS[action]
            for action in handlers
            if action in ACTION_FIELDS
        }
        self.info = PlatformInfo(key, label, "legacy", actions)
        self._handlers = dict(handlers)

    def _call(self, action: str, driver: Any, **params: Any) -> Any:
        handler = self._handlers.get(action)
        if handler is None:
            raise PlatformActionNotSupported(
                f"{self.label} does not support {action!r}"
            )
        return handler(driver, **params)

    def follow_user(self, driver: Any, username: str, **kwargs: Any) -> Any:
        return self._call("follow", driver, username=username)

    def create_post(self, driver: Any, text: str, **kwargs: Any) -> Any:
        return self._call("post", driver, text=text)

    def comment_on_post(
        self, driver: Any, url: str, text: str, **kwargs: Any
    ) -> Any:
        return self._call("comment", driver, url=url, text=text)


class DashboardLogHandler(logging.Handler):
    """Copy log records into the dashboard's in-memory log buffer."""

    def __init__(self, state: "DashboardState") -> None:
        super().__init__()
        self._state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - bad format strings
            return
        self._state._append_log(  # noqa: SLF001 - intentional internal hook
            {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            }
        )


class DeviceStream:
    """One shared screenshot loop feeding any number of MJPEG subscribers."""

    def __init__(
        self, udid: str, get_driver: Callable[[], Any], interval: float = 1.0
    ) -> None:
        self.udid = udid
        self._get_driver = get_driver
        self._interval = max(0.05, float(interval))
        self._condition = threading.Condition()
        self._subscribers = 0
        self._frame: Optional[bytes] = None
        self._frame_id = 0
        self._closed = False
        self._failures = 0
        self._thread = threading.Thread(
            target=self._capture_loop, name=f"screen-{udid}", daemon=True
        )

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    def add_subscriber(self) -> None:
        with self._condition:
            self._subscribers += 1

    def remove_subscriber(self) -> None:
        with self._condition:
            self._subscribers = max(0, self._subscribers - 1)
            if self._subscribers == 0:
                self._closed = True
                self._condition.notify_all()

    def start(self) -> None:
        self._thread.start()

    def frames(self) -> Iterable[bytes]:
        """Yield PNG frames; returns when the stream is closed."""
        last = -1
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._frame_id != last or self._closed, timeout=2.0
                )
                if self._frame_id == last:
                    if self._closed:
                        return
                    continue
                last = self._frame_id
                frame = self._frame
            if frame is not None:
                yield frame

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if (
            self._thread.is_alive()
            and threading.current_thread() is not self._thread
        ):
            self._thread.join(timeout=1.0)

    def _capture_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
            started = time.monotonic()
            frame: Optional[bytes] = None
            try:
                frame = self._get_driver().get_screenshot_as_png()
            except Exception:  # device dropped or Appium is busy
                self._failures += 1
            else:
                self._failures = 0

            with self._condition:
                if self._closed:
                    return
                if frame:
                    self._frame = frame
                    self._frame_id += 1
                    self._condition.notify_all()
                elif self._failures >= 5:
                    self._closed = True
                    self._condition.notify_all()
                    return

            elapsed = time.monotonic() - started
            time.sleep(max(0.05, self._interval - elapsed))


class ScreenStreamManager:
    """Shares one capture loop per device between all connected viewers."""

    def __init__(
        self,
        bot: Any,
        *,
        default_interval: float = 1.0,
        min_interval: float = 0.3,
        max_interval: float = 10.0,
    ) -> None:
        self.bot = bot
        self.default_interval = float(default_interval)
        self._min = float(min_interval)
        self._max = float(max_interval)
        self._streams: Dict[str, DeviceStream] = {}
        self._lock = threading.Lock()

    def clamp_interval(self, interval: Any) -> float:
        if interval is None:
            return self.default_interval
        try:
            value = float(interval)
        except (TypeError, ValueError):
            return self.default_interval
        return max(self._min, min(self._max, value))

    def acquire(self, udid: str, interval: Any = None) -> DeviceStream:
        interval_value = self.clamp_interval(interval)
        with self._lock:
            stream = self._streams.get(udid)
            if stream is None or stream.closed:
                self.bot.get_driver(udid)  # raises when the device is offline
                stream = DeviceStream(
                    udid, lambda: self.bot.get_driver(udid), interval_value
                )
                stream.add_subscriber()
                stream.start()
                self._streams[udid] = stream
            else:
                stream.add_subscriber()
            return stream

    def release(self, stream: DeviceStream) -> None:
        with self._lock:
            stream.remove_subscriber()
            if stream.closed and self._streams.get(stream.udid) is stream:
                self._streams.pop(stream.udid, None)

    def close_all(self) -> None:
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.close()

    def set_bot(self, bot: Any) -> None:
        """Point the manager at a new bot when the Appium host changes."""

        with self._lock:
            if self._streams:
                raise RuntimeError("stop live viewers before changing Appium host")
            self.bot = bot


class _JobCancelled(Exception):
    """Internal signal used by the bulk runner when a job is cancelled."""

    def __init__(self, message: str, result: Any = None) -> None:
        super().__init__(message)
        self.result = result


class DashboardState:
    """Thread-safe state, job queue and API model for the dashboard."""

    def __init__(
        self,
        bot: Any,
        rack: Rack,
        *,
        max_workers: int = 8,
        platforms: Optional[Dict[str, PlatformAdapter]] = None,
        actions: Optional[Dict[str, Callable[..., Any]]] = None,
        max_jobs: int = MAX_JOBS,
        max_logs: int = MAX_LOGS,
        stream_interval: float = 1.0,
        manifest_path: Optional[Union[str, Path]] = None,
        bot_factory: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.bot = bot
        self.rack = rack
        self._manifest_path = (
            Path(manifest_path) if manifest_path is not None else None
        )
        self._bot_factory = bot_factory
        self._state_path = (
            self._manifest_path.with_name(
                self._manifest_path.stem + ".state.json"
            )
            if self._manifest_path is not None
            else None
        )
        self._daily_limits = dict(DEFAULT_DAILY_LIMITS)
        self._usage: Dict[str, Dict[str, int]] = {}
        self._analytics: Dict[str, Any] = {}
        self._max_workers = max(1, int(max_workers))
        if platforms is not None:
            self._platforms = dict(platforms)
        elif actions is not None:
            self._platforms = {"facebook": _LegacyPlatform(actions)}
        else:
            self._platforms = default_platforms()
        if not self._platforms:
            raise ValueError("at least one platform adapter is required")
        self._default_platform = next(iter(self._platforms))
        self.screen_streams = ScreenStreamManager(
            bot, default_interval=stream_interval
        )

        self._lock = threading.RLock()
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=max_logs)
        self._log_ids = itertools.count(1)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._job_order: Deque[str] = deque()
        self._job_ids = itertools.count(1)
        self._max_jobs = max_jobs
        self._load_state()

        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._closed = False
        self._worker = threading.Thread(
            target=self._worker_loop, name="dashboard-jobs", daemon=True
        )
        self._worker.start()
        self._scheduler_stop = threading.Event()
        self._scheduler = threading.Thread(
            target=self._scheduler_loop,
            name="dashboard-scheduler",
            daemon=True,
        )
        self._scheduler.start()

        self._log_handler = DashboardLogHandler(self)
        self._log_handler.setLevel(logging.INFO)
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger_levels: Dict[str, int] = {}
        for name in self._logger_names():
            logger = logging.getLogger(name)
            self._logger_levels[name] = logger.level
            logger.addHandler(self._log_handler)
            logger.setLevel(logging.INFO)

    def _logger_names(self) -> Iterable[str]:
        names = {
            LOGGER_NAME,
            getattr(SocialMediaBot, "__module__", "social_media_bot"),
            getattr(follow_user, "__module__", "facebook"),
        }
        for adapter in self._platforms.values():
            names.add(f"{adapter.info.key}.actions")
        return sorted(names)

    def _append_log(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            entry = dict(entry, id=next(self._log_ids))
            self._logs.append(entry)

    # ------------------------------------------------------------------
    # Scheduler / persistent automation state
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(
                self._state_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError) as exc:
            logging.getLogger(LOGGER_NAME).warning(
                "Ignoring automation state file: %s", exc
            )
            return
        if not isinstance(data, Mapping):
            return

        saved_limits = data.get("limits")
        if isinstance(saved_limits, Mapping):
            for action, value in saved_limits.items():
                if action not in DEFAULT_DAILY_LIMITS:
                    continue
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                self._daily_limits[action] = max(0, number)

        usage = data.get("usage")
        if isinstance(usage, Mapping):
            self._usage = {
                str(day): {
                    str(key): int(value)
                    for key, value in counters.items()
                    if isinstance(counters, Mapping)
                }
                for day, counters in usage.items()
                if isinstance(counters, Mapping)
            }
        analytics = data.get("analytics")
        if isinstance(analytics, Mapping):
            self._analytics = dict(analytics)

        max_id = 0
        for raw in data.get("scheduled", []):
            if not isinstance(raw, Mapping):
                continue
            job_id = str(raw.get("id") or "")
            if not job_id or job_id in self._jobs:
                continue
            try:
                scheduled_at = float(raw.get("scheduled_at"))
            except (TypeError, ValueError):
                continue
            payload = raw.get("payload")
            if not isinstance(payload, Mapping):
                continue
            job = {
                "id": job_id,
                "type": str(raw.get("type") or ""),
                "label": str(raw.get("label") or job_id),
                "payload": dict(payload),
                "status": "scheduled",
                "message": "Scheduled",
                "progress": raw.get("progress"),
                "cancel_requested": False,
                "created_at": float(raw.get("created_at") or time.time()),
                "scheduled_at": scheduled_at,
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._job_order.append(job_id)
            try:
                max_id = max(max_id, int(job_id.rsplit("-", 1)[1]))
            except (IndexError, ValueError):
                pass
        self._job_ids = itertools.count(max_id + 1)

    def _persist_state(self) -> None:
        if self._state_path is None:
            return
        with self._lock:
            scheduled = [
                {
                    key: job.get(key)
                    for key in (
                        "id",
                        "type",
                        "label",
                        "payload",
                        "progress",
                        "created_at",
                        "scheduled_at",
                    )
                }
                for job in self._jobs.values()
                if job.get("status") == "scheduled"
            ]
            data = {
                "limits": dict(self._daily_limits),
                "usage": self._usage,
                "analytics": self._analytics,
                "scheduled": scheduled,
            }
        temporary = self._state_path.with_name(
            self._state_path.name + ".tmp"
        )
        try:
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self._state_path)
        except OSError as exc:
            logging.getLogger(LOGGER_NAME).warning(
                "Could not save automation state: %s", exc
            )
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.wait(0.5):
            due: List[str] = []
            with self._lock:
                now = time.time()
                for job in self._jobs.values():
                    if job.get("status") != "scheduled":
                        continue
                    scheduled_at = job.get("scheduled_at")
                    if scheduled_at is not None and float(scheduled_at) <= now:
                        job["status"] = "queued"
                        job["message"] = "Queued"
                        due.append(job["id"])
                if due:
                    self._persist_state()
            for job_id in due:
                self._queue.put(job_id)


    # ------------------------------------------------------------------
    # Daily limits, analytics and health checks
    # ------------------------------------------------------------------
    @staticmethod
    def _today_key() -> str:
        return datetime.now().date().isoformat()

    def _consume_limit(self, platform: str, action: str, udid: str) -> None:
        if action not in self._daily_limits:
            return
        limit = int(self._daily_limits.get(action, 0))
        if limit <= 0:
            raise RuntimeError(f"{action} is disabled by daily limit 0")
        day = self._today_key()
        key = f"{platform}|{action}|{udid}"
        with self._lock:
            counters = self._usage.setdefault(day, {})
            used = int(counters.get(key, 0))
            if used >= limit:
                raise RuntimeError(
                    f"Daily limit reached for {action} on {udid} "
                    f"({used}/{limit})"
                )
            counters[key] = used + 1

    def _record_analytics(
        self,
        platform: str,
        action: str,
        udid: str,
        ok: bool,
    ) -> None:
        day = self._today_key()
        bucket = "ok" if ok else "failed"
        with self._lock:
            day_data = self._analytics.setdefault(day, {})
            platform_data = day_data.setdefault(platform, {})
            action_data = platform_data.setdefault(
                action, {"ok": 0, "failed": 0}
            )
            action_data[bucket] = int(action_data.get(bucket, 0)) + 1

    def automation_snapshot(self) -> Dict[str, Any]:
        day = self._today_key()
        with self._lock:
            usage = dict(self._usage.get(day, {}))
            analytics = {
                platform: {
                    action: dict(counters)
                    for action, counters in actions.items()
                }
                for platform, actions in self._analytics.get(day, {}).items()
            }
            ok = sum(
                int(counters.get("ok", 0))
                for actions in analytics.values()
                for counters in actions.values()
            )
            failed = sum(
                int(counters.get("failed", 0))
                for actions in analytics.values()
                for counters in actions.values()
            )
        return {
            "date": day,
            "limits": dict(self._daily_limits),
            "usage": usage,
            "analytics": analytics,
            "totals": {"ok": ok, "failed": failed},
        }

    def save_limits(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ValueError("limits must be an object")
        updates: Dict[str, int] = {}
        for action, value in payload.items():
            if action not in DEFAULT_DAILY_LIMITS:
                raise ValueError(f"unknown limit {action!r}")
            if isinstance(value, bool):
                raise ValueError(f"limit {action} harus berupa angka")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"limit {action} harus berupa angka"
                ) from exc
            if not 0 <= number <= 10_000:
                raise ValueError(f"limit {action} harus 0–10000")
            updates[action] = number
        with self._lock:
            self._daily_limits.update(updates)
        self._persist_state()
        return self.automation_snapshot()

    def submit_connect(self, udids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        """Queue a connect job for the whole rack or a subset of UDIDs."""
        selected = self._select_devices(udids)
        label = f"Connect {len(selected)} device(s)"
        return self._submit(
            "connect", {"udids": [spec.udid for spec in selected]}, label
        )

    def submit_disconnect(self) -> Dict[str, Any]:
        """Queue a disconnect-all job."""
        return self._submit("disconnect", {}, "Disconnect all devices")

    def submit_health(
        self,
        target: str = "all",
        *,
        schedule_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        if target != "all":
            known = {spec.udid for spec in self.rack.devices}
            if target not in known:
                raise ValueError(f"unknown target device {target!r}")
        label = "Health check · " + (
            "all connected devices" if target == "all" else target
        )
        return self._submit(
            "health", {"target": target}, label, schedule_at=schedule_at
        )

    def submit_campaign(
        self,
        text: str,
        platforms: Iterable[str],
        target: str = "all",
        *,
        interval: float = 0.0,
        schedule_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Queue a text post campaign across several platforms."""

        if not isinstance(text, str) or not text.strip():
            raise ValueError("campaign text tidak boleh kosong")
        if len(text) > 5000:
            raise ValueError("campaign text maksimal 5000 karakter")
        requested = [str(key) for key in platforms]
        if not requested:
            raise ValueError("pilih minimal satu platform")
        if len(set(requested)) != len(requested):
            raise ValueError("platform campaign tidak boleh duplikat")
        try:
            interval_value = float(interval)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval campaign harus berupa angka") from exc
        if not 0 <= interval_value <= MAX_BULK_INTERVAL:
            raise ValueError("interval campaign harus 0–300 detik")
        if target != "all":
            known = {spec.udid for spec in self.rack.devices}
            if target not in known:
                raise ValueError(f"unknown target device {target!r}")

        steps = []
        labels = []
        for key in requested:
            adapter = self._platforms.get(key)
            if adapter is None:
                raise ValueError(f"unknown platform {key!r}")
            if "post" not in adapter.info.actions:
                raise ValueError(f"{adapter.label} tidak mendukung text post")
            steps.append(
                {
                    "platform": key,
                    "action": "post",
                    "params": {"text": text},
                }
            )
            labels.append(adapter.label)
        label = f"Campaign: {' + '.join(labels)}"
        return self._submit(
            "campaign",
            {
                "target": target,
                "steps": steps,
                "interval": interval_value,
            },
            label,
            progress={
                "total": len(steps),
                "done": 0,
                "ok": 0,
                "failed": 0,
                "current": "",
            },
            schedule_at=schedule_at,
        )

    def submit_action(
        self,
        action: str,
        target: str = "all",
        params: Optional[Dict[str, Any]] = None,
        *,
        platform: Optional[str] = None,
        schedule_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Queue a follow/post/comment job for one platform.

        Args:
            action: ``follow``, ``post`` or ``comment`` (platform dependent).
            target: ``"all"`` or one rack UDID.
            params: Action arguments (username/text/url).
            platform: Platform key; defaults to the registry's first platform.
        """
        platform_key = platform or self._default_platform
        adapter = self._platforms.get(platform_key)
        if adapter is None:
            raise ValueError(f"unknown platform {platform_key!r}")
        if action not in adapter.info.actions:
            supported = ", ".join(sorted(adapter.info.actions))
            raise ValueError(
                f"{adapter.label} does not support {action!r}; "
                f"supported: {supported or 'none'}"
            )
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")

        required = adapter.info.required_fields(action)
        missing = [
            field
            for field in required
            if not isinstance(params.get(field), str) or not params[field].strip()
        ]
        if missing:
            raise ValueError(f"missing or empty field(s): {', '.join(missing)}")
        unknown = set(params) - set(required)
        if unknown:
            raise ValueError(
                f"unknown field(s) for {action}: {', '.join(sorted(unknown))}"
            )

        if target != "all":
            known = {spec.udid for spec in self.rack.devices}
            if target not in known:
                raise ValueError(f"unknown target device {target!r}")

        clean = {field: params[field].strip() for field in required}
        label = self._action_label(adapter, action, target, clean)
        return self._submit(
            "action",
            {
                "platform": platform_key,
                "action": action,
                "target": target,
                "params": clean,
            },
            label,
            schedule_at=schedule_at,
        )

    def submit_bulk(
        self,
        action: str,
        items: List[str],
        target: str = "all",
        *,
        platform: Optional[str] = None,
        interval: float = 0.0,
        retries: int = 0,
        schedule_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Queue one job that runs *action* over a list of targets.

        Items are plain strings:
        - follow: one username per item
        - post: one post text per item
        - comment: ``"https://post-url | komentar"`` per item
        """
        platform_key = platform or self._default_platform
        adapter = self._platforms.get(platform_key)
        if adapter is None:
            raise ValueError(f"unknown platform {platform_key!r}")
        if action not in adapter.info.actions:
            supported = ", ".join(sorted(adapter.info.actions))
            raise ValueError(
                f"{adapter.label} does not support {action!r}; "
                f"supported: {supported or 'none'}"
            )
        if not isinstance(items, list) or not items:
            raise ValueError("items must be a non-empty array")
        if len(items) > MAX_BULK_ITEMS:
            raise ValueError(f"items must be at most {MAX_BULK_ITEMS}")

        try:
            interval_value = float(interval)
            retries_value = int(retries)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval/retries must be numbers") from exc
        if not 0 <= interval_value <= MAX_BULK_INTERVAL:
            raise ValueError(
                f"interval must be between 0 and {MAX_BULK_INTERVAL:.0f}s"
            )
        if not 0 <= retries_value <= MAX_BULK_RETRIES:
            raise ValueError(f"retries must be between 0 and {MAX_BULK_RETRIES}")

        if target != "all":
            known = {spec.udid for spec in self.rack.devices}
            if target not in known:
                raise ValueError(f"unknown target device {target!r}")

        parsed: List[Dict[str, str]] = []
        for index, item in enumerate(items, 1):
            if isinstance(item, str):
                item = item.lstrip("\ufeff")
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"item #{index} is empty")
            if action in {"comment", "reply"}:
                url, separator, text = item.partition("|")
                if not separator or not url.strip() or not text.strip():
                    raise ValueError(
                        f"item #{index} must look like 'url | teks'"
                    )
                parsed.append({"url": url.strip(), "text": text.strip()})
            elif action in {"like", "save"}:
                parsed.append({"url": item.strip()})
            elif action == "follow":
                parsed.append({"username": item.strip()})
            else:
                parsed.append({"text": item})

        target_label = "all devices" if target == "all" else target
        label = (
            f"{adapter.label} bulk {action} · {len(parsed)} item(s) · "
            f"{target_label}"
        )
        payload = {
            "platform": platform_key,
            "action": action,
            "target": target,
            "items": parsed,
            "interval": interval_value,
            "retries": retries_value,
        }
        return self._submit(
            "bulk",
            payload,
            label,
            progress={
                "total": len(parsed),
                "done": 0,
                "ok": 0,
                "failed": 0,
                "current": "",
            },
            schedule_at=schedule_at,
        )

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of a bulk job (checked between items)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"unknown job {job_id!r}")
            if job["status"] == "scheduled":
                job["status"] = "cancelled"
                job["message"] = "Cancelled before schedule"
                job["finished_at"] = time.time()
                self._persist_state()
                return True
            if job["type"] not in {"bulk", "campaign"}:
                raise ValueError("only bulk or campaign jobs can be cancelled")
            if job["status"] not in {"queued", "running"}:
                return False
            job["cancel_requested"] = True
            job["message"] = "Cancelling…"
            return True

    def retry_job(self, job_id: str) -> Dict[str, Any]:
        """Re-queue a failed job, or only the failed items of a bulk job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ValueError(f"unknown job {job_id!r}")
            payload = dict(job["payload"])
            if job["type"] == "bulk":
                result = job.get("result") or {}
                target = payload.get("target", "all")
                failed_items = [
                    dict(item["params"])
                    for item in result.get("items", [])
                    if item.get("failed")
                    and (target != "all" or not item.get("ok"))
                ]
                if not failed_items:
                    raise ValueError(
                        "no retryable failed items (partial device failures "
                        "are not retried to avoid duplicates)"
                    )
                payload["items"] = failed_items
                label = (
                    f"Retry {len(failed_items)} failed item(s) · "
                    f"{job['label']}"
                )
                return self._submit(
                    "bulk",
                    payload,
                    label,
                    progress={
                        "total": len(failed_items),
                        "done": 0,
                        "ok": 0,
                        "failed": 0,
                        "current": "",
                    },
                )
            if job["status"] not in {"failed", "cancelled"}:
                raise ValueError("only failed or cancelled jobs can be retried")
            return self._submit(job["type"], payload, f"Retry: {job['label']}")

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def _select_devices(self, udids: Optional[Iterable[str]]) -> List[Any]:
        if udids is None:
            return list(self.rack.devices)
        requested = {str(udid) for udid in udids}
        known = {spec.udid for spec in self.rack.devices}
        unknown = requested - known
        if unknown:
            raise ValueError(f"unknown device(s): {', '.join(sorted(unknown))}")
        return [spec for spec in self.rack.devices if spec.udid in requested]

    def _submit(
        self,
        job_type: str,
        payload: Dict[str, Any],
        label: str,
        progress: Optional[Dict[str, Any]] = None,
        schedule_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        scheduled_at = float(schedule_at) if schedule_at is not None else None
        is_scheduled = (
            scheduled_at is not None and scheduled_at > time.time() + 0.5
        )
        with self._lock:
            job_id = f"job-{next(self._job_ids)}"
            job = {
                "id": job_id,
                "type": job_type,
                "label": label,
                "payload": payload,
                "status": "scheduled" if is_scheduled else "queued",
                "message": (
                    "Scheduled for "
                    + time.strftime(
                        "%Y-%m-%d %H:%M",
                        time.localtime(scheduled_at),
                    )
                    if is_scheduled
                    else "Queued"
                ),
                "progress": dict(progress) if progress else None,
                "cancel_requested": False,
                "created_at": time.time(),
                "scheduled_at": scheduled_at,
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
            }
            self._jobs[job_id] = job
            self._job_order.append(job_id)
            while len(self._job_order) > self._max_jobs:
                expired = self._job_order.popleft()
                expired_job = self._jobs.pop(expired, None)
                if expired_job and expired_job.get("status") == "scheduled":
                    self._persist_state()
            snapshot = dict(job)
            if is_scheduled:
                self._persist_state()
        if not is_scheduled:
            self._queue.put(job_id)
        return snapshot

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        logger = logging.getLogger(LOGGER_NAME)
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                self._run_job(job_id)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Dashboard job %s crashed", job_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["message"] = "Cancelled before start"
                job["finished_at"] = time.time()
                self._persist_state()
                return
            job["status"] = "running"
            job["started_at"] = time.time()
            job["message"] = "Running"
            payload = dict(job["payload"])
            job_type = job["type"]

        try:
            if job_type == "connect":
                result, message = self._do_connect(payload)
            elif job_type == "disconnect":
                result, message = self._do_disconnect()
            elif job_type == "action":
                result, message = self._do_action(payload)
            elif job_type == "bulk":
                result, message = self._do_bulk(job_id, payload)
            elif job_type == "campaign":
                result, message = self._do_campaign(job_id, payload)
            elif job_type == "health":
                result, message = self._do_health(payload)
            else:  # pragma: no cover - guarded by _submit callers
                raise ValueError(f"unknown job type {job_type!r}")
        except _JobCancelled as exc:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "cancelled"
                    job["result"] = exc.result
                    job["message"] = str(exc)
                    job["finished_at"] = time.time()
            self._persist_state()
            logging.getLogger(LOGGER_NAME).info("%s: %s", job_id, exc)
            return
        except Exception as exc:
            logging.getLogger(LOGGER_NAME).error("Job %s failed: %s", job_id, exc)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "failed"
                    job["error"] = str(exc)
                    job["message"] = str(exc)
                    job["finished_at"] = time.time()
            self._persist_state()
            return

        status = "succeeded"
        if job_type == "connect" and isinstance(result, dict):
            failed_devices = result.get("failed") or []
            if failed_devices and not result.get("connected"):
                # Nothing came up: report the job as failed instead of
                # "succeeded" with a 0/N message, so the badge is not misleading.
                status = "failed"

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["status"] = status
                job["result"] = result
                job["message"] = message
                job["finished_at"] = time.time()
                if status == "failed":
                    job["error"] = message
        self._persist_state()
        logging.getLogger(LOGGER_NAME).info("%s: %s", job_id, message)

    def _do_connect(self, payload: Dict[str, Any]) -> Any:
        selected = self._select_devices(payload.get("udids"))
        if not selected:
            return {"connected": [], "failed": []}, "No devices selected"

        subset = Rack(
            name=f"{self.rack.name}-subset",
            devices=selected,
            host=self.rack.host,
        )
        connected = self.bot.connect_devices(subset, continue_on_error=True)
        failed = [spec.udid for spec in selected if spec.udid not in connected]
        reasons = dict(getattr(self.bot, "last_connect_failures", None) or {})
        message = f"{len(connected)}/{len(selected)} device(s) connected"
        if failed:
            message += "; failed: " + ", ".join(
                (
                    f"{udid} ({_short_error(reasons.get(udid))})"
                    if reasons.get(udid)
                    else udid
                )
                for udid in failed
            )
        result: Dict[str, Any] = {"connected": list(connected), "failed": failed}
        if reasons:
            result["errors"] = {
                udid: reasons[udid] for udid in failed if udid in reasons
            }
        return result, message

    def _do_disconnect(self) -> Any:
        self.screen_streams.close_all()
        closed = self.bot.disconnect_all()
        return {"closed": closed}, f"{closed} session(s) closed"

    def _do_health(self, payload: Dict[str, Any]) -> Any:
        target = payload.get("target", "all")
        udids = (
            list(self.bot.device_ids)
            if target == "all"
            else [target]
        )
        if not udids:
            raise RuntimeError("No connected devices. Run Connect all first.")
        results: Dict[str, Dict[str, Any]] = {}
        ok = failed = 0
        for udid in udids:
            try:
                driver = self.bot.get_driver(udid)
                package = getattr(driver, "current_package", None)
                activity = getattr(driver, "current_activity", None)
                results[udid] = {
                    "ok": True,
                    "package": str(package) if package is not None else None,
                    "activity": str(activity) if activity is not None else None,
                }
                ok += 1
            except Exception as exc:
                results[udid] = {"ok": False, "error": str(exc)}
                failed += 1
        return (
            {"ok": ok, "failed": failed, "devices": results},
            f"Health check: {ok} ok, {failed} failed",
        )

    def _do_bulk(self, job_id: str, payload: Dict[str, Any]) -> Any:
        platform_key = payload.get("platform", self._default_platform)
        adapter = self._platforms.get(platform_key)
        if adapter is None:
            raise ValueError(f"unknown platform {platform_key!r}")
        action = payload["action"]
        target = payload["target"]
        items = list(payload["items"])
        interval = float(payload.get("interval", 0.0))
        retries = int(payload.get("retries", 0))
        method_name = ACTION_METHODS.get(action)
        handler = getattr(adapter, method_name, None) if method_name else None
        if not callable(handler):
            raise ValueError(f"{adapter.label} does not implement {action!r}")

        if target == "all" and not self.bot.device_ids:
            raise RuntimeError("No connected devices. Run Connect all first.")

        def run_item(driver: Any, udid: str) -> Any:
            try:
                self._consume_limit(platform_key, action, udid)
                value = handler(driver, **item)
            except Exception:
                self._record_analytics(platform_key, action, udid, False)
                raise
            self._record_analytics(platform_key, action, udid, True)
            return value

        total = len(items)
        done = ok = failed = 0
        results: List[Dict[str, Any]] = []
        self._set_progress(
            job_id,
            {
                "total": total,
                "done": 0,
                "ok": 0,
                "failed": 0,
                "current": self._item_label(action, items[0]) if items else "",
            },
        )

        for item in items:
            if self._is_cancelled(job_id):
                raise _JobCancelled(
                    f"Cancelled after {done}/{total} item(s)",
                    {"ok": ok, "failed": failed, "items": results},
                )

            attempts = max(1, retries + 1)
            item_ok = item_failed = 0
            last_error = ""
            for attempt in range(1, attempts + 1):
                item_ok = item_failed = 0
                last_error = ""
                try:
                    if target == "all":
                        tasks = self.bot.run_on_devices(
                            lambda driver, udid: run_item(driver, udid),
                            max_workers=self._max_workers,
                        )
                        item_ok = sum(1 for task in tasks.values() if task.ok)
                        item_failed = len(tasks) - item_ok
                        if item_failed:
                            last_error = "; ".join(
                                str(task.error)
                                for task in tasks.values()
                                if not task.ok
                            )[:500]
                    else:
                        run_item(self.bot.get_driver(target), target)
                        item_ok = 1
                except Exception as exc:
                    item_ok = 0
                    item_failed = 1
                    last_error = str(exc)

                # Retry only when the whole item failed; never repeat devices
                # that already succeeded within the same item.
                if item_failed == 0 or item_ok > 0 or attempt >= attempts:
                    break
                time.sleep(1.0)

            done += 1
            ok += item_ok
            failed += item_failed
            results.append(
                {
                    "index": done,
                    "params": item,
                    "ok": item_ok,
                    "failed": item_failed,
                    "error": last_error or None,
                }
            )
            next_label = (
                self._item_label(action, items[done])
                if done < total
                else ""
            )
            self._set_progress(
                job_id,
                {
                    "total": total,
                    "done": done,
                    "ok": ok,
                    "failed": failed,
                    "current": next_label,
                },
            )

            if interval > 0 and done < total:
                try:
                    self._sleep_cancellable(job_id, interval)
                except _JobCancelled:
                    raise _JobCancelled(
                        f"Cancelled after {done}/{total} item(s)",
                        {"ok": ok, "failed": failed, "items": results},
                    )

        message = (
            f"{adapter.label} bulk {action}: {done}/{total} item(s), "
            f"{ok} ok, {failed} failed"
        )
        return {"ok": ok, "failed": failed, "items": results}, message

    def _set_progress(self, job_id: str, progress: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job["progress"] = progress

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.get("cancel_requested"))

    def _sleep_cancellable(self, job_id: str, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._is_cancelled(job_id):
                raise _JobCancelled("Cancelled during interval")
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    @staticmethod
    def _item_label(action: str, item: Dict[str, str]) -> str:
        if action == "follow":
            return f"@{item.get('username', '')}"
        text = item.get("text", "").replace("\n", " ")
        if len(text) > 40:
            text = text[:40] + "…"
        if action in {"comment", "reply"}:
            url = item.get("url", "")
            return f"{url} · {text}" if url else text
        if action in {"like", "save"}:
            return item.get("url", "")
        return text

    def _do_campaign(self, job_id: str, payload: Dict[str, Any]) -> Any:
        """Run text posts sequentially across the selected platforms."""

        target = payload.get("target", "all")
        steps = list(payload.get("steps") or [])
        interval = float(payload.get("interval", 0.0))
        results: List[Dict[str, Any]] = []
        ok = failed = 0
        for index, step in enumerate(steps, 1):
            if self._is_cancelled(job_id):
                raise _JobCancelled(
                    "Campaign cancelled",
                    {"steps": results, "ok": ok, "failed": failed},
                )
            platform_key = step["platform"]
            adapter = self._platforms.get(platform_key)
            self._set_progress(
                job_id,
                {
                    "total": len(steps),
                    "done": index - 1,
                    "ok": ok,
                    "failed": failed,
                    "current": adapter.label if adapter else platform_key,
                },
            )
            try:
                result, _message = self._do_action(
                    {
                        "platform": platform_key,
                        "action": step["action"],
                        "target": target,
                        "params": dict(step.get("params") or {}),
                    }
                )
                step_ok = int(result.get("ok", 0))
                step_failed = int(result.get("failed", 0))
                ok += step_ok
                failed += step_failed
                results.append(
                    {
                        "platform": platform_key,
                        "ok": step_ok,
                        "failed": step_failed,
                        "result": result,
                    }
                )
            except Exception as exc:  # keep remaining platforms running
                failed += 1
                results.append(
                    {
                        "platform": platform_key,
                        "ok": 0,
                        "failed": 1,
                        "error": str(exc),
                    }
                )
            self._set_progress(
                job_id,
                {
                    "total": len(steps),
                    "done": index,
                    "ok": ok,
                    "failed": failed,
                    "current": adapter.label if adapter else platform_key,
                },
            )
            if index < len(steps) and interval > 0:
                self._sleep_cancellable(job_id, interval)
        return (
            {"steps": results, "ok": ok, "failed": failed},
            f"Campaign: {len(results)} platform(s), {ok} ok, {failed} failed",
        )

    def _do_action(self, payload: Dict[str, Any]) -> Any:
        platform_key = payload.get("platform", self._default_platform)
        adapter = self._platforms.get(platform_key)
        if adapter is None:
            raise ValueError(f"unknown platform {platform_key!r}")
        action = payload["action"]
        target = payload["target"]
        params = dict(payload["params"])
        method_name = ACTION_METHODS.get(action)
        handler = getattr(adapter, method_name, None) if method_name else None
        if not callable(handler):
            raise ValueError(
                f"{adapter.label} does not implement {action!r}"
            )

        def run(driver: Any, udid: str) -> Any:
            try:
                self._consume_limit(platform_key, action, udid)
                value = handler(driver, **params)
            except Exception:
                self._record_analytics(platform_key, action, udid, False)
                raise
            self._record_analytics(platform_key, action, udid, True)
            return _serialize(value)

        if target != "all":
            driver = self.bot.get_driver(target)
            value = run(driver, target)
            return {
                "ok": 1,
                "failed": 0,
                "devices": {target: {"ok": True, "result": _serialize(value)}},
            }, f"{adapter.label} {action} on {target}: ok"

        if not self.bot.device_ids:
            raise RuntimeError("No connected devices. Run Connect all first.")

        tasks = self.bot.run_on_devices(run, max_workers=self._max_workers)
        devices: Dict[str, Any] = {}
        ok = 0
        for udid, task in tasks.items():
            if task.ok:
                ok += 1
                devices[udid] = {"ok": True, "result": _serialize(task.result)}
            else:
                devices[udid] = {"ok": False, "error": str(task.error)}
        failed = len(devices) - ok
        return {
            "ok": ok,
            "failed": failed,
            "devices": devices,
        }, f"{adapter.label} {action}: {ok} ok, {failed} failed"

    def _action_label(
        self,
        adapter: PlatformAdapter,
        action: str,
        target: str,
        params: Dict[str, str],
    ) -> str:
        target_label = "all devices" if target == "all" else target
        prefix = f"{adapter.label} · "
        if action == "follow":
            return f"{prefix}Follow @{params['username']} · {target_label}"
        if action == "post":
            preview = params["text"].replace("\n", " ")
            if len(preview) > 32:
                preview = preview[:32] + "…"
            return f'{prefix}Post "{preview}" · {target_label}'
        if action == "comment":
            return f"{prefix}Comment · {target_label}"
        if action in {"like", "save"}:
            return f"{prefix}{action.title()} · {target_label}"
        if action == "reply":
            return f"{prefix}Reply · {target_label}"
        return f"{prefix}{action} · {target_label}"  # pragma: no cover

    # ------------------------------------------------------------------
    # API snapshots
    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        connections = self.bot.connections
        devices = []
        for spec in self.rack.devices:
            connection = connections.get(spec.udid)
            devices.append(
                {
                    "udid": spec.udid,
                    "slot": spec.connect_options.get("device_name") or spec.udid,
                    "appium_port": spec.appium_port,
                    "platform_version": spec.connect_options.get("platform_version"),
                    "connected": connection is not None,
                    "session_id": connection.session_id if connection else None,
                    "server_url": connection.server_url if connection else None,
                }
            )

        with self._lock:
            recent = list(self._jobs.values())[-10:]
            jobs = []
            for job in reversed(recent):
                result = job.get("result") or {}
                cancellable = (
                    job["status"] == "scheduled"
                    or (
                        job["type"] in {"bulk", "campaign"}
                        and job["status"] in {"queued", "running"}
                    )
                )
                retryable = job["status"] in {"failed", "cancelled"}
                if job["type"] == "bulk":
                    target = (job.get("payload") or {}).get("target", "all")
                    retryable = retryable or any(
                        item.get("failed")
                        and (target != "all" or not item.get("ok"))
                        for item in result.get("items", [])
                    )
                jobs.append(
                    {
                        "id": job.get("id"),
                        "type": job.get("type"),
                        "label": job.get("label"),
                        "status": job.get("status"),
                        "message": job.get("message"),
                        "progress": job.get("progress"),
                        "created_at": job.get("created_at"),
                        "scheduled_at": job.get("scheduled_at"),
                        "finished_at": job.get("finished_at"),
                        "cancellable": cancellable,
                        "retryable": retryable,
                    }
                )
            queued = sum(1 for job in self._jobs.values() if job["status"] == "queued")
            running = sum(1 for job in self._jobs.values() if job["status"] == "running")
            log_total = len(self._logs)

        connected = sum(1 for device in devices if device["connected"])
        return {
            "rack": {
                "name": self.rack.name,
                "host": self.rack.host,
                "size": len(self.rack),
            },
            "summary": {
                "connected": connected,
                "total": len(self.rack),
                "queued_jobs": queued,
                "running_jobs": running,
                "log_entries": log_total,
            },
            "platforms": [
                adapter.info.as_dict()
                for adapter in self._platforms.values()
            ],
            "default_platform": self._default_platform,
            "devices": devices,
            "jobs": jobs,
            "server_time": time.time(),
        }

    # ------------------------------------------------------------------
    # Rack configuration from the dashboard UI
    # ------------------------------------------------------------------
    def config(self) -> Dict[str, Any]:
        """Return the editable rack configuration for the setup form."""

        effective_host = getattr(
            self.bot, "host", self.rack.host or "127.0.0.1"
        )
        return {
            "rack": {
                "name": self.rack.name,
                "host": effective_host,
                "manifest": (
                    str(self._manifest_path) if self._manifest_path else None
                ),
            },
            "devices": [
                {
                    "slot": spec.connect_options.get("device_name") or spec.udid,
                    "udid": spec.udid,
                    "appium_port": spec.appium_port,
                }
                for spec in self.rack.devices
            ],
        }

    def scan_adb(self) -> Dict[str, Any]:
        """Scan ``adb devices -l`` for devices that can be configured."""

        try:
            output = run_adb()
        except FileNotFoundError as exc:
            raise ValueError(
                "adb tidak ditemukan. Tambahkan Android platform-tools ke PATH."
            ) from exc
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"Gagal scan ADB: {exc}") from exc

        ready, skipped = parse_adb_devices(output)
        return {
            "devices": [
                {"udid": serial, "status": "device"} for serial in ready
            ],
            "skipped": [
                {"udid": serial, "status": status} for serial, status in skipped
            ],
            "count": len(ready),
        }


    def save_config(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        """Validate, persist and apply a rack configuration from the UI.

        The rack can only be changed while no device is connected and no job is
        queued/running. Existing per-device connect options and unrelated
        top-level manifest keys are preserved.
        """

        if not isinstance(payload, Mapping):
            raise ValueError("config body must be an object")
        if tuple(self.bot.device_ids):
            raise ValueError(
                "Disconnect all devices before changing the rack configuration"
            )
        with self._lock:
            active = [
                job
                for job in self._jobs.values()
                if job.get("status") in {"queued", "running"}
            ]
        if active:
            raise ValueError(
                "Tunggu semua job selesai sebelum mengubah konfigurasi rack"
            )

        name = str(payload.get("rack") or self.rack.name).strip()
        host = str(payload.get("host") or "").strip()
        if not name:
            raise ValueError("nama rack tidak boleh kosong")
        if not host:
            raise ValueError("Appium host tidak boleh kosong")
        if host.startswith(("http://", "https://")):
            raise ValueError(
                "Isi Appium host tanpa http:// atau https:// "
                "(contoh: 127.0.0.1)"
            )

        raw_devices = payload.get("devices")
        if not isinstance(raw_devices, list) or not raw_devices:
            raise ValueError("minimal satu device harus diisi")
        if len(raw_devices) > MAX_RACK_DEVICES:
            raise ValueError(
                f"maksimal {MAX_RACK_DEVICES} device per rack"
            )

        old_options = {
            spec.udid: dict(spec.connect_options) for spec in self.rack.devices
        }
        specs: List[DeviceSpec] = []
        seen_slots = set()
        for index, item in enumerate(raw_devices, 1):
            if not isinstance(item, Mapping):
                raise ValueError(f"device #{index} harus berupa object")
            udid = str(item.get("udid") or "").strip()
            if not udid:
                raise ValueError(f"UDID device #{index} tidak boleh kosong")
            raw_port = item.get("appium_port", item.get("port"))
            try:
                port = int(raw_port)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"port device #{index} harus berupa angka"
                ) from exc

            slot = str(
                item.get("device_name")
                or item.get("slot")
                or f"slot-{index:02d}"
            ).strip()
            if not slot:
                raise ValueError(f"slot device #{index} tidak boleh kosong")
            if slot in seen_slots:
                raise ValueError(f"slot {slot!r} dipakai lebih dari sekali")
            seen_slots.add(slot)
            options = old_options.get(udid, {})
            extra = item.get("connect_options")
            if extra is not None:
                if not isinstance(extra, Mapping):
                    raise ValueError(
                        f"connect_options device #{index} harus berupa object"
                    )
                options.update(extra)
            options["device_name"] = slot
            specs.append(
                DeviceSpec(
                    udid=udid,
                    appium_port=port,
                    connect_options=options,
                )
            )

        rack = Rack(name=name, devices=specs, host=host)
        if self._manifest_path is None:
            raise ValueError(
                "Manifest path tidak tersedia; jalankan dashboard dengan --manifest"
            )

        existing: Dict[str, Any] = {}
        try:
            if self._manifest_path.exists():
                loaded = json.loads(
                    self._manifest_path.read_text(encoding="utf-8-sig")
                )
                if isinstance(loaded, Mapping):
                    existing = dict(loaded)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Manifest lama tidak bisa dibaca: {exc}") from exc

        manifest_devices: List[Dict[str, Any]] = []
        for spec in rack.devices:
            entry: Dict[str, Any] = {
                "udid": spec.udid,
                "appium_port": spec.appium_port,
                "device_name": spec.connect_options.get("device_name")
                or spec.udid,
            }
            for key, value in spec.connect_options.items():
                if key != "device_name":
                    entry[key] = value
            manifest_devices.append(entry)

        existing["rack"] = rack.name
        existing["host"] = rack.host
        existing["devices"] = manifest_devices

        current_host = getattr(
            self.bot, "host", self.rack.host or "127.0.0.1"
        )
        new_bot = None
        if host != current_host:
            if self._bot_factory is None:
                raise ValueError(
                    "Perubahan Appium host memerlukan restart dashboard"
                )
            new_bot = self._bot_factory(host)

        path = self._manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            raise ValueError(f"Manifest tidak bisa disimpan: {exc}") from exc
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

        if new_bot is not None:
            self.screen_streams.close_all()
            self.screen_streams.set_bot(new_bot)
            self.bot = new_bot
        self.rack = rack
        logging.getLogger(LOGGER_NAME).info(
            "Rack configuration saved: %s device(s), host %s",
            len(rack),
            host,
        )
        return self.config()


    def logs(self, since: int = 0, limit: int = 200) -> Dict[str, Any]:
        with self._lock:
            entries = [entry for entry in self._logs if entry["id"] > since]
            entries = entries[-max(1, min(limit, 500)):]
        return {
            "logs": entries,
            "last_id": entries[-1]["id"] if entries else since,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._scheduler_stop.set()
        self._scheduler.join(timeout=2.0)
        self._persist_state()
        self._queue.put(None)
        self._worker.join(timeout=2.0)
        self.screen_streams.close_all()
        for name, level in self._logger_levels.items():
            logger = logging.getLogger(name)
            logger.removeHandler(self._log_handler)
            logger.setLevel(level)

    close = shutdown


def _serialize(value: Any) -> Any:
    """Turn result dataclasses into JSON-friendly dictionaries."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def _schedule_timestamp(value: Any) -> Optional[float]:
    """Parse an ISO-8601 or epoch schedule value; empty means immediate."""

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("schedule_at must be an ISO date or epoch seconds")
    if isinstance(value, (int, float)):
        timestamp = float(value)
    else:
        try:
            timestamp = datetime.fromisoformat(
                str(value).strip().replace("Z", "+00:00")
            ).timestamp()
        except ValueError as exc:
            raise ValueError(
                "schedule_at must be an ISO date/time or epoch seconds"
            ) from exc
    now = time.time()
    if timestamp < now - 60:
        raise ValueError("schedule_at tidak boleh di masa lalu")
    if timestamp > now + MAX_SCHEDULE_AHEAD:
        raise ValueError("schedule_at terlalu jauh di masa depan")
    return timestamp


def _fraction(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number between 0 and 1") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return number


def _normalized_point(payload: Dict[str, Any], field: str) -> Tuple[float, float]:
    point = payload.get(field)
    if not isinstance(point, dict):
        raise ValueError(f"{field} must be an object with x/y")
    return (
        _fraction(point.get("x"), f"{field}.x"),
        _fraction(point.get("y"), f"{field}.y"),
    )


def _device_size(driver: Any) -> Tuple[int, int]:
    size = driver.get_window_size()
    width = int(size["width"])
    height = int(size["height"])
    if width <= 0 or height <= 0:
        raise RuntimeError("device reported an invalid screen size")
    return width, height


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Serve the SPA and the dashboard JSON API."""

    server: "DashboardServer"
    server_version = "SocialMediaBotDashboard/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger(LOGGER_NAME).debug(
            "%s - %s", self.address_string(), format % args
        )

    @staticmethod
    def _secure_equal(left: str, right: str) -> bool:
        return hmac.compare_digest(
            left.encode("utf-8"), right.encode("utf-8")
        )

    def _request_token(self) -> Optional[str]:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            pass
        else:
            morsel = cookie.get(AUTH_COOKIE)
            if morsel is not None:
                return morsel.value
        header = self.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return None

    def _authorized(self) -> bool:
        expected = self.server.auth_token
        if not expected:
            return True
        supplied = self._request_token()
        return bool(supplied) and self._secure_equal(supplied, expected)

    def _serve_login(
        self,
        status: int = HTTPStatus.OK,
        message: str = "",
    ) -> None:
        body = LOGIN_PAGE.replace("%ERROR%", message).encode("utf-8")
        self._send_bytes(body, "text/html; charset=utf-8", status)

    def _redirect_root(self, *, set_cookie: bool = False) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        if set_cookie and self.server.auth_token:
            cookie = SimpleCookie()
            cookie[AUTH_COOKIE] = self.server.auth_token
            morsel = cookie[AUTH_COOKIE]
            morsel["path"] = "/"
            morsel["httponly"] = True
            morsel["samesite"] = "Strict"
            self.send_header("Set-Cookie", morsel.OutputString())
        self.end_headers()

    def _read_form(self) -> Dict[str, List[str]]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid form body") from exc
        return parse_qs(text, keep_blank_values=True)

    def _unauthorized(self, path: str) -> None:
        if path == "/":
            self._serve_login(
                HTTPStatus.UNAUTHORIZED,
                "Masukkan token untuk melanjutkan.",
            )
        elif path.startswith("/api/"):
            self._send_json(
                {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED
            )
        else:
            self._send_bytes(
                b"Unauthorized",
                "text/plain; charset=utf-8",
                HTTPStatus.UNAUTHORIZED,
            )

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/login":
            if self._authorized():
                self._redirect_root()
            else:
                self._serve_login()
            return
        if not self._authorized():
            self._unauthorized(path)
            return
        if path in ("/", "/index.html"):
            self._serve_static("index.html")
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
        elif path == "/api/status":
            self._send_json(self.server.state.status())
        elif path == "/api/config":
            self._send_json(self.server.state.config())
        elif path == "/api/automation":
            self._send_json(self.server.state.automation_snapshot())
        elif path == "/api/config/scan":
            try:
                self._send_json(self.server.state.scan_adb())
            except ValueError as exc:
                self._send_json(
                    {"error": str(exc)}, HTTPStatus.BAD_REQUEST
                )
        elif path == "/api/logs":
            query = parse_qs(parsed.query)
            try:
                since = int(query.get("since", ["0"])[0])
            except (TypeError, ValueError):
                since = 0
            self._send_json(self.server.state.logs(since))
        else:
            segments = [
                unquote(part) for part in path.strip("/").split("/")
            ]
            if (
                len(segments) == 4
                and segments[0] == "api"
                and segments[1] == "devices"
            ):
                udid = segments[2]
                if segments[3] == "stream":
                    self._serve_stream(udid, parsed.query)
                    return
                if segments[3] == "screenshot":
                    self._serve_screenshot(udid)
                    return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            try:
                form = self._read_form()
            except ValueError:
                self._serve_login(
                    HTTPStatus.BAD_REQUEST, "Permintaan login tidak valid."
                )
                return
            supplied = (form.get("token") or [""])[0]
            expected = self.server.auth_token
            if (
                expected
                and supplied
                and self._secure_equal(supplied, expected)
            ):
                self._redirect_root(set_cookie=True)
            else:
                self._serve_login(
                    HTTPStatus.UNAUTHORIZED, "Token tidak valid."
                )
            return
        if not self._authorized():
            self._send_json(
                {"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED
            )
            return
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        try:
            if parsed.path == "/api/config":
                self._send_json(
                    self.server.state.save_config(payload), HTTPStatus.OK
                )
                return
            elif parsed.path == "/api/automation":
                limits = payload.get("limits", payload)
                self._send_json(
                    self.server.state.save_limits(limits), HTTPStatus.OK
                )
                return
            elif parsed.path == "/api/health":
                job = self.server.state.submit_health(
                    target=payload.get("target", "all"),
                    schedule_at=_schedule_timestamp(
                        payload.get("schedule_at")
                    ),
                )
                self._send_json(job, HTTPStatus.ACCEPTED)
                return
            elif parsed.path == "/api/connect":
                udids = payload.get("udids")
                if udids is not None and not isinstance(udids, list):
                    raise ValueError("udids must be a list")
                job = self.server.state.submit_connect(udids)
            elif parsed.path == "/api/disconnect":
                job = self.server.state.submit_disconnect()
            elif parsed.path == "/api/action":
                job = self.server.state.submit_action(
                    action=payload.get("action", ""),
                    target=payload.get("target", "all"),
                    params=payload.get("params"),
                    platform=payload.get("platform"),
                    schedule_at=_schedule_timestamp(
                        payload.get("schedule_at")
                    ),
                )
            elif parsed.path == "/api/bulk":
                job = self.server.state.submit_bulk(
                    action=payload.get("action", ""),
                    items=payload.get("items"),
                    target=payload.get("target", "all"),
                    platform=payload.get("platform"),
                    interval=payload.get("interval", 0),
                    retries=payload.get("retries", 0),
                    schedule_at=_schedule_timestamp(
                        payload.get("schedule_at")
                    ),
                )
            elif parsed.path == "/api/campaign":
                job = self.server.state.submit_campaign(
                    text=payload.get("text", ""),
                    platforms=payload.get("platforms", []),
                    target=payload.get("target", "all"),
                    interval=payload.get("interval", 0),
                    schedule_at=_schedule_timestamp(
                        payload.get("schedule_at")
                    ),
                )
            elif parsed.path.startswith("/api/devices/"):
                parts = [
                    unquote(part) for part in parsed.path.strip("/").split("/")
                ]
                if (
                    len(parts) == 4
                    and parts[0] == "api"
                    and parts[1] == "devices"
                    and parts[3] == "input"
                ):
                    self._serve_input(parts[2], payload)
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            elif parsed.path.startswith("/api/jobs/"):
                parts = [
                    unquote(part) for part in parsed.path.strip("/").split("/")
                ]
                if (
                    len(parts) != 4
                    or parts[0] != "api"
                    or parts[1] != "jobs"
                ):
                    self._send_json(
                        {"error": "not found"}, HTTPStatus.NOT_FOUND
                    )
                    return
                job_id, verb = parts[2], parts[3]
                if verb == "cancel":
                    cancelled = self.server.state.cancel_job(job_id)
                    self._send_json(
                        {
                            "cancelled": cancelled,
                            "job": self.server.state.get_job(job_id),
                        },
                        HTTPStatus.OK if cancelled else HTTPStatus.CONFLICT,
                    )
                    return
                if verb == "retry":
                    job = self.server.state.retry_job(job_id)
                    self._send_json(job, HTTPStatus.ACCEPTED)
                    return
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            logging.getLogger(LOGGER_NAME).exception("Dashboard request failed")
            self._send_json(
                {"error": "internal server error"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(job, HTTPStatus.ACCEPTED)

    def _serve_stream(self, udid: str, query: str) -> None:
        params = parse_qs(query)
        interval = params.get("interval", [None])[0]
        try:
            stream = self.server.state.screen_streams.acquire(udid, interval)
        except DeviceNotConnectedError:
            self._send_json(
                {"error": "device is not connected"}, HTTPStatus.CONFLICT
            )
            return
        except Exception:
            logging.getLogger(LOGGER_NAME).exception(
                "Could not start the screen stream for %s", udid
            )
            self._send_json(
                {"error": "stream unavailable"}, HTTPStatus.BAD_GATEWAY
            )
            return

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            for frame in stream.frames():
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/png\r\n")
                self.wfile.write(
                    f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                )
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.state.screen_streams.release(stream)

    def _serve_screenshot(self, udid: str) -> None:
        try:
            driver = self.server.state.bot.get_driver(udid)
        except DeviceNotConnectedError:
            self._send_json(
                {"error": "device is not connected"}, HTTPStatus.CONFLICT
            )
            return
        try:
            frame = driver.get_screenshot_as_png()
        except Exception:
            logging.getLogger(LOGGER_NAME).warning(
                "Screenshot failed for %s", udid, exc_info=True
            )
            self._send_json(
                {"error": "screenshot unavailable"}, HTTPStatus.BAD_GATEWAY
            )
            return
        self._send_bytes(frame, "image/png")

    def _serve_input(self, udid: str, payload: Dict[str, Any]) -> None:
        input_type = payload.get("type")
        try:
            driver = self.server.state.bot.get_driver(udid)
        except DeviceNotConnectedError:
            self._send_json(
                {"error": "device is not connected"}, HTTPStatus.CONFLICT
            )
            return

        try:
            if input_type == "tap":
                result = self._input_tap(driver, payload)
            elif input_type == "swipe":
                result = self._input_swipe(driver, payload)
            elif input_type == "text":
                result = self._input_text(driver, payload)
            elif input_type == "key":
                result = self._input_key(driver, payload)
            else:
                raise ValueError("type must be tap, swipe, text or key")
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception:
            logging.getLogger(LOGGER_NAME).warning(
                "Input %s failed for %s", input_type, udid, exc_info=True
            )
            self._send_json({"error": "input failed"}, HTTPStatus.BAD_GATEWAY)
            return

        self._send_json({"ok": True, "action": input_type, **result})

    @staticmethod
    def _input_tap(driver: Any, payload: Dict[str, Any]) -> Dict[str, int]:
        fraction_x = _fraction(payload.get("x"), "x")
        fraction_y = _fraction(payload.get("y"), "y")
        width, height = _device_size(driver)
        x = round((width - 1) * fraction_x)
        y = round((height - 1) * fraction_y)

        execute = getattr(driver, "execute_script", None)
        if callable(execute):
            try:
                execute("mobile: clickGesture", {"x": x, "y": y})
                return {"x": x, "y": y}
            except Exception:
                logging.getLogger(LOGGER_NAME).debug(
                    "mobile: clickGesture failed; falling back to tap",
                    exc_info=True,
                )
        tap = getattr(driver, "tap", None)
        if not callable(tap):
            raise RuntimeError("driver does not support tap")
        tap([(x, y)])
        return {"x": x, "y": y}

    @staticmethod
    def _input_swipe(driver: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        start_fx, start_fy = _normalized_point(payload, "from")
        end_fx, end_fy = _normalized_point(payload, "to")
        try:
            duration = int(payload.get("duration", 300))
        except (TypeError, ValueError) as exc:
            raise ValueError("duration must be a number") from exc
        duration = max(50, min(2000, duration))

        width, height = _device_size(driver)
        start_x = round((width - 1) * start_fx)
        start_y = round((height - 1) * start_fy)
        end_x = round((width - 1) * end_fx)
        end_y = round((height - 1) * end_fy)

        swipe = getattr(driver, "swipe", None)
        if callable(swipe):
            try:
                swipe(start_x, start_y, end_x, end_y, duration)
                return {
                    "x": start_x,
                    "y": start_y,
                    "to_x": end_x,
                    "to_y": end_y,
                    "duration": duration,
                }
            except Exception:
                logging.getLogger(LOGGER_NAME).debug(
                    "driver.swipe failed; trying mobile: swipeGesture",
                    exc_info=True,
                )

        execute = getattr(driver, "execute_script", None)
        if not callable(execute):
            raise RuntimeError("driver does not support swipe")
        horizontal = abs(end_x - start_x) >= abs(end_y - start_y)
        if horizontal:
            direction = "right" if end_x >= start_x else "left"
        else:
            direction = "down" if end_y >= start_y else "up"
        execute(
            "mobile: swipeGesture",
            {
                "left": min(start_x, end_x),
                "top": min(start_y, end_y),
                "width": max(1, abs(end_x - start_x)),
                "height": max(1, abs(end_y - start_y)),
                "direction": direction,
                "percent": 1.0,
            },
        )
        return {
            "x": start_x,
            "y": start_y,
            "to_x": end_x,
            "to_y": end_y,
            "duration": duration,
        }

    @staticmethod
    def _input_text(driver: Any, payload: Dict[str, Any]) -> Dict[str, int]:
        text = payload.get("text")
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")
        if len(text) > 1000:
            raise ValueError("text must be at most 1000 characters")

        execute = getattr(driver, "execute_script", None)
        if callable(execute):
            try:
                execute("mobile: type", {"text": text})
                return {"text_length": len(text)}
            except Exception:
                logging.getLogger(LOGGER_NAME).debug(
                    "mobile: type failed; trying active element",
                    exc_info=True,
                )

        try:
            active = driver.switch_to.active_element
        except Exception:
            active = None
        if active is None:
            raise RuntimeError("driver cannot type text")
        active.send_keys(text)
        return {"text_length": len(text)}

    @staticmethod
    def _input_key(driver: Any, payload: Dict[str, Any]) -> Dict[str, str]:
        key = str(payload.get("key", "")).lower()
        if key not in KEYCODES:
            raise ValueError(
                f"key must be one of: {', '.join(sorted(KEYCODES))}"
            )
        press = getattr(driver, "press_keycode", None)
        if not callable(press):
            raise RuntimeError("driver does not support key presses")
        press(KEYCODES[key])
        return {"key": key}

    def _read_json(self) -> Dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _serve_static(self, name: str) -> None:
        if not name or name != Path(name).name:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        file_path = (STATIC_DIR / name).resolve()
        if file_path.parent != STATIC_DIR or not file_path.is_file():
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }
        content_type = content_types.get(
            file_path.suffix.lower(), "application/octet-stream"
        )
        self._send_bytes(file_path.read_bytes(), content_type)

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _send_bytes(
        self, body: bytes, content_type: str, status: int = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


class DashboardServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the dashboard state."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: Any,
        state: DashboardState,
        *,
        auth_token: Optional[str] = None,
    ) -> None:
        self.state = state
        self.auth_token = auth_token or None
        super().__init__(address, DashboardRequestHandler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local web dashboard for the SocialMediaBot phone farm."
    )
    parser.add_argument(
        "--manifest",
        default="rack-01.json",
        help="Rack JSON manifest; dibuat lewat UI jika belum ada (default: rack-01.json)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=8080, help="HTTP port")
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("GADS_DASHBOARD_TOKEN"),
        help=(
            "Token login dashboard; wajib jika --host bukan loopback "
            "(default: $GADS_DASHBOARD_TOKEN)"
        ),
    )
    parser.add_argument(
        "--appium-host", help="Override the Appium host defined in the manifest"
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="Parallel workers for batch jobs"
    )
    parser.add_argument(
        "--stream-interval",
        type=float,
        default=1.0,
        help="Screenshot interval (seconds) for live screen streams",
    )
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("GADS_CLIENT_SECRET"),
        help="gads:clientSecret capability (default: $GADS_CLIENT_SECRET)",
    )
    parser.add_argument(
        "--connect",
        action="store_true",
        help="Queue a connect job immediately after startup",
    )
    return parser


def _local_ipv4_addresses() -> List[str]:
    """Best-effort list of LAN IPv4 addresses for the startup message."""

    addresses = set()
    try:
        for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET
        ):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(
        address
        for address in addresses
        if address and address != "0.0.0.0" and not address.startswith("127.")
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    if args.host not in LOOPBACK_HOSTS and not args.auth_token:
        print(
            "ERROR: --host non-loopback memerlukan --auth-token "
            "atau env GADS_DASHBOARD_TOKEN.",
            file=sys.stderr,
        )
        return 2
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    log = logging.getLogger(LOGGER_NAME)

    try:
        rack = load_rack(args.manifest)
    except FileNotFoundError:
        manifest_path = Path(args.manifest)
        print(
            f"Manifest {args.manifest} belum ada; dashboard mulai dalam mode setup. "
            "Gunakan Setup rack → Scan ADB → Simpan & terapkan."
        )
        rack = Rack(
            name=manifest_path.stem or "rack-01",
            devices=[],
            host=None,
            allow_empty=True,
        )
    except (OSError, ValueError) as exc:
        print(f"Manifest tidak valid: {exc}")
        return 2

    def make_bot(host: str) -> SocialMediaBot:
        return SocialMediaBot(
            host=host,
            client_secret=args.client_secret,
            max_workers=args.workers,
        )

    bot = make_bot(args.appium_host or rack.host or "127.0.0.1")
    state = DashboardState(
        bot,
        rack,
        max_workers=args.workers,
        stream_interval=args.stream_interval,
        manifest_path=args.manifest,
        bot_factory=make_bot,
    )

    try:
        server = DashboardServer(
            (args.host, args.port), state, auth_token=args.auth_token
        )
    except OSError as exc:
        print(f"Tidak bisa membuka {args.host}:{args.port} - {exc}")
        state.shutdown()
        return 2

    display_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    print(f"Dashboard ready: http://{display_host}:{args.port}")
    if args.auth_token:
        print("Auth: token login diperlukan.")
    if args.host == "0.0.0.0":
        for address in _local_ipv4_addresses():
            print(f"LAN: http://{address}:{args.port}")
    if args.host not in LOOPBACK_HOSTS:
        print(
            "PERINGATAN: dashboard terbuka ke jaringan; gunakan HTTPS/SSH "
            "tunnel karena token dikirim melalui HTTP."
        )

    if args.connect and len(rack):
        state.submit_connect()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard...")
    finally:
        server.server_close()
        state.shutdown()
        closed = state.bot.disconnect_all()
        if closed:
            print(f"{closed} session ditutup")
        log.info("Dashboard stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
