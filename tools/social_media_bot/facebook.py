"""Facebook actions built on top of an Appium driver.

The helpers are driver-first so they compose with the multi-device bot::

    bot.run_on_devices(lambda driver, udid: follow_user(driver, "target.account"))

Selectors target the official Facebook Android app (``com.facebook.katana``)
with English and Indonesian labels. Facebook changes its UI frequently; if a
step stops working, tune the locator lists at the bottom of this module.
"""

from __future__ import annotations

import logging
import os
import random
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
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
#: KEYCODE_WAKEUP turns the screen on without toggling it off again.
KEYCODE_WAKEUP = 224
POLL_INTERVAL = 0.5
OPTIONAL_TAB_TIMEOUT = 8.0
#: Seconds to wait for the composer editor before tapping the composer again.
EDITOR_FIRST_WAIT = 8.0
#: Seconds to wait for the composer entry before recovering the feed.
COMPOSER_FIRST_WAIT = 5.0
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

    When the composer screen takes a while (or a previous failed run left the app
    on it), the composer is tapped again and the focused field is reused before
    the step fails. The screen is woken up first (sleeping phones on a farm have
    no visible UI) and the whole flow is retried once when it failed before the
    Post tap. A failed run also saves the Appium page source and a screenshot and
    attaches the current package/activity/lock state plus the visible labels, so
    a slot that behaves differently can be diagnosed; the dump directory can be
    redirected with ``GADS_UI_DUMP_DIR``.

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
    last_error: Optional[BaseException] = None
    # One extra attempt is safe: a retry only happens when the flow never
    # reached the Post tap, so a published post can never be duplicated.
    for attempt in range(2):
        state: Dict[str, bool] = {"submitted": False}
        try:
            return _publish_post(
                driver, text, package, timeout, low, high, log, state
            )
        except FacebookAutomationError as exc:
            last_error = exc
            if state["submitted"] or attempt > 0:
                break
            log.warning(
                "Facebook: post attempt %d failed before submitting (%s); "
                "retrying once",
                attempt + 1,
                exc,
            )
            _pause(low, high, log, "retrying the post")
        except Exception as exc:  # noqa: BLE001 - wrapped for the operator
            last_error = exc
            break

    artifacts = _capture_failure_artifacts(driver, "post", log)
    context = _session_context(driver)
    details = " ".join(part for part in (context, artifacts) if part)
    if isinstance(last_error, FacebookAutomationError):
        message = str(last_error)
    elif last_error is not None:
        message = f"create_post failed on {package}: {last_error}"
    else:  # pragma: no cover - defensive
        message = f"create_post failed on {package}"
    if details:
        message = f"{message} ({details})"
    raise FacebookAutomationError(message) from last_error


def _publish_post(
    driver: Any,
    text: str,
    package: str,
    timeout: float,
    low: float,
    high: float,
    logger: logging.Logger,
    state: Dict[str, bool],
) -> PostResult:
    """Run one end-to-end publish attempt and return its result."""

    logger.info("Facebook: opening %s", package)
    _wake_device(driver, logger)
    driver.activate_app(package)
    _wait_for_foreground(driver, package, timeout, logger)
    _pause(low, high, logger, "opening Facebook")

    editor = _reuse_open_composer(driver, timeout, logger)
    if editor is None:
        composer = _open_post_composer_entry(
            driver, package, timeout, low, high, logger
        )
        editor = _open_post_composer(driver, composer, timeout, low, high, logger)
    # Newer Facebook builds open a separate "Buat postingan baru" screen where
    # the field is not focused yet: tap it first, otherwise send_keys goes to
    # the page instead of the text field.
    try:
        editor.click()
    except Exception:  # noqa: BLE001 - field may already be focused
        logger.debug("Facebook: could not tap the editor; typing into the focus")
    _pause(low, high, logger, "focusing the post editor")
    _clear(editor, logger)
    editor.send_keys(text)
    _pause(low, high, logger, "typing the post")

    # Some Facebook builds open a dedicated text screen: the top-right
    # "Selesai"/"Done" button must be tapped to commit the text before the
    # audience/Next screen (and the Post button) becomes available. Hiding the
    # keyboard first could dismiss that screen, so check for it before.
    done_button = _find_button(driver, _done_button_locators())
    if done_button is not None:
        _tap_element(driver, done_button, logger)
        _pause(low, high, logger, "finishing the post text")
    else:
        _hide_keyboard(driver, logger)

    # Facebook 588+ uses a two step composer: "Postingan baru" -> Berikutnya
    # -> audience screen -> final Posting/Post. Older builds publish straight
    # from the composer, so check for the final button first.
    post_button = _find_button(driver, _post_button_locators())
    if post_button is None:
        next_button = _wait_for_button(
            driver,
            _next_button_locators(),
            min(timeout, 10.0),
            "the Next button",
            required=False,
        )
        if next_button is not None:
            _tap_element(driver, next_button, logger)
            _pause(low, high, logger, "pressing Next")
        post_button = _wait_for_post_button(driver, timeout)

    # From here on the tap may already have published the post, so the caller
    # must not retry: a second attempt could duplicate it.
    state["submitted"] = True
    if not _tap_element(driver, post_button, logger):
        raise FacebookAutomationError(
            "the Post button was found but could not be tapped"
        )
    _pause(low, high, logger, "pressing Post")

    logger.info("Facebook: post published")
    return PostResult(text, STATUS_POSTED, _element_text(post_button))


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


