"""Facebook actions built on top of an Appium driver.

The helpers are driver-first so they compose with the multi-device bot::

    bot.run_on_devices(lambda driver, udid: follow_user(driver, "target.account"))

Selectors target the official Facebook Android app (``com.facebook.katana``)
with English and Indonesian labels. Facebook changes its UI frequently; if a
step stops working, tune the locator lists at the bottom of this module.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

try:
    from .errors import FacebookAutomationError
    from .models import (
        STATUS_ALREADY_FOLLOWING,
        STATUS_COMMENTED,
        STATUS_FOLLOWED,
        STATUS_POSTED,
        CommentResult,
        FollowResult,
        PostResult,
    )
except ImportError:  # pragma: no cover - direct module execution
    from errors import FacebookAutomationError
    from models import (
        STATUS_ALREADY_FOLLOWING,
        STATUS_COMMENTED,
        STATUS_FOLLOWED,
        STATUS_POSTED,
        CommentResult,
        FollowResult,
        PostResult,
    )

__all__ = [
    "FACEBOOK_PACKAGE",
    "CommentResult",
    "FollowResult",
    "PostResult",
    "STATUS_ALREADY_FOLLOWING",
    "STATUS_COMMENTED",
    "STATUS_FOLLOWED",
    "STATUS_POSTED",
    "comment_on_post",
    "create_post",
    "follow_user",
]

FACEBOOK_PACKAGE = "com.facebook.katana"

#: Locator strategy names; identical to ``appium.webdriver.common.appiumby.AppiumBy``.
BY_ACCESSIBILITY_ID = "accessibility id"
BY_CLASS_NAME = "class name"
BY_ANDROID_UIAUTOMATOR = "-android uiautomator"
BY_XPATH = "xpath"

KEYCODE_ENTER = 66
POLL_INTERVAL = 0.5
OPTIONAL_TAB_TIMEOUT = 8.0
#: Random pause inserted between steps, in seconds.
DEFAULT_DELAY_RANGE = (3.0, 7.0)

Locator = Tuple[str, str]


def follow_user(
    driver: Any,
    target_username: str,
    *,
    delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
    timeout: float = 20.0,
    package: str = FACEBOOK_PACKAGE,
    logger: Optional[logging.Logger] = None,
) -> FollowResult:
    """Open Facebook, search *target_username*, open the profile and follow.

    Steps: activate the app, open the search screen, type the username, submit
    the search, open the People tab when available, tap the matching profile,
    then tap **Follow** (or report that the account is already followed).

    A random pause taken from *delay_range* (default **3-7 seconds**) is applied
    after every step so the flow does not hammer the app.

    Args:
        driver: Connected Appium ``WebDriver``, e.g. ``bot.get_driver(udid)``.
        target_username: Facebook username/handle, with or without a leading @.
        delay_range: ``(min, max)`` seconds between steps.
        timeout: Seconds to wait for each UI element.
        package: Facebook package name (``com.facebook.lite`` for Lite).
        logger: Optional logger; defaults to a module-level logger.

    Returns:
        :class:`FollowResult` with status ``"followed"`` or
        ``"already_following"``.

    Raises:
        ValueError: *target_username*, *delay_range* or *timeout* is invalid.
        FacebookAutomationError: One of the UI steps could not be completed.
    """
    username = _normalize_username(target_username)
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    low, high = _validate_delay_range(delay_range)
    log = logger or logging.getLogger(__name__)

    log.info("Facebook: following %r", username)
    try:
        log.info("Facebook: opening %s", package)
        driver.activate_app(package)
        _wait_for_foreground(driver, package, timeout, log)
        _pause(low, high, log, "opening Facebook")

        search_entry = _wait_for_any(
            driver, _search_entry_locators(), timeout, "Facebook search entry"
        )
        search_entry.click()
        _pause(low, high, log, "opening search")

        search_box = _wait_for_any(
            driver, _search_box_locators(), timeout, "Facebook search box"
        )
        _clear(search_box, log)
        search_box.send_keys(username)
        _pause(low, high, log, "typing the username")

        driver.press_keycode(KEYCODE_ENTER)
        _pause(low, high, log, "submitting the search")

        _tap_optional(
            driver,
            _people_tab_locators(),
            min(timeout, OPTIONAL_TAB_TIMEOUT),
            log,
        )
        _pause(low, high, log, "opening the People tab")

        result = _wait_for_result(driver, username, timeout)
        result.click()
        _pause(low, high, log, "opening the profile")

        button, already_following = _wait_for_follow_state(driver, username, timeout)
        button_text = _element_text(button)
        if already_following:
            log.info("Facebook: %r is already followed", username)
            return FollowResult(username, STATUS_ALREADY_FOLLOWING, button_text)

        button.click()
        _pause(low, high, log, "pressing Follow")
        log.info("Facebook: followed %r", username)
        return FollowResult(username, STATUS_FOLLOWED, button_text)
    except FacebookAutomationError:
        raise
    except Exception as exc:
        raise FacebookAutomationError(
            f"follow_user({username!r}) failed on {package}: {exc}"
        ) from exc


def create_post(
    driver: Any,
    post_text: str,
    *,
    delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
    timeout: float = 20.0,
    package: str = FACEBOOK_PACKAGE,
    logger: Optional[logging.Logger] = None,
) -> PostResult:
    """Open Facebook and publish *post_text* as a new status post.

    Steps: activate the app, tap the "What's on your mind?" composer, type the
    text, then tap **Post**. A random pause (default **3-7 seconds**) separates
    every step.

    Args:
        driver: Connected Appium ``WebDriver``, e.g. ``bot.get_driver(udid)``.
        post_text: Text to publish. Must not be blank; at most 63,206 characters
            (Facebook's post limit). Newlines are preserved.
        delay_range: ``(min, max)`` seconds between steps.
        timeout: Seconds to wait for each UI element.
        package: Facebook package name (``com.facebook.lite`` for Lite).
        logger: Optional logger; defaults to a module-level logger.

    Returns:
        :class:`PostResult` with status ``"posted"``.

    Raises:
        ValueError: *post_text*, *delay_range* or *timeout* is invalid.
        FacebookAutomationError: One of the UI steps could not be completed.
    """
    text = _normalize_post_text(post_text)
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    low, high = _validate_delay_range(delay_range)
    log = logger or logging.getLogger(__name__)

    log.info("Facebook: creating a post (%d characters)", len(text))
    try:
        log.info("Facebook: opening %s", package)
        driver.activate_app(package)
        _wait_for_foreground(driver, package, timeout, log)
        _pause(low, high, log, "opening Facebook")

        composer = _wait_for_any(
            driver, _composer_prompt_locators(), timeout, "the post composer"
        )
        composer.click()
        _pause(low, high, log, "opening the composer")

        editor = _wait_for_any(
            driver, _composer_editor_locators(), timeout, "the post editor"
        )
        # Newer Facebook builds open a separate "Buat postingan baru" screen where
        # the field is not focused yet: tap it first, otherwise send_keys goes to
        # the page instead of the text field.
        try:
            editor.click()
        except Exception:  # noqa: BLE001 - field may already be focused
            log.debug("Facebook: could not tap the editor; typing into the focus")
        _pause(low, high, log, "focusing the post editor")
        _clear(editor, log)
        editor.send_keys(text)
        _pause(low, high, log, "typing the post")

        _hide_keyboard(driver, log)
        post_button = _wait_for_post_button(driver, timeout)
        post_button.click()
        _pause(low, high, log, "pressing Post")

        log.info("Facebook: post published")
        return PostResult(text, STATUS_POSTED, _element_text(post_button))
    except FacebookAutomationError:
        raise
    except Exception as exc:
        raise FacebookAutomationError(
            f"create_post failed on {package}: {exc}"
        ) from exc


def comment_on_post(
    driver: Any,
    target_url: str,
    comment_text: str,
    *,
    delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
    timeout: float = 20.0,
    package: str = FACEBOOK_PACKAGE,
    logger: Optional[logging.Logger] = None,
) -> CommentResult:
    """Open a Facebook post URL and submit *comment_text* as a comment.

    The URL is opened with the UiAutomator2 ``mobile: deepLink`` command (with a
    ``mobile: startActivity`` fallback) so it lands inside the Facebook app
    instead of a browser. Then the comment box is opened, the text typed and
    the comment sent (Send/Kirim button, IME "send" action, or Enter key).

    A random pause (default **3-7 seconds**) separates every step.

    Args:
        driver: Connected Appium ``WebDriver``, e.g. ``bot.get_driver(udid)``.
        target_url: Post URL, ``http(s)://...`` or an ``fb://`` deep link.
        comment_text: Comment body. Must not be blank; at most 8,000 characters
            (Facebook's comment limit). Newlines are preserved.
        delay_range: ``(min, max)`` seconds between steps.
        timeout: Seconds to wait for each UI element.
        package: Facebook package name (``com.facebook.lite`` for Lite).
        logger: Optional logger; defaults to a module-level logger.

    Returns:
        :class:`CommentResult` with status ``"commented"``.

    Raises:
        ValueError: *target_url*, *comment_text*, *delay_range* or *timeout* is
            invalid.
        FacebookAutomationError: The URL could not be opened in Facebook, or
            one of the UI steps could not be completed.
    """
    url = _normalize_target_url(target_url)
    text = _normalize_comment_text(comment_text)
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    low, high = _validate_delay_range(delay_range)
    log = logger or logging.getLogger(__name__)

    log.info("Facebook: commenting on %s", url)
    try:
        log.info("Facebook: opening the post URL")
        _open_url(driver, url, package, log)
        _wait_for_foreground(driver, package, timeout, log)
        _pause(low, high, log, "opening the post")

        editor = _find_displayed(driver, _comment_editor_locators())
        if editor is None:
            comment_button = _wait_for_any(
                driver, _comment_button_locators(), timeout, "the comment button"
            )
            comment_button.click()
            _pause(low, high, log, "opening the comment box")
            editor = _wait_for_any(
                driver,
                _comment_editor_locators(),
                timeout,
                "the comment editor",
            )

        editor.click()
        _clear(editor, log)
        editor.send_keys(text)
        _pause(low, high, log, "typing the comment")

        button_text = _submit_comment(driver, log)
        _pause(low, high, log, "sending the comment")

        log.info("Facebook: comment sent")
        return CommentResult(url, text, STATUS_COMMENTED, button_text)
    except FacebookAutomationError:
        raise
    except Exception as exc:
        raise FacebookAutomationError(
            f"comment_on_post({url!r}) failed on {package}: {exc}"
        ) from exc


# ----------------------------------------------------------------------
# Argument and selector helpers
# ----------------------------------------------------------------------
def _normalize_username(target_username: str) -> str:
    if not isinstance(target_username, str):
        raise ValueError("target_username must be a string")
    username = target_username.strip()
    if username.startswith("@"):
        username = username[1:].strip()
    if not username:
        raise ValueError("target_username must be a non-empty username")
    if len(username) > 100:
        raise ValueError("target_username must be at most 100 characters")
    if any(ord(character) < 32 for character in username):
        raise ValueError("target_username contains control characters")
    return username


def _normalize_post_text(post_text: str) -> str:
    if not isinstance(post_text, str):
        raise ValueError("post_text must be a string")
    if not post_text.strip():
        raise ValueError("post_text must not be empty")
    if len(post_text) > 63206:
        raise ValueError("post_text must be at most 63206 characters")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in post_text):
        raise ValueError("post_text contains unsupported control characters")
    return post_text


def _normalize_target_url(target_url: str) -> str:
    if not isinstance(target_url, str):
        raise ValueError("target_url must be a string")
    url = target_url.strip()
    if not url:
        raise ValueError("target_url must not be empty")
    if len(url) > 2048:
        raise ValueError("target_url must be at most 2048 characters")

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https", "fb"}:
        raise ValueError("target_url must start with http://, https:// or fb://")
    if parts.scheme in {"http", "https"} and not parts.netloc:
        raise ValueError(
            "target_url must include a host, e.g. https://www.facebook.com/..."
        )
    if parts.scheme == "fb" and not (parts.netloc or parts.path):
        raise ValueError("target_url must be a valid fb:// deep link")
    return url


def _normalize_comment_text(comment_text: str) -> str:
    if not isinstance(comment_text, str):
        raise ValueError("comment_text must be a string")
    if not comment_text.strip():
        raise ValueError("comment_text must not be empty")
    if len(comment_text) > 8000:
        raise ValueError("comment_text must be at most 8000 characters")
    if any(
        ord(character) < 32 and character not in "\n\r\t"
        for character in comment_text
    ):
        raise ValueError("comment_text contains unsupported control characters")
    return comment_text


def _validate_delay_range(delay_range: Sequence[float]) -> Tuple[float, float]:
    try:
        low, high = delay_range
        low = float(low)
        high = float(high)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "delay_range must be a (min, max) pair of seconds"
        ) from exc
    if low < 0 or high < low:
        raise ValueError("delay_range must satisfy 0 <= min <= max")
    return low, high


def _escape_uiautomator(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _xpath_literal(value: str) -> str:
    """Return an XPath 1.0 string literal that can hold both quote types."""
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"

    parts = value.split('"')
    pieces: List[str] = []
    for index, part in enumerate(parts):
        if part:
            pieces.append(f'"{part}"')
        if index != len(parts) - 1:
            pieces.append("'\"'")
    if len(pieces) < 2:
        pieces.append('""')
    return "concat(" + ", ".join(pieces) + ")"


def _pause(
    low: float,
    high: float,
    logger: logging.Logger,
    after_step: str,
) -> None:
    seconds = random.uniform(low, high)
    logger.debug("Random delay %.2fs after %s", seconds, after_step)
    time.sleep(seconds)


def _clear(element: Any, logger: logging.Logger) -> None:
    try:
        element.clear()
    except Exception:  # some Facebook fields expose an empty EditText
        logger.debug("Could not clear the search box; typing on top", exc_info=True)


def _hide_keyboard(driver: Any, logger: logging.Logger) -> None:
    hide = getattr(driver, "hide_keyboard", None)
    if not callable(hide):
        return
    try:
        hide()
    except Exception:
        logger.debug("Keyboard was not visible", exc_info=True)


def _element_text(element: Any) -> str:
    try:
        text = element.get_attribute("text") or element.get_attribute("content-desc")
    except Exception:
        return ""
    return (text or "").strip()


def _wait_for_foreground(
    driver: Any, package: str, timeout: float, logger: logging.Logger
) -> None:
    query_state = getattr(driver, "query_app_state", None)
    if not callable(query_state):
        return

    deadline = time.monotonic() + timeout
    while True:
        try:
            state = query_state(package)
        except Exception:
            logger.debug("query_app_state failed; assuming the app is in front")
            return
        if state == 4:  # RUNNING_IN_FOREGROUND
            return
        if time.monotonic() >= deadline:
            raise FacebookAutomationError(
                f"{package} did not reach the foreground within {timeout:.0f}s"
            )
        time.sleep(POLL_INTERVAL)


def _open_url(
    driver: Any,
    url: str,
    package: str,
    logger: logging.Logger,
) -> None:
    opened_with = ""
    execute = getattr(driver, "execute_script", None)
    if callable(execute):
        attempts = (
            (
                "mobile: deepLink",
                {"url": url, "package": package, "waitForLaunch": True},
            ),
            (
                "mobile: startActivity",
                {
                    "action": "android.intent.action.VIEW",
                    "uri": url,
                    "package": package,
                    "wait": True,
                },
            ),
        )
        for script, args in attempts:
            try:
                execute(script, args)
            except Exception:
                logger.debug("%s failed for %s", script, url, exc_info=True)
                continue
            opened_with = script
            logger.debug("Opened %s with %s", url, script)
            break

    if not opened_with:
        get = getattr(driver, "get", None)
        if callable(get):
            try:
                get(url)
                opened_with = "driver.get"
            except Exception:
                logger.debug("driver.get failed for %s", url, exc_info=True)

    if not opened_with:
        raise FacebookAutomationError(
            f"Could not open {url!r} inside {package}. "
            "Use a post URL with app links enabled or pass an fb:// deep link."
        )


# ----------------------------------------------------------------------
# Element lookup helpers
# ----------------------------------------------------------------------
def _find_displayed(driver: Any, locators: Sequence[Locator]) -> Any:
    for by, value in locators:
        try:
            elements = driver.find_elements(by, value)
        except Exception:
            continue
        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue
    return None


def _wait_for_any(
    driver: Any,
    locators: Sequence[Locator],
    timeout: float,
    description: str,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        element = _find_displayed(driver, locators)
        if element is not None:
            return element
        if time.monotonic() >= deadline:
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for {description}"
            )
        time.sleep(POLL_INTERVAL)


def _wait_for_result(driver: Any, username: str, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    locators = _result_locators(username)
    while True:
        for by, value in locators:
            try:
                elements = driver.find_elements(by, value)
            except Exception:
                continue
            for element in elements:
                if _is_profile_result(element):
                    return element
        if time.monotonic() >= deadline:
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for a search result "
                f"matching {username!r}"
            )
        time.sleep(POLL_INTERVAL)


def _is_profile_result(element: Any) -> bool:
    try:
        if not element.is_displayed():
            return False
        class_name = (element.get_attribute("class") or "").lower()
        resource_id = (element.get_attribute("resource-id") or "").lower()
    except Exception:
        return False

    if "edittext" in class_name:
        return False
    # Exclude the search field itself, which also contains the typed username.
    search_field_hints = (
        "search_edit",
        "search_bar",
        "search_box",
        "search_field",
        "search_input",
    )
    return not any(hint in resource_id for hint in search_field_hints)


def _wait_for_follow_state(
    driver: Any, username: str, timeout: float
) -> Tuple[Any, bool]:
    deadline = time.monotonic() + timeout
    while True:
        following = _find_displayed(driver, _following_locators())
        if following is not None:
            return following, True

        follow = _find_displayed(driver, _follow_locators())
        if follow is not None:
            return follow, False

        if time.monotonic() >= deadline:
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for the Follow button "
                f"on {username!r}"
            )
        time.sleep(POLL_INTERVAL)


def _wait_for_post_button(driver: Any, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        for by, value in _post_button_locators():
            try:
                elements = driver.find_elements(by, value)
            except Exception:
                continue
            for element in elements:
                try:
                    if not element.is_displayed():
                        continue
                    class_name = (element.get_attribute("class") or "").lower()
                    clickable = element.get_attribute("clickable")
                except Exception:
                    continue
                # Never mistake the composer EditText for the Post button, even
                # when the post text itself is "Post".
                if "edittext" in class_name:
                    continue
                if isinstance(clickable, str) and clickable.lower() == "false":
                    continue
                return element
        if time.monotonic() >= deadline:
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for the Post button"
            )
        time.sleep(POLL_INTERVAL)


def _submit_comment(driver: Any, logger: logging.Logger) -> str:
    button = _find_displayed(driver, _send_button_locators())
    if button is not None:
        button.click()
        return _element_text(button)

    execute = getattr(driver, "execute_script", None)
    if callable(execute):
        try:
            execute("mobile: performEditorAction", {"action": "send"})
            return "IME_SEND"
        except Exception:
            logger.debug(
                "mobile: performEditorAction failed; pressing Enter",
                exc_info=True,
            )

    driver.press_keycode(KEYCODE_ENTER)
    return "ENTER"


def _tap_optional(
    driver: Any,
    locators: Sequence[Locator],
    timeout: float,
    logger: logging.Logger,
) -> Any:
    try:
        element = _wait_for_any(driver, locators, timeout, "optional tab")
    except FacebookAutomationError:
        logger.debug("Optional element not found; continuing")
        return None
    try:
        element.click()
    except Exception:
        logger.debug("Optional element could not be tapped", exc_info=True)
        return None
    return element


# ----------------------------------------------------------------------
# Locators (tune these when Facebook changes its UI)
# ----------------------------------------------------------------------
def _search_entry_locators() -> List[Locator]:
    return [
        (BY_ACCESSIBILITY_ID, "Search"),
        (BY_ACCESSIBILITY_ID, "Cari"),
        (BY_ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Search")'),
        (BY_ANDROID_UIAUTOMATOR, 'new UiSelector().descriptionContains("Cari")'),
        (
            BY_XPATH,
            '//*[@content-desc="Search" or @text="Search" '
            'or @content-desc="Cari" or @text="Cari"]',
        ),
    ]


def _search_box_locators() -> List[Locator]:
    return [
        (BY_CLASS_NAME, "android.widget.EditText"),
        (BY_XPATH, "//android.widget.EditText"),
    ]


def _people_tab_locators() -> List[Locator]:
    return [
        (BY_ANDROID_UIAUTOMATOR, 'new UiSelector().text("People")'),
        (BY_ANDROID_UIAUTOMATOR, 'new UiSelector().text("Orang")'),
        (BY_XPATH, '//*[@text="People" or @text="Orang"]'),
    ]


def _result_locators(username: str) -> List[Locator]:
    escaped = _escape_uiautomator(username)
    literal = _xpath_literal(username)
    return [
        (BY_ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped}")'),
        (BY_ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains("{escaped}")'),
        (
            BY_XPATH,
            "//android.widget.TextView[contains(@text, "
            f"{literal}) or contains(@content-desc, {literal})]",
        ),
    ]


def _follow_locators() -> List[Locator]:
    return [
        (BY_XPATH, '//*[@text="Follow" or @text="Ikuti"]'),
        (BY_ACCESSIBILITY_ID, "Follow"),
        (BY_ACCESSIBILITY_ID, "Ikuti"),
    ]


def _following_locators() -> List[Locator]:
    return [
        (BY_XPATH, '//*[@text="Following" or @text="Mengikuti"]'),
        (BY_ACCESSIBILITY_ID, "Following"),
        (BY_ACCESSIBILITY_ID, "Mengikuti"),
    ]


_COMPOSER_PROMPTS = (
    "What's on your mind",
    "Apa yang Anda pikirkan",
    "Apa yang ada di pikiran",
)


def _composer_prompt_locators() -> List[Locator]:
    locators: List[Locator] = []
    for prompt in _COMPOSER_PROMPTS:
        escaped = _escape_uiautomator(prompt)
        locators.append(
            (BY_ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped}")')
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{escaped}")',
            )
        )

    conditions: List[str] = []
    for prompt in _COMPOSER_PROMPTS:
        literal = _xpath_literal(prompt)
        conditions.append(f"contains(@text, {literal})")
        conditions.append(f"contains(@content-desc, {literal})")
    locators.append((BY_XPATH, "//*[" + " or ".join(conditions) + "]"))
    return locators


def _composer_editor_locators() -> List[Locator]:
    return [
        (BY_CLASS_NAME, "android.widget.EditText"),
        (BY_XPATH, "//android.widget.EditText"),
        # Newer composers may expose the field as a view whose text/content-desc
        # is the placeholder, so try those as last-resort fallbacks.
        (
            BY_ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("Apa yang Anda pikirkan")',
        ),
        (
            BY_ANDROID_UIAUTOMATOR,
            'new UiSelector().descriptionContains("Apa yang Anda pikirkan")',
        ),
        (
            BY_ANDROID_UIAUTOMATOR,
            'new UiSelector().textContains("What\'s on your mind")',
        ),
    ]


#: Labels used by the final submit button across Facebook builds/locales.
_POST_BUTTON_LABELS = ("Post", "Posting", "Bagikan", "Kirim", "Share")


def _post_button_locators() -> List[Locator]:
    locators: List[Locator] = []
    for label in _POST_BUTTON_LABELS:
        locators.append((BY_XPATH, f'//*[@text="{label}"]'))
        locators.append((BY_ACCESSIBILITY_ID, label))
    # Wrapped labels such as "Bagikan sekarang" / "Posting sebagai …".
    for label in ("Bagikan", "Kirim", "Share"):
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{label}")',
            )
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{label}")',
            )
        )
    return locators


def _comment_button_locators() -> List[Locator]:
    return [
        (
            BY_XPATH,
            '//*[@content-desc="Comment" or @text="Comment" '
            'or @content-desc="Komentari" or @text="Komentari"]',
        ),
        (BY_ACCESSIBILITY_ID, "Comment"),
        (BY_ACCESSIBILITY_ID, "Komentari"),
    ]


def _comment_editor_locators() -> List[Locator]:
    return [
        (BY_CLASS_NAME, "android.widget.EditText"),
        (BY_XPATH, "//android.widget.EditText"),
    ]


def _send_button_locators() -> List[Locator]:
    return [
        (
            BY_XPATH,
            '//*[@content-desc="Send" or @text="Send" '
            'or @content-desc="Kirim" or @text="Kirim"]',
        ),
        (BY_ACCESSIBILITY_ID, "Send"),
        (BY_ACCESSIBILITY_ID, "Kirim"),
    ]
