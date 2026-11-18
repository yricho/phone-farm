"""Tests for the multi-platform adapter registry and generic flows."""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import platforms.base as base  # noqa: E402
import platforms.facebook as facebook_adapter  # noqa: E402
from errors import PlatformActionNotSupported  # noqa: E402
from models import (  # noqa: E402
    STATUS_ALREADY_FOLLOWING,
    STATUS_COMMENTED,
    STATUS_FOLLOWED,
    STATUS_LIKED,
    STATUS_POSTED,
    STATUS_REPLIED,
    STATUS_SAVED,
)
from platforms import default_platforms, platform_catalog  # noqa: E402
from platforms.instagram import InstagramPlatform  # noqa: E402
from platforms.threads import ThreadsPlatform  # noqa: E402
from platforms.tiktok import TikTokPlatform  # noqa: E402
from platforms.x import XPlatform  # noqa: E402


class FakeElement:
    def __init__(
        self,
        text="",
        content_desc="",
        class_name="android.widget.TextView",
        resource_id="",
        displayed=True,
    ):
        self.text = text
        self.content_desc = content_desc
        self.class_name = class_name
        self.resource_id = resource_id
        self.displayed = displayed
        self.clicked = 0

    def click(self):
        self.clicked += 1

    def clear(self):
        self.text = ""

    def send_keys(self, value):
        self.text += value

    def is_displayed(self):
        return self.displayed

    def get_attribute(self, name):
        return {
            "text": self.text,
            "content-desc": self.content_desc,
            "class": self.class_name,
            "resource-id": self.resource_id,
        }.get(name)


def _match_uiautomator(element, selector):
    for pattern in ("textContains", "descriptionContains", "text"):
        match = re.search(rf'{pattern}\("((?:[^"\\]|\\.)*)"\)', selector)
        if match is None:
            continue
        needle = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
        if pattern == "text":
            return element.text == needle
        if pattern == "descriptionContains":
            return needle in element.content_desc
        return needle in element.text
    return False


class FakeDriver:
    def __init__(self, elements, app_state=4):
        self.elements = list(elements)
        self.app_state = app_state
        self.activated = []
        self.keycodes = []
        self.keyboard_hidden = 0
        self.scripts = []

    def activate_app(self, package):
        self.activated.append(package)

    def query_app_state(self, package):
        return self.app_state

    def press_keycode(self, keycode):
        self.keycodes.append(keycode)

    def hide_keyboard(self):
        self.keyboard_hidden += 1

    def execute_script(self, script, args=None):
        self.scripts.append((script, args))
        return None

    def find_elements(self, by, value):
        return [
            element
            for element in self.elements
            if self._matches(element, by, value)
        ]

    @classmethod
    def _matches(cls, element, by, value):
        if by == "accessibility id":
            return value in (element.content_desc, element.text)
        if by == "class name":
            return element.class_name == value
        if by == "id":
            return element.resource_id == value
        if by == "xpath":
            tokens = [
                first or second
                for first, second in re.findall(
                    r'"([^"]*)"|\'([^\']*)\'', value
                )
            ]
            return any(
                token in (element.text, element.content_desc)
                for token in tokens
            )
        if by == "-android uiautomator":
            return _match_uiautomator(element, value)
        return False


def build_follow_elements(username="target.account", following_text=None):
    entry = FakeElement(text="Search", content_desc="Search")
    box = FakeElement(
        class_name="android.widget.EditText",
        resource_id="com.example:id/search_edit_text",
    )
    profile = FakeElement(text=username)
    elements = [entry, box, profile]
    refs = {"entry": entry, "box": box, "profile": profile}
    if following_text is not None:
        following = FakeElement(text=following_text, content_desc=following_text)
        elements.append(following)
        refs["following"] = following
        refs["follow"] = None
    else:
        follow = FakeElement(text="Follow", content_desc="Follow")
        elements.append(follow)
        refs["following"] = None
        refs["follow"] = follow
    return elements, refs


def build_post_elements(composer_text="Compose", submit_text="Post"):
    composer = FakeElement(text=composer_text, content_desc=composer_text)
    editor = FakeElement(class_name="android.widget.EditText")
    submit = FakeElement(text=submit_text, content_desc=submit_text)
    return [composer, editor, submit], {
        "composer": composer,
        "editor": editor,
        "submit": submit,
    }


