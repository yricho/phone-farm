"""Exception hierarchy shared by the phone-farm automation modules."""

from __future__ import annotations

__all__ = [
    "AppiumNotInstalledError",
    "DeviceConnectionError",
    "DeviceNotConnectedError",
    "FacebookAutomationError",
    "PlatformActionNotSupported",
    "PlatformAutomationError",
    "SocialMediaBotError",
]


class SocialMediaBotError(Exception):
    """Base class for every error raised by the automation helpers."""


class AppiumNotInstalledError(SocialMediaBotError):
    """Raised when ``Appium-Python-Client`` is not installed in the environment."""


class DeviceConnectionError(SocialMediaBotError):
    """Raised when an Appium session cannot be created for a device."""


class DeviceNotConnectedError(SocialMediaBotError, KeyError):
    """Raised when a driver is requested for a device without an active session."""

    def __init__(self, udid: str) -> None:
        super().__init__(udid)
        self.udid = udid

    def __str__(self) -> str:
        return f"Device {self.udid!r} is not connected"


class PlatformAutomationError(SocialMediaBotError):
    """Raised when a platform UI step cannot be completed."""


class FacebookAutomationError(PlatformAutomationError):
    """Raised when a Facebook UI step cannot be completed."""


class PlatformActionNotSupported(SocialMediaBotError):
    """Raised when a platform does not support the requested action."""
