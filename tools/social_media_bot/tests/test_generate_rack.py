"""Tests for generating rack manifests from adb output."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_rack  # noqa: E402
from rack import load_rack  # noqa: E402

SAMPLE = """List of devices attached
R38M20TEST01\tdevice product:a52q model:SM_A525F device:a52q
RF8M6TEST02\tdevice product:a52q model:SM_A525F device:a52q
BADUNAUTH01\tunauthorized
OFFLINE001\toffline
* daemon started successfully *
"""


class ParseAdbTests(unittest.TestCase):
    def test_only_ready_devices_are_kept(self):
        ready, skipped = generate_rack.parse_adb_devices(SAMPLE)

        self.assertEqual(ready, ["R38M20TEST01", "RF8M6TEST02"])
        self.assertEqual(
            skipped,
            [("BADUNAUTH01", "unauthorized"), ("OFFLINE001", "offline")],
        )

    def test_duplicate_ready_serials_are_removed(self):
        ready, _ = generate_rack.parse_adb_devices(
            "List of devices attached\n"
            "R38M20TEST01\tdevice\n"
            "R38M20TEST01\tdevice\n"
        )
        self.assertEqual(ready, ["R38M20TEST01"])

    def test_empty_output_has_no_devices(self):
        ready, skipped = generate_rack.parse_adb_devices(
            "List of devices attached\n\n"
        )
        self.assertEqual(ready, [])
        self.assertEqual(skipped, [])


class BuildManifestTests(unittest.TestCase):
    def test_builds_consecutive_ports_and_slots(self):
        manifest = generate_rack.build_manifest(
            ["serial-a", "serial-b"],
            name="rack-lab",
            host="192.168.1.20",
            first_port=4723,
        )

        self.assertEqual(manifest["rack"], "rack-lab")
        self.assertEqual(manifest["host"], "192.168.1.20")
        self.assertEqual(
            manifest["devices"],
            [
                {
                    "udid": "serial-a",
                    "appium_port": 4723,
                    "device_name": "slot-01",
                },
                {
                    "udid": "serial-b",
                    "appium_port": 4724,
                    "device_name": "slot-02",
                },
            ],
        )


class GenerateRackCliTests(unittest.TestCase):
    def test_generates_a_loadable_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "adb.txt"
            output = Path(tmp) / "rack.json"
            source.write_text(SAMPLE, encoding="utf-8")

            code = generate_rack.main(
                [
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--name",
                    "rack-lab",
                ]
            )

            self.assertEqual(code, 1)  # one unauthorized/offline pair skipped
            rack = load_rack(output)
            self.assertEqual(rack.name, "rack-lab")
            self.assertEqual(len(rack), 2)
            self.assertEqual(
                rack.device_map,
                {"R38M20TEST01": 4723, "RF8M6TEST02": 4724},
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["devices"][0]["device_name"], "slot-01")

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "adb.txt"
            output = Path(tmp) / "rack.json"
            source.write_text(SAMPLE, encoding="utf-8")
            output.write_text("old", encoding="utf-8")

            code = generate_rack.main(
                ["--input", str(source), "--output", str(output)]
            )

            self.assertEqual(code, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), "old")

    def test_force_overwrites_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "adb.txt"
            output = Path(tmp) / "rack.json"
            source.write_text(SAMPLE, encoding="utf-8")
            output.write_text("old", encoding="utf-8")

            code = generate_rack.main(
                [
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--force",
                ]
            )

            self.assertEqual(code, 1)
            self.assertIn("R38M20TEST01", output.read_text(encoding="utf-8"))

    def test_no_ready_devices_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "adb.txt"
            output = Path(tmp) / "rack.json"
            source.write_text(
                "List of devices attached\nBADUNAUTH01\tunauthorized\n",
                encoding="utf-8",
            )

            code = generate_rack.main(
                ["--input", str(source), "--output", str(output)]
            )

            self.assertEqual(code, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
