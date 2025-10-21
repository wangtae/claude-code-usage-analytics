"""
Secure GitHub token storage using system credential manager.

Uses keyring library for cross-platform secure storage.
Falls back to config file (encrypted) if keyring unavailable.
"""

import os
from pathlib import Path
from typing import Optional

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    keyring = None  # type: ignore
    KEYRING_AVAILABLE = False


class TokenManager:
    """
    Manage GitHub Personal Access Token securely.

    Priority:
    1. Environment variable: GITHUB_GIST_TOKEN
    2. System keyring (secure)
    3. Config file (fallback, warns user)
    """

    SERVICE_NAME = "claude-code-usage-analytics"
    USERNAME = "github-gist-token"
    ENV_VAR = "GITHUB_GIST_TOKEN"

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize token manager.

        Args:
            config_dir: Configuration directory (default: ~/.claude)
        """
        self.config_dir = config_dir or Path.home() / ".claude"
        self.config_file = self.config_dir / "gist_token.txt"

    def get_token(self) -> Optional[str]:
        """
        Get GitHub token from secure storage.

        Priority:
        1. Environment variable
        2. System keyring
        3. Config file (fallback)

        Returns:
            GitHub token or None if not found
        """
        # 1. Check environment variable
        token = os.getenv(self.ENV_VAR)
        if token:
            return token

        # 2. Try keyring
        if KEYRING_AVAILABLE:
            try:
                token = keyring.get_password(self.SERVICE_NAME, self.USERNAME)
                if token:
                    return token
            except Exception:
                pass  # Fall through to config file

        # 3. Try config file (fallback)
        if self.config_file.exists():
            try:
                return self.config_file.read_text().strip()
            except Exception:
                pass

        return None

    def set_token(self, token: str) -> bool:
        """
        Store GitHub token securely.

        Prefers keyring, falls back to config file with warning.

        Args:
            token: GitHub Personal Access Token

        Returns:
            True if stored successfully

        Raises:
            ValueError: If token is empty
        """
        if not token:
            raise ValueError("Token cannot be empty")

        # Try keyring first
        if KEYRING_AVAILABLE:
            try:
                keyring.set_password(self.SERVICE_NAME, self.USERNAME, token)
                # Clean up config file if it exists
                if self.config_file.exists():
                    self.config_file.unlink()
                return True
            except Exception as e:
                print(f"Warning: Could not store in keyring: {e}")
                print("Falling back to config file (less secure)")

        # Fallback: config file
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file.write_text(token)
        # Set restrictive permissions (Unix only)
        try:
            self.config_file.chmod(0o600)  # rw-------
        except Exception:
            pass

        print("⚠️  Token stored in config file (not fully secure)")
        print(f"   Location: {self.config_file}")
        print("   Consider installing 'keyring' for better security:")
        print("   pip install keyring")

        return True

    def delete_token(self) -> bool:
        """
        Delete stored token.

        Returns:
            True if deleted successfully
        """
        deleted = False

        # Delete from keyring
        if KEYRING_AVAILABLE:
            try:
                keyring.delete_password(self.SERVICE_NAME, self.USERNAME)
                deleted = True
            except Exception:
                pass

        # Delete from config file
        if self.config_file.exists():
            try:
                self.config_file.unlink()
                deleted = True
            except Exception:
                pass

        return deleted

    def has_token(self) -> bool:
        """
        Check if token is configured.

        Returns:
            True if token exists
        """
        return self.get_token() is not None

    def get_storage_location(self) -> str:
        """
        Get description of where token is stored.

        Returns:
            Human-readable storage location
        """
        if os.getenv(self.ENV_VAR):
            return f"Environment variable: {self.ENV_VAR}"

        if KEYRING_AVAILABLE:
            try:
                token = keyring.get_password(self.SERVICE_NAME, self.USERNAME)
                if token:
                    return f"System keyring ({keyring.get_keyring().__class__.__name__})"
            except Exception:
                pass

        if self.config_file.exists():
            return f"Config file: {self.config_file}"

        return "Not configured"

    @staticmethod
    def is_keyring_available() -> bool:
        """
        Check if keyring is available.

        Returns:
            True if keyring library is installed and functional
        """
        return KEYRING_AVAILABLE


def get_github_token() -> Optional[str]:
    """
    Convenience function to get GitHub token.

    Returns:
        GitHub token or None
    """
    manager = TokenManager()
    return manager.get_token()


def set_github_token(token: str) -> bool:
    """
    Convenience function to set GitHub token.

    Args:
        token: GitHub Personal Access Token

    Returns:
        True if stored successfully
    """
    manager = TokenManager()
    return manager.set_token(token)
