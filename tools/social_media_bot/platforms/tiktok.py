"""TikTok adapter: follow and comment (posts require a video)."""

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

__all__ = ["TikTokPlatform"]


class TikTokPlatform(PlatformAdapter):
    info = PlatformInfo(
        key="tiktok",
        label="TikTok",
        package="com.zhiliaoapp.musically",
        actions={
            "follow": ("username",),
            "comment": ("url", "text"),
            "like": ("url",),
            "save": ("url",),
            "reply": ("url", "text"),
        },
        notes=(
            "TikTok hanya mendukung follow & comment; posting butuh video. "
            "Untuk region tertentu package bisa com.ss.android.ugc.trill."
        ),
    )
    comment_max_chars = 150

    def search_entry_locators(self) -> List[Locator]:
        return text_locators("Search", "Cari") + contains_locators(
            "Search", "Cari"
        )

    def search_scope_locators(self) -> List[Locator]:
        return text_locators("Users", "Pengguna")

    def follow_locators(self) -> List[Locator]:
        return text_locators("Follow", "Ikuti")

    def following_locators(self) -> List[Locator]:
        return text_locators("Following", "Mengikuti", "Friends", "Teman")

    def comment_button_locators(self) -> List[Locator]:
        return text_locators("Comment", "Komentar", "Komentari")

    def comment_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Send", "Kirim")
