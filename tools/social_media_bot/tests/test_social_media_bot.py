"""Unit tests for the multi-device Appium skeleton.

Run from the repository root:

    python3 -m unittest discover -s tools/social_media_bot/tests -v
"""

import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_media_bot as smb  # noqa: E402
from social_media_bot import (  # noqa: E402
    AppiumNotInstalledError,
    DeviceConnectionError,
    DeviceNotConnectedError,
    SocialMediaBot,
)

REAL_CREATE_DRIVER = SocialMediaBot._create_driver


class FakeDriver:
    """Minimal stand-in for an Appium ``WebDriver``."""

    _sequence = 0

    def __init__(self, session_id=None):
        FakeDriver._sequence += 1
        self.session_id = session_id or f"session-{FakeDriver._sequence}"
        self.quit_calls = 0
        self.implicit_wait = None

    def quit(self):
        self.quit_calls += 1

    def implicitly_wait(self, seconds):
        self.implicit_wait = seconds


class FakeOptions:
    """Stand-in for ``UiAutomator2Options``."""

    def __init__(self):
        self.caps = None

    def load_capabilities(self, caps):
        self.caps = caps
        return self


class FakeWebdriver:
    """Stand-in for the ``appium.webdriver`` module."""

    def __init__(self):
        self.calls = []

    def Remote(self, **kwargs):  # noqa: N802 - mirrors the real API
        self.calls.append(kwargs)
        driver = FakeDriver()
        driver.options = kwargs.get("options")
        return driver


