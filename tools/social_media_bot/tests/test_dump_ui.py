"""Tests for the Appium page-source dump helper."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dump_ui  # noqa: E402
from rack import DeviceSpec, Rack  # noqa: E402

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" text="" content-desc="" bounds="[0,0][1080,2400]">
    <node class="android.widget.EditText" text="Apa yang Anda pikirkan?" content-desc="" resource-id="com.facebook.katana:id/composer" bounds="[40,300][1040,420]" />
    <node class="android.widget.Button" text="Posting" content-desc="" resource-id="" bounds="[900,120][1040,200]" />
    <node class="android.widget.TextView" text="" content-desc="Buat postingan baru" resource-id="" bounds="[100,60][600,120]" />
    <node class="android.widget.TextView" text="" content-desc="" resource-id="" bounds="[0,0][10,10]" />
    <node class="android.view.View" text="abaikan" content-desc="" resource-id="" bounds="[0,0][10,10]" />
  </node>
</hierarchy>
"""


class SummarizeTests(unittest.TestCase):
    def test_keeps_labelled_interactive_nodes(self):
        rows = dump_ui.summarize_page_source(PAGE)

        self.assertEqual(
            [row["class"] for row in rows], ["EditText", "Button", "TextView"]
        )
        self.assertEqual(rows[0]["text"], "Apa yang Anda pikirkan?")
        self.assertEqual(rows[1]["text"], "Posting")
        self.assertEqual(rows[2]["desc"], "Buat postingan baru")
        self.assertIn("composer", rows[0]["resource_id"])

    def test_limit_is_applied(self):
        self.assertEqual(len(dump_ui.summarize_page_source(PAGE, limit=1)), 1)
        self.assertEqual(len(dump_ui.summarize_page_source(PAGE, limit=None)), 3)

    def test_invalid_xml_raises(self):
        with self.assertRaises(ValueError):
            dump_ui.summarize_page_source("<hierarchy><node")


class FindDeviceTests(unittest.TestCase):
    def setUp(self):
        self.rack = Rack(
            name="rack-test",
            host="127.0.0.1",
            devices=[
                DeviceSpec("AAA", 4723, {"device_name": "slot-01"}),
                DeviceSpec("BBB", 4724, {"device_name": "slot-02"}),
            ],
        )

    def test_find_by_udid(self):
        self.assertEqual(dump_ui.find_device(self.rack, udid="BBB").appium_port, 4724)

    def test_find_by_slot(self):
        self.assertEqual(dump_ui.find_device(self.rack, slot="slot-01").udid, "AAA")

    def test_unknown_device_raises(self):
        with self.assertRaises(ValueError):
            dump_ui.find_device(self.rack, udid="ZZZ")


class FormatTests(unittest.TestCase):
    def test_format_rows_has_header_and_clips_long_text(self):
        rows = [
            {
                "class": "EditText",
                "text": "x" * 60,
                "desc": "",
                "resource_id": "",
                "bounds": "[0,0][1,1]",
            }
        ]
        text = dump_ui.format_rows(rows)

        self.assertIn("class", text.splitlines()[0])
        self.assertIn("…", text)


if __name__ == "__main__":
    unittest.main()