def _wake_device(driver: Any, logger: logging.Logger) -> None:
    """Turn the screen on and warn when the device is still locked.

    Phones on a farm commonly fall asleep while waiting in the queue; a sleeping
    device still reports Facebook as foreground but has no visible UI, which
    used to surface as ``waiting for the post composer`` only on some slots.
    """

    press = getattr(driver, "press_keycode", None)
    if callable(press):
        try:
            press(KEYCODE_WAKEUP)
        except Exception:
            logger.debug("Could not wake the screen", exc_info=True)

    is_locked = getattr(driver, "is_locked", None)
    if not callable(is_locked):
        return
    try:
        locked = bool(is_locked())
    except Exception:
        logger.debug("Could not read the lock state", exc_info=True)
        return
    if not locked:
        return

    logger.warning("Facebook: the device is locked; trying a swipe to unlock")
    swipe = getattr(driver, "swipe", None)
    get_size = getattr(driver, "get_window_size", None)
    if callable(swipe) and callable(get_size):
        try:
            size = get_size()
            width = int(size.get("width", 0))
            height = int(size.get("height", 0))
            if width > 0 and height > 0:
                swipe(
                    width // 2,
                    int(height * 0.8),
                    width // 2,
                    int(height * 0.2),
                    200,
                )
        except Exception:
            logger.debug("Could not swipe the keyguard away", exc_info=True)

    try:
        if is_locked():
            logger.warning(
                "Facebook: the device is still locked; unlock it manually"
            )
    except Exception:
        logger.debug("Could not re-read the lock state", exc_info=True)


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
    *,
    required: bool = True,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        element = _find_displayed(driver, locators)
        if element is not None:
            return element
        if time.monotonic() >= deadline:
            if not required:
                return None
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for {description}"
            )
        time.sleep(POLL_INTERVAL)


def _safe_attr(element: Any, name: str) -> str:
    """Return an element attribute as text, tolerating driver errors."""

    try:
        value = element.get_attribute(name)
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _element_signature(element: Any) -> Optional[Tuple[str, str]]:
    """Identify an element by ``resource-id``/``bounds`` so it can be skipped.

    Returns ``None`` when the driver exposes neither, in which case the element
    cannot be told apart from others and must not be used as an exclusion key.
    """

    resource_id = _safe_attr(element, "resource-id")
    bounds = _safe_attr(element, "bounds")
    if not resource_id and not bounds:
        return None
    return (resource_id, bounds)


def _element_key(element: Any) -> Optional[Tuple[str, ...]]:
    """Return a stable key for *element* so it can be skipped reliably.

    The Appium element id is preferred: two different nodes can share the same
    ``resource-id`` and ``bounds`` (for example the feed composer pill and the
    text field of the composer screen it opens), which made the old
    ``resource-id``/``bounds`` comparison exclude the real post editor.
    """

    element_id = getattr(element, "id", None)
    if isinstance(element_id, str) and element_id:
        return ("id", element_id)
    signature = _element_signature(element)
    if signature is None:
        return None
    return ("signature",) + signature


