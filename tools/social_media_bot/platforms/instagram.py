"""Instagram adapter: follow and comment (text-only posts are not supported)."""

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

__all__ = ["InstagramPlatform"]


class InstagramPlatform(PlatformAdapter):
    info = PlatformInfo(
        key="instagram",
        label="Instagram",
        package="com.instagram.android",
        actions={
            "follow": ("username",),
            "comment": ("url", "text"),
            "like": ("url",),
            "save": ("url",),
            "reply": ("url", "text"),
        },
        notes=(
            "Instagram hanya mendukung follow & comment; posting butuh media "
            "sehingga tidak tersedia lewat teks saja."
        ),
    )
    comment_max_chars = 2200

    def search_entry_locators(self) -> List[Locator]:
        return text_locators(
            "Search and Explore",
            "Search",
            "Cari dan jelajahi",
            "Cari",
        ) + contains_locators("Search and Explore", "Cari dan jelajahi")

    def follow_locators(self) -> List[Locator]:
        return text_locators("Follow", "Follow Back", "Ikuti", "Ikuti Balik")

    def following_locators(self) -> List[Locator]:
        return text_locators("Following", "Mengikuti")

    def comment_button_locators(self) -> List[Locator]:
        return text_locators("Comment", "Komentari", "Komentar")

    def comment_submit_locators(self) -> List[Locator]:
        return text_locators("Post", "Posting")
