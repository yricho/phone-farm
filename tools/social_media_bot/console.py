"""Small runtime helpers shared by the command-line entry points."""

from __future__ import annotations

import sys
from typing import Any


def configure_stdio() -> None:
    """Make console output safe on Windows code pages.

    Windows terminals often default to a legacy encoding (for example cp1252)
    while this project logs Indonesian text and occasional Unicode symbols such
    as ``·`` or ``…``. Reconfiguring stdout/stderr to UTF-8 prevents
    ``UnicodeEncodeError`` when those lines are printed. The call is a no-op
    when the stream does not support ``reconfigure`` (for example under some
    test runners or embedded interpreters).
    """

    for name in ("stdout", "stderr"):
        stream: Any = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, LookupError, OSError, ValueError):
            continue