#: Placeholders used by the composer text field across Facebook builds/locales.
_EDITOR_PLACEHOLDERS = (
    "Apa yang Anda pikirkan",
    "Apa yang ada di pikiran",
    "What's on your mind",
    "Tulis sesuatu",
    "Tuliskan sesuatu",
    "Bagikan sesuatu",
    "Ceritakan sesuatu",
    "Tulis postingan",
    "Write something",
    "Share something",
)

#: ``resource-id`` fragments that identify a composer text field.
_EDITOR_RESOURCE_HINTS = (
    "composer",
    "post_text",
    "status_edit",
    "text_input",
)


def _is_editor_candidate(element: Any) -> bool:
    """True when *element* looks like a field the post text can be typed into."""

    try:
        if not element.is_displayed():
            return False
        class_name = (element.get_attribute("class") or "").lower()
        text = element.get_attribute("text") or ""
        description = element.get_attribute("content-desc") or ""
        resource_id = (element.get_attribute("resource-id") or "").lower()
        clickable = element.get_attribute("clickable")
        focusable = element.get_attribute("focusable")
    except Exception:
        return False

    if "edittext" in class_name:
        return True

    tappable = any(
        isinstance(flag, str) and flag.lower() == "true"
        for flag in (clickable, focusable)
    )
    if any(hint in resource_id for hint in _EDITOR_RESOURCE_HINTS):
        # Only an editable-looking class, never a clickable container whose
        # resource-id happens to contain "composer".
        return "edit" in class_name or "input" in class_name

    haystack = f"{text}\n{description}".lower()
    if not any(
        placeholder.lower() in haystack for placeholder in _EDITOR_PLACEHOLDERS
    ):
        return False
    # Views used as editors are tappable; plain labels (screen titles) are not.
    return tappable


def _looks_like_composer_field(element: Any) -> bool:
    """True when *element* carries a composer placeholder or resource-id."""

    try:
        class_name = (element.get_attribute("class") or "").lower()
        text = element.get_attribute("text") or ""
        description = element.get_attribute("content-desc") or ""
        resource_id = (element.get_attribute("resource-id") or "").lower()
    except Exception:
        return False
    if "edittext" not in class_name:
        return False
    haystack = f"{text}\n{description}".lower()
    if any(
        placeholder.lower() in haystack for placeholder in _EDITOR_PLACEHOLDERS
    ):
        return True
    return any(hint in resource_id for hint in _EDITOR_RESOURCE_HINTS)


def _wait_for_editor(
    driver: Any,
    locators: Sequence[Locator],
    timeout: float,
    description: str,
    *,
    exclude: Any = None,
    required: bool = True,
) -> Any:
    """Wait for a text field that is *not* the composer prompt that was tapped.

    On several Facebook builds the feed composer ("Apa yang Anda pikirkan?") is
    itself an ``EditText``. Without this check the bot could type into the feed
    instead of the "Buat postingan baru" screen and then time out waiting for
    the Post button.
    """

    excluded_key = _element_key(exclude) if exclude is not None else None
    deadline = time.monotonic() + timeout
    while True:
        for by, value in locators:
            try:
                elements = driver.find_elements(by, value)
            except Exception:
                continue
            for element in elements:
                if not _is_editor_candidate(element):
                    continue
                if excluded_key is not None and _element_key(element) == excluded_key:
                    continue
                return element
        if time.monotonic() >= deadline:
            if not required:
                return None
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for {description}"
            )
        time.sleep(POLL_INTERVAL)


