"""Unit tests for the rack manifest helpers."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rack import (  # noqa: E402
    DeviceSpec,
    Rack,
    load_rack,
    sequential_rack,
)


class DeviceSpecTests(unittest.TestCase):
    def test_valid_spec_strips_udid(self):
        spec = DeviceSpec("  R58M0001  ", 4723, {"device_name": "slot-01"})
        self.assertEqual(spec.udid, "R58M0001")
        self.assertEqual(spec.appium_port, 4723)
        self.assertEqual(spec.connect_options, {"device_name": "slot-01"})

    def test_invalid_udid_and_port_are_rejected(self):
        for udid, port in [("", 4723), ("   ", 4723), ("udid", 0), ("udid", "4723")]:
            with self.subTest(udid=udid, port=port):
                with self.assertRaises(ValueError):
                    DeviceSpec(udid, port)

    def test_unknown_connect_option_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            DeviceSpec("udid", 4723, {"appium_port": 4724})
        self.assertIn("unknown connect option", str(ctx.exception))


class RackTests(unittest.TestCase):
    def test_rack_exposes_device_map_and_slots(self):
        rack = Rack(
            name="rack-01",
            devices=[DeviceSpec("d1", 4723), DeviceSpec("d2", 4724)],
            host="10.0.0.5",
        )
        self.assertEqual(len(rack), 2)
        self.assertEqual([spec.udid for spec in rack], ["d1", "d2"])
        self.assertEqual(rack.device_map, {"d1": 4723, "d2": 4724})

    def test_rack_rejects_duplicates_and_empty(self):
        with self.assertRaises(ValueError):
            Rack(name="empty", devices=[])
        with self.assertRaises(ValueError):
            Rack(
                name="dupe-udid",
                devices=[DeviceSpec("d1", 4723), DeviceSpec("d1", 4724)],
            )
        with self.assertRaises(ValueError):
            Rack(
                name="dupe-port",
                devices=[DeviceSpec("d1", 4723), DeviceSpec("d2", 4723)],
            )

    def test_rack_can_be_explicitly_empty_for_ui_setup(self):
        rack = Rack(name="setup", devices=[], allow_empty=True)
        self.assertEqual(len(rack), 0)
        self.assertEqual(rack.device_map, {})

    def test_sequential_rack_assigns_20_consecutive_ports(self):
        udids = [f"phone-{index:02d}" for index in range(1, 21)]
        rack = sequential_rack("rack-01", udids, first_port=4723)

        self.assertEqual(len(rack), 20)
        self.assertEqual(rack.devices[0].appium_port, 4723)
        self.assertEqual(rack.devices[-1].appium_port, 4742)


class LoadRackTests(unittest.TestCase):
    def test_load_from_mapping_with_port_alias(self):
        rack = load_rack(
            {
                "rack": "rack-02",
                "host": "192.168.1.20",
                "devices": [
                    {"udid": "d1", "port": 4723},
                    {"udid": "d2", "appium_port": 4724, "device_name": "slot-02"},
                ],
            }
        )
        self.assertEqual(rack.name, "rack-02")
        self.assertEqual(rack.host, "192.168.1.20")
        self.assertEqual(rack.device_map, {"d1": 4723, "d2": 4724})
        self.assertEqual(rack.devices[1].connect_options, {"device_name": "slot-02"})

    def test_load_from_json_file(self):
        payload = {
            "rack": "rack-03",
            "devices": [{"udid": "d1", "appium_port": 4723}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "rack.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            rack = load_rack(manifest)

        self.assertEqual(rack.name, "rack-03")
        self.assertEqual(len(rack), 1)

    def test_load_json_file_with_utf8_bom(self):
        """Windows editors often save JSON as UTF-8 with a BOM."""
        payload = {
            "rack": "rack-bom",
            "devices": [{"udid": "d1", "appium_port": 4723}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "rack.json"
            manifest.write_bytes(
                b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
            )
            rack = load_rack(manifest)

        self.assertEqual(rack.name, "rack-bom")
        self.assertEqual(rack.device_map, {"d1": 4723})

    def test_malformed_manifests_are_rejected(self):
        invalid_payloads = [
            [],
            {},
            {"devices": []},
            {"devices": "nope"},
            {"devices": [{"appium_port": 4723}]},
            {"devices": [{"udid": "d1"}]},
            {"devices": [{"udid": "d1", "port": 4723, "appium_port": 4724}]},
            {"devices": [{"udid": "d1", "appium_port": 4723, "bogus": True}]},
            {"devices": [{"udid": "d1", "appium_port": 4723}, {"udid": "d1", "appium_port": 4724}]},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    load_rack(payload)

    def test_invalid_json_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "broken.json"
            manifest.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_rack(manifest)


if __name__ == "__main__":
    unittest.main()