def build_comment_elements(comment_text="Comment", send_text="Post"):
    button = FakeElement(text=comment_text, content_desc=comment_text)
    editor = FakeElement(class_name="android.widget.EditText")
    send = FakeElement(text=send_text, content_desc=send_text)
    return [button, editor, send], {
        "button": button,
        "editor": editor,
        "send": send,
    }


class PlatformRegistryTests(unittest.TestCase):
    def test_registry_contains_five_networks_with_expected_packages(self):
        adapters = default_platforms()

        self.assertEqual(
            sorted(adapters),
            ["facebook", "instagram", "threads", "tiktok", "x"],
        )
        self.assertEqual(
            adapters["instagram"].info.package, "com.instagram.android"
        )
        self.assertEqual(
            adapters["threads"].info.package, "com.instagram.barcelona"
        )
        self.assertEqual(adapters["x"].info.package, "com.twitter.android")
        self.assertEqual(
            adapters["tiktok"].info.package, "com.zhiliaoapp.musically"
        )

    def test_capability_matrix(self):
        adapters = default_platforms()

        self.assertTrue(adapters["facebook"].supports("post"))
        self.assertFalse(adapters["instagram"].supports("post"))
        self.assertTrue(adapters["threads"].supports("post"))
        self.assertTrue(adapters["x"].supports("post"))
        self.assertFalse(adapters["tiktok"].supports("post"))
        for adapter in adapters.values():
            self.assertTrue(adapter.supports("follow"))
            self.assertTrue(adapter.supports("comment"))
            self.assertTrue(adapter.supports("like"))
            self.assertTrue(adapter.supports("save"))
            self.assertTrue(adapter.supports("reply"))

    def test_catalog_exposes_actions_for_the_dashboard(self):
        catalog = {item["key"]: item for item in platform_catalog()}

        self.assertEqual(len(catalog), 5)
        self.assertEqual(
            sorted(catalog["instagram"]["actions"]),
            ["comment", "follow", "like", "reply", "save"],
        )
        self.assertEqual(
            sorted(catalog["x"]["actions"]),
            ["comment", "follow", "like", "post", "reply", "save"],
        )
        self.assertTrue(catalog["threads"]["notes"])

    def test_facebook_adapter_delegates_to_legacy_functions(self):
        adapter = facebook_adapter.FacebookPlatform()
        with mock.patch.object(
            facebook_adapter.facebook_actions,
            "follow_user",
            return_value="followed",
        ) as legacy:
            result = adapter.follow_user(object(), "target.account")

        self.assertEqual(result, "followed")
        legacy.assert_called_once()