def _open_post_composer(
    driver: Any,
    composer: Any,
    timeout: float,
    low: float,
    high: float,
    logger: logging.Logger,
) -> Any:
    """Tap the feed composer and return the field the post text goes into.

    Facebook 588+ opens a separate "Buat postingan baru" screen. A previous
    failed run can leave the app on that screen (or reopen it directly), in which
    case the element found as "composer" *is* the post editor. That case is
    recovered through the focused element, otherwise the editor would be tapped
    and then excluded from the search, ending in ``Timed out ... waiting for the
    post editor``. When the composer entry itself is missing (feed scrolled away,
    popup on screen, app on another tab) the flow returns to the feed with an
    ``fb://feed`` deep link, dismisses common popups and scrolls to the top.
    """

    _tap_element(driver, composer, logger)
    _pause(low, high, logger, "opening the composer")

    editor = _wait_for_editor(
        driver,
        _composer_editor_locators(),
        min(timeout, EDITOR_FIRST_WAIT),
        "the post editor",
        exclude=composer,
        required=False,
    )
    if editor is not None:
        return editor

    # The tapped element may already be the editor (composer screen was still
    # open from a failed run, or the build focuses the field in place).
    editor = _active_editor(driver, composer)
    if editor is not None:
        logger.info("Facebook: using the focused field as the post editor")
        return editor

    logger.info("Facebook: post editor not visible yet; tapping the composer again")
    _tap_element(driver, composer, logger)
    _pause(low, high, logger, "reopening the composer")

    editor = _wait_for_editor(
        driver,
        _composer_editor_locators(),
        timeout,
        "the post editor",
        exclude=composer,
        required=False,
    )
    if editor is not None:
        return editor

    editor = _active_editor(driver, composer)
    if editor is None and _is_editor_candidate(composer):
        logger.warning(
            "Facebook: falling back to the tapped composer field as the post editor"
        )
        editor = composer
    if editor is None:
        raise FacebookAutomationError(
            f"Timed out after {timeout:.0f}s waiting for the post editor"
        )
    return editor


def _reuse_open_composer(
    driver: Any, timeout: float, logger: logging.Logger
) -> Any:
    """Return the editor when Facebook is already showing the composer screen."""

    if not _composer_screen_open(driver):
        return None
    editor = _wait_for_editor(
        driver,
        _composer_editor_locators(),
        0.0,
        "the post editor",
        required=False,
    )
    if editor is not None:
        logger.info(
            "Facebook: reusing the composer screen left open by an earlier run"
        )
    return editor


def _open_post_composer_entry(
    driver: Any,
    package: str,
    timeout: float,
    low: float,
    high: float,
    logger: logging.Logger,
) -> Any:
    """Return the element that opens the post composer.

    Facebook can be left on the composer/audience screen by a previous run, its
    feed can be scrolled away, or a popup can cover the composer. When the entry
    is not visible the flow returns to the feed, dismisses common popups and
    scrolls up before giving up, so a stale screen does not turn into an
    immediate timeout.
    """

    deadline = time.monotonic() + timeout
    composer = _wait_for_composer_entry(
        driver, deadline, min(COMPOSER_FIRST_WAIT, timeout)
    )
    if composer is not None:
        return composer

    if time.monotonic() < deadline:
        logger.info("Facebook: composer not visible; returning to the feed")
        _return_to_feed(driver, package, low, high, logger)
        _dismiss_popups(driver, logger)
        composer = _wait_for_composer_entry(
            driver, deadline, min(COMPOSER_FIRST_WAIT, timeout)
        )
        if composer is not None:
            return composer

    if time.monotonic() < deadline:
        logger.info("Facebook: scrolling the feed to the top")
        _scroll_to_top(driver, logger)
        composer = _wait_for_composer_entry(driver, deadline, timeout)
        if composer is not None:
            return composer

    raise FacebookAutomationError(
        f"Timed out after {timeout:.0f}s waiting for the post composer"
    )


def _wait_for_composer_entry(
    driver: Any, deadline: float, max_wait: float
) -> Any:
    """Poll the composer entry locators until *deadline* or *max_wait*."""

    end = min(deadline, time.monotonic() + max(0.0, max_wait))
    while True:
        entry = _find_displayed(driver, _composer_prompt_locators())
        if entry is not None:
            return entry
        if time.monotonic() >= end:
            return None
        time.sleep(POLL_INTERVAL)


