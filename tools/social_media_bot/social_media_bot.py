"""Multi-device Appium skeleton for social-media automation.

Each Android device is expected to be reachable through its own Appium server
instance, i.e. one host/port pair per device (4723, 4724, 4725, ...). The bot
keeps the live ``webdriver.Remote`` sessions in a thread-safe registry keyed by
device UDID so several devices can be driven in parallel.

Typical usage::

    bot = SocialMediaBot(host="127.0.0.1")
    bot.connect_devices({
        "R58M1234ABC": 4723,
        "R58M5678DEF": 4724,
    })
    try:
        driver = bot.get_driver("R58M1234ABC")
        ...
    finally:
        bot.disconnect_all()
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Tuple,
    Union,
)

try:  # Support both package and direct-module execution.
    from .errors import (
        AppiumNotInstalledError,
        DeviceConnectionError,
        DeviceNotConnectedError,
        SocialMediaBotError,
    )
    from .facebook import (
        FACEBOOK_PACKAGE,
        CommentResult,
        FacebookAutomationError,
        FollowResult,
        PostResult,
        comment_on_post,
        create_post,
        follow_user,
    )
    from .platforms import (
        FacebookPlatform,
        InstagramPlatform,
        PlatformAdapter,
        PlatformInfo,
        ThreadsPlatform,
        TikTokPlatform,
        XPlatform,
        default_platforms,
        platform_catalog,
    )
    from .rack import DeviceSpec, Rack, load_rack
except ImportError:  # pragma: no cover - depends on how the module is loaded
    from errors import (  # type: ignore[no-redef]
        AppiumNotInstalledError,
        DeviceConnectionError,
        DeviceNotConnectedError,
        SocialMediaBotError,
    )
    from facebook import (  # type: ignore[no-redef]
        FACEBOOK_PACKAGE,
        CommentResult,
        FacebookAutomationError,
        FollowResult,
        PostResult,
        comment_on_post,
        create_post,
        follow_user,
    )
    from platforms import (  # type: ignore[no-redef]
        FacebookPlatform,
        InstagramPlatform,
        PlatformAdapter,
        PlatformInfo,
        ThreadsPlatform,
        TikTokPlatform,
        XPlatform,
        default_platforms,
        platform_catalog,
    )
    from rack import DeviceSpec, Rack, load_rack  # type: ignore[no-redef]

try:  # Keep the module importable (e.g. for unit tests) without the client.
    from appium import webdriver
    from appium.options.android import UiAutomator2Options

    _APPIUM_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:  # pragma: no cover - depends on the environment
    webdriver = None  # type: ignore[assignment]
    UiAutomator2Options = None  # type: ignore[assignment]
    _APPIUM_IMPORT_ERROR = exc

__all__ = [
    "AppiumNotInstalledError",
    "DeviceConnection",
    "DeviceConnectionError",
    "DeviceNotConnectedError",
    "DeviceSpec",
    "DeviceTaskResult",
    "FACEBOOK_PACKAGE",
    "FacebookAutomationError",
    "FollowResult",
    "PostResult",
    "CommentResult",
    "Rack",
    "SocialMediaBot",
    "SocialMediaBotError",
    "ThreadsPlatform",
    "TikTokPlatform",
    "XPlatform",
    "comment_on_post",
    "create_post",
    "default_platforms",
    "follow_user",
    "load_rack",
    "platform_catalog",
]

Driver = Any


@dataclass
class DeviceConnection:
    """Bookkeeping for one live Appium session."""

    udid: str
    appium_port: int
    server_url: str
    driver: Driver
    capabilities: Dict[str, Any]
    implicit_wait: float = 0.0

    @property
    def session_id(self) -> Optional[str]:
        """Appium session id reported by the driver, if any."""
        return getattr(self.driver, "session_id", None)


@dataclass
class DeviceTaskResult:
    """Outcome of one action executed by :meth:`SocialMediaBot.run_on_devices`."""

    udid: str
    result: Any = None
    error: Optional[BaseException] = None

    @property
    def ok(self) -> bool:
        """``True`` when the action completed without raising."""
        return self.error is None


class SocialMediaBot:
    """Owns one Appium session per Android device.

    The class only handles connection lifecycle. Social-media flows
    (login, posting, scrolling, ...) are built on top of the drivers returned
    by :meth:`connect_device` / :meth:`get_driver`.
    """

    DEFAULT_PLATFORM_NAME = "Android"
    DEFAULT_AUTOMATION_NAME = "UiAutomator2"
    DEFAULT_NEW_COMMAND_TIMEOUT = 300
    DEFAULT_MAX_WORKERS = 8

    def __init__(
        self,
        host: str = "127.0.0.1",
        *,
        scheme: str = "http",
        client_secret: Optional[str] = None,
        platform_name: str = DEFAULT_PLATFORM_NAME,
        automation_name: str = DEFAULT_AUTOMATION_NAME,
        implicit_wait: float = 0.0,
        new_command_timeout: int = DEFAULT_NEW_COMMAND_TIMEOUT,
        max_workers: int = DEFAULT_MAX_WORKERS,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Create the bot.

        Args:
            host: Appium host shared by all devices.
            scheme: ``http`` (default) or ``https``.
            client_secret: Optional ``gads:clientSecret`` capability used by
                GADS per-device Appium endpoints. Never logged.
            platform_name: Default ``platformName`` capability.
            automation_name: Default ``appium:automationName`` capability.
            implicit_wait: Default implicit wait (seconds) applied after connect.
            new_command_timeout: Default ``appium:newCommandTimeout`` (seconds).
            max_workers: Default parallelism for batch operations. Tune it to
                the rack size and Appium host capacity (8 is a safe start for
                20 phones).
            logger: Optional logger; defaults to a module-level logger.
        """
        normalized_host = host.strip() if isinstance(host, str) else ""
        if not normalized_host:
            raise ValueError("host must be a non-empty string")

        normalized_scheme = scheme.strip().lower() if isinstance(scheme, str) else ""
        if normalized_scheme not in {"http", "https"}:
            raise ValueError(f"scheme must be 'http' or 'https', got {scheme!r}")

        if int(new_command_timeout) < 0:
            raise ValueError("new_command_timeout must be >= 0")

        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise ValueError(
                f"max_workers must be an int, got {type(max_workers).__name__}"
            )
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")

        self._host = normalized_host
        self._scheme = normalized_scheme
        self._client_secret = client_secret
        self._platform_name = platform_name
        self._automation_name = automation_name
        self._implicit_wait = float(implicit_wait)
        self._new_command_timeout = int(new_command_timeout)
        self._max_workers = max_workers
        self._logger = logger or logging.getLogger(__name__)

        # udid -> DeviceConnection, guarded by _lock. Drivers live here so the
        # bot can reuse them across calls and shut them down in disconnect_all.
        self._devices: Dict[str, DeviceConnection] = {}
        self._lock = threading.RLock()
        # udid -> lock serialising connect/reconnect/disconnect for one device.
        # Lets different phones open sessions in parallel without racing.
        self._device_locks: Dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------
    @property
    def device_ids(self) -> Tuple[str, ...]:
        """UDIDs of every currently connected device."""
        with self._lock:
            return tuple(self._devices)

    @property
    def host(self) -> str:
        """Appium host used by this bot."""
        return self._host

    @property
    def connections(self) -> Dict[str, DeviceConnection]:
        """Snapshot of the registry (``udid -> DeviceConnection``)."""
        with self._lock:
            return dict(self._devices)

    @property
    def drivers(self) -> Dict[str, Driver]:
        """Snapshot of the live drivers (``udid -> Appium driver``)."""
        with self._lock:
            return {udid: conn.driver for udid, conn in self._devices.items()}

    def is_connected(self, udid: str) -> bool:
        """Return ``True`` when *udid* has an active session."""
        with self._lock:
            return udid in self._devices

    def get_driver(self, udid: str) -> Driver:
        """Return the driver for *udid*.

        Raises:
            DeviceNotConnectedError: If the device has no active session.
        """
        with self._lock:
            connection = self._devices.get(udid)
        if connection is None:
            raise DeviceNotConnectedError(udid)
        return connection.driver

    def get_connection(self, udid: str) -> DeviceConnection:
        """Return the :class:`DeviceConnection` bookkeeping for *udid*."""
        with self._lock:
            connection = self._devices.get(udid)
        if connection is None:
            raise DeviceNotConnectedError(udid)
        return connection

    def __len__(self) -> int:
        with self._lock:
            return len(self._devices)

    def __contains__(self, udid: object) -> bool:
        with self._lock:
            return udid in self._devices

    def __iter__(self) -> Iterator[str]:
        return iter(self.device_ids)

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} host={self._host!r} "
            f"connected={len(self)} devices={list(self.device_ids)!r}>"
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect_device(
        self,
        udid: str,
        appium_port: int,
        *,
        platform_version: Optional[str] = None,
        device_name: Optional[str] = None,
        app_package: Optional[str] = None,
        app_activity: Optional[str] = None,
        no_reset: bool = True,
        new_command_timeout: Optional[int] = None,
        extra_capabilities: Optional[Mapping[str, Any]] = None,
        server_path: Optional[str] = None,
        implicit_wait: Optional[float] = None,
    ) -> Driver:
        """Open an Appium session for one device and remember it.

        Connecting the same *udid* twice closes the previous session first, so
        no orphan session is left behind on the Appium server.

        Args:
            udid: Device UDID (``adb devices`` / GADS device list).
            appium_port: Port of the Appium server dedicated to this device.
            platform_version: Optional ``appium:platformVersion`` capability.
            device_name: Optional ``appium:deviceName``; defaults to *udid*.
            app_package: Optional ``appium:appPackage`` to launch.
            app_activity: Optional ``appium:appActivity`` to launch.
            no_reset: ``appium:noReset``; ``True`` keeps app data between runs.
            new_command_timeout: Override ``appium:newCommandTimeout`` (seconds).
            extra_capabilities: Extra W3C capabilities merged last (overrides).
            server_path: Optional URL path prefix, e.g. ``/device/<id>/appium``
                for GADS per-device endpoints or ``/wd/hub`` for Appium 1.x.
            implicit_wait: Override the instance default implicit wait (seconds).

        Returns:
            The created Appium driver.

        Raises:
            ValueError: Invalid *udid*, port or timeout.
            AppiumNotInstalledError: Appium-Python-Client is unavailable.
            DeviceConnectionError: The session could not be created.
        """
        clean_udid = self._validate_udid(udid)
        port = self._validate_port(appium_port)
        server_url = self._build_server_url(port, server_path)
        capabilities = self._build_capabilities(
            clean_udid,
            platform_version=platform_version,
            device_name=device_name,
            app_package=app_package,
            app_activity=app_activity,
            no_reset=no_reset,
            new_command_timeout=new_command_timeout,
            extra_capabilities=extra_capabilities,
        )

        with self._lock_for(clean_udid):
            return self._open_session(
                clean_udid, port, server_url, capabilities, implicit_wait
            )

    def connect_devices(
        self,
        devices: Union[
            Mapping[str, int],
            Iterable[Tuple[str, int]],
            Iterable[DeviceSpec],
            Rack,
        ],
        *,
        continue_on_error: bool = False,
        max_workers: Optional[int] = None,
        **connect_kwargs: Any,
    ) -> Dict[str, Driver]:
        """Connect several devices in one call, optionally in parallel.

        Args:
            devices: A :class:`~rack.Rack`, a mapping ``udid -> appium_port``,
                an iterable of ``(udid, appium_port)`` pairs, or an iterable of
                :class:`~rack.DeviceSpec`. Per-device options from a rack entry
                are merged over *connect_kwargs*.
            continue_on_error: When ``True`` failing devices are logged and
                skipped. When ``False`` the first failure (in rack order) is
                raised after the batch finishes; sessions created successfully
                stay registered.
            max_workers: Parallel workers for this call. ``1`` keeps the batch
                sequential; ``None`` uses the instance default.
            **connect_kwargs: Forwarded to :meth:`connect_device`.

        Returns:
            ``udid -> driver`` for every device connected successfully, in the
            same order as the input.
        """
        items = self._normalize_devices(devices)
        if not items:
            return {}

        workers = self._resolve_workers(max_workers, len(items))
        order = {udid: index for index, (udid, _, _) in enumerate(items)}

        if workers == 1:
            connected: Dict[str, Driver] = {}
            for udid, appium_port, options in items:
                try:
                    connected[udid] = self.connect_device(
                        udid, appium_port, **{**connect_kwargs, **options}
                    )
                except DeviceConnectionError as exc:
                    if not continue_on_error:
                        raise
                    self._logger.error(
                        "Skipping device %s after a connection failure: %s",
                        udid,
                        exc,
                    )
            return connected

        drivers: Dict[str, Driver] = {}
        failures: Dict[str, Exception] = {}
        self._logger.info(
            "Connecting %d device(s) with %d worker(s)", len(items), workers
        )
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="smb-connect"
        ) as pool:
            futures = {
                pool.submit(
                    self.connect_device,
                    udid,
                    appium_port,
                    **{**connect_kwargs, **options},
                ): udid
                for udid, appium_port, options in items
            }
            for future in as_completed(futures):
                udid = futures[future]
                try:
                    drivers[udid] = future.result()
                except Exception as exc:  # noqa: BLE001 - reported below
                    failures[udid] = exc

        # Keep the registry in rack order so device_ids is deterministic for
        # callers even though sessions finish in arbitrary order.
        with self._lock:
            reordered = {
                udid: self._devices[udid]
                for udid, _, _ in items
                if udid in self._devices
            }
            for udid, connection in self._devices.items():
                reordered.setdefault(udid, connection)
            self._devices = reordered

        for udid in sorted(failures, key=order.__getitem__):
            error = failures[udid]
            # Missing dependencies / bad arguments are not per-device issues:
            # surface them even when continue_on_error is set.
            if isinstance(error, (AppiumNotInstalledError, ValueError)):
                raise error
            if not continue_on_error:
                raise error
            self._logger.error(
                "Skipping device %s after a connection failure: %s",
                udid,
                error,
            )
        return {
            udid: drivers[udid] for udid in sorted(drivers, key=order.__getitem__)
        }

    def disconnect_device(self, udid: str, *, ignore_missing: bool = False) -> bool:
        """Close the session for one device and drop it from the registry.

        Args:
            udid: Device to disconnect.
            ignore_missing: Return ``False`` instead of raising when the device
                is not connected.

        Returns:
            ``True`` when a session was closed cleanly.
        """
        with self._lock_for(udid):
            with self._lock:
                connection = self._devices.pop(udid, None)

            if connection is None:
                if ignore_missing:
                    self._logger.debug(
                        "Device %s is not connected; nothing to close", udid
                    )
                    return False
                raise DeviceNotConnectedError(udid)

            closed = self._quit_driver(udid, connection.driver)
            if closed:
                self._logger.info(
                    "Disconnected %s (session %s)", udid, connection.session_id
                )
            return closed

    def disconnect_all(self) -> int:
        """Close every active session.

        Failures are logged and do not stop the remaining disconnections, so a
        crashed device never leaks the sessions of the other devices. Safe to
        call multiple times.

        Returns:
            Number of sessions closed successfully.
        """
        with self._lock:
            connections = list(self._devices.values())
            self._devices.clear()

        if not connections:
            self._logger.debug("No active Appium sessions to disconnect")
            return 0

        closed = 0
        for connection in connections:
            with self._lock_for(connection.udid):
                if self._quit_driver(connection.udid, connection.driver):
                    closed += 1

        self._logger.info("Disconnected %d of %d device(s)", closed, len(connections))
        return closed

    def reconnect_device(
        self,
        udid: str,
        *,
        attempts: int = 3,
        delay: float = 2.0,
    ) -> Driver:
        """Replace a dropped session using the stored URL/capabilities.

        The current driver is closed first, then up to *attempts* sessions are
        created with *delay* seconds between tries. Useful when a phone
        occasionally drops off ADB during a long farm run.

        Args:
            udid: Device to reconnect.
            attempts: Maximum number of session creation attempts.
            delay: Seconds to wait between attempts.

        Returns:
            The new driver.

        Raises:
            ValueError: Invalid *attempts* or *delay*.
            DeviceNotConnectedError: The device has no stored connection.
            DeviceConnectionError: Every attempt failed; the device stays
                disconnected and must be re-added with :meth:`connect_device`.
        """
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("attempts must be an int >= 1")
        if delay < 0:
            raise ValueError("delay must be >= 0")

        with self._lock_for(udid):
            with self._lock:
                connection = self._devices.pop(udid, None)
            if connection is None:
                raise DeviceNotConnectedError(udid)

            self._logger.warning("Reconnecting %s on %s", udid, connection.server_url)
            self._quit_driver(udid, connection.driver)

            last_error: Optional[Exception] = None
            for attempt in range(1, attempts + 1):
                try:
                    driver = self._create_driver(
                        connection.server_url, connection.capabilities
                    )
                except Exception as exc:  # noqa: BLE001 - retried below
                    last_error = exc
                    if attempt < attempts:
                        self._logger.warning(
                            "Reconnect %s failed (attempt %d/%d): %s",
                            udid,
                            attempt,
                            attempts,
                            exc,
                        )
                        time.sleep(delay)
                    continue

                if connection.implicit_wait > 0:
                    try:
                        driver.implicitly_wait(connection.implicit_wait)
                    except Exception:
                        self._quit_driver(udid, driver)
                        raise

                new_connection = DeviceConnection(
                    udid=connection.udid,
                    appium_port=connection.appium_port,
                    server_url=connection.server_url,
                    driver=driver,
                    capabilities=connection.capabilities,
                    implicit_wait=connection.implicit_wait,
                )
                with self._lock:
                    self._devices[udid] = new_connection
                self._logger.info(
                    "Reconnected %s (session %s)", udid, new_connection.session_id
                )
                return driver

            assert last_error is not None
            raise DeviceConnectionError(
                f"Could not reconnect {udid!r} after {attempts} attempt(s): {last_error}"
            ) from last_error

    def run_on_devices(
        self,
        action: Callable[[Driver, str], Any],
        udids: Optional[Iterable[str]] = None,
        *,
        max_workers: Optional[int] = None,
        raise_on_error: bool = False,
    ) -> Dict[str, DeviceTaskResult]:
        """Run *action* on several devices in parallel.

        Typical rack usage::

            results = bot.run_on_devices(
                lambda driver, udid: driver.activate_app("com.instagram.android")
            )
            failed = [udid for udid, result in results.items() if not result.ok]

        Args:
            action: Callable receiving ``(driver, udid)``.
            udids: Devices to target; defaults to every connected device.
            max_workers: Parallel workers; ``None`` uses the instance default.
            raise_on_error: Re-raise the first error (in input order) after all
                devices finished instead of only reporting it in the results.

        Returns:
            ``udid -> DeviceTaskResult`` in input order, holding either the
            return value or the exception raised for that device.
        """
        if not callable(action):
            raise TypeError("action must be callable")

        targets = list(self.device_ids if udids is None else udids)
        if not targets:
            return {}
        for udid in targets:
            if not self.is_connected(udid):
                raise DeviceNotConnectedError(udid)

        workers = self._resolve_workers(max_workers, len(targets))
        results: Dict[str, DeviceTaskResult] = {}

        def _run(udid: str) -> DeviceTaskResult:
            try:
                return DeviceTaskResult(
                    udid, result=action(self.get_driver(udid), udid)
                )
            except Exception as exc:  # noqa: BLE001 - collected per device
                return DeviceTaskResult(udid, error=exc)

        if workers == 1:
            for udid in targets:
                results[udid] = _run(udid)
        else:
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="smb-task"
            ) as pool:
                futures = {pool.submit(_run, udid): udid for udid in targets}
                for future in as_completed(futures):
                    results[futures[future]] = future.result()

        ordered = {udid: results[udid] for udid in targets}
        if raise_on_error:
            for udid in targets:
                error = ordered[udid].error
                if error is not None:
                    raise error
        return ordered

    def close(self) -> int:
        """Alias for :meth:`disconnect_all`."""
        return self.disconnect_all()

    def __enter__(self) -> "SocialMediaBot":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.disconnect_all()
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _open_session(
        self,
        udid: str,
        port: int,
        server_url: str,
        capabilities: Dict[str, Any],
        implicit_wait: Optional[float],
    ) -> Driver:
        """Create the session; caller must hold the per-device lock."""
        with self._lock:
            previous = self._devices.pop(udid, None)
        if previous is not None:
            self._logger.warning(
                "Device %s already had a session; closing it before reconnecting",
                udid,
            )
            self._quit_driver(udid, previous.driver)

        self._logger.info("Creating Appium session for %s on %s", udid, server_url)
        driver = self._create_driver(server_url, capabilities)

        wait = self._implicit_wait if implicit_wait is None else float(implicit_wait)
        if wait > 0:
            try:
                driver.implicitly_wait(wait)
            except Exception:
                self._quit_driver(udid, driver)
                raise

        connection = DeviceConnection(
            udid=udid,
            appium_port=port,
            server_url=server_url,
            driver=driver,
            capabilities=capabilities,
            implicit_wait=wait,
        )
        with self._lock:
            self._devices[udid] = connection

        self._logger.info(
            "Connected %s on port %d (session %s)",
            udid,
            port,
            connection.session_id,
        )
        return driver

    def _lock_for(self, udid: str) -> threading.Lock:
        """Return the lock serialising one device's session lifecycle."""
        with self._lock:
            lock = self._device_locks.get(udid)
            if lock is None:
                lock = threading.Lock()
                self._device_locks[udid] = lock
            return lock

    def _create_driver(self, server_url: str, capabilities: Dict[str, Any]) -> Driver:
        """Create the ``webdriver.Remote`` session (patched in unit tests)."""
        if webdriver is None:
            raise AppiumNotInstalledError(
                "Appium-Python-Client is not installed. Install it with "
                "`pip install -r requirements.txt`."
            ) from _APPIUM_IMPORT_ERROR

        try:
            if UiAutomator2Options is not None:
                options = UiAutomator2Options().load_capabilities(dict(capabilities))
                return webdriver.Remote(command_executor=server_url, options=options)
            # Fallback for Appium-Python-Client < 2.0.
            return webdriver.Remote(
                command_executor=server_url,
                desired_capabilities=dict(capabilities),
            )
        except Exception as exc:
            raise DeviceConnectionError(
                f"Could not create an Appium session at {server_url}: {exc}"
            ) from exc

    def _quit_driver(self, udid: str, driver: Driver) -> bool:
        """Quit one driver, logging instead of raising on failure."""
        try:
            driver.quit()
        except Exception:
            self._logger.exception("Failed to close the Appium session for %s", udid)
            return False
        return True

    def _build_server_url(self, appium_port: int, server_path: Optional[str]) -> str:
        path = ""
        if server_path:
            trimmed = server_path.strip()
            if trimmed:
                path = trimmed if trimmed.startswith("/") else f"/{trimmed}"
                path = path.rstrip("/")
        return f"{self._scheme}://{self._host}:{appium_port}{path}"

    def _build_capabilities(
        self,
        udid: str,
        *,
        platform_version: Optional[str],
        device_name: Optional[str],
        app_package: Optional[str],
        app_activity: Optional[str],
        no_reset: bool,
        new_command_timeout: Optional[int],
        extra_capabilities: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        timeout = (
            self._new_command_timeout
            if new_command_timeout is None
            else int(new_command_timeout)
        )
        if timeout < 0:
            raise ValueError("new_command_timeout must be >= 0")

        capabilities: Dict[str, Any] = {
            "platformName": self._platform_name,
            "appium:automationName": self._automation_name,
            "appium:udid": udid,
            "appium:deviceName": device_name or udid,
            "appium:noReset": bool(no_reset),
            "appium:newCommandTimeout": timeout,
        }
        if platform_version:
            capabilities["appium:platformVersion"] = str(platform_version)
        if app_package:
            capabilities["appium:appPackage"] = app_package
        if app_activity:
            capabilities["appium:appActivity"] = app_activity
        if self._client_secret:
            capabilities["gads:clientSecret"] = self._client_secret
        if extra_capabilities:
            capabilities.update(extra_capabilities)
        return capabilities

    def _resolve_workers(self, max_workers: Optional[int], count: int) -> int:
        """Pick the worker count for one batch, clamped to the batch size."""
        workers = self._max_workers if max_workers is None else max_workers
        if isinstance(workers, bool) or not isinstance(workers, int):
            raise ValueError(
                f"max_workers must be an int, got {type(workers).__name__}"
            )
        if workers < 1:
            raise ValueError("max_workers must be >= 1")
        return min(count, workers)

    @staticmethod
    def _validate_udid(udid: str) -> str:
        if not isinstance(udid, str) or not udid.strip():
            raise ValueError("udid must be a non-empty string")
        return udid.strip()

    @staticmethod
    def _validate_port(appium_port: int) -> int:
        if isinstance(appium_port, bool) or not isinstance(appium_port, int):
            raise ValueError(
                f"appium_port must be an int, got {type(appium_port).__name__}"
            )
        if not 1 <= appium_port <= 65535:
            raise ValueError(f"appium_port must be between 1 and 65535, got {appium_port}")
        return appium_port

    @staticmethod
    def _normalize_devices(
        devices: Union[
            Mapping[str, int],
            Iterable[Tuple[str, int]],
            Iterable[DeviceSpec],
            Rack,
        ],
    ) -> Iterable[Tuple[str, int, Dict[str, Any]]]:
        if isinstance(devices, Rack):
            devices = devices.devices
        if isinstance(devices, Mapping):
            return [(udid, port, {}) for udid, port in devices.items()]

        items: list = []
        for device in devices:
            if isinstance(device, DeviceSpec):
                items.append(
                    (device.udid, device.appium_port, dict(device.connect_options))
                )
            else:
                udid, port = device
                items.append((udid, port, {}))
        return items
