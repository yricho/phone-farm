"""Multi-device Appium automation skeleton (social media use case)."""

from .errors import (
    AppiumNotInstalledError,
    DeviceConnectionError,
    DeviceNotConnectedError,
    FacebookAutomationError,
    PlatformActionNotSupported,
    PlatformAutomationError,
    SocialMediaBotError,
)
from .facebook import (
    FACEBOOK_PACKAGE,
    CommentResult,
    FollowResult,
    PostResult,
    comment_on_post,
    create_post,
    follow_user,
)
from .platforms import (
    FacebookPlatform,
    InstagramPlatform,
    PlatformAdapter,
    PlatformInfo,
    ThreadsPlatform,
    TikTokPlatform,
    XPlatform,
    default_platforms,
    platform_catalog,
)
from .rack import DeviceSpec, Rack, load_rack, sequential_rack
from .social_media_bot import DeviceConnection, DeviceTaskResult, SocialMediaBot

__all__ = [
    "AppiumNotInstalledError",
    "CommentResult",
    "DeviceConnection",
    "DeviceConnectionError",
    "DeviceNotConnectedError",
    "DeviceSpec",
    "DeviceTaskResult",
    "FACEBOOK_PACKAGE",
    "FacebookAutomationError",
    "FacebookPlatform",
    "FollowResult",
    "InstagramPlatform",
    "PlatformActionNotSupported",
    "PlatformAdapter",
    "PlatformAutomationError",
    "PlatformInfo",
    "PostResult",
    "Rack",
    "SocialMediaBot",
    "SocialMediaBotError",
    "ThreadsPlatform",
    "TikTokPlatform",
    "XPlatform",
    "comment_on_post",
    "create_post",
    "default_platforms",
    "follow_user",
    "load_rack",
    "platform_catalog",
    "sequential_rack",
]
