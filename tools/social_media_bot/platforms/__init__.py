"""Platform registry for the multi-network phone farm."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .base import PlatformAdapter, PlatformInfo
from .facebook import FacebookPlatform
from .instagram import InstagramPlatform
from .threads import ThreadsPlatform
from .tiktok import TikTokPlatform
from .x import XPlatform

__all__ = [
    "DEFAULT_PLATFORM",
    "FacebookPlatform",
    "InstagramPlatform",
    "PlatformAdapter",
    "PlatformInfo",
    "ThreadsPlatform",
    "TikTokPlatform",
    "XPlatform",
    "default_platforms",
    "platform_catalog",
]

DEFAULT_PLATFORM = "facebook"


def default_platforms() -> Dict[str, PlatformAdapter]:
    """Return one adapter per supported network, keyed by platform key."""
    adapters: Iterable[PlatformAdapter] = (
        FacebookPlatform(),
        InstagramPlatform(),
        ThreadsPlatform(),
        XPlatform(),
        TikTokPlatform(),
    )
    return {adapter.info.key: adapter for adapter in adapters}


def platform_catalog(
    platforms: Optional[Dict[str, PlatformAdapter]] = None,
) -> List[dict]:
    """Serializable capability list for the dashboard UI."""
    adapters = platforms or default_platforms()
    return [adapter.info.as_dict() for adapter in adapters.values()]
