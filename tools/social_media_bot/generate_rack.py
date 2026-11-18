#!/usr/bin/env python3
"""Generate a rack manifest from Android ``adb devices -l`` output.

Examples::

    python generate_rack.py --output rack-01.json
    python generate_rack.py --name rack-lab --first-port 4723 --force
    python generate_rack.py --input adb-devices.txt --output rack-01.json

The command only includes devices whose ADB status is ``device``. Devices in
``unauthorized``, ``offline`` or other states are reported and skipped.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

try:  # Support both package and direct-script execution.
    from .console import configure_stdio
except ImportError:  # pragma: no cover - direct script execution
    from console import configure_stdio

DEFAULT_FIRST_PORT = 4723
DEFAULT_HOST = "127.0.0.1"


def parse_adb_devices(output: str) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Return ``(ready_serials, skipped)`` from ``adb devices -l`` output.

    ``ready_serials`` keeps ADB order, removes duplicates, and contains only
    lines whose status is exactly ``device``. ``skipped`` contains pairs of
    ``(serial, status)`` for devices that need attention.
    """

    ready: List[str] = []
    skipped: List[Tuple[str, str]] = []
    seen = set()

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, status = parts[0], parts[1]
        if status == "device":
            if serial not in seen:
                seen.add(serial)
                ready.append(serial)
        else:
            skipped.append((serial, status))

    return ready, skipped


def build_manifest(
    serials: Sequence[str],
    *,
    name: str = "rack-01",
    host: str = DEFAULT_HOST,
    first_port: int = DEFAULT_FIRST_PORT,
) -> Dict[str, object]:
    """Build a JSON-serializable manifest with consecutive Appium ports."""

    devices = [
        {
            "udid": serial,
            "appium_port": first_port + index,
            "device_name": f"slot-{index + 1:02d}",
        }
        for index, serial in enumerate(serials)
    ]
    return {"rack": name, "host": host, "devices": devices}


def run_adb(adb: str = "adb", timeout: float = 20.0) -> str:
    """Run ``adb devices -l`` and return stdout as UTF-8 text."""

    result = subprocess.run(
        [adb, "devices", "-l"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            f"`{adb} devices -l` gagal (exit {result.returncode}): {message}"
        )
    return result.stdout


def write_manifest(manifest: Dict[str, object], output: Union[str, Path]) -> Path:
    """Write UTF-8 JSON (without BOM) and return the resolved path."""

    path = Path(output)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a rack manifest from `adb devices -l`."
    )
    parser.add_argument(
        "--output",
        default="rack-01.json",
        help="Output manifest path (default: rack-01.json)",
    )
    parser.add_argument(
        "--name", default="rack-01", help="Rack name in the manifest"
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="Appium host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--first-port",
        type=int,
        default=DEFAULT_FIRST_PORT,
        help=f"First Appium port (default: {DEFAULT_FIRST_PORT})",
    )
    parser.add_argument(
        "--adb", default="adb", help="Path to the adb executable (default: adb)"
    )
    parser.add_argument(
        "--input",
        help="Read saved `adb devices -l` output from this file instead of running adb",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite --output if it already exists",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)

    output = Path(args.output)
    if output.exists() and not args.force:
        print(
            f"{output} sudah ada. Gunakan --force untuk menimpa, "
            "atau pilih --output lain."
        )
        return 2

    try:
        if args.input:
            text = Path(args.input).read_text(encoding="utf-8-sig")
        else:
            text = run_adb(args.adb)
    except FileNotFoundError:
        print(
            f"adb tidak ditemukan: {args.adb!r}. "
            "Tambahkan Android platform-tools ke PATH."
        )
        return 2
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Gagal membaca daftar device: {exc}")
        return 2

    ready, skipped = parse_adb_devices(text)
    if not ready:
        print("Tidak ada device berstatus `device` di output adb.")
        if skipped:
            print("Device yang perlu diperiksa:")
            for serial, status in skipped:
                print(f"  {serial:<22} {status}")
        return 2

    manifest = build_manifest(
        ready,
        name=args.name,
        host=args.host,
        first_port=args.first_port,
    )
    path = write_manifest(manifest, output)

    last_port = args.first_port + len(ready) - 1
    print(
        f"Rack {args.name!r}: {len(ready)} device, "
        f"port {args.first_port}..{last_port}"
    )
    for index, serial in enumerate(ready, 1):
        print(
            f"  slot-{index:02d}  {serial:<22} port {args.first_port + index - 1}"
        )

    if skipped:
        print("Peringatan: device berikut dilewati:")
        for serial, status in skipped:
            print(f"  {serial:<22} {status}")

    print(f"Manifest ditulis: {path.resolve()}")
    print("Sesuaikan device_name dengan label fisik slot sebelum produksi.")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
