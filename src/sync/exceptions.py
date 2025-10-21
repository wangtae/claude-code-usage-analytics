"""
Custom exceptions for Gist synchronization.
"""


class ConflictError(Exception):
    """
    Raised when Gist sync conflict cannot be auto-resolved.

    This typically occurs when:
    - Multiple devices push simultaneously
    - Remote manifest is newer than local
    - Auto-merge fails after max retries
    """
    pass


class SyncError(Exception):
    """
    Generic synchronization error.

    Base class for all sync-related errors.
    """
    pass


class TokenError(Exception):
    """
    Raised when GitHub token is invalid or missing.
    """
    pass


class ManifestError(Exception):
    """
    Raised when manifest is corrupted or invalid.
    """
    pass
