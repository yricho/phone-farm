"""Tests for the dashboard state, job queue and HTTP API."""

import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dashboard  # noqa: E402
from rack import DeviceSpec, Rack, load_rack  # noqa: E402
from social_media_bot import DeviceNotConnectedError, DeviceTaskResult  # noqa: E402


def make_rack(count=4, name="rack-test"):
    return Rack(
        name=name,
        host="10.0.0.5",
        devices=[
            DeviceSpec(
                f"d{index}",
                4723 + index,
                {"device_name": f"slot-{index}"},
            )
            for index in range(count)
        ],
    )


class FakeScreenDriver:
    """Minimal driver exposing Appium's screenshot + input APIs."""

    def __init__(self, frame=b"\x89PNG\r\n\x1a\nfake-frame"):
        self.frame = frame
        self.calls = 0
        self.scripts = []
        self.taps = []
        self.swipes = []
        self.keys = []

    def get_screenshot_as_png(self):
        self.calls += 1
        return self.frame

    def get_window_size(self):
        return {"width": 1080, "height": 2400}

    def execute_script(self, script, args=None):
        self.scripts.append((script, args))
        if script == "mobile: clickGesture":
            self.taps.append(args)
        return None

    def tap(self, coordinates):
        self.taps.append({"tap": coordinates})

    def swipe(self, start_x, start_y, end_x, end_y, duration):
        self.swipes.append((start_x, start_y, end_x, end_y, duration))

    def press_keycode(self, keycode):
        self.keys.append(keycode)


class FakeBot:
    """In-memory stand-in for SocialMediaBot."""

    def __init__(self, rack=None, driver_factory=None):
        self._connections = {}
        self.connect_calls = []
        self.run_calls = []
        self.disconnect_calls = 0
        self.last_connect_failures = {}
        self.driver_factory = driver_factory or (lambda udid: object())
        if rack is not None:
            self.connect_devices(rack)

    @property
    def connections(self):
        return dict(self._connections)

    @property
    def device_ids(self):
        return tuple(self._connections)

    def connect_devices(self, rack, *, continue_on_error=False, max_workers=None):
        self.connect_calls.append(rack)
        connected = {}
        self.last_connect_failures = {}
        for spec in rack.devices:
            if spec.udid == "broken":
                self.last_connect_failures[spec.udid] = "session boom"
                continue
            self._connections[spec.udid] = SimpleNamespace(
                session_id=f"session-{spec.udid}",
                server_url=f"http://{rack.host}:{spec.appium_port}",
            )
            connected[spec.udid] = SimpleNamespace(session_id=f"session-{spec.udid}")
        return connected

    def disconnect_all(self):
        self.disconnect_calls += 1
        count = len(self._connections)
        self._connections.clear()
        return count

    def run_on_devices(
        self, action, udids=None, *, max_workers=None, raise_on_error=False
    ):
        targets = list(udids if udids is not None else self._connections)
        self.run_calls.append((targets, max_workers))
        results = {}
        for udid in targets:
            try:
                results[udid] = DeviceTaskResult(
                    udid, result=action(object(), udid)
                )
            except Exception as exc:  # noqa: BLE001 - collected per device
                results[udid] = DeviceTaskResult(udid, error=exc)
        return results

    def get_driver(self, udid):
        if udid not in self._connections:
            raise DeviceNotConnectedError(udid)
        return self.driver_factory(udid)


def fake_actions():
    return {
        "follow": lambda driver, username: {
            "username": username,
            "status": "followed",
        },
        "post": lambda driver, text: {"post_text": text, "status": "posted"},
        "comment": lambda driver, url, text: {
            "target_url": url,
            "comment_text": text,
            "status": "commented",
        },
    }


def wait_job(state, job_id, timeout=3.0, statuses=("succeeded", "failed")):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = state.get_job(job_id)
        if job is not None and job["status"] in statuses:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish: {state.get_job(job_id)}")


