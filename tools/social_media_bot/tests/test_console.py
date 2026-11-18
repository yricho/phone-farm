"""Tests for Windows-friendly console setup."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from console import configure_stdio  # noqa: E402


class FakeStream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


class ConfigureStdioTests(unittest.TestCase):
    def test_configures_stdout_and_stderr_as_utf8(self):
        stdout = FakeStream()
        stderr = FakeStream()
        with mock.patch.object(sys, "stdout", stdout), mock.patch.object(
            sys, "stderr", stderr
        ):
            configure_stdio()

        for stream in (stdout, stderr):
            self.assertEqual(
                stream.calls,
                [{"encoding": "utf-8", "errors": "replace"}],
            )

    def test_ignores_streams_without_reconfigure(self):
        with mock.patch.object(sys, "stdout", object()), mock.patch.object(
            sys, "stderr", object()
        ):
            configure_stdio()

    def test_reconfigure_failure_is_ignored(self):
        class BrokenStream:
            def reconfigure(self, **kwargs):
                raise OSError("console is gone")

        with mock.patch.object(sys, "stdout", BrokenStream()), mock.patch.object(
            sys, "stderr", BrokenStream()
        ):
            configure_stdio()


if __name__ == "__main__":
    unittest.main()
