"""User-facing error type."""

from __future__ import annotations


class VrecError(Exception):
    """An error whose message is meant to be shown to the user as-is (no traceback)."""
