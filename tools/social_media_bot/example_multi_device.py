#!/usr/bin/env python3
"""Contoh: kendalikan beberapa HP Android sekaligus lewat Appium.

Jalankan dari folder ini:

    python3 example_multi_device.py

Ganti UDID dan port dengan milik Anda. UDID bisa dilihat dengan ``adb devices``
(atau dari daftar device di UI GADS), sedangkan port adalah port Appium server
yang melayani masing-masing perangkat.
"""

import logging

from console import configure_stdio
from social_media_bot import SocialMediaBot

# udid -> port Appium masing-masing perangkat
DEVICES = {
    "R58M1234ABC": 4723,
    "R58M5678DEF": 4724,
    "R58M9012GHI": 4725,
    "R58M3456JKL": 4726,
}


def main() -> None:
    configure_stdio()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    bot = SocialMediaBot(host="127.0.0.1")
    try:
        bot.connect_devices(DEVICES)

        for udid, connection in bot.connections.items():
            print(f"{udid} -> session {connection.session_id} ({connection.server_url})")

        # Tempat alur otomasi media sosial Anda, misalnya:
        # from appium.webdriver.common.appiumby import AppiumBy
        #
        # driver = bot.get_driver("R58M1234ABC")
        # driver.activate_app("com.instagram.android")
        # driver.find_element(AppiumBy.ACCESSIBILITY_ID, "Profile").click()
    finally:
        closed = bot.disconnect_all()
        print(f"{closed} session ditutup")


if __name__ == "__main__":
    main()
