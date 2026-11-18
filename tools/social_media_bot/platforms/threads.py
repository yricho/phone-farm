"""Threads adapter: follow, text post and comment."""

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

__all__ = ["ThreadsPlatform"]


class ThreadsPlatform(PlatformAdapter):
    info = PlatformInfo(
        key="threads",
        label="Threads",
        package="com.instagram.barcelona",
        actions={
            "follow": ("username",),
            "post": ("text",),
            "comment": ("url", "text"),
            "like": ("url",),
            "save": ("url",),
            "reply": ("url", "text"),
        },
        notes="Threads mendukung posting teks, follow dan balasan.",
    )
    post_max_chars = 500
    comment_max_chars = 500

    def search_entry_locators(self) -> List[Locator]:
        return text_locators("Search", "Cari") + contains_locators(
            "Search", "Cari"
        )

    def composer_locators(self) -> List[Locator]:
        return text_locators(
            "Create", "Buat", "New thread", "Thread baru"
        ) + contains_locators("Create", "Buat")

    def follow_locators(self) -> List[Locator]:
        return text_locators("Follow", "Ikuti")

    def following_locators(self) -> List[Locator]:
        return text_locators("Following", "Mengikuti")

    def post_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Posting")

    def comment_button_locators(self) -> List[Locator]:
        return text_locators("Reply", "Balas", "Comment", "Komentari")

    def comment_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Posting")
