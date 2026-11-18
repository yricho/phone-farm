"""Facebook adapter (wrapper around the legacy facebook.py actions)."""

from __future__ import annotations

from typing import Any

try:
    from .base import PlatformAdapter, PlatformInfo
except ImportError:  # pragma: no cover - direct module execution
    from base import PlatformAdapter, PlatformInfo  # type: ignore[no-redef]

try:
    from .. import facebook as facebook_actions
except ImportError:  # pragma: no cover - direct module execution
    import facebook as facebook_actions  # type: ignore[no-redef]

__all__ = ["FacebookPlatform"]


class FacebookPlatform(PlatformAdapter):
    info = PlatformInfo(
        key="facebook",
        label="Facebook",
        package="com.facebook.katana",
        actions={
            "follow": ("username",),
            "post": ("text",),
            "comment": ("url", "text"),
            "like": ("url",),
            "save": ("url",),
            "reply": ("url", "text"),
        },
        notes="Teks post didukung penuh.",
    )

    def follow_user(self, driver: Any, username: str, **kwargs: Any) -> Any:
        return facebook_actions.follow_user(driver, username, **kwargs)

    def create_post(self, driver: Any, text: str, **kwargs: Any) -> Any:
        return facebook_actions.create_post(driver, text, **kwargs)

    def comment_on_post(
        self, driver: Any, url: str, text: str, **kwargs: Any
    ) -> Any:
        return facebook_actions.comment_on_post(driver, url, text, **kwargs)