def _return_to_feed(
    driver: Any,
    package: str,
    low: float,
    high: float,
    logger: logging.Logger,
) -> None:
    """Bring Facebook back to the home feed with an ``fb://`` deep link."""

    for url in ("fb://feed", "fb://home"):
        try:
            _open_url(driver, url, package, logger)
        except FacebookAutomationError:
            logger.debug("Could not open %s", url, exc_info=True)
            continue
        try:
            _wait_for_foreground(driver, package, 5.0, logger)
        except FacebookAutomationError:
            logger.debug("Facebook did not report the foreground state", exc_info=True)
        _pause(low, high, logger, "returning to the feed")
        return
    logger.warning("Facebook: could not return to the feed with a deep link")


#: Buttons of common Facebook popups that cover the composer.
_DISMISS_LABELS = (
    "Nanti",
    "Bukan sekarang",
    "Nanti saja",
    "Not now",
    "No thanks",
    "Tidak, terima kasih",
    "Skip",
    "Lewati",
)


def _dismiss_popups(driver: Any, logger: logging.Logger) -> None:
    """Tap away common "not now"-style popups that hide the feed composer."""

    for label in _DISMISS_LABELS:
        element = _find_displayed(
            driver,
            [
                (BY_XPATH, f'//*[@text="{label}"]'),
                (BY_ACCESSIBILITY_ID, label),
            ],
        )
        if element is not None and _tap_element(driver, element, logger):
            logger.info("Facebook: dismissed a popup (%s)", label)


def _scroll_to_top(driver: Any, logger: logging.Logger, swipes: int = 2) -> None:
    """Swipe down a couple of times so the feed composer is back on screen."""

    get_size = getattr(driver, "get_window_size", None)
    if not callable(get_size):
        return
    try:
        size = get_size()
        width = int(size.get("width", 0))
        height = int(size.get("height", 0))
    except Exception:
        logger.debug("Could not read the window size", exc_info=True)
        return
    if width <= 0 or height <= 0:
        return
    swipe = getattr(driver, "swipe", None)
    if not callable(swipe):
        return
    center = width // 2
    start_y = max(1, int(height * 0.25))
    end_y = max(start_y + 1, int(height * 0.8))
    for _ in range(max(0, swipes)):
        try:
            swipe(center, start_y, center, end_y, 300)
        except Exception:
            logger.debug("Could not scroll the feed", exc_info=True)
            return


def _active_editor(driver: Any, expected: Any = None) -> Any:
    """Return the focused text field when it belongs to the composer.

    The focused element is only trusted when it is the composer element that was
    just tapped or when it carries a composer placeholder/resource-id; otherwise
    a previously focused field (a comment box, the search bar) could receive the
    post text.
    """

    switch_to = getattr(driver, "switch_to", None)
    if switch_to is None:
        return None
    try:
        element = switch_to.active_element
    except Exception:
        return None
    if element is None or not _is_editor_candidate(element):
        return None
    if expected is not None and _element_key(element) == _element_key(expected):
        return element
    if _looks_like_composer_field(element):
        return element
    return None


def _tap_element(driver: Any, element: Any, logger: logging.Logger) -> bool:
    """Tap *element*, falling back to a centre-of-bounds gesture."""

    try:
        element.click()
        return True
    except Exception:
        logger.debug("element.click() failed; trying a coordinate tap", exc_info=True)

    try:
        location = element.location
        size = element.size
        x = int(location["x"] + size["width"] / 2)
        y = int(location["y"] + size["height"] / 2)
    except Exception:
        return False

    execute = getattr(driver, "execute_script", None)
    if callable(execute):
        try:
            execute("mobile: clickGesture", {"x": x, "y": y})
            return True
        except Exception:
            logger.debug("mobile: clickGesture failed", exc_info=True)

    tap = getattr(driver, "tap", None)
    if callable(tap):
        try:
            tap([(x, y)])
            return True
        except Exception:
            logger.debug("driver.tap failed", exc_info=True)
    return False


