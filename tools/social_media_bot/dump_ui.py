#!/usr/bin/env python3
"""Dump the Appium page source of one phone to fix UI locators.

When a platform changes its layout (for example the new Facebook composer page),
open the screen you want to inspect on the phone, then run this script. It prints
the interactive elements (class, text, content-desc, resource-id, bounds) and can
save the full XML for sharing::

    python3 dump_ui.py --manifest rack-01.json --udid R58M00000001
    python3 dump_ui.py --manifest rack-01.json --slot slot-01 --out fb-post.xml

The phone must already have an Appium server running on its port.
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:  # Support both package and direct-script execution.
    from .console import configure_stdio
    from .rack import DeviceSpec, load_rack
    from .social_media_bot import SocialMediaBot
except ImportError:  # pragma: no cover - depends on how the script is loaded
    from console import configure_stdio
    from rack import DeviceSpec, load_rack
    from social_media_bot import SocialMediaBot

INTERESTING_CLASSES = ("EditText", "Button", "TextView", "ImageView")


def summarize_page_source(
    xml_text: str, limit: Optional[int] = 60
) -> List[Dict[str, str]]:
    """Return the labelled nodes of an Appium page source, in document order."""

    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"page source bukan XML yang valid: {exc}") from exc

    rows: List[Dict[str, str]] = []
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        class_name = node.get("class") or ""
        if not text and not desc:
            continue
        if not any(name in class_name for name in INTERESTING_CLASSES):
            continue
        rows.append(
            {
                "class": class_name.rsplit(".", 1)[-1],
                "text": text,
                "desc": desc,
                "resource_id": node.get("resource-id") or "",
                "bounds": node.get("bounds") or "",
            }
        )
        if limit is not None and len(rows) >= limit:
            break
    return rows


def find_device(
    rack, *, udid: Optional[str] = None, slot: Optional[str] = None
) -> DeviceSpec:
    """Return the manifest entry matching *udid* or *slot* (``device_name``)."""

    for spec in rack.devices:
        if udid and spec.udid == udid:
            return spec
        if slot and str(spec.connect_options.get("device_name")) == slot:
            return spec
    label = udid or slot or ""
    raise ValueError(f"device {label!r} tidak ada di manifest")


def _clip(value: str, width: int) -> str:
    value = (value or "").replace("\n", " ")
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def format_rows(rows: Sequence[Dict[str, str]]) -> str:
    """Render the element table printed on the console."""

    lines = [
        f"{'#':<3} {'class':<12} {'text':<38} {'content-desc':<22} bounds"
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"{index:<3} {_clip(row['class'], 12):<12} "
            f"{_clip(row['text'], 38):<38} {_clip(row['desc'], 22):<22} "
            f"{row['bounds']}"
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dump Appium page source elements for one phone."
    )
    parser.add_argument(
        "--manifest",
        default="rack-01.json",
        help="Rack JSON manifest (default: rack-01.json)",
    )
    parser.add_argument("--udid", help="Device UDID to inspect")
    parser.add_argument(
        "--slot", help="Device slot name (device_name), e.g. slot-01"
    )
    parser.add_argument(
        "--host", help="Override the Appium host defined in the manifest"
    )
    parser.add_argument(
        "--scheme", default="http", help="http or https (default: http)"
    )
    parser.add_argument(
        "--out", help="Write the raw page source XML to this file"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=60,
        help="Maximum rows to print; 0 means no limit (default: 60)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)

    if not args.udid and not args.slot:
        print("Pilih device: --udid <UDID> atau --slot slot-01")
        return 2

    try:
        rack = load_rack(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"Manifest tidak valid: {exc}")
        return 2

    try:
        spec = find_device(rack, udid=args.udid, slot=args.slot)
    except ValueError as exc:
        print(str(exc))
        return 2

    host = args.host or rack.host or "127.0.0.1"
    bot = SocialMediaBot(host=host, scheme=args.scheme)
    print(f"Menghubungkan {spec.udid} di {host}:{spec.appium_port} ...")
    try:
        driver = bot.connect_device(
            spec.udid, spec.appium_port, **dict(spec.connect_options)
        )
        source = driver.page_source
    except Exception as exc:  # noqa: BLE001 - reported to the operator
        print(f"Gagal membaca UI: {exc}")
        print(
            "Pastikan Appium di port itu hidup dan layar target (mis. halaman "
            "'Buat postingan baru') sedang terbuka di HP."
        )
        return 1
    finally:
        bot.disconnect_all()

    if args.out:
        path = Path(args.out)
        path.write_text(source, encoding="utf-8")
        print(f"Page source disimpan: {path.resolve()}")

    try:
        rows = summarize_page_source(
            source, limit=args.limit if args.limit > 0 else None
        )
    except ValueError as exc:
        print(str(exc))
        return 1

    if not rows:
        print(
            "Tidak ada elemen berlabel. Buka dulu layar yang ingin diperiksa "
            "(mis. halaman buat postingan), lalu jalankan lagi."
        )
        return 1

    print()
    print(format_rows(rows))
    print()
    print(f"{len(rows)} elemen ditampilkan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

