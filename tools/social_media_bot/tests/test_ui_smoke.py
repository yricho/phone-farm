"""Headless Chrome smoke tests for the dashboard UI.

The test is skipped when Chrome/Chromium is not installed. It uses only Python
stdlib plus the browser's DevTools protocol; no Playwright/Selenium dependency.
"""

from __future__ import annotations

import base64
import json
import os
import signal
import socket
import struct
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
CHROME = next(
    (candidate for candidate in CHROME_CANDIDATES if Path(candidate).is_file()),
    None,
)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_http(url: str, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"timeout waiting for {url}")


class WebSocket:
    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        self.sock = socket.create_connection(
            (parsed.hostname, parsed.port), timeout=10
        )
        key = base64.b64encode(os.urandom(16)).decode()
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(response.decode(errors="replace"))
        self.buffer = b""

    def _read_exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("websocket closed")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send(self, payload) -> None:
        data = json.dumps(payload).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        header += mask
        masked = bytes(
            byte ^ mask[index % 4] for index, byte in enumerate(data)
        )
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if second & 0x80 else None
        data = self._read_exact(length)
        if mask:
            data = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(data)
            )
        if opcode == 8:
            raise RuntimeError("websocket close frame")
        return json.loads(data) if opcode in (1, 2) else None

    def call(self, call_id: int, method: str, params=None):
        self.send({"id": call_id, "method": method, "params": params or {}})
        while True:
            message = self.recv()
            if message and message.get("id") == call_id:
                return message

    def close(self) -> None:
        self.sock.close()

UI_EXPRESSION = r"""
(() => {
  const symbolIds = new Set(
    [...document.querySelectorAll("symbol")].map((symbol) => symbol.id),
  );
  const missingUseTargets = [...document.querySelectorAll("use")]
    .map((use) => use.getAttribute("href") || "")
    .filter((href) => href.startsWith("#") && !symbolIds.has(href.slice(1)));
  const missingCtaIcons = [
    ...document.querySelectorAll("button.btn:not(.theme-toggle)"),
  ]
    .filter((button) => !button.querySelector("svg"))
    .map((button) => button.id || button.className);

  const readAccordion = () =>
    [...document.querySelectorAll("#control-accordion .accordion-item")].map(
      (item) => {
        const button = item.querySelector(".accordion-toggle");
        const body = document.getElementById(
          button.getAttribute("aria-controls"),
        );
        return {
          title: item.querySelector(".accordion-title").textContent.trim(),
          open: item.classList.contains("is-open"),
          expanded: button.getAttribute("aria-expanded"),
          hidden: body.hidden,
        };
      },
    );

  const initialTheme = document.documentElement.dataset.theme;
  document.getElementById("btn-theme").click();
  const themeButton = document.getElementById("btn-theme");
  const toggled = {
    theme: document.documentElement.dataset.theme,
    stored: localStorage.getItem("gads.theme"),
    pressed: themeButton.getAttribute("aria-pressed"),
    sun: getComputedStyle(document.querySelector(".theme-icon-sun")).display,
    moon: getComputedStyle(document.querySelector(".theme-icon-moon")).display,
  };

  const states = { initial: readAccordion() };
  document.getElementById("accordion-action").click();
  states.action = readAccordion();
  document.getElementById("accordion-campaign").click();
  states.campaign = readAccordion();
  document.getElementById("accordion-activity").click();
  states.activity = readAccordion();
  document.getElementById("accordion-activity").click();
  states.closed = readAccordion();
  document.getElementById("accordion-automation").click();
  states.automation = readAccordion();
  const maxOpen = Math.max(
    ...Object.values(states).map(
      (state) => state.filter((item) => item.open).length,
    ),
  );

  document.getElementById("accordion-setup").click();
  const remove = document.querySelector(".setup-remove");
  const pause = document.getElementById("log-pause");
  pause.click();
  const paused = {
    value: pause.dataset.paused,
    label: pause.querySelector("span").textContent,
    pauseIcon: getComputedStyle(
      pause.querySelector('[data-icon="pause"]'),
    ).display,
    playIcon: getComputedStyle(
      pause.querySelector('[data-icon="play"]'),
    ).display,
  };
  pause.click();

  return {
    initialTheme,
    toggled,
    ctaMissingIcons: missingCtaIcons,
    missingUseTargets,
    spriteSymbols: symbolIds.size,
    ctaCount: document.querySelectorAll("button.btn").length,
    removeIcon: remove?.querySelector("use")?.getAttribute("href") || null,
    paused,
    maxOpen,
    closedOpen: states.closed.filter((item) => item.open).length,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth,
  };
})()
"""


