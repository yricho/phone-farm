"""Tests for the rack connectivity diagnostics helper."""

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import diagnose  # noqa: E402
from rack import DeviceSpec, Rack  # noqa: E402


def make_rack():
    return Rack(
        name="rack-test",
        host="10.0.0.5",
        devices=[
            DeviceSpec("d1", 4723, {"device_name": "slot-01"}),
            DeviceSpec("d2", 4724, {"device_name": "slot-02"}),
        ],
    )


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class ProbeTests(unittest.TestCase):
    def _probe(self, body=None, error=None):
        if error is not None:
            patch = mock.patch.object(
                diagnose.urllib.request, "urlopen", side_effect=error
            )
        else:
            patch = mock.patch.object(
                diagnose.urllib.request, "urlopen", return_value=FakeResponse(body)
            )
        with patch:
            return diagnose.probe_appium("127.0.0.1", 4723)

    def test_ready_status(self):
        probe = self._probe(json.dumps({"value": {"ready": True}}).encode())
        self.assertTrue(probe.ready)
        self.assertIn("4723", probe.detail)

    def test_not_ready_status(self):
        probe = self._probe(
            json.dumps({"value": {"ready": False, "message": "installing"}}).encode()
        )
        self.assertFalse(probe.ready)
        self.assertIn("installing", probe.detail)

    def test_connection_error(self):
        probe = self._probe(error=urllib.error.URLError("Connection refused"))
        self.assertFalse(probe.ready)
        self.assertIn("Connection refused", probe.detail)

    def test_non_json_body(self):
        probe = self._probe(b"<html>oops</html>")
        self.assertFalse(probe.ready)
        self.assertIn("bukan JSON", probe.detail)


class AnalyseTests(unittest.TestCase):
    def test_ready_device_with_known_udid_has_no_issues(self):
        spec = make_rack().devices[0]
        issues = diagnose.analyse_device(spec, diagnose.AppiumProbe(True, "ok"), ["d1"])
        self.assertEqual(issues, [])

    def test_missing_udid_is_reported(self):
        spec = make_rack().devices[0]
        issues = diagnose.analyse_device(spec, diagnose.AppiumProbe(True, "ok"), ["zzz"])
        self.assertEqual(len(issues), 1)
        self.assertIn("adb devices", issues[0])

    def test_dead_appium_is_reported(self):
        spec = make_rack().devices[0]
        issues = diagnose.analyse_device(
            spec, diagnose.AppiumProbe(False, "tidak ada Appium"), None
        )
        self.assertEqual(issues, ["tidak ada Appium"])


class CollectTests(unittest.TestCase):
    def test_collect_reports_marks_failed_ports(self):
        def probe(host, port, *, scheme="http", timeout=1.0):
            return diagnose.AppiumProbe(port == 4723, f"probe {port}")

        reports = diagnose.collect_reports(
            make_rack(), "127.0.0.1", adb_serials=["d1", "d2"], probe=probe
        )

        self.assertEqual([report.slot for report in reports], ["slot-01", "slot-02"])
        self.assertEqual([report.ok for report in reports], [True, False])
        self.assertIn("probe 4724", reports[1].issues[0])


class DependencyTests(unittest.TestCase):
    def _status(self, appium_version, selenium_version, importable):
        with mock.patch.object(
            diagnose,
            "_package_version",
            side_effect=[appium_version, selenium_version],
        ), mock.patch.object(
            diagnose, "_module_importable", return_value=importable
        ):
            return diagnose.check_dependencies()

    def test_missing_appium_is_flagged(self):
        status = self._status(None, None, False)

        self.assertFalse(status.ok)
        self.assertIn("TIDAK TERPASANG", status.summary())
        self.assertIn("MASALAH", status.summary())

    def test_installed_appium_is_ok(self):
        status = self._status("6.1.7", "4.53.0", True)

        self.assertTrue(status.ok)
        self.assertIn("6.1.7", status.summary())
        self.assertIn("4.53.0", status.summary())
        self.assertIn("OK", status.summary())


class MainTests(unittest.TestCase):
    def test_main_reports_problems_and_exits_one(self):
        manifest = {
            "rack": "rack-test",
            "host": "127.0.0.1",
            "devices": [{"udid": "d1", "appium_port": 4723}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rack.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(
                diagnose,
                "probe_appium",
                return_value=diagnose.AppiumProbe(False, "tidak ada Appium di :4723"),
            ):
                code = diagnose.main(["--manifest", str(path), "--no-adb"])

        self.assertEqual(code, 1)

    def test_main_returns_two_for_missing_manifest(self):
        self.assertEqual(diagnose.main(["--manifest", "/nonexistent/rack.json"]), 2)


if __name__ == "__main__":
    unittest.main()
