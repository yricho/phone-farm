"""Result models shared by every platform adapter."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CommentResult",
    "EngagementResult",
    "FollowResult",
    "PostResult",
    "STATUS_ALREADY_FOLLOWING",
    "STATUS_COMMENTED",
    "STATUS_FOLLOWED",
    "STATUS_LIKED",
    "STATUS_POSTED",
    "STATUS_REPLIED",
    "STATUS_SAVED",
]

STATUS_FOLLOWED = "followed"
STATUS_ALREADY_FOLLOWING = "already_following"
STATUS_POSTED = "posted"
STATUS_COMMENTED = "commented"
STATUS_LIKED = "liked"
STATUS_SAVED = "saved"
STATUS_REPLIED = "replied"


@dataclass(frozen=True)
class FollowResult:
    """Outcome of a follow action."""

    username: str
    status: str
    button_text: str = ""

    @property
    def followed(self) -> bool:
        """``True`` only when the adapter actually pressed Follow."""
        return self.status == STATUS_FOLLOWED


@dataclass(frozen=True)
class PostResult:
    """Outcome of a create-post action."""

    post_text: str
    status: str
    button_text: str = ""

    @property
    def posted(self) -> bool:
        """``True`` when the Post button was pressed."""
        return self.status == STATUS_POSTED


@dataclass(frozen=True)
class CommentResult:
    """Outcome of a comment action."""

    target_url: str
    comment_text: str
    status: str
    button_text: str = ""

    @property
    def commented(self) -> bool:
        """``True`` when the comment was submitted."""
        return self.status == STATUS_COMMENTED


@dataclass(frozen=True)
class EngagementResult:
    """Outcome of like/save/reply actions."""

    target_url: str
    action: str
    status: str
    button_text: str = ""

    @property
    def done(self) -> bool:
        return self.status in {
            STATUS_LIKED,
            STATUS_SAVED,
            STATUS_REPLIED,
        }
