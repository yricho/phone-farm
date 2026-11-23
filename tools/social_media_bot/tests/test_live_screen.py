"""Tests for the live screen pipeline: frame sources, encoding and streaming."""

import io
import struct
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dashboard  # noqa: E402
from test_dashboard import FakeBot, FakeScreenDriver, make_rack  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is optional
    Image = None

needs_pillow = unittest.skipIf(Image is None, "Pillow is not installed")

PNG = b"\x89PNG\r\n\x1a\n"


def png_bytes(width=1080, height=2400, color=(10, 120, 200)):
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, "PNG")
    return output.getvalue()


def raw_bytes(width, height, pixel_format=1, header=16, pixel=b"\x0a\x78\xc8\xff"):
    head = struct.pack("<III", width, height, pixel_format)
    if header == 16:
        head += struct.pack("<I", 1)
    return head + pixel * (width * height)


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def completed(stdout, code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")


class RawFrameTests(unittest.TestCase):
    def test_parses_both_header_layouts(self):
        for header in (12, 16):
            frame = dashboard.RawFrame.parse(raw_bytes(4, 3, header=header))
            self.assertIsNotNone(frame)
            self.assertEqual((frame.width, frame.height), (4, 3))
            self.assertEqual(frame.offset, header)
            self.assertEqual(frame.rawmode, "RGBX")

    def test_bgra_format(self):
        frame = dashboard.RawFrame.parse(raw_bytes(2, 2, pixel_format=5))
        self.assertEqual(frame.rawmode, "BGRX")

    def test_rejects_unknown_layouts(self):
        self.assertIsNone(dashboard.RawFrame.parse(b"short"))
        self.assertIsNone(dashboard.RawFrame.parse(raw_bytes(2, 2, pixel_format=4)))
        self.assertIsNone(dashboard.RawFrame.parse(raw_bytes(2, 2) + b"extra"))
        self.assertIsNone(dashboard.RawFrame.parse(PNG + b"\x00" * 20))


class FrameEncoderTests(unittest.TestCase):
    @needs_pillow
    def test_png_is_downscaled_to_jpeg(self):
        encoder = dashboard.FrameEncoder(max_width=540, quality=70)
        source = png_bytes()

        payload, content_type = encoder.encode(source)

        self.assertEqual(content_type, "image/jpeg")
        self.assertLess(len(payload), len(source))
        image = Image.open(io.BytesIO(payload))
        self.assertEqual(image.size, (540, 1200))

    @needs_pillow
    def test_raw_frame_is_encoded(self):
        encoder = dashboard.FrameEncoder(max_width=0)
        frame = dashboard.RawFrame.parse(raw_bytes(8, 4, pixel_format=5))

        payload, content_type = encoder.encode(frame)

        self.assertEqual(content_type, "image/jpeg")
        image = Image.open(io.BytesIO(payload))
        self.assertEqual(image.size, (8, 4))
        # BGRA bytes 0a 78 c8 -> RGB (200, 120, 10)
        red, green, blue = image.convert("RGB").getpixel((0, 0))
        self.assertGreater(red, 180)
        self.assertLess(blue, 40)

    def test_jpeg_passes_through(self):
        jpeg = b"\xff\xd8jpeg\xff\xd9"
        self.assertEqual(
            dashboard.FrameEncoder().encode(jpeg), (jpeg, "image/jpeg")
        )

    def test_unreadable_png_passes_through(self):
        frame = PNG + b"not really"
        self.assertEqual(dashboard.FrameEncoder().encode(frame), (frame, "image/png"))

    def test_without_pillow_png_passes_through(self):
        with mock.patch.object(dashboard, "_PILImage", None):
            encoder = dashboard.FrameEncoder()
            self.assertFalse(encoder.enabled)
            frame = PNG + b"frame"
            self.assertEqual(encoder.encode(frame), (frame, "image/png"))

    def test_quality_is_clamped(self):
        self.assertEqual(dashboard.FrameEncoder(quality=500).quality, 95)
        self.assertEqual(dashboard.FrameEncoder(quality=0).quality, 10)
        self.assertEqual(dashboard.FrameEncoder(max_width=-5).max_width, 0)


class AdbGrabberFormatTests(unittest.TestCase):
    PROBE = SimpleNamespace(returncode=0, stdout="device\n", stderr="")

    @needs_pillow
    def test_benchmark_keeps_the_faster_format(self):
        clock = FakeClock()
        raw = raw_bytes(4, 4)
        png = PNG + b"frame"
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[-1] == "get-state":
                return self.PROBE
            if command[-1] == "-p":
                clock.now += 0.6  # phone-side PNG encoding is slow
                return completed(png)
            clock.now += 0.2
            return completed(raw)

        with mock.patch.object(dashboard.subprocess, "run", side_effect=run):
            grabber = dashboard.AdbFrameGrabber("d0", clock=clock)
            self.assertEqual(grabber.capture_format, "auto")
            self.assertIsInstance(grabber.capture(), dashboard.RawFrame)
            self.assertEqual(grabber.capture(), png)
            self.assertEqual(grabber.capture_format, "raw")
            self.assertIsInstance(grabber.capture(), dashboard.RawFrame)

        self.assertEqual(calls[1][-1], "screencap")
        self.assertEqual(calls[2][-1], "-p")

    @needs_pillow
    def test_unknown_raw_layout_switches_to_png(self):
        png = PNG + b"frame"

        def run(command, **kwargs):
            if command[-1] == "get-state":
                return self.PROBE
            if command[-1] == "-p":
                return completed(png)
            return completed(b"garbage-raw-output")

        with mock.patch.object(dashboard.subprocess, "run", side_effect=run):
            grabber = dashboard.AdbFrameGrabber("d0")
            self.assertIsNone(grabber.capture())
            self.assertEqual(grabber.capture_format, "png")
            self.assertEqual(grabber.capture(), png)

    def test_without_pillow_only_png_is_used(self):
        with mock.patch.object(dashboard, "_PILImage", None):
            grabber = dashboard.AdbFrameGrabber("d0", raw=True)
        self.assertEqual(grabber.capture_format, "png")

    def test_paused_adb_path_is_retried(self):
        clock = FakeClock()
        outputs = [self.PROBE] + [completed(b"bad")] * dashboard.ADB_MAX_FAILURES
        outputs += [self.PROBE, completed(PNG + b"back")]
        with mock.patch.object(
            dashboard.subprocess, "run", side_effect=outputs
        ) as run:
            grabber = dashboard.AdbFrameGrabber("d0", raw=False, clock=clock)
            for _ in range(dashboard.ADB_MAX_FAILURES):
                self.assertIsNone(grabber.capture())
            self.assertFalse(grabber.available)
            self.assertIsNone(grabber.capture())  # still paused, no adb call

            clock.now += dashboard.ADB_RETRY_SECONDS
            self.assertEqual(grabber.capture(), PNG + b"back")

        self.assertEqual(run.call_count, len(outputs))

    def test_failed_probe_is_retried(self):
        clock = FakeClock()
        missing = SimpleNamespace(returncode=1, stdout="", stderr="not found")
        with mock.patch.object(
            dashboard.subprocess,
            "run",
            side_effect=[missing, self.PROBE, completed(PNG + b"x")],
        ):
            grabber = dashboard.AdbFrameGrabber("d0", raw=False, clock=clock)
            self.assertIsNone(grabber.capture())
            clock.now += dashboard.ADB_RETRY_SECONDS
            self.assertEqual(grabber.capture(), PNG + b"x")

    def test_capture_png_always_uses_png(self):
        with mock.patch.object(
            dashboard.subprocess,
            "run",
            side_effect=[self.PROBE, completed(PNG + b"full")],
        ) as run:
            grabber = dashboard.AdbFrameGrabber("d0", raw=True)
            self.assertEqual(grabber.capture_png(), PNG + b"full")
        self.assertEqual(run.call_args.args[0][-1], "-p")


class FakeMjpegResponse:
    """File-like upstream response delivering bytes in fixed chunks."""

    def __init__(self, payload, chunk=7):
        self._chunks = [payload[i : i + chunk] for i in range(0, len(payload), chunk)]
        self.closed = threading.Event()

    def read1(self, size):
        if self._chunks:
            return self._chunks.pop(0)
        # Keep the connection "open" like a live stream until closed
        self.closed.wait(2.0)
        return b""

    def close(self):
        self.closed.set()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class MjpegFrameSourceTests(unittest.TestCase):
    FRAME_A = b"\xff\xd8frame-a\xff\xd9"
    FRAME_B = b"\xff\xd8frame-b-longer\xff\xd9"

    def _source(self, payload, **kwargs):
        requests = []
        response = FakeMjpegResponse(payload)

        def opener(request, timeout):
            requests.append(request)
            return response

        source = dashboard.MjpegFrameSource(
            "http://gads/device/d0/android-stream-mjpeg",
            headers={"Authorization": "Bearer t0k"},
            opener=opener,
            **kwargs,
        )
        self.addCleanup(source.close)
        return source, requests

    def _wait_for(self, source, expected):
        deadline = time.time() + 2.0
        frame = source.capture()
        while frame != expected and time.time() < deadline:
            time.sleep(0.01)
            frame = source.capture()
        return frame

    def test_frames_are_split_without_content_length(self):
        payload = (
            b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            + self.FRAME_A
            + b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            + self.FRAME_B
        )
        source, requests = self._source(payload)

        self.assertEqual(self._wait_for(source, self.FRAME_B), self.FRAME_B)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer t0k")
        # Same object until a new frame arrives (lets the stream skip dupes)
        self.assertIs(source.capture(), source.capture())

    def test_stale_frames_are_dropped(self):
        clock = FakeClock()
        source, _ = self._source(self.FRAME_A, clock=clock, stale_after=5.0)
        self.assertEqual(self._wait_for(source, self.FRAME_A), self.FRAME_A)

        clock.now += 6.0
        self.assertIsNone(source.capture())


class StaticSource:
    def __init__(self, frame):
        self.frame = frame
        self.calls = 0
        self.closed = False

    def capture(self):
        self.calls += 1
        return self.frame

    def close(self):
        self.closed = True


class DeviceStreamTests(unittest.TestCase):
    def setUp(self):
        self.driver = FakeScreenDriver()

    def _stream(self, sources, interval=0.05, **kwargs):
        stream = dashboard.DeviceStream(
            "d0", lambda: self.driver, interval, sources=sources, **kwargs
        )
        stream.add_subscriber()
        self.addCleanup(stream.close)
        return stream

    def test_mjpeg_duplicates_are_not_resent(self):
        source = StaticSource(b"\xff\xd8same\xff\xd9")
        stream = self._stream([("mjpeg", source)])
        stream.start()

        items = stream.frame_items()
        self.assertEqual(next(items), (source.frame, "image/jpeg"))
        deadline = time.time() + 1.0
        while source.calls < 5 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(stream._frame_id, 1)
        self.assertEqual(stream.info()["source"], "mjpeg")

    def test_broken_source_falls_back_to_appium(self):
        class Broken:
            def capture(self):
                raise RuntimeError("adb exploded")

        stream = self._stream([("adb", Broken())])
        stream.start()

        self.assertEqual(next(stream.frames()), self.driver.frame)
        self.assertEqual(stream.source, "appium")

    def test_sources_are_closed_with_the_stream(self):
        source = StaticSource(PNG + b"x")
        stream = self._stream([("adb", source)])
        stream.start()
        next(stream.frames())

        stream.close()
        self.assertTrue(source.closed)

    @needs_pillow
    def test_frames_are_encoded_once_as_jpeg(self):
        source = StaticSource(png_bytes(1080, 2400))
        stream = self._stream(
            [("adb", source)], encoder=dashboard.FrameEncoder(max_width=360)
        )
        stream.start()

        payload, content_type = next(stream.frame_items())
        self.assertEqual(content_type, "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(payload)).width, 360)
        info = stream.info()
        self.assertEqual(info["content_type"], "image/jpeg")
        self.assertEqual(info["frame_bytes"], len(payload))

    def test_fastest_viewer_interval_wins(self):
        stream = dashboard.DeviceStream("d0", lambda: self.driver, 1.0)
        self.assertEqual(stream.interval, 1.0)

        stream.add_subscriber(2.0)
        stream.add_subscriber(0.5)
        self.assertEqual(stream.interval, 0.5)

        stream.remove_subscriber(0.5)
        self.assertEqual(stream.interval, 2.0)
        stream.remove_subscriber(2.0)
        self.assertTrue(stream.closed)

    def test_auto_interval_depends_on_source(self):
        stream = dashboard.DeviceStream("d0", lambda: self.driver, None)
        self.assertEqual(stream.interval, stream.AUTO_APPIUM_INTERVAL)
        stream.source = "adb"
        self.assertEqual(stream.interval, stream.AUTO_FAST_INTERVAL)
        stream.source = "appium"
        self.assertEqual(stream.interval, stream.AUTO_APPIUM_INTERVAL)


class ScreenStreamManagerLiveTests(unittest.TestCase):
    def setUp(self):
        self.rack = make_rack(2)
        self.driver = FakeScreenDriver()
        self.bot = FakeBot(self.rack, driver_factory=lambda udid: self.driver)

    def test_mjpeg_url_is_validated(self):
        with self.assertRaises(ValueError):
            dashboard.ScreenStreamManager(self.bot, mjpeg_url="http://gads/stream")
        with self.assertRaises(ValueError):
            dashboard.ScreenStreamManager(self.bot, mjpeg_url="ftp://gads/{udid}")
        manager = dashboard.ScreenStreamManager(self.bot, mjpeg_url="  ")
        self.assertIsNone(manager.mjpeg_url)

    def test_sources_order_adb_then_mjpeg(self):
        manager = dashboard.ScreenStreamManager(
            self.bot,
            mjpeg_url="http://hub:10000/device/{udid}/android-stream-mjpeg",
            mjpeg_token="secret",
        )

        sources = manager._frame_sources_for("emu_Provider/AVD")

        self.assertEqual([name for name, _ in sources], ["adb", "mjpeg"])
        mjpeg = sources[1][1]
        self.assertEqual(
            mjpeg.url,
            "http://hub:10000/device/emu_Provider%2FAVD/android-stream-mjpeg",
        )
        self.assertEqual(mjpeg._headers, {"Authorization": "Bearer secret"})

    def test_appium_source_has_no_extra_sources(self):
        manager = dashboard.ScreenStreamManager(
            self.bot,
            screen_source="appium",
            mjpeg_url="http://hub/device/{udid}/android-stream-mjpeg",
        )
        self.assertEqual(manager._frame_sources_for("d0"), [])

    def test_grabber_is_reused_per_device(self):
        manager = dashboard.ScreenStreamManager(self.bot)
        self.assertIs(manager._grabber("d0"), manager._grabber("d0"))
        self.assertIsNot(manager._grabber("d0"), manager._grabber("d1"))

    def test_snapshot_prefers_adb(self):
        manager = dashboard.ScreenStreamManager(self.bot)
        with mock.patch.object(
            dashboard.AdbFrameGrabber, "capture_png", return_value=PNG + b"adb"
        ):
            self.assertEqual(manager.snapshot("d0"), PNG + b"adb")
        self.assertEqual(self.driver.calls, 0)

        with mock.patch.object(
            dashboard.AdbFrameGrabber, "capture_png", return_value=None
        ):
            self.assertEqual(manager.snapshot("d0"), self.driver.frame)

    def test_snapshot_requires_a_session(self):
        manager = dashboard.ScreenStreamManager(self.bot)
        with self.assertRaises(dashboard.DeviceNotConnectedError):
            manager.snapshot("ghost")

    def test_viewer_intervals_are_shared_and_released(self):
        manager = dashboard.ScreenStreamManager(self.bot)
        self.addCleanup(manager.close_all)
        with mock.patch.object(
            dashboard.AdbFrameGrabber, "capture", return_value=PNG + b"x"
        ):
            first = manager.acquire("d0", 2.0)
            second = manager.acquire("d0", "0.5")
            self.assertIs(first, second)
            self.assertEqual(first.interval, 0.5)
            self.assertEqual(manager.stream_info("d0")["viewers"], 2)

            manager.release(second, "0.5")
            self.assertEqual(first.interval, 2.0)
            manager.release(first, 2.0)

        self.assertTrue(first.closed)
        self.assertIsNone(manager.stream_info("d0"))


class LiveScreenStatusTests(unittest.TestCase):
    def test_status_reports_stream_tuning_and_info(self):
        rack = make_rack(1)
        driver = FakeScreenDriver()
        bot = FakeBot(rack, driver_factory=lambda udid: driver)
        state = dashboard.DashboardState(
            bot,
            rack,
            stream_max_width=480,
            stream_quality=60,
            mjpeg_url="http://hub/device/{udid}/android-stream-mjpeg",
        )
        self.addCleanup(state.shutdown)

        status = state.status()
        tuning = status["tuning"]
        self.assertIsNone(tuning["stream_interval"])
        self.assertEqual(tuning["stream_max_width"], 480)
        self.assertEqual(tuning["stream_quality"], 60)
        self.assertTrue(tuning["stream_mjpeg"])
        self.assertEqual(tuning["stream_jpeg"], Image is not None)
        self.assertIsNone(status["devices"][0]["stream"])

        with mock.patch.object(
            dashboard.AdbFrameGrabber, "capture", return_value=PNG + b"x"
        ):
            stream = state.screen_streams.acquire("d0")
            try:
                next(stream.frames())
                info = state.status()["devices"][0]["stream"]
            finally:
                state.screen_streams.release(stream)
        self.assertEqual(info["source"], "adb")
        self.assertEqual(info["viewers"], 1)


class LiveScreenCliTests(unittest.TestCase):
    def test_stream_flags(self):
        args = dashboard.build_parser().parse_args(
            [
                "--stream-max-width",
                "540",
                "--stream-quality",
                "60",
                "--mjpeg-url",
                "http://hub/device/{udid}/android-stream-mjpeg",
                "--mjpeg-token",
                "tok",
            ]
        )
        self.assertEqual(args.stream_max_width, 540)
        self.assertEqual(args.stream_quality, 60)
        self.assertEqual(args.mjpeg_token, "tok")
        self.assertIn("{udid}", args.mjpeg_url)

    def test_stream_interval_defaults_to_auto(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            args = dashboard.build_parser().parse_args([])
        self.assertIsNone(args.stream_interval)

    def test_invalid_mjpeg_url_exits_cleanly(self):
        with mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
            code = dashboard.main(["--mjpeg-url", "http://hub/no-placeholder"])
        self.assertEqual(code, 2)
        self.assertIn("{udid}", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