class SocialMediaBotTests(unittest.TestCase):
    def setUp(self):
        self.created = []
        self._factory_patcher = mock.patch.object(
            SocialMediaBot, "_create_driver", new=self._fake_create
        )
        self._factory_patcher.start()
        self.addCleanup(self._factory_patcher.stop)
        self.bot = SocialMediaBot(host="127.0.0.1")

    def _fake_create(self, server_url, capabilities):
        driver = FakeDriver()
        driver.server_url = server_url
        driver.capabilities = capabilities
        self.created.append(driver)
        return driver

    # -- connect_device -------------------------------------------------
    def test_connect_device_creates_session_and_tracks_it(self):
        driver = self.bot.connect_device("device-1", 4723)

        self.assertIs(driver, self.created[0])
        self.assertEqual(self.bot.device_ids, ("device-1",))
        self.assertIn("device-1", self.bot)
        self.assertTrue(self.bot.is_connected("device-1"))
        self.assertIs(self.bot.get_driver("device-1"), driver)
        self.assertEqual(driver.server_url, "http://127.0.0.1:4723")

        connection = self.bot.get_connection("device-1")
        self.assertEqual(connection.appium_port, 4723)
        self.assertEqual(connection.session_id, driver.session_id)
        self.assertEqual(connection.capabilities["platformName"], "Android")
        self.assertEqual(connection.capabilities["appium:automationName"], "UiAutomator2")
        self.assertEqual(connection.capabilities["appium:udid"], "device-1")
        self.assertEqual(connection.capabilities["appium:deviceName"], "device-1")
        self.assertTrue(connection.capabilities["appium:noReset"])
        self.assertEqual(connection.capabilities["appium:newCommandTimeout"], 300)
        self.assertNotIn("gads:clientSecret", connection.capabilities)

    def test_connect_device_merges_optional_capabilities(self):
        bot = SocialMediaBot(client_secret="s3cr3t")
        bot.connect_device(
            "device-2",
            4724,
            platform_version="14",
            app_package="com.instagram.android",
            app_activity=".activity.MainTabActivity",
            new_command_timeout=120,
            extra_capabilities={"appium:noReset": False, "appium:fullReset": True},
            server_path="device/abc/appium/",
        )

        connection = bot.get_connection("device-2")
        caps = connection.capabilities
        self.assertEqual(connection.server_url, "http://127.0.0.1:4724/device/abc/appium")
        self.assertEqual(caps["appium:platformVersion"], "14")
        self.assertEqual(caps["appium:appPackage"], "com.instagram.android")
        self.assertEqual(caps["appium:appActivity"], ".activity.MainTabActivity")
        self.assertEqual(caps["appium:newCommandTimeout"], 120)
        self.assertFalse(caps["appium:noReset"])
        self.assertTrue(caps["appium:fullReset"])
        self.assertEqual(caps["gads:clientSecret"], "s3cr3t")

    def test_connect_device_reconnects_and_closes_previous_session(self):
        first = self.bot.connect_device("device-1", 4723)
        second = self.bot.connect_device("device-1", 4724)

        self.assertEqual(first.quit_calls, 1)
        self.assertEqual(len(self.bot), 1)
        self.assertIs(self.bot.get_driver("device-1"), second)

    def test_connect_device_applies_implicit_wait(self):
        driver = self.bot.connect_device("device-1", 4723, implicit_wait=0.5)
        self.assertEqual(driver.implicit_wait, 0.5)

    def test_invalid_arguments_are_rejected(self):
        invalid_calls = [
            ("", 4723),
            ("   ", 4723),
            ("device-1", 0),
            ("device-1", 70000),
            ("device-1", "4723"),
            ("device-1", True),
        ]
        for udid, port in invalid_calls:
            with self.subTest(udid=udid, port=port):
                with self.assertRaises(ValueError):
                    self.bot.connect_device(udid, port)
        self.assertEqual(len(self.bot), 0)

    # -- multi-device ---------------------------------------------------
    def test_connect_devices_keeps_every_session(self):
        devices = {"d1": 4723, "d2": 4724, "d3": 4725, "d4": 4726}
        connected = self.bot.connect_devices(devices)

        self.assertEqual(set(connected), set(devices))
        self.assertEqual(len(self.bot), 4)
        self.assertEqual(
            [self.bot.get_connection(udid).server_url for udid in devices],
            [f"http://127.0.0.1:{port}" for port in devices.values()],
        )
        self.assertEqual(len({driver.session_id for driver in connected.values()}), 4)

    def test_connect_devices_accepts_iterable_of_pairs(self):
        self.bot.connect_devices([("d1", 4723), ("d2", 4724)])
        self.assertEqual(self.bot.device_ids, ("d1", "d2"))

    def test_connect_devices_raises_on_first_error_by_default(self):
        def flaky(_bot, server_url, capabilities):
            if server_url.endswith(":4724"):
                raise DeviceConnectionError("boom")
            return self._fake_create(server_url, capabilities)

        with mock.patch.object(SocialMediaBot, "_create_driver", new=flaky):
            with self.assertRaises(DeviceConnectionError):
                self.bot.connect_devices(
                    {"d1": 4723, "d2": 4724, "d3": 4725}, max_workers=1
                )

        self.assertEqual(self.bot.device_ids, ("d1",))

    def test_connect_devices_continue_on_error_skips_failed_device(self):
        def flaky(_bot, server_url, capabilities):
            if server_url.endswith(":4724"):
                raise DeviceConnectionError("boom")
            return self._fake_create(server_url, capabilities)

        with mock.patch.object(SocialMediaBot, "_create_driver", new=flaky):
            with self.assertLogs(smb.__name__, level="ERROR"):
                connected = self.bot.connect_devices(
                    {"d1": 4723, "d2": 4724}, continue_on_error=True
                )

        self.assertEqual(list(connected), ["d1"])
        self.assertEqual(self.bot.device_ids, ("d1",))

    def test_connect_devices_parallel_handles_a_20_phone_rack(self):
        active = 0
        max_active = 0
        counter_lock = threading.Lock()

        def slow_create(_bot, server_url, capabilities):
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with counter_lock:
                active -= 1
            return self._fake_create(server_url, capabilities)

        devices = {f"phone-{index:02d}": 4723 + index for index in range(20)}
        with mock.patch.object(SocialMediaBot, "_create_driver", new=slow_create):
            connected = self.bot.connect_devices(devices, max_workers=8)

        self.assertEqual(list(connected), list(devices))
        self.assertEqual(len(self.bot), 20)
        self.assertEqual(self.bot.device_ids, tuple(devices))
        self.assertGreater(max_active, 1)
        self.assertEqual(
            [self.bot.get_connection(udid).appium_port for udid in devices],
            [4723 + index for index in range(20)],
        )

    def test_connect_devices_parallel_collects_failures(self):
        def flaky(_bot, server_url, capabilities):
            if server_url.endswith(":4724"):
                raise DeviceConnectionError("boom")
            return self._fake_create(server_url, capabilities)

        with mock.patch.object(SocialMediaBot, "_create_driver", new=flaky):
            with self.assertLogs(smb.__name__, level="ERROR"):
                connected = self.bot.connect_devices(
                    {"d1": 4723, "d2": 4724, "d3": 4725},
                    continue_on_error=True,
                    max_workers=3,
                )

        self.assertEqual(list(connected), ["d1", "d3"])
        self.assertEqual(set(self.bot.device_ids), {"d1", "d3"})

    def test_connect_devices_parallel_surfaces_missing_appium(self):
        def no_appium(_bot, server_url, capabilities):
            raise AppiumNotInstalledError("not installed")

        with mock.patch.object(SocialMediaBot, "_create_driver", new=no_appium):
            with self.assertRaises(AppiumNotInstalledError):
                self.bot.connect_devices(
                    {"d1": 4723, "d2": 4724},
                    continue_on_error=True,
                    max_workers=2,
                )

        self.assertEqual(len(self.bot), 0)

    def test_connect_devices_raises_first_failure_in_rack_order(self):
        def flaky(_bot, server_url, capabilities):
            if server_url.endswith((":4724", ":4725")):
                raise DeviceConnectionError(f"boom {server_url}")
            return self._fake_create(server_url, capabilities)

        with mock.patch.object(SocialMediaBot, "_create_driver", new=flaky):
            with self.assertRaises(DeviceConnectionError) as ctx:
                self.bot.connect_devices(
                    {"d1": 4723, "d2": 4724, "d3": 4725}, max_workers=3
                )

        self.assertIn(":4724", str(ctx.exception))

    def test_connect_devices_accepts_rack_with_per_device_options(self):
        rack = smb.Rack(
            name="rack-01",
            devices=[
                smb.DeviceSpec("d1", 4723, {"device_name": "slot-01"}),
                smb.DeviceSpec("d2", 4724, {"app_package": "com.instagram.android"}),
            ],
        )

        connected = self.bot.connect_devices(rack, max_workers=1)

        self.assertEqual(list(connected), ["d1", "d2"])
        caps_d1 = self.bot.get_connection("d1").capabilities
        caps_d2 = self.bot.get_connection("d2").capabilities
        self.assertEqual(caps_d1["appium:deviceName"], "slot-01")
        self.assertEqual(caps_d2["appium:appPackage"], "com.instagram.android")

    def test_concurrent_connect_for_same_udid_is_serialized(self):
        def slow_create(_bot, server_url, capabilities):
            time.sleep(0.02)
            return self._fake_create(server_url, capabilities)

        with mock.patch.object(SocialMediaBot, "_create_driver", new=slow_create):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(self.bot.connect_device, "d1", 4723),
                    pool.submit(self.bot.connect_device, "d1", 4724),
                ]
                for future in futures:
                    future.result()

        self.assertEqual(len(self.bot), 1)
        self.assertEqual(len(self.created), 2)
        self.assertEqual(sum(driver.quit_calls for driver in self.created), 1)

    # -- batch actions & recovery ---------------------------------------
    def test_run_on_devices_collects_results_and_errors(self):
        self.bot.connect_devices({f"d{index}": 4723 + index for index in range(5)})

        def action(driver, udid):
            if udid == "d2":
                raise RuntimeError("tap failed")
            return driver.session_id

        results = self.bot.run_on_devices(
            action, [f"d{index}" for index in range(5)], max_workers=3
        )

        self.assertEqual(list(results), ["d0", "d1", "d2", "d3", "d4"])
        self.assertTrue(results["d0"].ok)
        self.assertEqual(
            results["d0"].result, self.bot.get_driver("d0").session_id
        )
        self.assertFalse(results["d2"].ok)
        self.assertIsInstance(results["d2"].error, RuntimeError)
        self.assertEqual(
            [udid for udid, result in results.items() if result.ok],
            ["d0", "d1", "d3", "d4"],
        )

    def test_run_on_devices_can_raise_first_error(self):
        self.bot.connect_device("d1", 4723)

        def action(driver, udid):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.bot.run_on_devices(action, raise_on_error=True)

    def test_run_on_devices_rejects_unknown_device(self):
        with self.assertRaises(DeviceNotConnectedError):
            self.bot.run_on_devices(lambda driver, udid: None, ["ghost"])

    def test_reconnect_device_replaces_driver(self):
        old_driver = self.bot.connect_device("d1", 4723)

        new_driver = self.bot.reconnect_device("d1", attempts=2, delay=0)

        self.assertEqual(old_driver.quit_calls, 1)
        self.assertIsNot(old_driver, new_driver)
        self.assertIs(self.bot.get_driver("d1"), new_driver)
        self.assertEqual(new_driver.server_url, "http://127.0.0.1:4723")
        self.assertEqual(new_driver.capabilities["appium:udid"], "d1")
        self.assertEqual(self.bot.get_connection("d1").appium_port, 4723)

    def test_reconnect_device_raises_after_all_attempts(self):
        self.bot.connect_device("d1", 4723)

        def always_fail(_bot, server_url, capabilities):
            raise DeviceConnectionError("server down")

        with mock.patch.object(SocialMediaBot, "_create_driver", new=always_fail):
            with self.assertRaises(DeviceConnectionError):
                self.bot.reconnect_device("d1", attempts=2, delay=0)

        self.assertFalse(self.bot.is_connected("d1"))

    def test_reconnect_device_unknown_device_raises(self):
        with self.assertRaises(DeviceNotConnectedError):
            self.bot.reconnect_device("ghost")

    # -- disconnect -----------------------------------------------------
    def test_disconnect_all_closes_every_session_and_clears_registry(self):
        drivers = self.bot.connect_devices({"d1": 4723, "d2": 4724, "d3": 4725})

        self.assertEqual(self.bot.disconnect_all(), 3)
        self.assertEqual(len(self.bot), 0)
        self.assertEqual(self.bot.device_ids, ())
        self.assertTrue(all(driver.quit_calls == 1 for driver in drivers.values()))
        self.assertEqual(self.bot.disconnect_all(), 0)

    def test_disconnect_all_continues_after_a_failed_quit(self):
        drivers = self.bot.connect_devices({"d1": 4723, "d2": 4724, "d3": 4725})
        drivers["d2"].quit = mock.Mock(side_effect=RuntimeError("session gone"))

        with self.assertLogs(smb.__name__, level="ERROR"):
            closed = self.bot.disconnect_all()

        self.assertEqual(closed, 2)
        self.assertEqual(len(self.bot), 0)
        self.assertEqual(drivers["d1"].quit_calls, 1)
        self.assertEqual(drivers["d3"].quit_calls, 1)

    def test_disconnect_device_removes_one_session(self):
        self.bot.connect_devices({"d1": 4723, "d2": 4724})

        self.assertTrue(self.bot.disconnect_device("d2"))
        self.assertEqual(self.bot.device_ids, ("d1",))
        with self.assertRaises(DeviceNotConnectedError):
            self.bot.disconnect_device("d2")
        self.assertFalse(self.bot.disconnect_device("d2", ignore_missing=True))

    def test_get_driver_unknown_device_raises(self):
        with self.assertRaises(DeviceNotConnectedError):
            self.bot.get_driver("ghost")

    def test_context_manager_disconnects_all(self):
        with SocialMediaBot() as bot:
            bot.connect_devices({"d1": 4723, "d2": 4724})
            drivers = bot.drivers
            self.assertEqual(len(bot), 2)

        self.assertEqual(len(bot), 0)
        self.assertTrue(all(driver.quit_calls == 1 for driver in drivers.values()))

    def test_repr_mentions_connected_devices(self):
        self.bot.connect_device("d1", 4723)
        self.assertIn("connected=1", repr(self.bot))
        self.assertIn("d1", repr(self.bot))

    # -- real driver creation -------------------------------------------
    def test_create_driver_uses_w3c_options(self):
        fake_webdriver = FakeWebdriver()

        with mock.patch.object(SocialMediaBot, "_create_driver", new=REAL_CREATE_DRIVER):
            with mock.patch.object(smb, "webdriver", fake_webdriver):
                with mock.patch.object(smb, "UiAutomator2Options", FakeOptions):
                    self.bot.connect_device("d1", 4723)

        self.assertEqual(len(fake_webdriver.calls), 1)
        call = fake_webdriver.calls[0]
        self.assertEqual(call["command_executor"], "http://127.0.0.1:4723")
        self.assertNotIn("desired_capabilities", call)
        self.assertIsInstance(call["options"], FakeOptions)
        self.assertEqual(call["options"].caps["appium:udid"], "d1")

    def test_missing_appium_client_raises_helpful_error(self):
        with mock.patch.object(SocialMediaBot, "_create_driver", new=REAL_CREATE_DRIVER):
            with mock.patch.object(smb, "webdriver", None):
                with mock.patch.object(smb, "_APPIUM_IMPORT_ERROR", ImportError("missing")):
                    with self.assertRaises(AppiumNotInstalledError) as ctx:
                        self.bot.connect_device("d1", 4723)

        self.assertIn("Appium-Python-Client is not installed", str(ctx.exception))
        self.assertEqual(len(self.bot), 0)

    def test_remote_failure_is_wrapped_in_device_connection_error(self):
        class ExplodingWebdriver:
            @staticmethod
            def Remote(**kwargs):  # noqa: N802 - mirrors the real API
                raise RuntimeError("cannot reach server")

        with mock.patch.object(SocialMediaBot, "_create_driver", new=REAL_CREATE_DRIVER):
            with mock.patch.object(smb, "webdriver", ExplodingWebdriver):
                with mock.patch.object(smb, "UiAutomator2Options", FakeOptions):
                    with self.assertRaises(DeviceConnectionError) as ctx:
                        self.bot.connect_device("d1", 4723)

        self.assertIn("http://127.0.0.1:4723", str(ctx.exception))
        self.assertEqual(len(self.bot), 0)


@unittest.skipIf(smb.webdriver is None, "Appium-Python-Client is not installed")
class RealAppiumOptionsTests(unittest.TestCase):
    """Guards the skeleton against API changes in Appium-Python-Client."""

    def test_capabilities_round_trip_through_uiautomator2_options(self):
        bot = SocialMediaBot(client_secret="top-secret")
        capabilities = bot._build_capabilities(
            "device-1",
            platform_version="14",
            device_name=None,
            app_package="com.instagram.android",
            app_activity=None,
            no_reset=True,
            new_command_timeout=90,
            extra_capabilities=None,
        )

        options = smb.UiAutomator2Options().load_capabilities(capabilities)
        serialized = options.to_capabilities()

        self.assertEqual(serialized["platformName"], "Android")
        self.assertEqual(serialized["appium:automationName"], "UiAutomator2")
        self.assertEqual(serialized["appium:udid"], "device-1")
        self.assertEqual(serialized["appium:newCommandTimeout"], 90)
        self.assertEqual(serialized["gads:clientSecret"], "top-secret")


if __name__ == "__main__":
    unittest.main()
