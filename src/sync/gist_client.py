"""
GitHub Gist API client for usage data synchronization.

Uses GitHub REST API v3 for Gist operations.
No external dependencies (uses requests library).
"""

import json
import time
from typing import Any, Optional, TYPE_CHECKING
from datetime import datetime, timezone

if TYPE_CHECKING:
    import requests as requests_module
else:
    try:
        import requests as requests_module
    except ImportError:
        requests_module = None  # type: ignore


class GistClient:
    """
    GitHub Gist API client.

    Handles authentication, file operations, and error handling.
    """

    API_BASE = "https://api.github.com"
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds

    def __init__(self, token: str):
        """
        Initialize Gist client.

        Args:
            token: GitHub Personal Access Token with 'gist' scope

        Raises:
            ImportError: If requests library not installed
            ValueError: If token is empty
        """
        if requests_module is None:
            raise ImportError(
                "requests library required. Install with: pip install requests"
            )

        if not token:
            raise ValueError("GitHub token is required")

        self.token = token
        self.session = requests_module.Session()
        self.session.headers.update({
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "claude-code-usage-analytics",
        })

    def create_gist(
        self,
        files: dict[str, str],
        description: str = "Claude Code Usage Analytics Data",
        public: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new Gist.

        Args:
            files: Dictionary of {filename: content}
            description: Gist description
            public: If True, create public Gist (default: private)

        Returns:
            Gist data including ID and URL

        Raises:
            RuntimeError: If API request fails
        """
        payload = {
            "description": description,
            "public": public,
            "files": {
                filename: {"content": content}
                for filename, content in files.items()
            },
        }

        response = self._request("POST", f"{self.API_BASE}/gists", json=payload)
        return response.json()

    def get_gist(self, gist_id: str) -> dict[str, Any]:
        """
        Get Gist by ID.

        Args:
            gist_id: Gist ID

        Returns:
            Gist data

        Raises:
            RuntimeError: If Gist not found or API fails
        """
        response = self._request("GET", f"{self.API_BASE}/gists/{gist_id}")
        return response.json()

    def update_gist(
        self,
        gist_id: str,
        files: dict[str, Optional[str]],
        description: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Update existing Gist.

        Args:
            gist_id: Gist ID
            files: Dictionary of {filename: content} (None to delete file)
            description: New description (optional)

        Returns:
            Updated Gist data

        Raises:
            RuntimeError: If API request fails
        """
        payload: dict[str, Any] = {
            "files": {}
        }

        for filename, content in files.items():
            if content is None:
                # Delete file
                payload["files"][filename] = None
            else:
                # Update or add file
                payload["files"][filename] = {"content": content}

        if description is not None:
            payload["description"] = description

        response = self._request(
            "PATCH",
            f"{self.API_BASE}/gists/{gist_id}",
            json=payload
        )
        return response.json()

    def delete_gist(self, gist_id: str) -> None:
        """
        Delete Gist.

        Args:
            gist_id: Gist ID

        Raises:
            RuntimeError: If API request fails
        """
        self._request("DELETE", f"{self.API_BASE}/gists/{gist_id}")

    def list_gists(self, per_page: int = 100) -> list[dict[str, Any]]:
        """
        List authenticated user's Gists.

        Args:
            per_page: Results per page (max 100)

        Returns:
            List of Gist data

        Raises:
            RuntimeError: If API request fails
        """
        response = self._request(
            "GET",
            f"{self.API_BASE}/gists",
            params={"per_page": per_page}
        )
        return response.json()

    def find_gist_by_description(self, description: str) -> Optional[dict[str, Any]]:
        """
        Find Gist by description.

        Args:
            description: Gist description to search for

        Returns:
            Gist data if found, None otherwise
        """
        gists = self.list_gists()
        for gist in gists:
            if gist.get("description") == description:
                return gist
        return None

    def get_file_content(self, gist_id: str, filename: str) -> str:
        """
        Get content of a specific file from Gist.

        Args:
            gist_id: Gist ID
            filename: Filename

        Returns:
            File content as string

        Raises:
            RuntimeError: If file not found or API fails
        """
        gist = self.get_gist(gist_id)
        files = gist.get("files", {})

        if filename not in files:
            raise RuntimeError(f"File '{filename}' not found in Gist")

        file_data = files[filename]
        content = file_data.get("content")

        if content is None:
            # Content not in response (truncated), fetch raw URL
            raw_url = file_data.get("raw_url")
            if raw_url:
                response = self._request("GET", raw_url)
                return response.text

        return content or ""

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any
    ) -> Any:  # returns requests.Response when available
        """
        Make HTTP request with retry logic.

        Args:
            method: HTTP method
            url: Request URL
            **kwargs: Additional arguments for requests

        Returns:
            Response object

        Raises:
            RuntimeError: If request fails after retries
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.session.request(method, url, **kwargs)

                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", self.RETRY_DELAY))
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue

                # Raise for HTTP errors
                response.raise_for_status()
                return response

            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(self.RETRY_DELAY * (attempt + 1))  # Exponential backoff
                    continue
                else:
                    raise RuntimeError(f"GitHub API request failed: {e}") from e

        raise RuntimeError("Unexpected error in _request")

    def test_token(self) -> bool:
        """
        Test if token is valid.

        Returns:
            True if token is valid
        """
        try:
            response = self._request("GET", f"{self.API_BASE}/user")
            return response.status_code == 200
        except Exception:
            return False

    def get_rate_limit(self) -> dict[str, Any]:
        """
        Get current rate limit status.

        Returns:
            Rate limit information
        """
        response = self._request("GET", f"{self.API_BASE}/rate_limit")
        return response.json()
