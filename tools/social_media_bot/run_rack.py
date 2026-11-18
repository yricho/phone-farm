#!/usr/bin/env python3
"""CLI: connect a whole rack (typically 20 phones) from a JSON manifest.

Examples::

    python3 run_rack.py --manifest rack-01.example.json
    python3 run_rack.py --manifest rack-01.json --workers 10 --hold
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Dict, List, Optional

try:  # Support both package and direct-script execution.
    from .console import configure_stdio
    from .rack import load_rack
    from .social_media_bot import Driver, SocialMediaBot, SocialMediaBotError
except ImportError:  # pragma: no cover - depends on how the script is loaded
    from console import configure_stdio
    from rack import load_rack
    from social_media_bot import Driver, SocialMediaBot, SocialMediaBotError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Connect every phone in a rack manifest over Appium."
    )
    parser.add_argument(
        "--manifest", required=True, help="Path to the rack JSON manifest"
    )
    parser.add_argument(
        "--host", help="Override the Appium host defined in the manifest"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Parallel connection workers (default: SocialMediaBot default)",
    )
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Keep the sessions open until Ctrl+C",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    try:
        rack = load_rack(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"Manifest tidak valid: {exc}")
        return 2

    bot = SocialMediaBot(host=args.host or rack.host or "127.0.0.1")
    connected: Dict[str, Driver] = {}
    missing: List[str] = list(rack.device_map)
    try:
        connected = bot.connect_devices(
            rack, continue_on_error=True, max_workers=args.workers
        )

        print(f"Rack {rack.name!r}: {len(connected)}/{len(rack)} HP terhubung")
        for udid, connection in bot.connections.items():
            print(
                f"  {udid:<22} port {connection.appium_port:<6} "
                f"session {connection.session_id}"
            )

        missing = [udid for udid in rack.device_map if udid not in connected]
        if missing:
            print("Tidak terhubung: " + ", ".join(missing))

        if args.hold and connected:
            print("Tekan Ctrl+C untuk menutup semua session...")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nMenutup semua session...")
    except (SocialMediaBotError, ValueError) as exc:
        print(f"Gagal menjalankan rack: {exc}")
        return 2
    finally:
        bot.disconnect_all()

    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