class DashboardStateTests(unittest.TestCase):
    def setUp(self):
        self.rack = make_rack()
        self.bot = FakeBot()
        self.state = dashboard.DashboardState(
            self.bot, self.rack, max_workers=2, actions=fake_actions()
        )
        self.addCleanup(self.state.shutdown)

    def test_status_reports_devices_and_summary(self):
        snapshot = self.state.status()

        self.assertEqual(snapshot["rack"]["name"], "rack-test")
        self.assertEqual(snapshot["summary"]["total"], 4)
        self.assertEqual(snapshot["summary"]["connected"], 0)
        self.assertEqual(snapshot["devices"][0]["slot"], "slot-0")
        self.assertFalse(snapshot["devices"][0]["connected"])

        self.bot.connect_devices(self.rack)
        snapshot = self.state.status()

        self.assertEqual(snapshot["summary"]["connected"], 4)
        self.assertTrue(snapshot["devices"][0]["connected"])
        self.assertEqual(snapshot["devices"][0]["session_id"], "session-d0")

    def test_connect_job_connects_the_rack(self):
        job = self.state.submit_connect()
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["connected"], ["d0", "d1", "d2", "d3"])
        self.assertEqual(done["result"]["failed"], [])
        self.assertEqual(len(self.bot.connect_calls), 1)
        self.assertEqual(self.state.status()["summary"]["connected"], 4)

    def test_connect_job_reports_failure_reasons(self):
        rack = Rack(
            name="rack-test",
            host="10.0.0.5",
            devices=[DeviceSpec("broken", 4725, {"device_name": "slot-x"})],
        )
        state = dashboard.DashboardState(
            self.bot, rack, max_workers=2, actions=fake_actions()
        )
        self.addCleanup(state.shutdown)

        job = state.submit_connect()
        done = wait_job(state, job["id"])

        self.assertEqual(done["result"]["failed"], ["broken"])
        self.assertEqual(done["result"]["errors"], {"broken": "session boom"})
        self.assertIn("broken (session boom)", done["message"])
        self.assertEqual(done["status"], "failed")

    def test_connect_job_can_target_a_subset(self):
        job = self.state.submit_connect(["d1", "d3"])
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["result"]["connected"], ["d1", "d3"])
        self.assertEqual(len(self.bot.connect_calls[0]), 2)
        self.assertEqual(self.state.status()["summary"]["connected"], 2)

    def test_connect_job_rejects_unknown_device(self):
        with self.assertRaises(ValueError):
            self.state.submit_connect(["ghost"])

    def test_disconnect_job(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_disconnect()
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["closed"], 4)
        self.assertEqual(self.state.status()["summary"]["connected"], 0)

    def test_follow_action_on_all_devices(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_action(
            "follow", "all", {"username": "target.account"}
        )
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["ok"], 4)
        self.assertEqual(done["result"]["failed"], 0)
        self.assertIn("Follow @target.account", done["label"])
        self.assertEqual(self.bot.run_calls[0][0], ["d0", "d1", "d2", "d3"])

    def test_post_action_on_one_device(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_action("post", "d1", {"text": "Halo"})
        done = wait_job(self.state, job["id"])

        self.assertEqual(list(done["result"]["devices"]), ["d1"])
        self.assertEqual(
            done["result"]["devices"]["d1"]["result"]["post_text"], "Halo"
        )
        self.assertEqual(self.bot.run_calls, [])

    def test_action_requires_connected_devices(self):
        job = self.state.submit_action("follow", "all", {"username": "x"})
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "failed")
        self.assertIn("No connected devices", done["message"])

    def test_status_exposes_platform_catalog(self):
        state = dashboard.DashboardState(self.bot, self.rack)
        self.addCleanup(state.shutdown)

        snapshot = state.status()
        keys = [platform["key"] for platform in snapshot["platforms"]]

        self.assertEqual(
            keys, ["facebook", "instagram", "threads", "x", "tiktok"]
        )
        self.assertEqual(snapshot["default_platform"], "facebook")
        instagram = next(
            platform
            for platform in snapshot["platforms"]
            if platform["key"] == "instagram"
        )
        self.assertNotIn("post", instagram["actions"])
        self.assertTrue(instagram["notes"])

    def test_action_dispatch_uses_the_selected_platform(self):
        calls = []
        facebook = dashboard._LegacyPlatform(fake_actions())
        instagram = dashboard._LegacyPlatform(
            {
                "follow": lambda driver, username: calls.append(username)
                or {"username": username}
            },
            key="instagram",
            label="Instagram",
        )
        state = dashboard.DashboardState(
            self.bot,
            self.rack,
            platforms={"facebook": facebook, "instagram": instagram},
        )
        self.addCleanup(state.shutdown)
        self.bot.connect_devices(self.rack)

        job = state.submit_action(
            "follow", "all", {"username": "target"}, platform="instagram"
        )
        done = wait_job(state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(calls, ["target"] * 4)
        self.assertIn("Instagram", done["label"])

        with self.assertRaises(ValueError):
            state.submit_action(
                "post", "all", {"text": "x"}, platform="instagram"
            )
        with self.assertRaises(ValueError):
            state.submit_action(
                "follow", "all", {"username": "x"}, platform="ghost"
            )

    def test_bulk_follow_runs_every_item(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_bulk("follow", ["a", "b", "c"])
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["progress"]["done"], 3)
        self.assertEqual(done["result"]["ok"], 12)
        self.assertEqual(done["result"]["failed"], 0)
        self.assertEqual(len(done["result"]["items"]), 3)
        self.assertIn("3/3 item(s)", done["message"])
        self.assertEqual(len(self.bot.run_calls), 3)

    def test_bulk_strips_utf8_bom_from_first_item(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_bulk("follow", ["\ufeffalpha", "beta"])
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        usernames = [
            item["params"]["username"] for item in done["result"]["items"]
        ]
        self.assertEqual(usernames, ["alpha", "beta"])

    def test_bulk_comment_parses_url_and_text(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_bulk(
            "comment",
            ["https://x.test/p/1 | Halo", "https://x.test/p/2 | Hai"],
        )
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["result"]["ok"], 8)
        self.assertEqual(
            done["result"]["items"][0]["params"],
            {"url": "https://x.test/p/1", "text": "Halo"},
        )

    def test_bulk_validation(self):
        cases = [
            ("follow", []),
            ("comment", ["no pipe"]),
        ]
        for action, items in cases:
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    self.state.submit_bulk(action, items)

        for kwargs in (
            {"interval": -1},
            {"retries": 99},
            {"target": "ghost"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.state.submit_bulk("follow", ["a"], **kwargs)

        state = dashboard.DashboardState(self.bot, self.rack)
        self.addCleanup(state.shutdown)
        with self.assertRaises(ValueError):
            state.submit_bulk("post", ["Hello"], platform="instagram")

    def test_bulk_requires_connected_devices(self):
        job = self.state.submit_bulk("follow", ["a"])
        done = wait_job(self.state, job["id"])

        self.assertEqual(done["status"], "failed")
        self.assertIn("No connected devices", done["message"])

    def test_bulk_retry_requeues_only_failed_items(self):
        def flaky(driver, username):
            if username == "bad":
                raise RuntimeError("boom")
            return {"username": username}

        platforms = {
            "facebook": dashboard._LegacyPlatform(
                {"follow": flaky}, key="facebook", label="Facebook"
            )
        }
        state = dashboard.DashboardState(
            self.bot, self.rack, platforms=platforms
        )
        self.addCleanup(state.shutdown)
        self.bot.connect_devices(self.rack)

        job = state.submit_bulk("follow", ["good", "bad"])
        done = wait_job(state, job["id"])
        self.assertEqual(done["result"]["ok"], 4)
        self.assertEqual(done["result"]["failed"], 4)

        retry = state.retry_job(job["id"])
        self.assertEqual(
            [item["username"] for item in retry["payload"]["items"]], ["bad"]
        )

        retried = wait_job(state, retry["id"])
        self.assertEqual(retried["progress"]["total"], 1)
        self.assertEqual(len(retried["result"]["items"]), 1)

    def test_cancel_bulk_job(self):
        self.bot.connect_devices(self.rack)

        job = self.state.submit_bulk("follow", ["a", "b", "c"], interval=2.0)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            current = self.state.get_job(job["id"])
            progress = current["progress"] if current else None
            if progress and progress["done"] >= 1:
                break
            time.sleep(0.01)

        self.assertTrue(self.state.cancel_job(job["id"]))
        done = wait_job(
            self.state, job["id"], statuses=("cancelled",), timeout=3.0
        )

        self.assertEqual(done["status"], "cancelled")
        self.assertIn("Cancelled", done["message"])
        self.assertLess(done["progress"]["done"], 3)

    def test_cancel_rejects_non_bulk_and_unknown_jobs(self):
        job = self.state.submit_action("follow", "all", {"username": "a"})

        with self.assertRaises(ValueError):
            self.state.cancel_job(job["id"])
        with self.assertRaises(ValueError):
            self.state.cancel_job("ghost")

    def test_action_reports_per_device_failures(self):
        self.bot.connect_devices(self.rack)
        calls = {"count": 0}

        def flaky(driver, username):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("boom")
            return {"username": username}

        actions = dict(fake_actions(), follow=flaky)
        state = dashboard.DashboardState(self.bot, self.rack, actions=actions)
        self.addCleanup(state.shutdown)

        job = state.submit_action("follow", "all", {"username": "x"})
        done = wait_job(state, job["id"])

        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["ok"], 3)
        self.assertEqual(done["result"]["failed"], 1)
        failures = [
            udid for udid, value in done["result"]["devices"].items() if not value["ok"]
        ]
        self.assertEqual(len(failures), 1)
        self.assertIn("boom", done["result"]["devices"][failures[0]]["error"])

    def test_action_validation(self):
        cases = [
            ("nope", "all", {"username": "x"}),
            ("follow", "all", {}),
            ("follow", "all", {"username": "  "}),
            ("follow", "all", {"username": "x", "text": "y"}),
            ("follow", "ghost", {"username": "x"}),
        ]
        for action, target, params in cases:
            with self.subTest(action=action, target=target, params=params):
                with self.assertRaises(ValueError):
                    self.state.submit_action(action, target, params)

        with self.assertRaises(ValueError):
            self.state.submit_action("follow", "all", None)

    def test_logs_are_captured_and_filterable(self):
        logging.getLogger(dashboard.LOGGER_NAME).info("hello dashboard")

        payload = self.state.logs()

        self.assertTrue(
            any("hello dashboard" in entry["message"] for entry in payload["logs"])
        )
        self.assertEqual(self.state.logs(since=payload["last_id"])["logs"], [])

    def test_shutdown_is_idempotent(self):
        self.state.shutdown()
        self.state.shutdown()

        self.assertFalse(self.state._worker.is_alive())


class RackConfigTests(unittest.TestCase):
    def setUp(self):
        self.rack = make_rack(2)
        self.bot = FakeBot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest = Path(self.tmp.name) / "rack.json"
        self.manifest.write_text(
            json.dumps({"notes": "keep-me"}), encoding="utf-8"
        )
        self.state = dashboard.DashboardState(
            self.bot,
            self.rack,
            max_workers=2,
            actions=fake_actions(),
            manifest_path=self.manifest,
        )
        self.addCleanup(self.state.shutdown)

    def test_config_exposes_editable_devices(self):
        config = self.state.config()

        self.assertEqual(config["rack"]["name"], "rack-test")
        self.assertEqual(config["rack"]["host"], "10.0.0.5")
        self.assertEqual(len(config["devices"]), 2)
        self.assertEqual(config["devices"][0]["slot"], "slot-0")
        self.assertEqual(config["devices"][0]["appium_port"], 4723)
        self.assertEqual(config["devices"][0]["udid"], "d0")

    def test_empty_setup_rack_can_be_configured(self):
        empty = Rack(
            name="rack-setup", devices=[], host=None, allow_empty=True
        )
        state = dashboard.DashboardState(
            self.bot,
            empty,
            max_workers=1,
            actions=fake_actions(),
            manifest_path=self.manifest,
        )
        self.addCleanup(state.shutdown)

        self.assertEqual(state.config()["devices"], [])
        state.save_config(
            {
                "rack": "rack-setup",
                "host": "127.0.0.1",
                "devices": [
                    {
                        "udid": "FIRST",
                        "appium_port": 4723,
                        "device_name": "slot-01",
                    }
                ],
            }
        )

        self.assertEqual(state.rack.device_map, {"FIRST": 4723})
        self.assertEqual(load_rack(self.manifest).device_map, {"FIRST": 4723})

    def test_save_config_updates_rack_and_manifest(self):
        result = self.state.save_config(
            {
                "rack": "rack-new",
                "host": "10.0.0.5",
                "devices": [
                    {
                        "udid": "d0",
                        "appium_port": 4730,
                        "device_name": "slot-a",
                    },
                    {
                        "udid": "new-phone",
                        "appium_port": 4731,
                        "device_name": "slot-b",
                    },
                ],
            }
        )

        self.assertEqual(result["rack"]["name"], "rack-new")
        self.assertEqual(len(result["devices"]), 2)
        self.assertEqual(self.state.rack.name, "rack-new")
        self.assertEqual(
            self.state.rack.device_map,
            {"d0": 4730, "new-phone": 4731},
        )
        self.assertEqual(
            self.state.rack.devices[0].connect_options["device_name"],
            "slot-a",
        )

        saved = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(saved["notes"], "keep-me")
        self.assertEqual(saved["devices"][1]["udid"], "new-phone")
        self.assertEqual(load_rack(self.manifest).name, "rack-new")

    def test_save_config_rejects_connected_devices(self):
        self.bot.connect_devices(self.rack)

        with self.assertRaises(ValueError) as ctx:
            self.state.save_config(
                {
                    "rack": "rack-test",
                    "host": "10.0.0.5",
                    "devices": [
                        {"udid": "d0", "appium_port": 4723}
                    ],
                }
            )

        self.assertIn("Disconnect all", str(ctx.exception))

    def test_save_config_can_switch_appium_host(self):
        created = []

        def factory(host):
            created.append(host)
            return FakeBot()

        state = dashboard.DashboardState(
            self.bot,
            self.rack,
            manifest_path=self.manifest,
            bot_factory=factory,
        )
        self.addCleanup(state.shutdown)

        state.save_config(
            {
                "rack": "rack-test",
                "host": "127.0.0.1",
                "devices": [{"udid": "d0", "appium_port": 4723}],
            }
        )

        self.assertEqual(created, ["127.0.0.1"])
        self.assertIsNot(state.bot, self.bot)
        self.assertEqual(state.rack.host, "127.0.0.1")

    def test_scan_adb_reports_ready_and_skipped(self):
        output = (
            "List of devices attached\n"
            "PHONE1\tdevice product:a model:b device:c\n"
            "PHONE2\tunauthorized\n"
        )
        with mock.patch.object(dashboard, "run_adb", return_value=output):
            result = self.state.scan_adb()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["devices"], [{"udid": "PHONE1", "status": "device"}])
        self.assertEqual(
            result["skipped"], [{"udid": "PHONE2", "status": "unauthorized"}]
        )


class SchedulerCampaignTests(unittest.TestCase):
    def setUp(self):
        self.rack = make_rack(2)
        self.bot = FakeBot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest = Path(self.tmp.name) / "rack.json"
        self.state = dashboard.DashboardState(
            self.bot,
            self.rack,
            max_workers=2,
            actions=fake_actions(),
            manifest_path=self.manifest,
        )
        self.addCleanup(self.state.shutdown)

    def test_action_can_be_scheduled(self):
        self.bot.connect_devices(self.rack)
        job = self.state.submit_action(
            "follow",
            "all",
            {"username": "scheduled"},
            schedule_at=time.time() + 2.0,
        )
        self.assertEqual(job["status"], "scheduled")
        done = wait_job(self.state, job["id"], timeout=5.0)
        self.assertEqual(done["status"], "succeeded")
        self.assertIsNotNone(done["started_at"])

    def test_scheduled_job_persists_across_state_restart(self):
        job = self.state.submit_action(
            "follow",
            "all",
            {"username": "persisted"},
            schedule_at=time.time() + 30,
        )
        self.state.shutdown()

        restored = dashboard.DashboardState(
            self.bot,
            self.rack,
            max_workers=2,
            actions=fake_actions(),
            manifest_path=self.manifest,
        )
        self.addCleanup(restored.shutdown)
        loaded = restored.get_job(job["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "scheduled")
        self.assertTrue(restored.cancel_job(job["id"]))
        self.assertEqual(
            restored.get_job(job["id"])["status"], "cancelled"
        )

    def test_scheduled_job_can_be_cancelled(self):
        job = self.state.submit_action(
            "follow",
            "all",
            {"username": "later"},
            schedule_at=time.time() + 30,
        )
        self.assertTrue(self.state.cancel_job(job["id"]))
        self.assertEqual(
            self.state.get_job(job["id"])["status"], "cancelled"
        )

    def test_daily_limits_and_analytics(self):
        self.bot.connect_devices(self.rack)
        self.state.save_limits({"follow": 1})

        first = self.state.submit_action(
            "follow", "all", {"username": "one"}
        )
        first_done = wait_job(self.state, first["id"])
        self.assertEqual(first_done["result"]["failed"], 0)
        self.assertEqual(first_done["result"]["ok"], 2)

        second = self.state.submit_action(
            "follow", "all", {"username": "two"}
        )
        second_done = wait_job(self.state, second["id"])
        self.assertEqual(second_done["result"]["ok"], 0)
        self.assertEqual(second_done["result"]["failed"], 2)

        snapshot = self.state.automation_snapshot()
        self.assertEqual(snapshot["limits"]["follow"], 1)
        self.assertEqual(snapshot["totals"]["ok"], 2)
        self.assertEqual(snapshot["totals"]["failed"], 2)

    def test_health_check_reports_connected_devices(self):
        self.bot.connect_devices(self.rack)
        job = self.state.submit_health()
        done = wait_job(self.state, job["id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["ok"], 2)
        self.assertEqual(done["result"]["failed"], 0)

    def test_campaign_posts_to_each_platform(self):
        self.bot.connect_devices(self.rack)
        job = self.state.submit_campaign("Halo campaign", ["facebook"])
        done = wait_job(self.state, job["id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["ok"], 2)
        self.assertEqual(len(done["result"]["steps"]), 1)
        self.assertEqual(done["result"]["steps"][0]["platform"], "facebook")




class ScreenStreamManagerTests(unittest.TestCase):
    def setUp(self):
        self.rack = make_rack(2)
        self.driver = FakeScreenDriver()
        self.bot = FakeBot(self.rack, driver_factory=lambda udid: self.driver)

    def test_frames_are_shared_between_subscribers(self):
        manager = dashboard.ScreenStreamManager(self.bot, default_interval=0.01)
        self.addCleanup(manager.close_all)

        stream = manager.acquire("d0", 0.01)
        frames = stream.frames()
        first = next(frames)
        second = next(frames)

        self.assertTrue(first.startswith(b"\x89PNG"))
        self.assertGreaterEqual(self.driver.calls, 2)

        shared = manager.acquire("d0", 0.01)
        self.assertIs(shared, stream)
        manager.release(shared)
        manager.release(stream)

        self.assertTrue(stream.closed)

    def test_acquire_rejects_offline_device(self):
        manager = dashboard.ScreenStreamManager(self.bot)
        self.addCleanup(manager.close_all)

        with self.assertRaises(DeviceNotConnectedError):
            manager.acquire("ghost")

    def test_interval_is_clamped(self):
        manager = dashboard.ScreenStreamManager(self.bot, default_interval=1.0)

        self.assertEqual(manager.clamp_interval(None), 1.0)
        self.assertEqual(manager.clamp_interval("bad"), 1.0)
        self.assertEqual(manager.clamp_interval(0.01), 0.3)
        self.assertEqual(manager.clamp_interval(99), 10.0)

    def test_stream_closes_after_repeated_screenshot_failures(self):
        class BrokenDriver:
            def get_screenshot_as_png(self):
                raise RuntimeError("device gone")

        bot = FakeBot(self.rack, driver_factory=lambda udid: BrokenDriver())
        manager = dashboard.ScreenStreamManager(bot, default_interval=0.01)
        self.addCleanup(manager.close_all)

        stream = manager.acquire("d0", 0.01)
        deadline = time.time() + 2.0
        while not stream.closed and time.time() < deadline:
            time.sleep(0.01)

        self.assertTrue(stream.closed)
        manager.release(stream)


class DashboardHttpTests(unittest.TestCase):
    def setUp(self):
        self.rack = make_rack(2)
        self.screen_driver = FakeScreenDriver()
        self.bot = FakeBot(
            driver_factory=lambda udid: self.screen_driver
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest = Path(self.tmp.name) / "rack.json"
        self.state = dashboard.DashboardState(
            self.bot,
            self.rack,
            max_workers=2,
            actions=fake_actions(),
            manifest_path=self.manifest,
        )
        self.server = dashboard.DashboardServer(("127.0.0.1", 0), self.state)
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="dashboard-test", daemon=True
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.server.shutdown()
        self.server.server_close()
        self.state.shutdown()
        self.thread.join(timeout=2.0)

    def request(self, path, method="GET", payload=None, raw=None):
        if raw is not None:
            data = raw
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
        else:
            data = None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.read().decode("utf-8")
            finally:
                error.close()

    def request_json(self, path, method="GET", payload=None, raw=None):
        status, body = self.request(path, method, payload, raw)
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, {"body": body}

    def test_index_and_static_assets(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Phone Farm", body)
        self.assertIn("/static/app.js", body)
        self.assertIn('id="screen-panel"', body)
        self.assertIn('id="screen-panel-img"', body)
        self.assertIn('id="live-device-list"', body)
        self.assertIn('id="live-strip-next"', body)
        self.assertIn('id="screen-size"', body)
        self.assertIn('value="actual"', body)
        self.assertIn('id="setup-scan"', body)
        self.assertIn('id="setup-save"', body)
        self.assertIn('id="setup-devices"', body)
        self.assertIn('id="campaign-text"', body)
        self.assertIn('id="accordion-automation"', body)
        self.assertIn('id="limit-follow"', body)
        self.assertIn('id="health-check"', body)
        self.assertIn('id="notify-enable"', body)
        self.assertIn('id="btn-theme"', body)
        self.assertIn('name="theme-color"', body)
        self.assertIn("dataset.theme", body)
        self.assertIn('class="workspace"', body)
        self.assertIn('class="stats"', body)
        self.assertIn('aria-label="Ringkasan" hidden', body)
        self.assertIn('id="accordion-action"', body)
        self.assertIn('id="accordion-jobs"', body)
        self.assertIn('id="accordion-activity"', body)
        self.assertIn('id="control-accordion"', body)
        self.assertEqual(body.count('class="accordion-glyph"'), 6)
        self.assertIn('class="icon-sprite"', body)
        self.assertGreaterEqual(body.count('class="btn-icon"'), 20)
        self.assertNotIn('id="devices-grid"', body)
        self.assertNotIn('id="device-search"', body)
        self.assertNotIn('id="btn-live"', body)
        self.assertNotIn('id="screen-modal"', body)
        self.assertNotIn('modal-backdrop', body)

        status, script = self.request("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("/api/status", script)
        self.assertIn("screen-panel", script)
        self.assertIn("renderLiveStrip", script)
        self.assertIn("applyScreenSize", script)
        self.assertIn("scanSetup", script)
        self.assertIn("renderCampaignPlatforms", script)
        self.assertIn("maybeNotify", script)
        self.assertIn("applyTheme", script)
        self.assertIn("gads.theme", script)
        self.assertIn("closeAccordion", script)
        self.assertIn("setButtonLabel", script)
        self.assertIn("icon(\"x\")", script)
        self.assertIn("\\uFEFF", script)

        status, styles = self.request("/static/styles.css")
        self.assertEqual(status, 200)
        self.assertIn("--bg", styles)
        self.assertIn('html[data-theme="light"]', styles)
        self.assertIn("--surface-input", styles)
        self.assertIn(".accordion-glyph", styles)
        self.assertIn(".btn-icon", styles)
        self.assertIn("[hidden]", styles)
        self.assertIn("display: none !important", styles)
        self.assertNotIn(".devices-grid", styles)
        self.assertIn('[data-screen-size="actual"]', styles)

        status, _body = self.request("/static/missing.js")
        self.assertEqual(status, 404)

    def test_status_endpoint(self):
        status, payload = self.request_json("/api/status")

        self.assertEqual(status, 200)
        self.assertEqual(payload["rack"]["name"], "rack-test")
        self.assertEqual(payload["summary"]["total"], 2)
        self.assertEqual(len(payload["devices"]), 2)

    def test_screenshot_endpoint(self):
        status, body = self.request_json("/api/devices/d0/screenshot")
        self.assertEqual(status, 409)
        self.assertIn("not connected", body["error"])

        self.bot.connect_devices(self.rack)
        with urllib.request.urlopen(
            self.base + "/api/devices/d0/screenshot", timeout=3
        ) as response:
            frame = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(frame, self.screen_driver.frame)

    def test_stream_endpoint(self):
        status, body = self.request_json("/api/devices/d0/stream")
        self.assertEqual(status, 409)
        self.assertIn("not connected", body["error"])

        self.bot.connect_devices(self.rack)
        response = urllib.request.urlopen(
            self.base + "/api/devices/d0/stream?interval=0.05", timeout=3
        )
        try:
            self.assertEqual(response.status, 200)
            self.assertEqual(
                response.headers.get_content_type(),
                "multipart/x-mixed-replace",
            )
            chunk = response.read(128)
            self.assertIn(b"--frame", chunk)
            self.assertIn(b"Content-Type: image/png", chunk)
        finally:
            response.close()

        deadline = time.time() + 2.0
        while self.state.screen_streams._streams and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.state.screen_streams._streams, {})

    def test_input_tap_swipe_text_and_key(self):
        self.bot.connect_devices(self.rack)

        status, body = self.request_json(
            "/api/devices/d0/input",
            "POST",
            {"type": "tap", "x": 0.5, "y": 0.25},
        )
        self.assertEqual(status, 200)
        self.assertEqual((body["x"], body["y"]), (540, 600))
        self.assertEqual(self.screen_driver.taps[-1], {"x": 540, "y": 600})

        status, _body = self.request_json(
            "/api/devices/d0/input",
            "POST",
            {
                "type": "swipe",
                "from": {"x": 0.5, "y": 0.8},
                "to": {"x": 0.5, "y": 0.2},
                "duration": 300,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.screen_driver.swipes[-1], (540, 1919, 540, 480, 300)
        )

        status, _body = self.request_json(
            "/api/devices/d0/input",
            "POST",
            {"type": "text", "text": "Halo"},
        )
        self.assertEqual(status, 200)
        self.assertIn(
            ("mobile: type", {"text": "Halo"}), self.screen_driver.scripts
        )

        status, _body = self.request_json(
            "/api/devices/d0/input",
            "POST",
            {"type": "key", "key": "back"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.screen_driver.keys[-1], 4)

    def test_input_validation_and_offline_device(self):
        status, body = self.request_json(
            "/api/devices/d0/input", "POST", {"type": "tap", "x": 0.5, "y": 0.5}
        )
        self.assertEqual(status, 409)
        self.assertIn("not connected", body["error"])

        self.bot.connect_devices(self.rack)
        invalid = [
            {"type": "nope"},
            {"type": "tap", "x": 1.5, "y": 0.5},
            {"type": "swipe", "from": {"x": 0.1, "y": 0.1}},
            {"type": "text", "text": ""},
            {"type": "key", "key": "nope"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                status, body = self.request_json(
                    "/api/devices/d0/input", "POST", payload
                )
                self.assertEqual(status, 400)
                self.assertIn("error", body)

    def test_input_failure_returns_bad_gateway(self):
        self.bot.connect_devices(self.rack)
        self.screen_driver.execute_script = mock.Mock(
            side_effect=RuntimeError("no gesture")
        )
        self.screen_driver.tap = mock.Mock(
            side_effect=RuntimeError("no tap")
        )

        status, body = self.request_json(
            "/api/devices/d0/input",
            "POST",
            {"type": "tap", "x": 0.5, "y": 0.5},
        )
        self.assertEqual(status, 502)
        self.assertIn("input failed", body["error"])

    def test_connect_endpoint_runs_a_job(self):
        status, job = self.request_json("/api/connect", "POST", {})

        self.assertEqual(status, 202)
        self.assertIn("id", job)

        done = wait_job(self.state, job["id"])
        self.assertEqual(done["status"], "succeeded")

        status, snapshot = self.request_json("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["summary"]["connected"], 2)

    def test_action_endpoint_runs_a_job(self):
        self.bot.connect_devices(self.rack)

        status, job = self.request_json(
            "/api/action",
            "POST",
            {
                "action": "comment",
                "target": "d0",
                "params": {
                    "url": "https://www.facebook.com/example/posts/1",
                    "text": "Halo",
                },
            },
        )

        self.assertEqual(status, 202)
        done = wait_job(self.state, job["id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["ok"], 1)
        self.assertEqual(
            done["result"]["devices"]["d0"]["result"]["comment_text"], "Halo"
        )

    def test_bulk_endpoint_runs_and_reports_progress(self):
        self.bot.connect_devices(self.rack)

        status, job = self.request_json(
            "/api/bulk",
            "POST",
            {
                "platform": "facebook",
                "action": "follow",
                "items": ["a", "b"],
            },
        )

        self.assertEqual(status, 202)
        done = wait_job(self.state, job["id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["result"]["ok"], 4)
        self.assertEqual(done["progress"]["done"], 2)

        status, snapshot = self.request_json("/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["jobs"][0]["progress"]["done"], 2)

        status, body = self.request_json(
            f"/api/jobs/{job['id']}/retry", "POST", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("retryable", body["error"])

    def test_bulk_endpoint_validation(self):
        cases = [
            {"platform": "facebook", "action": "follow", "items": []},
            {"platform": "facebook", "action": "comment", "items": ["no pipe"]},
            {"platform": "instagram", "action": "post", "items": ["x"]},
            {
                "platform": "facebook",
                "action": "follow",
                "items": ["a"],
                "interval": -1,
            },
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                status, body = self.request_json("/api/bulk", "POST", payload)
                self.assertEqual(status, 400)
                self.assertIn("error", body)

    def test_retry_unknown_or_succeeded_job_is_rejected(self):
        job = self.state.submit_connect()
        wait_job(self.state, job["id"])

        status, body = self.request_json(
            f"/api/jobs/{job['id']}/retry", "POST", {}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", body)

        status, body = self.request_json("/api/jobs/ghost/retry", "POST", {})
        self.assertEqual(status, 400)

    def test_invalid_requests_are_rejected(self):
        cases = [
            ("/api/action", {"action": "nope", "params": {"username": "x"}}),
            ("/api/action", {"action": "follow", "params": {}}),
            (
                "/api/action",
                {"action": "follow", "target": "ghost", "params": {"username": "x"}},
            ),
            (
                "/api/action",
                {
                    "platform": "ghost",
                    "action": "follow",
                    "params": {"username": "x"},
                },
            ),
            ("/api/connect", {"udids": "not-a-list"}),
        ]
        for path, payload in cases:
            with self.subTest(path=path, payload=payload):
                status, body = self.request_json(path, "POST", payload)
                self.assertEqual(status, 400)
                self.assertIn("error", body)

        status, body = self.request_json("/api/action", "POST", raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

        status, body = self.request_json("/api/nope")
        self.assertEqual(status, 404)

        status, body = self.request_json("/api/nope", "POST", {})
        self.assertEqual(status, 404)

    def test_config_endpoint_scan_and_save(self):
        status, payload = self.request_json("/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(payload["rack"]["name"], "rack-test")

        adb_output = (
            "List of devices attached\n"
            "PHONE1\tdevice\n"
            "PHONE2\tunauthorized\n"
        )
        with mock.patch.object(
            dashboard, "run_adb", return_value=adb_output
        ):
            status, payload = self.request_json("/api/config/scan")

        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["devices"][0]["udid"], "PHONE1")

        status, payload = self.request_json(
            "/api/config",
            "POST",
            {
                "rack": "rack-ui",
                "host": "10.0.0.5",
                "devices": [
                    {
                        "udid": "PHONE1",
                        "appium_port": 4723,
                        "device_name": "slot-01",
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["rack"]["name"], "rack-ui")
        self.assertEqual(self.state.rack.device_map, {"PHONE1": 4723})
        self.assertEqual(load_rack(self.manifest).name, "rack-ui")

    def test_automation_campaign_and_health_endpoints(self):
        status, payload = self.request_json("/api/automation")
        self.assertEqual(status, 200)
        self.assertIn("follow", payload["limits"])

        status, payload = self.request_json(
            "/api/automation",
            "POST",
            {"limits": {"follow": 5}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["limits"]["follow"], 5)

        self.bot.connect_devices(self.rack)
        status, campaign = self.request_json(
            "/api/campaign",
            "POST",
            {
                "text": "Halo campaign",
                "platforms": ["facebook"],
                "target": "all",
                "interval": 0,
            },
        )
        self.assertEqual(status, 202)
        campaign_done = wait_job(self.state, campaign["id"])
        self.assertEqual(campaign_done["status"], "succeeded")
        self.assertEqual(campaign_done["result"]["ok"], 2)

        status, health = self.request_json(
            "/api/health", "POST", {"target": "all"}
        )
        self.assertEqual(status, 202)
        health_done = wait_job(self.state, health["id"])
        self.assertEqual(health_done["status"], "succeeded")
        self.assertEqual(health_done["result"]["ok"], 2)

    def test_scheduled_action_endpoint(self):
        status, job = self.request_json(
            "/api/action",
            "POST",
            {
                "platform": "facebook",
                "action": "follow",
                "target": "all",
                "params": {"username": "later"},
                "schedule_at": time.time() + 30,
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(job["status"], "scheduled")
        self.assertIsNotNone(job["scheduled_at"])

        status, payload = self.request_json(
            f"/api/jobs/{job['id']}/cancel", "POST", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["cancelled"])

    def test_logs_endpoint(self):
        logging.getLogger(dashboard.LOGGER_NAME).info("api log line")

        status, payload = self.request_json("/api/logs?since=0")

        self.assertEqual(status, 200)
        self.assertTrue(
            any("api log line" in entry["message"] for entry in payload["logs"])
        )

        status, payload = self.request_json(
            f"/api/logs?since={payload['last_id']}"
        )
        self.assertEqual(payload["logs"], [])


class DashboardAuthTests(unittest.TestCase):
    TOKEN = "test-dashboard-token"

    def setUp(self):
        self.rack = make_rack(1)
        self.bot = FakeBot()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = dashboard.DashboardState(
            self.bot,
            self.rack,
            actions=fake_actions(),
            manifest_path=Path(self.tmp.name) / "rack.json",
        )
        self.server = dashboard.DashboardServer(
            ("127.0.0.1", 0), self.state, auth_token=self.TOKEN
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="dashboard-auth-test",
            daemon=True,
        )
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.server.shutdown()
        self.server.server_close()
        self.state.shutdown()
        self.thread.join(timeout=2.0)

    def _request(self, method, path, body=None, headers=None):
        connection = HTTPConnection(self.host, self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return (
                response.status,
                response.read().decode("utf-8"),
                dict(response.getheaders()),
            )
        finally:
            connection.close()

    def test_unauthenticated_requests_are_rejected(self):
        status, body, _headers = self._request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertIn("unauthorized", body)

        status, body, _headers = self._request("GET", "/")
        self.assertEqual(status, 401)
        self.assertIn("Token akses", body)

        status, _body, _headers = self._request("GET", "/static/app.js")
        self.assertEqual(status, 401)

    def test_login_rejects_wrong_token(self):
        status, body, _headers = self._request(
            "POST",
            "/login",
            body=urlencode({"token": "wrong"}).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
        )
        self.assertEqual(status, 401)
        self.assertIn("Token tidak valid", body)

    def test_login_sets_cookie_and_allows_api(self):
        status, _body, headers = self._request(
            "POST",
            "/login",
            body=urlencode({"token": self.TOKEN}).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded"
            },
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/")
        set_cookie = headers.get("Set-Cookie", "")
        self.assertIn(f"{dashboard.AUTH_COOKIE}={self.TOKEN}", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)

        cookie = f"{dashboard.AUTH_COOKIE}={self.TOKEN}"
        status, body, _headers = self._request(
            "GET", "/api/status", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["rack"]["name"], "rack-test")

        status, body, _headers = self._request(
            "GET", "/", headers={"Cookie": cookie}
        )
        self.assertEqual(status, 200)
        self.assertIn("Phone Farm", body)

    def test_bearer_token_allows_api(self):
        status, body, _headers = self._request(
            "GET",
            "/api/status",
            headers={"Authorization": f"Bearer {self.TOKEN}"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["rack"]["name"], "rack-test")

    def test_main_requires_token_for_non_loopback(self):
        with mock.patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(
            io.StringIO()
        ):
            self.assertEqual(
                dashboard.main(["--host", "0.0.0.0", "--port", "0"]),
                2,
            )


if __name__ == "__main__":
    unittest.main()