@unittest.skipUnless(CHROME, "Chrome/Chromium is not installed")
class DashboardUiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        manifest = Path(self.tmp.name) / "rack.json"
        manifest.write_text(
            json.dumps(
                {
                    "rack": "ui-smoke",
                    "host": "127.0.0.1",
                    "devices": [
                        {
                            "udid": "SMOKE001",
                            "appium_port": 4723,
                            "device_name": "slot-01",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.server_port = free_port()
        self.debug_port = free_port()
        while self.debug_port == self.server_port:
            self.debug_port = free_port()
        self.server = subprocess.Popen(
            [
                "python3",
                "dashboard.py",
                "--manifest",
                str(manifest),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.server_port),
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(self._stop, self.server)
        wait_http(f"http://127.0.0.1:{self.server_port}/api/status")
        self.chrome = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--hide-scrollbars",
                "--window-size=1280,900",
                f"--remote-debugging-port={self.debug_port}",
                f"--user-data-dir={Path(self.tmp.name) / 'chrome'}",
                f"http://127.0.0.1:{self.server_port}/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(self._stop, self.chrome)
        targets = wait_http(
            f"http://127.0.0.1:{self.debug_port}/json/list"
        )
        page = next(
            target
            for target in targets
            if target.get("type") == "page"
            and str(self.server_port) in target.get("url", "")
        )
        self.ws = WebSocket(page["webSocketDebuggerUrl"])
        self.addCleanup(self.ws.close)
        self.ws.call(1, "Runtime.enable")
        self.ws.call(2, "Page.enable")
        time.sleep(1.5)

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _evaluate(self, expression, call_id):
        response = self.ws.call(
            call_id,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        if "exceptionDetails" in response.get("result", {}):
            self.fail(json.dumps(response["result"]["exceptionDetails"]))
        return response["result"]["result"].get("value")

    def test_dashboard_ui_smoke(self):
        result = self._evaluate(UI_EXPRESSION, 3)
        self.assertIn(result["initialTheme"], {"dark", "light"})
        self.assertIn(result["toggled"]["theme"], {"dark", "light"})
        self.assertNotEqual(result["toggled"]["theme"], result["initialTheme"])
        self.assertEqual(
            result["toggled"]["stored"], result["toggled"]["theme"]
        )
        self.assertEqual(
            result["toggled"]["pressed"],
            "true" if result["toggled"]["theme"] == "light" else "false",
        )
        self.assertEqual(result["ctaMissingIcons"], [])
        self.assertEqual(result["missingUseTargets"], [])
        self.assertGreaterEqual(result["spriteSymbols"], 21)
        self.assertGreaterEqual(result["ctaCount"], 20)
        self.assertEqual(result["maxOpen"], 1)
        self.assertEqual(result["closedOpen"], 0)
        self.assertEqual(result["removeIcon"], "#icon-x")
        self.assertEqual(result["paused"]["value"], "true")
        self.assertEqual(result["paused"]["label"], "Resume")
        self.assertEqual(result["paused"]["pauseIcon"], "none")
        self.assertEqual(result["paused"]["playIcon"], "block")
        self.assertFalse(result["horizontalOverflow"])

        self.ws.call(4, "Page.reload", {"ignoreCache": True})
        time.sleep(1.5)
        reloaded = self._evaluate(
            "({theme: document.documentElement.dataset.theme,"
            " stored: localStorage.getItem('gads.theme')})",
            5,
        )
        self.assertEqual(reloaded["theme"], result["toggled"]["theme"])
        self.assertEqual(reloaded["stored"], result["toggled"]["theme"])


if __name__ == "__main__":
    unittest.main()
