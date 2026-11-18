"""Unit tests for the Facebook follow flow.

The fake driver mimics the subset of the Appium/UiAutomator2 API used by
``facebook.follow_user`` so the flow can be verified without a real phone.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import facebook  # noqa: E402
from facebook import (  # noqa: E402
    FacebookAutomationError,
    comment_on_post,
    create_post,
    follow_user,
)


class FakeElement:
    """Minimal UiAutomator2 element."""

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
    """Fake Appium driver resolving the locators used by ``follow_user``."""

    def __init__(self, elements, app_state=4):
        self.elements = list(elements)
        self.app_state = app_state
        self.activated = []
        self.keycodes = []
        self.keyboard_hidden = 0
        self.scripts = []
        self.script_error = None

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
        if self.script_error is not None:
            raise self.script_error
        return None

    def find_elements(self, by, value):
        return [element for element in self.elements if self._matches(element, by, value)]

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
                for first, second in re.findall(r'"([^"]*)"|\'([^\']*)\'', value)
            ]
            return any(
                token in (element.text, element.content_desc) for token in tokens
            )
        if by == "-android uiautomator":
            return _match_uiautomator(element, value)
        return False


def build_flow_elements(
    *,
    username="target.account",
    follow_text="Follow",
    following_text=None,
    with_people_tab=False,
):
    """Return (elements, refs) for one scripted Facebook flow."""
    search_entry = FakeElement(content_desc="Search")
    search_box = FakeElement(
        class_name="android.widget.EditText",
        resource_id="com.facebook.katana:id/search_edit_text",
    )
    profile = FakeElement(
        text=username,
        resource_id="com.facebook.katana:id/search_result_title",
    )

    elements = [search_entry, search_box, profile]
    refs = {
        "search_entry": search_entry,
        "search_box": search_box,
        "profile": profile,
        "people_tab": None,
        "follow": None,
        "following": None,
    }

    if with_people_tab:
        people_tab = FakeElement(text="People")
        elements.append(people_tab)
        refs["people_tab"] = people_tab

    if following_text is not None:
        following = FakeElement(text=following_text, content_desc=following_text)
        elements.append(following)
        refs["following"] = following
    else:
        follow = FakeElement(text=follow_text, content_desc=follow_text)
        elements.append(follow)
        refs["follow"] = follow

    return elements, refs


def build_composer_elements(
    *,
    prompt_text="What's on your mind, Tester?",
    button_text="Post",
    with_prompt=True,
    with_button=True,
):
    """Return (elements, refs) for one scripted post-composer flow."""
    prompt = FakeElement(text=prompt_text)
    editor = FakeElement(
        class_name="android.widget.EditText",
        resource_id="com.facebook.katana:id/composer_edit_text",
    )
    button = FakeElement(
        text=button_text,
        content_desc=button_text,
        class_name="android.widget.Button",
    )

    elements = [editor]
    if with_prompt:
        elements.insert(0, prompt)
    if with_button:
        elements.append(button)

    return elements, {"prompt": prompt, "editor": editor, "button": button}


class RevealOnClickElement(FakeElement):
    """Element that makes another element visible when clicked."""

    def __init__(self, reveal_target, **kwargs):
        super().__init__(**kwargs)
        self.reveal_target = reveal_target

    def click(self):
        super().click()
        self.reveal_target.displayed = True


def build_comment_elements(
    *,
    editor_visible=True,
    with_send_button=True,
    send_text="Send",
):
    """Return (elements, refs) for one scripted comment flow."""
    editor = FakeElement(
        class_name="android.widget.EditText",
        resource_id="com.facebook.katana:id/comment_edit_text",
        displayed=editor_visible,
    )
    comment_button = RevealOnClickElement(
        editor,
        text="Comment",
        content_desc="Comment",
        class_name="android.widget.Button",
    )
    send_button = FakeElement(
        text=send_text,
        content_desc=send_text,
        class_name="android.widget.Button",
    )

    elements = [comment_button, editor]
    if with_send_button:
        elements.append(send_button)

    return elements, {
        "comment_button": comment_button,
        "editor": editor,
        "send_button": send_button,
    }


class FollowUserTests(unittest.TestCase):
    def setUp(self):
        sleep_patcher = mock.patch.object(facebook.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_follow_user_happy_path(self):
        elements, refs = build_flow_elements()
        driver = FakeDriver(elements)

        result = follow_user(driver, "target.account", timeout=0.2)

        self.assertEqual(result.status, facebook.STATUS_FOLLOWED)
        self.assertTrue(result.followed)
        self.assertEqual(result.button_text, "Follow")
        self.assertEqual(driver.activated, ["com.facebook.katana"])
        self.assertEqual(driver.keycodes, [66])
        self.assertEqual(refs["search_entry"].clicked, 1)
        self.assertEqual(refs["search_box"].text, "target.account")
        self.assertEqual(refs["profile"].clicked, 1)
        self.assertEqual(refs["follow"].clicked, 1)

    def test_follow_user_strips_leading_at(self):
        elements, refs = build_flow_elements()
        driver = FakeDriver(elements)

        result = follow_user(driver, "@target.account", timeout=0.2)

        self.assertEqual(result.username, "target.account")
        self.assertEqual(refs["search_box"].text, "target.account")

    def test_follow_user_taps_people_tab_when_present(self):
        elements, refs = build_flow_elements(with_people_tab=True)
        driver = FakeDriver(elements)

        follow_user(driver, "target.account", timeout=0.2)

        self.assertEqual(refs["people_tab"].clicked, 1)

    def test_follow_user_reports_already_following(self):
        elements, refs = build_flow_elements(following_text="Mengikuti")
        driver = FakeDriver(elements)

        result = follow_user(driver, "target.account", timeout=0.2)

        self.assertEqual(result.status, facebook.STATUS_ALREADY_FOLLOWING)
        self.assertFalse(result.followed)
        self.assertEqual(result.button_text, "Mengikuti")
        self.assertEqual(refs["profile"].clicked, 1)
        self.assertIsNone(refs["follow"])

    def test_follow_user_raises_when_follow_button_is_missing(self):
        search_entry = FakeElement(content_desc="Search")
        search_box = FakeElement(
            class_name="android.widget.EditText",
            resource_id="com.facebook.katana:id/search_edit_text",
        )
        profile = FakeElement(text="target.account")
        driver = FakeDriver([search_entry, search_box, profile])

        with self.assertRaises(FacebookAutomationError) as ctx:
            follow_user(driver, "target.account", timeout=0.05)

        self.assertIn("Follow button", str(ctx.exception))

    def test_follow_user_wraps_driver_errors(self):
        class BrokenActivateDriver(FakeDriver):
            def activate_app(self, package):
                raise RuntimeError("app not installed")

        with self.assertRaises(FacebookAutomationError) as ctx:
            follow_user(BrokenActivateDriver([]), "target.account", timeout=0.1)

        self.assertIn("app not installed", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_follow_user_validates_arguments(self):
        driver = FakeDriver([])
        for username in ["", "   ", "@", "bad\nname", "\t", None, 123]:
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    follow_user(driver, username, timeout=0.1)

        for delay_range in [(7, 3), (-1, 5), (3,), "ab"]:
            with self.subTest(delay_range=delay_range):
                with self.assertRaises(ValueError):
                    follow_user(
                        driver,
                        "target.account",
                        delay_range=delay_range,
                        timeout=0.1,
                    )

        for timeout in (0, -1):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    follow_user(driver, "target.account", timeout=timeout)

    def test_default_random_delay_is_between_3_and_7_seconds(self):
        elements, _refs = build_flow_elements()
        driver = FakeDriver(elements)

        with mock.patch.object(facebook.random, "uniform", return_value=5.0) as uniform:
            result = follow_user(driver, "target.account", timeout=0.2)

        self.assertTrue(result.followed)
        for call in uniform.call_args_list:
            self.assertEqual(call, mock.call(3.0, 7.0))
        self.assertGreaterEqual(self.sleep.call_args_list.count(mock.call(5.0)), 6)

    def test_escape_helpers(self):
        self.assertEqual(
            facebook._escape_uiautomator('a"b\\c'),
            'a\\"b\\\\c',
        )
        self.assertEqual(facebook._xpath_literal("target"), '"target"')
        literal = facebook._xpath_literal("a\"b'c")
        self.assertTrue(literal.startswith("concat("))
        self.assertIn('"', literal)
        self.assertIn("'", literal)


class CreatePostTests(unittest.TestCase):
    def setUp(self):
        sleep_patcher = mock.patch.object(facebook.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_create_post_happy_path(self):
        elements, refs = build_composer_elements()
        driver = FakeDriver(elements)

        result = create_post(driver, "Halo dari phone farm!")

        self.assertEqual(result.status, facebook.STATUS_POSTED)
        self.assertTrue(result.posted)
        self.assertEqual(result.button_text, "Post")
        self.assertEqual(result.post_text, "Halo dari phone farm!")
        self.assertEqual(driver.activated, ["com.facebook.katana"])
        self.assertEqual(refs["prompt"].clicked, 1)
        self.assertEqual(refs["editor"].text, "Halo dari phone farm!")
        self.assertEqual(refs["button"].clicked, 1)
        self.assertEqual(driver.keyboard_hidden, 1)

    def test_create_post_supports_indonesian_labels(self):
        elements, refs = build_composer_elements(
            prompt_text="Apa yang Anda pikirkan, Tester?",
            button_text="Posting",
        )
        driver = FakeDriver(elements)

        result = create_post(driver, "Halo dunia")

        self.assertTrue(result.posted)
        self.assertEqual(result.button_text, "Posting")
        self.assertEqual(refs["button"].clicked, 1)

    def test_create_post_taps_the_editor_before_typing(self):
        elements, refs = build_composer_elements()
        driver = FakeDriver(elements)

        create_post(driver, "Halo")

        self.assertEqual(refs["editor"].clicked, 1)

    def test_create_post_handles_separate_composer_page(self):
        class RevealPage(FakeElement):
            def __init__(self, targets, **kwargs):
                super().__init__(**kwargs)
                self.targets = targets

            def click(self):
                super().click()
                for target in self.targets:
                    target.displayed = True

        editor = FakeElement(class_name="android.widget.EditText", displayed=False)
        button = FakeElement(
            text="Bagikan",
            content_desc="Bagikan",
            class_name="android.widget.Button",
            displayed=False,
        )
        composer = RevealPage([editor, button], text="Apa yang Anda pikirkan?")
        driver = FakeDriver([composer, editor, button])

        result = create_post(driver, "Halo halaman baru")

        self.assertTrue(result.posted)
        self.assertEqual(result.button_text, "Bagikan")
        self.assertEqual(editor.text, "Halo halaman baru")
        self.assertEqual(button.clicked, 1)

    def test_create_post_ignores_disabled_post_button(self):
        class Disabled(FakeElement):
            def get_attribute(self, name):
                if name == "clickable":
                    return "false"
                return super().get_attribute(name)

        prompt = FakeElement(text="What's on your mind, Tester?")
        editor = FakeElement(class_name="android.widget.EditText")
        disabled = Disabled(
            text="Post", content_desc="Post", class_name="android.widget.Button"
        )
        enabled = FakeElement(
            text="Bagikan", content_desc="Bagikan", class_name="android.widget.Button"
        )
        driver = FakeDriver([prompt, editor, disabled, enabled])

        result = create_post(driver, "Halo")

        self.assertEqual(result.button_text, "Bagikan")
        self.assertEqual(disabled.clicked, 0)
        self.assertEqual(enabled.clicked, 1)

    def test_create_post_skips_feed_composer_edittext(self):
        # The feed pill "Apa yang Anda pikirkan?" is an EditText on many builds;
        # it must never receive the post text.
        feed_pill = FakeElement(
            text="Apa yang Anda pikirkan?",
            class_name="android.widget.EditText",
            resource_id="com.facebook.katana:id/feed_composer",
        )
        editor = FakeElement(
            class_name="android.widget.EditText",
            resource_id="com.facebook.katana:id/composer_edit_text",
        )
        button = FakeElement(
            text="Posting",
            content_desc="Posting",
            class_name="android.widget.Button",
        )
        driver = FakeDriver([feed_pill, editor, button])

        result = create_post(driver, "Halo halaman baru")

        self.assertTrue(result.posted)
        self.assertEqual(feed_pill.text, "Apa yang Anda pikirkan?")
        self.assertEqual(editor.text, "Halo halaman baru")
        self.assertEqual(editor.clicked, 1)

    def test_create_post_preserves_multiline_text(self):
        elements, refs = build_composer_elements()
        driver = FakeDriver(elements)
        text = "Baris 1\nBaris 2\n#farm"

        create_post(driver, text)

        self.assertEqual(refs["editor"].text, text)

    def test_create_post_does_not_click_editor_typed_as_post(self):
        elements, refs = build_composer_elements(button_text="Post")
        driver = FakeDriver(elements)

        result = create_post(driver, "Post")

        self.assertTrue(result.posted)
        self.assertEqual(refs["editor"].text, "Post")
        self.assertEqual(refs["button"].clicked, 1)
        # The editor is tapped once to focus it; it must never be the element
        # that submits the post (the Button is).
        self.assertEqual(refs["editor"].clicked, 1)
        self.assertEqual(result.button_text, "Post")

    def test_create_post_raises_when_composer_is_missing(self):
        elements, _refs = build_composer_elements(with_prompt=False)
        driver = FakeDriver(elements)

        with self.assertRaises(FacebookAutomationError) as ctx:
            create_post(driver, "Halo", timeout=0.05)

        self.assertIn("post composer", str(ctx.exception))

    def test_create_post_raises_when_post_button_is_missing(self):
        elements, _refs = build_composer_elements(with_button=False)
        driver = FakeDriver(elements)

        with self.assertRaises(FacebookAutomationError) as ctx:
            create_post(driver, "Halo", timeout=0.05)

        self.assertIn("Post button", str(ctx.exception))

    def test_create_post_wraps_driver_errors(self):
        class BrokenActivateDriver(FakeDriver):
            def activate_app(self, package):
                raise RuntimeError("app not installed")

        with self.assertRaises(FacebookAutomationError) as ctx:
            create_post(BrokenActivateDriver([]), "Halo", timeout=0.1)

        self.assertIn("app not installed", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)

    def test_create_post_validates_arguments(self):
        driver = FakeDriver([])
        invalid_texts = ["", "   ", "\n\t", None, 123, "x" * 63207, "bad\x00text"]
        for post_text in invalid_texts:
            with self.subTest(post_text=str(post_text)[:20]):
                with self.assertRaises(ValueError):
                    create_post(driver, post_text, timeout=0.1)

        for delay_range in [(7, 3), (-1, 5), (3,), "ab"]:
            with self.subTest(delay_range=delay_range):
                with self.assertRaises(ValueError):
                    create_post(
                        driver,
                        "Halo",
                        delay_range=delay_range,
                        timeout=0.1,
                    )

        for timeout in (0, -1):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    create_post(driver, "Halo", timeout=timeout)

    def test_create_post_default_random_delay_is_between_3_and_7_seconds(self):
        elements, _refs = build_composer_elements()
        driver = FakeDriver(elements)

        with mock.patch.object(facebook.random, "uniform", return_value=5.0) as uniform:
            result = create_post(driver, "Halo")

        self.assertTrue(result.posted)
        for call in uniform.call_args_list:
            self.assertEqual(call, mock.call(3.0, 7.0))
        self.assertGreaterEqual(self.sleep.call_args_list.count(mock.call(5.0)), 4)


class CommentOnPostTests(unittest.TestCase):
    URL = "https://www.facebook.com/example/posts/1234567890"

    def setUp(self):
        sleep_patcher = mock.patch.object(facebook.time, "sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_comment_on_post_happy_path(self):
        elements, refs = build_comment_elements()
        driver = FakeDriver(elements)

        result = comment_on_post(driver, self.URL, "Nice post!")

        self.assertEqual(result.status, facebook.STATUS_COMMENTED)
        self.assertTrue(result.commented)
        self.assertEqual(result.target_url, self.URL)
        self.assertEqual(result.comment_text, "Nice post!")
        self.assertEqual(result.button_text, "Send")
        self.assertEqual(refs["editor"].text, "Nice post!")
        self.assertEqual(refs["send_button"].clicked, 1)
        self.assertEqual(refs["comment_button"].clicked, 0)
        self.assertEqual(driver.scripts[0][0], "mobile: deepLink")
        self.assertEqual(
            driver.scripts[0][1],
            {
                "url": self.URL,
                "package": "com.facebook.katana",
                "waitForLaunch": True,
            },
        )

    def test_comment_opens_the_comment_box_when_editor_is_hidden(self):
        elements, refs = build_comment_elements(editor_visible=False)
        driver = FakeDriver(elements)

        result = comment_on_post(driver, self.URL, "Halo")

        self.assertTrue(result.commented)
        self.assertEqual(refs["comment_button"].clicked, 1)
        self.assertEqual(refs["editor"].text, "Halo")
        self.assertEqual(refs["send_button"].clicked, 1)

    def test_comment_accepts_fb_deep_link(self):
        elements, _refs = build_comment_elements()
        driver = FakeDriver(elements)
        deep_link = "fb://post/1234567890"

        result = comment_on_post(driver, deep_link, "Halo")

        self.assertEqual(result.target_url, deep_link)
        self.assertEqual(driver.scripts[0][1]["url"], deep_link)

    def test_comment_supports_indonesian_send_label(self):
        elements, refs = build_comment_elements(send_text="Kirim")
        driver = FakeDriver(elements)

        result = comment_on_post(driver, self.URL, "Halo")

        self.assertEqual(result.button_text, "Kirim")
        self.assertEqual(refs["send_button"].clicked, 1)

    def test_comment_falls_back_to_ime_send_action(self):
        elements, refs = build_comment_elements(with_send_button=False)
        driver = FakeDriver(elements)

        result = comment_on_post(driver, self.URL, "Halo")

        self.assertTrue(result.commented)
        self.assertEqual(result.button_text, "IME_SEND")
        self.assertEqual(refs["editor"].text, "Halo")
        self.assertIn(
            ("mobile: performEditorAction", {"action": "send"}),
            driver.scripts,
        )

    def test_comment_falls_back_to_enter_when_ime_action_fails(self):
        class NoImeDriver(FakeDriver):
            def execute_script(self, script, args=None):
                if script == "mobile: performEditorAction":
                    raise RuntimeError("ime action unsupported")
                return super().execute_script(script, args)

        elements, refs = build_comment_elements(with_send_button=False)
        driver = NoImeDriver(elements)

        result = comment_on_post(driver, self.URL, "Halo")

        self.assertEqual(result.button_text, "ENTER")
        self.assertEqual(driver.keycodes, [66])
        self.assertEqual(refs["editor"].text, "Halo")

    def test_comment_preserves_multiline_text(self):
        elements, refs = build_comment_elements()
        driver = FakeDriver(elements)
        text = "Baris 1\nBaris 2\n#farm"

        comment_on_post(driver, self.URL, text)

        self.assertEqual(refs["editor"].text, text)

    def test_comment_raises_when_url_cannot_be_opened(self):
        driver = FakeDriver([])
        driver.script_error = RuntimeError("no app link")

        with self.assertRaises(FacebookAutomationError) as ctx:
            comment_on_post(driver, self.URL, "Halo")

        self.assertIn("Could not open", str(ctx.exception))
        self.assertIn("com.facebook.katana", str(ctx.exception))

    def test_comment_raises_when_facebook_is_not_in_foreground(self):
        elements, _refs = build_comment_elements()
        driver = FakeDriver(elements, app_state=3)

        with self.assertRaises(FacebookAutomationError) as ctx:
            comment_on_post(driver, self.URL, "Halo", timeout=0.05)

        self.assertIn("foreground", str(ctx.exception))

    def test_comment_raises_when_comment_box_is_missing(self):
        driver = FakeDriver([FakeElement(text="Something else")])

        with self.assertRaises(FacebookAutomationError) as ctx:
            comment_on_post(driver, self.URL, "Halo", timeout=0.05)

        self.assertIn("comment button", str(ctx.exception))

    def test_comment_validates_arguments(self):
        driver = FakeDriver([])
        invalid_urls = [
            "",
            "   ",
            "www.facebook.com/post",
            "ftp://facebook.com/post",
            "https://",
            "fb://",
            None,
            123,
            "h" * 2049,
        ]
        for url in invalid_urls:
            with self.subTest(url=str(url)[:30]):
                with self.assertRaises(ValueError):
                    comment_on_post(driver, url, "Halo", timeout=0.1)

        invalid_comments = ["", "   ", "\n\t", None, 123, "x" * 8001, "bad\x00text"]
        for text in invalid_comments:
            with self.subTest(comment=str(text)[:20]):
                with self.assertRaises(ValueError):
                    comment_on_post(driver, self.URL, text, timeout=0.1)

        for delay_range in [(7, 3), (-1, 5), (3,), "ab"]:
            with self.subTest(delay_range=delay_range):
                with self.assertRaises(ValueError):
                    comment_on_post(
                        driver,
                        self.URL,
                        "Halo",
                        delay_range=delay_range,
                        timeout=0.1,
                    )

        for timeout in (0, -1):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    comment_on_post(driver, self.URL, "Halo", timeout=timeout)

    def test_comment_default_random_delay_is_between_3_and_7_seconds(self):
        elements, _refs = build_comment_elements()
        driver = FakeDriver(elements)

        with mock.patch.object(facebook.random, "uniform", return_value=5.0) as uniform:
            result = comment_on_post(driver, self.URL, "Halo")

        self.assertTrue(result.commented)
        for call in uniform.call_args_list:
            self.assertEqual(call, mock.call(3.0, 7.0))
        self.assertGreaterEqual(self.sleep.call_args_list.count(mock.call(5.0)), 3)


if __name__ == "__main__":
    unittest.main()
