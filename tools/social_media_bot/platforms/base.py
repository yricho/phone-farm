"""Shared engine for Instagram, Threads, X and TikTok adapters.

Every adapter supplies package metadata, supported actions and locator lists;
this module runs the common Appium flows (search → profile → follow,
composer → post, URL → comment → send) with random 3-7 s pauses.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

try:
    from ..errors import PlatformActionNotSupported, PlatformAutomationError
    from ..models import (
        STATUS_ALREADY_FOLLOWING,
        STATUS_COMMENTED,
        STATUS_FOLLOWED,
        STATUS_LIKED,
        STATUS_POSTED,
        STATUS_REPLIED,
        STATUS_SAVED,
        CommentResult,
        EngagementResult,
        FollowResult,
        PostResult,
    )
except ImportError:  # pragma: no cover - direct module execution
    from errors import PlatformActionNotSupported, PlatformAutomationError
    from models import (
        STATUS_ALREADY_FOLLOWING,
        STATUS_COMMENTED,
        STATUS_FOLLOWED,
        STATUS_LIKED,
        STATUS_POSTED,
        STATUS_REPLIED,
        STATUS_SAVED,
        CommentResult,
        EngagementResult,
        FollowResult,
        PostResult,
    )

BY_ACCESSIBILITY_ID = "accessibility id"
BY_CLASS_NAME = "class name"
BY_ANDROID_UIAUTOMATOR = "-android uiautomator"
BY_XPATH = "xpath"

KEYCODE_ENTER = 66
POLL_INTERVAL = 0.5
OPTIONAL_TAB_TIMEOUT = 8.0
DEFAULT_DELAY_RANGE = (3.0, 7.0)

Locator = Tuple[str, str]


@dataclass(frozen=True)
class PlatformInfo:
    """Static description of one social platform."""

    key: str
    label: str
    package: str
    actions: Mapping[str, Sequence[str]]
    notes: str = ""

    def supports(self, action: str) -> bool:
        return action in self.actions

    def required_fields(self, action: str) -> Tuple[str, ...]:
        return tuple(self.actions.get(action, ()))

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "package": self.package,
            "actions": {
                name: list(fields) for name, fields in self.actions.items()
            },
            "notes": self.notes,
        }


# ----------------------------------------------------------------------
# Validation and selector helpers
# ----------------------------------------------------------------------
def normalize_username(target_username: str) -> str:
    if not isinstance(target_username, str):
        raise ValueError("username must be a string")
    username = target_username.strip()
    if username.startswith("@"):
        username = username[1:].strip()
    if not username:
        raise ValueError("username must not be empty")
    if len(username) > 100:
        raise ValueError("username must be at most 100 characters")
    if any(ord(character) < 32 for character in username):
        raise ValueError("username contains control characters")
    return username


def normalize_text(value: str, field: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    if len(value) > max_chars:
        raise ValueError(f"{field} must be at most {max_chars} characters")
    if any(
        ord(character) < 32 and character not in "\n\r\t"
        for character in value
    ):
        raise ValueError(f"{field} contains unsupported control characters")
    return value


def normalize_url(target_url: str) -> str:
    if not isinstance(target_url, str):
        raise ValueError("url must be a string")
    url = target_url.strip()
    if not url:
        raise ValueError("url must not be empty")
    if len(url) > 2048:
        raise ValueError("url must be at most 2048 characters")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("url must be an http(s) URL")
    return url


def validate_delay_range(delay_range: Sequence[float]) -> Tuple[float, float]:
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


def validate_timeout(timeout: float) -> float:
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    return float(timeout)


def escape_uiautomator(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def xpath_literal(value: str) -> str:
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


def text_locators(*labels: str) -> List[Locator]:
    """Exact text/content-desc locators plus an XPath fallback."""
    locators: List[Locator] = [
        (BY_ACCESSIBILITY_ID, label) for label in labels
    ]
    conditions = []
    for label in labels:
        literal = xpath_literal(label)
        conditions.append(f"@text={literal}")
        conditions.append(f"@content-desc={literal}")
    if conditions:
        locators.append((BY_XPATH, "//*[" + " or ".join(conditions) + "]"))
    return locators


def contains_locators(*texts: str) -> List[Locator]:
    """UiAutomator contains() locators for labels with dynamic suffixes."""
    locators: List[Locator] = []
    for text in texts:
        escaped = escape_uiautomator(text)
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{escaped}")',
            )
        )
        locators.append(
            (
                BY_ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{escaped}")',
            )
        )
    return locators


def result_locators(username: str) -> List[Locator]:
    escaped = escape_uiautomator(username)
    literal = xpath_literal(username)
    return [
        (BY_ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{escaped}")'),
        (
            BY_ANDROID_UIAUTOMATOR,
            f'new UiSelector().descriptionContains("{escaped}")',
        ),
        (
            BY_XPATH,
            "//android.widget.TextView[contains(@text, "
            f"{literal}) or contains(@content-desc, {literal})]",
        ),
    ]


# ----------------------------------------------------------------------
# Runtime helpers
# ----------------------------------------------------------------------
def pause(
    low: float, high: float, logger: logging.Logger, after_step: str
) -> None:
    seconds = random.uniform(low, high)
    logger.debug("Random delay %.2fs after %s", seconds, after_step)
    time.sleep(seconds)


def element_text(element: Any) -> str:
    try:
        text = element.get_attribute("text") or element.get_attribute("content-desc")
    except Exception:
        return ""
    return (text or "").strip()


def clear_field(element: Any, logger: logging.Logger) -> None:
    try:
        element.clear()
    except Exception:
        logger.debug("Could not clear the field; typing on top", exc_info=True)


def hide_keyboard(driver: Any, logger: logging.Logger) -> None:
    hide = getattr(driver, "hide_keyboard", None)
    if not callable(hide):
        return
    try:
        hide()
    except Exception:
        logger.debug("Keyboard was not visible", exc_info=True)


def find_displayed(driver: Any, locators: Sequence[Locator]) -> Any:
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


def wait_for_any(
    driver: Any,
    locators: Sequence[Locator],
    timeout: float,
    description: str,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        element = find_displayed(driver, locators)
        if element is not None:
            return element
        if time.monotonic() >= deadline:
            raise PlatformAutomationError(
                f"Timed out after {timeout:.0f}s waiting for {description}"
            )
        time.sleep(POLL_INTERVAL)


def wait_for_foreground(
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
            raise PlatformAutomationError(
                f"{package} did not reach the foreground within {timeout:.0f}s"
            )
        time.sleep(POLL_INTERVAL)


def open_url(driver: Any, url: str, package: str, logger: logging.Logger) -> None:
    opened = False
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
                opened = True
                break
            except Exception:
                logger.debug("%s failed for %s", script, url, exc_info=True)

    if not opened:
        get = getattr(driver, "get", None)
        if callable(get):
            try:
                get(url)
                opened = True
            except Exception:
                logger.debug("driver.get failed for %s", url, exc_info=True)

    if not opened:
        raise PlatformAutomationError(
            f"Could not open {url!r} inside {package}. "
            "Use an app-link URL or a platform deep link."
        )


def is_result_candidate(element: Any) -> bool:
    try:
        if not element.is_displayed():
            return False
        class_name = (element.get_attribute("class") or "").lower()
        resource_id = (element.get_attribute("resource-id") or "").lower()
    except Exception:
        return False
    if "edittext" in class_name:
        return False
    hints = (
        "search_edit",
        "search_bar",
        "search_box",
        "search_field",
        "search_input",
    )
    return not any(hint in resource_id for hint in hints)


def wait_for_result(
    driver: Any, locators: Sequence[Locator], username: str, timeout: float
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        for by, value in locators:
            try:
                elements = driver.find_elements(by, value)
            except Exception:
                continue
            for element in elements:
                if is_result_candidate(element):
                    return element
        if time.monotonic() >= deadline:
            raise PlatformAutomationError(
                f"Timed out after {timeout:.0f}s waiting for a search result "
                f"matching {username!r}"
            )
        time.sleep(POLL_INTERVAL)


def wait_for_follow_state(
    driver: Any,
    follow_locators: Sequence[Locator],
    following_locators: Sequence[Locator],
    username: str,
    timeout: float,
) -> Tuple[Any, bool]:
    deadline = time.monotonic() + timeout
    while True:
        following = find_displayed(driver, following_locators)
        if following is not None:
            return following, True
        follow = find_displayed(driver, follow_locators)
        if follow is not None:
            return follow, False
        if time.monotonic() >= deadline:
            raise PlatformAutomationError(
                f"Timed out after {timeout:.0f}s waiting for the Follow button "
                f"on {username!r}"
            )
        time.sleep(POLL_INTERVAL)


def tap_optional(
    driver: Any,
    locators: Sequence[Locator],
    timeout: float,
    logger: logging.Logger,
) -> Any:
    try:
        element = wait_for_any(driver, locators, timeout, "optional tab")
    except PlatformAutomationError:
        logger.debug("Optional element not found; continuing")
        return None
    try:
        element.click()
    except Exception:
        logger.debug("Optional element could not be tapped", exc_info=True)
        return None
    return element


def submit(driver: Any, locators: Sequence[Locator], logger: logging.Logger) -> str:
    button = find_displayed(driver, locators)
    if button is not None:
        button.click()
        return element_text(button)

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


class PlatformAdapter:
    """Default Appium flows driven by per-platform locator lists."""

    info = PlatformInfo("base", "Base", "", {})
    search_submit_with_enter = True
    post_max_chars = 5000
    comment_max_chars = 8000

    # -- metadata ------------------------------------------------------
    @property
    def key(self) -> str:
        return self.info.key

    @property
    def label(self) -> str:
        return self.info.label

    def supports(self, action: str) -> bool:
        return self.info.supports(action)

    def required_fields(self, action: str) -> Tuple[str, ...]:
        return self.info.required_fields(action)

    def _require(self, action: str) -> None:
        if not self.supports(action):
            supported = ", ".join(sorted(self.info.actions))
            raise PlatformActionNotSupported(
                f"{self.label} does not support {action!r}. "
                f"Supported actions: {supported or 'none'}."
            )

    # -- actions -------------------------------------------------------
    def follow_user(
        self,
        driver: Any,
        username: str,
        *,
        delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> FollowResult:
        self._require("follow")
        target = normalize_username(username)
        timeout = validate_timeout(timeout)
        low, high = validate_delay_range(delay_range)
        log = logger or logging.getLogger(f"{self.key}.actions")

        try:
            self._activate(driver, timeout, log)
            pause(low, high, log, "opening the app")

            entry = wait_for_any(
                driver,
                self.search_entry_locators(),
                timeout,
                f"{self.label} search",
            )
            entry.click()
            pause(low, high, log, "opening search")

            box = wait_for_any(
                driver,
                self.search_box_locators(),
                timeout,
                f"{self.label} search box",
            )
            box.click()
            clear_field(box, log)
            box.send_keys(target)
            pause(low, high, log, "typing the username")

            if self.search_submit_with_enter:
                driver.press_keycode(KEYCODE_ENTER)
                pause(low, high, log, "submitting the search")

            tap_optional(
                driver,
                self.search_scope_locators(),
                min(timeout, OPTIONAL_TAB_TIMEOUT),
                log,
            )
            pause(low, high, log, "selecting the People tab")

            result = wait_for_result(
                driver,
                self.result_locators(target),
                target,
                timeout,
            )
            result.click()
            pause(low, high, log, "opening the profile")

            button, already_following = wait_for_follow_state(
                driver,
                self.follow_locators(),
                self.following_locators(),
                target,
                timeout,
            )
            button_text = element_text(button)
            if already_following:
                return FollowResult(
                    target, STATUS_ALREADY_FOLLOWING, button_text
                )

            button.click()
            pause(low, high, log, "pressing Follow")
            return FollowResult(target, STATUS_FOLLOWED, button_text)
        except (PlatformActionNotSupported, PlatformAutomationError):
            raise
        except Exception as exc:
            raise PlatformAutomationError(
                f"{self.label} follow_user({target!r}) failed: {exc}"
            ) from exc

    def create_post(
        self,
        driver: Any,
        text: str,
        *,
        delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> PostResult:
        self._require("post")
        post_text = normalize_text(text, "post_text", self.post_max_chars)
        timeout = validate_timeout(timeout)
        low, high = validate_delay_range(delay_range)
        log = logger or logging.getLogger(f"{self.key}.actions")

        try:
            self._activate(driver, timeout, log)
            pause(low, high, log, "opening the app")

            composer = wait_for_any(
                driver,
                self.composer_locators(),
                timeout,
                f"{self.label} composer",
            )
            composer.click()
            pause(low, high, log, "opening the composer")

            editor = wait_for_any(
                driver,
                self.post_editor_locators(),
                timeout,
                f"{self.label} post editor",
            )
            editor.click()
            clear_field(editor, log)
            editor.send_keys(post_text)
            pause(low, high, log, "typing the post")

            hide_keyboard(driver, log)
            button_text = submit(driver, self.post_submit_locators(), log)
            pause(low, high, log, "pressing Post")
            return PostResult(post_text, STATUS_POSTED, button_text)
        except (PlatformActionNotSupported, PlatformAutomationError):
            raise
        except Exception as exc:
            raise PlatformAutomationError(
                f"{self.label} create_post failed: {exc}"
            ) from exc

    def comment_on_post(
        self,
        driver: Any,
        url: str,
        text: str,
        *,
        delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> CommentResult:
        self._require("comment")
        target_url = normalize_url(url)
        comment_text = normalize_text(
            text, "comment_text", self.comment_max_chars
        )
        timeout = validate_timeout(timeout)
        low, high = validate_delay_range(delay_range)
        log = logger or logging.getLogger(f"{self.key}.actions")

        try:
            open_url(driver, target_url, self.info.package, log)
            wait_for_foreground(driver, self.info.package, timeout, log)
            pause(low, high, log, "opening the post")

            editor = find_displayed(driver, self.comment_editor_locators())
            if editor is None:
                button = wait_for_any(
                    driver,
                    self.comment_button_locators(),
                    timeout,
                    f"{self.label} comment button",
                )
                button.click()
                pause(low, high, log, "opening the comment box")
                editor = wait_for_any(
                    driver,
                    self.comment_editor_locators(),
                    timeout,
                    f"{self.label} comment editor",
                )

            editor.click()
            clear_field(editor, log)
            editor.send_keys(comment_text)
            pause(low, high, log, "typing the comment")

            button_text = submit(
                driver, self.comment_submit_locators(), log
            )
            pause(low, high, log, "sending the comment")
            return CommentResult(
                target_url, comment_text, STATUS_COMMENTED, button_text
            )
        except (PlatformActionNotSupported, PlatformAutomationError):
            raise
        except Exception as exc:
            raise PlatformAutomationError(
                f"{self.label} comment_on_post({target_url!r}) failed: {exc}"
            ) from exc

    def like_post(
        self,
        driver: Any,
        url: str,
        *,
        delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> EngagementResult:
        self._require("like")
        target_url = normalize_url(url)
        timeout = validate_timeout(timeout)
        low, high = validate_delay_range(delay_range)
        log = logger or logging.getLogger(f"{self.key}.actions")
        try:
            open_url(driver, target_url, self.info.package, log)
            wait_for_foreground(driver, self.info.package, timeout, log)
            pause(low, high, log, "opening the post")
            button = find_displayed(driver, self.like_locators())
            if button is None:
                already = find_displayed(driver, self.liked_locators())
                if already is not None:
                    return EngagementResult(
                        target_url, "like", "already_liked", element_text(already)
                    )
                raise PlatformAutomationError("Like button not found")
            button.click()
            pause(low, high, log, "liking the post")
            return EngagementResult(
                target_url, "like", STATUS_LIKED, element_text(button)
            )
        except (PlatformActionNotSupported, PlatformAutomationError):
            raise
        except Exception as exc:
            raise PlatformAutomationError(
                f"{self.label} like_post({target_url!r}) failed: {exc}"
            ) from exc

    def save_post(
        self,
        driver: Any,
        url: str,
        *,
        delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> EngagementResult:
        self._require("save")
        target_url = normalize_url(url)
        timeout = validate_timeout(timeout)
        low, high = validate_delay_range(delay_range)
        log = logger or logging.getLogger(f"{self.key}.actions")
        try:
            open_url(driver, target_url, self.info.package, log)
            wait_for_foreground(driver, self.info.package, timeout, log)
            pause(low, high, log, "opening the post")
            button = find_displayed(driver, self.save_locators())
            if button is None:
                already = find_displayed(driver, self.saved_locators())
                if already is not None:
                    return EngagementResult(
                        target_url, "save", "already_saved", element_text(already)
                    )
                raise PlatformAutomationError("Save button not found")
            button.click()
            pause(low, high, log, "saving the post")
            return EngagementResult(
                target_url, "save", STATUS_SAVED, element_text(button)
            )
        except (PlatformActionNotSupported, PlatformAutomationError):
            raise
        except Exception as exc:
            raise PlatformAutomationError(
                f"{self.label} save_post({target_url!r}) failed: {exc}"
            ) from exc

    def reply_to_post(
        self,
        driver: Any,
        url: str,
        text: str,
        *,
        delay_range: Sequence[float] = DEFAULT_DELAY_RANGE,
        timeout: float = 20.0,
        logger: Optional[logging.Logger] = None,
    ) -> EngagementResult:
        self._require("reply")
        target_url = normalize_url(url)
        reply_text = normalize_text(
            text, "reply_text", self.comment_max_chars
        )
        timeout = validate_timeout(timeout)
        low, high = validate_delay_range(delay_range)
        log = logger or logging.getLogger(f"{self.key}.actions")
        try:
            open_url(driver, target_url, self.info.package, log)
            wait_for_foreground(driver, self.info.package, timeout, log)
            pause(low, high, log, "opening the post")
            editor = find_displayed(driver, self.comment_editor_locators())
            if editor is None:
                button = wait_for_any(
                    driver,
                    self.comment_button_locators(),
                    timeout,
                    f"{self.label} reply button",
                )
                button.click()
                pause(low, high, log, "opening the reply box")
                editor = wait_for_any(
                    driver,
                    self.comment_editor_locators(),
                    timeout,
                    f"{self.label} reply editor",
                )
            editor.click()
            clear_field(editor, log)
            editor.send_keys(reply_text)
            pause(low, high, log, "typing the reply")
            button_text = submit(driver, self.comment_submit_locators(), log)
            pause(low, high, log, "sending the reply")
            return EngagementResult(
                target_url, "reply", STATUS_REPLIED, button_text
            )
        except (PlatformActionNotSupported, PlatformAutomationError):
            raise
        except Exception as exc:
            raise PlatformAutomationError(
                f"{self.label} reply_to_post({target_url!r}) failed: {exc}"
            ) from exc

    # -- helpers -------------------------------------------------------
    def _activate(
        self, driver: Any, timeout: float, logger: logging.Logger
    ) -> None:
        driver.activate_app(self.info.package)
        wait_for_foreground(driver, self.info.package, timeout, logger)

    # -- locator defaults (override per platform) ----------------------
    def search_entry_locators(self) -> List[Locator]:
        return text_locators("Search", "Cari") + contains_locators(
            "Search", "Cari"
        )

    def search_box_locators(self) -> List[Locator]:
        return [
            (BY_CLASS_NAME, "android.widget.EditText"),
            (BY_XPATH, "//android.widget.EditText"),
        ]

    def search_scope_locators(self) -> List[Locator]:
        return []

    def result_locators(self, username: str) -> List[Locator]:
        return result_locators(username)

    def follow_locators(self) -> List[Locator]:
        return text_locators("Follow", "Ikuti")

    def following_locators(self) -> List[Locator]:
        return text_locators("Following", "Mengikuti")

    def composer_locators(self) -> List[Locator]:
        return text_locators(
            "Create", "Buat", "Compose", "Tulis", "Post", "Posting"
        )

    def post_editor_locators(self) -> List[Locator]:
        return self.search_box_locators()

    def post_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Posting")

    def comment_button_locators(self) -> List[Locator]:
        return text_locators("Comment", "Komentari", "Reply", "Balas")

    def comment_editor_locators(self) -> List[Locator]:
        return self.search_box_locators()

    def comment_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Posting", "Send", "Kirim")

    def like_locators(self) -> List[Locator]:
        return text_locators("Like", "Suka")

    def liked_locators(self) -> List[Locator]:
        return text_locators("Unlike", "Batal Suka", "Liked", "Disukai")

    def save_locators(self) -> List[Locator]:
        return text_locators("Save", "Simpan", "Bookmark", "Tandai")

    def saved_locators(self) -> List[Locator]:
        return text_locators("Saved", "Tersimpan", "Disimpan")