class PlatformFlowTests(unittest.TestCase):
    def setUp(self):
        sleep_patcher = mock.patch.object(base.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_instagram_follow_user(self):
        elements, refs = build_follow_elements()
        driver = FakeDriver(elements)

        result = InstagramPlatform().follow_user(
            driver, "target.account", delay_range=(0, 0), timeout=0.1
        )

        self.assertEqual(result.status, STATUS_FOLLOWED)
        self.assertEqual(result.username, "target.account")
        self.assertEqual(driver.activated, ["com.instagram.android"])
        self.assertEqual(refs["box"].text, "target.account")
        self.assertEqual(refs["profile"].clicked, 1)
        self.assertEqual(refs["follow"].clicked, 1)
        self.assertEqual(driver.keycodes, [66])

    def test_threads_post(self):
        elements, refs = build_post_elements(composer_text="Create")
        driver = FakeDriver(elements)

        result = ThreadsPlatform().create_post(
            driver, "Halo Threads", delay_range=(0, 0), timeout=0.1
        )

        self.assertEqual(result.status, STATUS_POSTED)
        self.assertTrue(result.posted)
        self.assertEqual(driver.activated, ["com.instagram.barcelona"])
        self.assertEqual(refs["composer"].clicked, 1)
        self.assertEqual(refs["editor"].text, "Halo Threads")
        self.assertEqual(refs["submit"].clicked, 1)

    def test_x_post_and_reply(self):
        elements, refs = build_post_elements(composer_text="Compose")
        driver = FakeDriver(elements)

        result = XPlatform().create_post(
            driver, "Halo X", delay_range=(0, 0), timeout=0.1
        )

        self.assertEqual(result.status, STATUS_POSTED)
        self.assertEqual(refs["editor"].text, "Halo X")

        elements, refs = build_comment_elements()
        driver = FakeDriver(elements)
        reply = XPlatform().comment_on_post(
            driver,
            "https://x.com/example/status/1",
            "Balasan",
            delay_range=(0, 0),
            timeout=0.1,
        )

        self.assertEqual(reply.status, STATUS_COMMENTED)
        self.assertEqual(driver.scripts[0][0], "mobile: deepLink")
        self.assertEqual(
            driver.scripts[0][1]["package"], "com.twitter.android"
        )
        self.assertEqual(refs["editor"].text, "Balasan")
        self.assertEqual(refs["send"].clicked, 1)

    def test_tiktok_already_following(self):
        elements, refs = build_follow_elements(following_text="Mengikuti")
        driver = FakeDriver(elements)

        result = TikTokPlatform().follow_user(
            driver, "target.account", delay_range=(0, 0), timeout=0.1
        )

        self.assertEqual(result.status, STATUS_ALREADY_FOLLOWING)
        self.assertFalse(result.followed)
        self.assertIsNone(refs["follow"])

    def test_instagram_like_and_save(self):
        like = FakeElement(text="Like", content_desc="Like")
        driver = FakeDriver([like])
        result = InstagramPlatform().like_post(
            driver,
            "https://www.instagram.com/p/example/",
            delay_range=(0, 0),
            timeout=0.1,
        )
        self.assertEqual(result.status, STATUS_LIKED)
        self.assertEqual(like.clicked, 1)

        save = FakeElement(text="Save", content_desc="Save")
        driver = FakeDriver([save])
        result = InstagramPlatform().save_post(
            driver,
            "https://www.instagram.com/p/example/",
            delay_range=(0, 0),
            timeout=0.1,
        )
        self.assertEqual(result.status, STATUS_SAVED)
        self.assertEqual(save.clicked, 1)

    def test_x_reply_to_post(self):
        elements, refs = build_comment_elements()
        driver = FakeDriver(elements)
        result = XPlatform().reply_to_post(
            driver,
            "https://x.com/example/status/1",
            "Balasan",
            delay_range=(0, 0),
            timeout=0.1,
        )
        self.assertEqual(result.status, STATUS_REPLIED)
        self.assertEqual(refs["editor"].text, "Balasan")
        self.assertEqual(refs["send"].clicked, 1)

    def test_comment_opens_the_comment_box_when_needed(self):
        elements, refs = build_comment_elements(comment_text="Komentari")
        refs["editor"].displayed = False

        class RevealDriver(FakeDriver):
            def find_elements(self, by, value):
                matches = super().find_elements(by, value)
                if refs["button"] in matches:
                    refs["editor"].displayed = True
                return matches

        driver = RevealDriver(elements)
        result = InstagramPlatform().comment_on_post(
            driver,
            "https://www.instagram.com/p/abc123/",
            "Halo",
            delay_range=(0, 0),
            timeout=0.1,
        )

        self.assertTrue(result.commented)
        self.assertEqual(refs["button"].clicked, 1)
        self.assertEqual(refs["editor"].text, "Halo")

    def test_unsupported_media_posts_raise(self):
        driver = FakeDriver([])

        for adapter in (InstagramPlatform(), TikTokPlatform()):
            with self.subTest(platform=adapter.key):
                with self.assertRaises(PlatformActionNotSupported):
                    adapter.create_post(driver, "Halo")

    def test_argument_validation(self):
        driver = FakeDriver([])

        with self.assertRaises(ValueError):
            InstagramPlatform().follow_user(
                driver, "@", delay_range=(0, 0), timeout=0.1
            )
        with self.assertRaises(ValueError):
            ThreadsPlatform().create_post(
                driver, "x" * 501, delay_range=(0, 0), timeout=0.1
            )
        with self.assertRaises(ValueError):
            XPlatform().comment_on_post(
                driver, "ftp://x.com/status/1", "Halo", timeout=0.1
            )
        with self.assertRaises(ValueError):
            InstagramPlatform().follow_user(
                driver,
                "target",
                delay_range=(7, 3),
                timeout=0.1,
            )

    def test_default_random_delay_is_between_3_and_7_seconds(self):
        elements, _refs = build_follow_elements()
        driver = FakeDriver(elements)

        with mock.patch.object(
            base.random, "uniform", return_value=5.0
        ) as uniform:
            result = InstagramPlatform().follow_user(
                driver, "target.account", timeout=0.1
            )

        self.assertTrue(result.followed)
        for call in uniform.call_args_list:
            self.assertEqual(call, mock.call(3.0, 7.0))
        self.assertGreaterEqual(
            self.sleep.call_args_list.count(mock.call(5.0)), 6
        )


if __name__ == "__main__":
    unittest.main()