def _visible_texts(driver: Any, limit: int = 8) -> List[str]:
    """Return the first labelled nodes of the current page source.

    The short list is appended to the raised error so an operator can tell a
    login/checkpoint screen or dialog apart from a plain feed without opening
    the XML dump.
    """

    try:
        source = driver.page_source
    except Exception:
        return []
    if not isinstance(source, str) or not source:
        return []
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return []

    seen = set()
    texts: List[str] = []
    for node in root.iter("node"):
        for attr in ("text", "content-desc"):
            value = " ".join((node.get(attr) or "").split())
            if not value or value in seen:
                continue
            seen.add(value)
            texts.append(value if len(value) <= 32 else value[:31] + "…")
            break
        if len(texts) >= limit:
            break
    return texts


def _session_context(driver: Any) -> str:
    """Summarise the device state for a failed action (package/activity/lock)."""

    parts: List[str] = []
    for attr in ("current_package", "current_activity"):
        try:
            value = getattr(driver, attr, None)
        except Exception:
            value = None
        if isinstance(value, str) and value:
            parts.append(f"{attr}={value}")

    is_locked = getattr(driver, "is_locked", None)
    if callable(is_locked):
        try:
            parts.append(f"locked={bool(is_locked())}")
        except Exception:
            pass

    texts = _visible_texts(driver)
    if texts:
        parts.append("visible=" + " | ".join(texts))
    return "; ".join(parts)


def _capture_failure_artifacts(
    driver: Any, action: str, logger: logging.Logger
) -> str:
    """Save the page source and screenshot of a failed step.

    Returns a short ``UI dump: <paths>`` note to append to the raised error so the
    operator can send the files without re-running the flow. The target directory
    can be overridden with ``GADS_UI_DUMP_DIR``; it defaults to
    ``<tempdir>/gads-social-media-bot``.
    """

    configured = os.environ.get("GADS_UI_DUMP_DIR")
    try:
        directory = (
            Path(configured)
            if configured
            else Path(tempfile.gettempdir()) / "gads-social-media-bot"
        )
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.debug("Could not prepare the artifact directory", exc_info=True)
        return ""

    stamp = time.strftime("%Y%m%d-%H%M%S")
    saved: List[str] = []

    source = None
    try:
        source = driver.page_source
    except Exception:
        # A dead Appium session must not hide the original failure.
        logger.debug("Could not read the page source", exc_info=True)
    if isinstance(source, str) and source:
        path = directory / f"facebook-{action}-{stamp}.xml"
        try:
            path.write_text(source, encoding="utf-8")
            saved.append(str(path))
        except Exception:
            logger.debug("Could not save the page source", exc_info=True)

    screenshot = getattr(driver, "get_screenshot_as_png", None)
    if callable(screenshot):
        path = directory / f"facebook-{action}-{stamp}.png"
        try:
            path.write_bytes(screenshot())
            saved.append(str(path))
        except Exception:
            logger.debug("Could not save the screenshot", exc_info=True)

    if not saved:
        return ""
    return "UI dump: " + ", ".join(saved)


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


def _matches_button(element: Any, *, strict: bool = True) -> bool:
    """True when *element* is a visible, enabled, non-EditText button."""

    try:
        if not element.is_displayed():
            return False
        class_name = (element.get_attribute("class") or "").lower()
        clickable = element.get_attribute("clickable")
        enabled = element.get_attribute("enabled")
        text = element.get_attribute("text") or ""
        description = element.get_attribute("content-desc") or ""
    except Exception:
        return False
    # Never mistake the composer EditText for a button, even when the post text
    # itself is "Post".
    if "edittext" in class_name:
        return False
    # "Postingan baru" is the composer screen title, never the submit button.
    if f"{text} {description}".strip().lower().startswith("postingan"):
        return False
    if isinstance(enabled, str) and enabled.lower() == "false":
        return False
    # Many Facebook toolbars wrap the label in a non-clickable TextView whose
    # parent handles the tap. The relaxed pass accepts those and `_tap_element`
    # taps their centre, so the flow does not time out on such builds.
    if strict and isinstance(clickable, str) and clickable.lower() == "false":
        return False
    return True


def _find_button(driver: Any, locators: Sequence[Locator]) -> Any:
    for strict in (True, False):
        for by, value in locators:
            try:
                elements = driver.find_elements(by, value)
            except Exception:
                continue
            for element in elements:
                if _matches_button(element, strict=strict):
                    return element
    return None


