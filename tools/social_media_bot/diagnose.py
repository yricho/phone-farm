#!/usr/bin/env python3
"""Diagnose why phones in a rack manifest cannot connect through Appium.

The order of checks mirrors the connection flow, so the first problem found is
the one to fix:

1. the manifest itself (host, duplicate UDIDs/ports);
2. ``adb devices -l`` on this machine versus the UDIDs in the manifest;
3. ``GET /status`` on the Appium server of every device;
4. optionally one real ``POST /session`` per device (``--live-test``).

Examples::

    python3 diagnose.py --manifest rack-01.json
    python3 diagnose.py --manifest rack-01.json --live-test
    python3 diagnose.py --manifest rack-01.json --input adb-devices.txt
    python3 diagnose.py --manifest rack-01.json --no-adb --appium-host 192.168.1.20

Exit codes: ``0`` every check passed, ``1`` problems found, ``2`` the manifest
could not be read.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # Support both package and direct-script execution.
    from .console import configure_stdio
    from .generate_rack import parse_adb_devices, run_adb
    from .rack import DeviceSpec, Rack, load_rack
except ImportError:  # pragma: no cover - depends on how the script is loaded
    from console import configure_stdio
    from generate_rack import parse_adb_devices, run_adb
    from rack import DeviceSpec, Rack, load_rack

DEFAULT_TIMEOUT = 3.0
LIVE_ERROR_LIMIT = 300


@dataclass
class AppiumProbe:
    """Result of the ``GET /status`` probe for one Appium server."""

    ready: bool
    detail: str


@dataclass
class DeviceReport:
    """Per-device diagnosis: identity plus every problem found."""

    slot: str
    udid: str
    port: int
    issues: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


def _package_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _module_importable(name: str) -> bool:
    try:
        importlib.import_module(name)
    except Exception:  # noqa: BLE001 - any import failure means unusable
        return False
    return True


@dataclass
class DependencyStatus:
    """Appium-Python-Client / selenium availability for this interpreter."""

    python: str
    executable: str
    appium_version: Optional[str]
    selenium_version: Optional[str]
    importable: bool

    @property
    def ok(self) -> bool:
        return self.importable

    def summary(self) -> str:
        appium = self.appium_version or "TIDAK TERPASANG"
        selenium = self.selenium_version or "TIDAK TERPASANG"
        state = "OK" if self.ok else "MASALAH"
        return (
            f"Python {self.python}: Appium-Python-Client {appium}, "
            f"selenium {selenium} [{state}]"
        )


def check_dependencies() -> DependencyStatus:
    """Report whether the interpreter running this script can use Appium."""

    return DependencyStatus(
        python=sys.version.split()[0],
        executable=sys.executable,
        appium_version=_package_version("Appium-Python-Client"),
        selenium_version=_package_version("selenium"),
        importable=_module_importable("appium"),
    )


@dataclass
class AndroidEnv:
    """``ANDROID_HOME``/``ANDROID_SDK_ROOT`` as seen by the Appium server."""

    android_home: Optional[str]
    android_sdk_root: Optional[str]
    adb_path: Optional[str]
    java_home: Optional[str] = None
    java_path: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.android_home or self.android_sdk_root)

    @property
    def java_ok(self) -> bool:
        return bool(self.java_home or self.java_path)

    def summary(self) -> str:
        home = self.android_home or "-"
        root = self.android_sdk_root or "-"
        adb = self.adb_path or "tidak ditemukan"
        state = "OK" if self.ok else "MASALAH"
        return (
            f"ANDROID_HOME {home} · ANDROID_SDK_ROOT {root} · adb {adb} "
            f"[{state}]"
        )

    def java_summary(self) -> str:
        home = self.java_home or "-"
        java = self.java_path or "tidak ditemukan"
        state = "OK" if self.java_ok else "MASALAH"
        return f"JAVA_HOME {home} · java {java} [{state}]"


def check_android_env() -> AndroidEnv:
    """Inspect the Android SDK/Java variables the uiautomator2 driver requires."""

    return AndroidEnv(
        android_home=os.environ.get("ANDROID_HOME") or None,
        android_sdk_root=os.environ.get("ANDROID_SDK_ROOT") or None,
        adb_path=shutil.which("adb"),
        java_home=os.environ.get("JAVA_HOME") or None,
        java_path=shutil.which("java"),
    )


def probe_appium(
    host: str,
    port: int,
    *,
    scheme: str = "http",
    timeout: float = DEFAULT_TIMEOUT,
) -> AppiumProbe:
    """Call ``GET /status`` on one Appium server and summarise the answer."""

    url = f"{scheme}://{host}:{port}/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return AppiumProbe(False, f"HTTP {exc.code} dari {url}")
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return AppiumProbe(False, f"tidak ada Appium di {url} ({reason})")

    try:
        payload = json.loads(body)
    except ValueError:
        return AppiumProbe(False, f"respons bukan JSON dari {url}")

    value = payload.get("value") if isinstance(payload, dict) else None
    if isinstance(value, dict) and value.get("ready") is False:
        message = value.get("message") or "status ready=false"
        return AppiumProbe(False, f"Appium belum siap di {url} ({message})")
    return AppiumProbe(True, f"Appium merespons di {url}")


def slot_name(spec: DeviceSpec) -> str:
    """Return the human label of a device (``device_name`` or the UDID)."""

    return str(spec.connect_options.get("device_name") or spec.udid)


def analyse_device(
    spec: DeviceSpec,
    probe: AppiumProbe,
    adb_serials: Optional[Sequence[str]],
) -> List[str]:
    """Return every reason *spec* would fail to connect."""

    issues: List[str] = []
    if not probe.ready:
        issues.append(probe.detail)
    if adb_serials is not None and spec.udid not in adb_serials:
        issues.append(
            f"UDID {spec.udid!r} tidak ada di `adb devices` "
            "(UDID contoh, atau HP tersambung ke PC lain)"
        )
    return issues


def collect_reports(
    rack: Rack,
    host: str,
    *,
    scheme: str = "http",
    timeout: float = DEFAULT_TIMEOUT,
    adb_serials: Optional[Sequence[str]] = None,
    probe: Callable[..., AppiumProbe] = probe_appium,
) -> List[DeviceReport]:
    """Probe every Appium server in *rack* and collect per-device problems."""

    reports: List[DeviceReport] = []
    for spec in rack.devices:
        result = probe(host, spec.appium_port, scheme=scheme, timeout=timeout)
        reports.append(
            DeviceReport(
                slot=slot_name(spec),
                udid=spec.udid,
                port=spec.appium_port,
                issues=analyse_device(spec, result, adb_serials),
            )
        )
    return reports


def live_test(rack: Rack, host: str, *, scheme: str = "http") -> Dict[str, str]:
    """Open and immediately close one real Appium session per device.

    Returns ``udid -> error message`` (empty string means the session was
    created successfully), matching what ``connect_devices`` would report.
    """

    try:  # Support both package and direct-script execution.
        from .social_media_bot import SocialMediaBot
    except ImportError:  # pragma: no cover - direct script execution
        from social_media_bot import SocialMediaBot

    bot = SocialMediaBot(host=host, scheme=scheme)
    errors: Dict[str, str] = {}
    try:
        for spec in rack.devices:
            try:
                bot.connect_device(
                    spec.udid,
                    spec.appium_port,
                    **dict(spec.connect_options),
                )
            except Exception as exc:  # noqa: BLE001 - reported per device
                errors[spec.udid] = str(exc)
            else:
                errors[spec.udid] = ""
        return errors
    finally:
        bot.disconnect_all()


def _short(message: str, limit: int = LIVE_ERROR_LIMIT) -> str:
    text = " ".join(str(message).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


#: ``(substring of the Appium error, explanation)`` used by ``--live-test``.
ERROR_HINTS: Sequence[Tuple[str, str]] = (
    (
        "neither android_home nor android_sdk_root",
        "Appium tidak punya ANDROID_HOME/ANDROID_SDK_ROOT. Set keduanya ke "
        "folder SDK Android (bukan folder platform-tools), lalu tutup dan buka "
        "ulang window Appium.",
    ),
    (
        "connection refused",
        "Appium tidak berjalan di port itu. Jalankan `appium -p <port>` lalu cek "
        "`curl http://<host>:<port>/status`.",
    ),
    (
        "failed to establish a new connection",
        "Appium tidak berjalan di port itu, atau host/port di manifest salah.",
    ),
    (
        "max retries exceeded",
        "Appium tidak bisa dihubungi: cek host/port manifest dan pastikan server "
        "`appium -p <port>` hidup.",
    ),
    (
        "could not find a connected android device",
        "UDID tidak dikenal adb di mesin Appium. Cek `adb devices -l` di mesin itu, "
        "lalu buat ulang manifest: `python generate_rack.py --output "
        "<manifest> --force`.",
    ),
    (
        "unable to find an active device or emulator with udid",
        "UDID di manifest tidak ada di mesin Appium: salah ketik, HP tersambung "
        "ke PC lain, atau masih UDID contoh.",
    ),
    (
        "unauthorized",
        "HP belum mengizinkan USB debugging: terima dialog Allow USB debugging, "
        "lalu jalankan ulang `adb devices`.",
    ),
    (
        "unable to connect to adb",
        "Appium tidak menemukan adb: tambahkan Android platform-tools ke PATH, "
        "lalu restart Appium.",
    ),
    (
        "instrumentation process cannot be initialized",
        "Driver uiautomator2 gagal start: `appium driver install uiautomator2`, "
        "buka kunci layar HP, lalu cabut-pasang kabel USB.",
    ),
    (
        "requested a new session but one was in progress",
        "Masih ada session lama di port itu: restart Appium atau putuskan "
        "session sebelumnya.",
    ),
    (
        "could not proxy command",
        "Appium crash/hang saat membuat session: lihat log Appium di terminal, "
        "biasanya HP terputus atau driver bermasalah.",
    ),
    (
        "timed out",
        "Appium/HP tidak merespons tepat waktu: HP hang, kabel lemah, atau "
        "server terlalu sibuk.",
    ),
    (
        "install_failed",
        "Instalasi APK gagal di HP: cek ruang penyimpanan dan dialog izin "
        "pemasangan aplikasi.",
    ),
)


def explain_error(message: Optional[str]) -> Optional[str]:
    """Map a raw Appium error to an actionable Indonesian hint."""

    if not message:
        return None
    lowered = str(message).lower()
    for needle, hint in ERROR_HINTS:
        if needle in lowered:
            return hint
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose why a rack manifest cannot connect over Appium."
    )
    parser.add_argument(
        "--manifest",
        default="rack-01.json",
        help="Rack JSON manifest (default: rack-01.json)",
    )
    parser.add_argument(
        "--host", help="Override the Appium host defined in the manifest"
    )
    parser.add_argument(
        "--appium-host",
        dest="host",
        help="Alias for --host (same name as dashboard.py)",
    )
    parser.add_argument(
        "--scheme", default="http", help="http or https (default: http)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument("--adb", default="adb", help="Path to adb (default: adb)")
    parser.add_argument(
        "--input",
        help="Read saved `adb devices -l` output from this file instead of running adb",
    )
    parser.add_argument(
        "--no-adb",
        action="store_true",
        help="Skip the adb check (useful when the phones are on another PC)",
    )
    parser.add_argument(
        "--live-test",
        action="store_true",
        help="Also try one real Appium session per device",
    )
    return parser


def _load_adb_serials(args: argparse.Namespace) -> Optional[List[str]]:
    """Return the UDIDs adb reports as ``device``, or ``None`` when unknown."""

    try:
        if args.input:
            text = Path(args.input).read_text(encoding="utf-8-sig")
        else:
            text = run_adb(args.adb)
    except FileNotFoundError:
        print(
            f"adb tidak ditemukan ({args.adb!r}); cek UDID dilewati. "
            "Pakai --no-adb untuk membisukan pesan ini."
        )
        return None
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Gagal membaca daftar device: {exc}")
        return None

    serials, skipped = parse_adb_devices(text)
    print(f"adb: {len(serials)} device berstatus `device`")
    for serial, status in skipped:
        print(f"  dilewati: {serial:<22} {status}")
    return serials


def _print_hints(
    reports: Sequence[DeviceReport],
    adb_serials: Optional[Sequence[str]],
    android: Optional[AndroidEnv] = None,
) -> None:
    hints: List[str] = []
    if android is not None and not android.ok:
        hints.append(
            "ANDROID_HOME/ANDROID_SDK_ROOT kosong: driver uiautomator2 menolak "
            "membuat session. Arahkan ke folder yang berisi platform-tools, "
            "lalu restart Appium."
        )
    if android is not None and not android.java_ok:
        hints.append(
            "JAVA_HOME/java tidak ditemukan: install JDK 17+, set JAVA_HOME, "
            "lalu restart Appium."
        )
    broken = [report for report in reports if not report.ok]
    if broken and all(
        any("Appium" in issue for issue in report.issues) for report in broken
    ):
        hints.append(
            "Tidak ada Appium yang merespons. Jalankan satu server per port, "
            "mis. `appium -p 4723`, `appium -p 4724`, `appium -p 4725`."
        )
    if adb_serials is not None and any(
        any("adb devices" in issue for issue in report.issues) for report in broken
    ):
        hints.append(
            "UDID di manifest tidak cocok dengan adb. Buat ulang manifest: "
            "`python generate_rack.py --output <manifest> --force`."
        )
    if hints:
        print("Saran:")
        for hint in hints:
            print(f"  - {hint}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)

    deps = check_dependencies()
    print(deps.summary())
    if not deps.ok:
        print(
            "  Appium-Python-Client belum bisa di-import oleh interpreter ini. "
            "Install dengan interpreter yang sama:"
        )
        print(f'    "{deps.executable}" -m pip install -r requirements.txt')

    android = check_android_env()
    print(android.summary())
    print(android.java_summary())
    if not android.ok:
        print(
            "  Appium butuh ANDROID_HOME/ANDROID_SDK_ROOT. Arahkan ke folder yang "
            "berisi platform-tools (contoh: C:\\Android\\sdk), lalu tutup dan "
            "buka ulang window Appium."
        )
    if not android.java_ok:
        print(
            "  Driver uiautomator2 juga butuh JDK: install JDK 17+ lalu set "
            "JAVA_HOME, kemudian restart Appium."
        )

    try:
        rack = load_rack(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"Manifest tidak valid: {exc}")
        return 2

    host = args.host or rack.host or "127.0.0.1"
    print(f"Rack {rack.name!r}: {len(rack)} device · Appium host {host}")

    adb_serials = None if args.no_adb else _load_adb_serials(args)
    reports = collect_reports(
        rack,
        host,
        scheme=args.scheme,
        timeout=args.timeout,
        adb_serials=adb_serials,
    )

    print()
    for report in reports:
        state = "OK" if report.ok else "MASALAH"
        print(
            f"  {report.slot:<10} {report.udid:<22} port {report.port:<6} {state}"
        )
        for issue in report.issues:
            print(f"      - {issue}")

    exit_code = 0 if deps.ok and all(report.ok for report in reports) else 1

    live_errors: Optional[Dict[str, str]] = None
    if args.live_test:
        print()
        print("Tes session Appium sebenarnya:")
        live_errors = live_test(rack, host, scheme=args.scheme)
        for udid, error in live_errors.items():
            print(f"  {udid:<22} " + ("OK" if not error else "GAGAL"))
            if error:
                print(f"      - {_short(error)}")
                hint = explain_error(error)
                if hint:
                    print(f"      → {hint}")
        if any(live_errors.values()):
            exit_code = 1

    print()
    ready = sum(1 for report in reports if report.ok)
    suffix = ""
    if live_errors is not None:
        ready = sum(1 for error in live_errors.values() if not error)
        suffix = " (live test)"
    print(f"Siap: {ready}/{len(reports)} device{suffix}")
    _print_hints(reports, adb_serials, android)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
