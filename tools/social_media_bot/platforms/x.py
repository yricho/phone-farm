"""X (Twitter) adapter: follow, text post and reply."""

from __future__ import annotations

from typing import List

try:
    from .base import (
        Locator,
        PlatformAdapter,
        PlatformInfo,
        contains_locators,
        text_locators,
    )
except ImportError:  # pragma: no cover - direct module execution
    from base import (  # type: ignore[no-redef]
        Locator,
        PlatformAdapter,
        PlatformInfo,
        contains_locators,
        text_locators,
    )

__all__ = ["XPlatform"]


class XPlatform(PlatformAdapter):
    info = PlatformInfo(
        key="x",
        label="X",
        package="com.twitter.android",
        actions={
            "follow": ("username",),
            "post": ("text",),
            "comment": ("url", "text"),
            "like": ("url",),
            "save": ("url",),
            "reply": ("url", "text"),
        },
        notes="Post teks maksimal 280 karakter (akun gratis).",
    )
    post_max_chars = 280
    comment_max_chars = 280

    def search_entry_locators(self) -> List[Locator]:
        return text_locators("Search", "Cari") + contains_locators(
            "Search", "Cari"
        )

    def composer_locators(self) -> List[Locator]:
        return text_locators(
            "Compose", "Tulis", "Post", "Posting"
        ) + contains_locators("Compose", "Tulis")

    def follow_locators(self) -> List[Locator]:
        return text_locators("Follow", "Ikuti")

    def following_locators(self) -> List[Locator]:
        return text_locators("Following", "Mengikuti")

    def post_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Posting")

    def comment_button_locators(self) -> List[Locator]:
        return text_locators("Reply", "Balas")

    def comment_submit_locators(self) -> List[Locator]:
        return text_locators("Reply", "Balas", "Post", "Posting")