def _wait_for_button(
    driver: Any,
    locators: Sequence[Locator],
    timeout: float,
    description: str,
    *,
    required: bool = True,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        element = _find_button(driver, locators)
        if element is not None:
            return element
        if time.monotonic() >= deadline:
            if not required:
                return None
            raise FacebookAutomationError(
                f"Timed out after {timeout:.0f}s waiting for {description}"
            )
        time.sleep(POLL_INTERVAL)


def _wait_for_post_button(
    driver: Any, timeout: float, *, required: bool = True
) -> Any:
    return _wait_for_button(
        driver,
        _post_button_locators(),
        timeout,
        "the Post button",
        required=required,
    )


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
    "Bagikan sesuatu",
    "Tulis sesuatu",
    "Tulis postingan",
    "Share something",
    "Write something",
    "Buat postingan",
    "Create post",
)

#: Titles of the full-screen composer ("Postingan baru" / "Buat postingan baru").
_COMPOSER_SCREEN_MARKERS = (
    "Buat postingan baru",
    "Postingan baru",
    "Create post",
    "New post",
)


def _composer_screen_locators() -> List[Locator]:
    locators: List[Locator] = []
    for marker in _COMPOSER_SCREEN_MARKERS:
        escaped = _escape_uiautomator(marker)
        locators.append(
            (BY_ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped}")')
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{escaped}")',
            )
        )
    return locators


def _composer_screen_open(driver: Any) -> bool:
    """True when Facebook already shows the full-screen post composer."""

    return _find_displayed(driver, _composer_screen_locators()) is not None


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

    # Resource ids used by the feed composer pill on several builds.
    for hint in ("feed_composer", "composer_pill"):
        escaped = _escape_uiautomator(hint)
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().resourceIdMatches(".*{escaped}.*")',
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
    locators: List[Locator] = [
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
    # Extra placeholders and resource-ids used by newer composer builds.
    for placeholder in _EDITOR_PLACEHOLDERS:
        escaped = _escape_uiautomator(placeholder)
        locators.append(
            (BY_ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped}")')
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{escaped}")',
            )
        )
    for hint in _EDITOR_RESOURCE_HINTS:
        escaped = _escape_uiautomator(hint)
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().resourceIdMatches(".*{escaped}.*")',
            )
        )
    return locators


#: Labels used by the final submit button across Facebook builds/locales.
_POST_BUTTON_LABELS = ("Post", "Posting", "Bagikan", "Kirim", "Share")


def _post_button_locators() -> List[Locator]:
    locators: List[Locator] = []
    for label in _POST_BUTTON_LABELS:
        locators.append((BY_XPATH, f'//*[@text="{label}"]'))
        locators.append((BY_ACCESSIBILITY_ID, label))
    # Wrapped labels such as "Bagikan sekarang" / "Posting sebagai …".
    for label in ("Posting", "Bagikan", "Kirim", "Share"):
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().textStartsWith("{label}")',
            )
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionStartsWith("{label}")',
            )
        )
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


#: First-step labels of the two-step composer (Facebook 588+): type the text,
#: tap Next, then tap the final Post button on the audience screen.
_NEXT_BUTTON_LABELS = ("Berikutnya", "Next", "Lanjut", "Selanjutnya")


def _next_button_locators() -> List[Locator]:
    locators: List[Locator] = []
    for label in _NEXT_BUTTON_LABELS:
        locators.append((BY_XPATH, f'//*[@text="{label}"]'))
        locators.append((BY_ACCESSIBILITY_ID, label))
    for label in ("Berikutnya", "Selanjutnya"):
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{label}")',
            )
        )
    return locators


#: Top-right button of the dedicated text screen; the text is only committed
#: after tapping it, before the audience/Next screen becomes available.
_DONE_BUTTON_LABELS = ("Selesai", "Done")


def _done_button_locators() -> List[Locator]:
    locators: List[Locator] = []
    for label in _DONE_BUTTON_LABELS:
        escaped = _escape_uiautomator(label)
        locators.append((BY_XPATH, f'//*[@text="{label}"]'))
        locators.append((BY_ACCESSIBILITY_ID, label))
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().textStartsWith("{escaped}")',
            )
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionStartsWith("{escaped}")',
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
