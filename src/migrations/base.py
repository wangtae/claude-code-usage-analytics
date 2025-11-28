"""
Base migration class and utilities.

Each migration must inherit from Migration and implement up() and down() methods.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class MigrationResult:
    """Result of a migration execution."""
    success: bool
    message: str
    error: Optional[str] = None


class Migration(ABC):
    """
    Base class for all migrations.

    Each migration should have:
    - version: Semantic version string (e.g., "1.7.5")
    - name: Human-readable migration name
    - description: What this migration does

    Migrations are run in version order (sorted semantically).
    """

    # Override these in subclasses
    version: str = "0.0.0"
    name: str = "Base Migration"
    description: str = "Base migration class"

    @abstractmethod
    def up(self) -> MigrationResult:
        """
        Apply the migration.

        Returns:
            MigrationResult with success status and message
        """
        pass

    @abstractmethod
    def down(self) -> MigrationResult:
        """
        Rollback the migration.

        Returns:
            MigrationResult with success status and message
        """
        pass

    def check_required(self) -> bool:
        """
        Check if this migration needs to be run.

        Default implementation returns True (always run if version not recorded).
        Override for custom logic.

        Returns:
            True if migration should be run
        """
        return True

    def __repr__(self) -> str:
        return f"Migration({self.version}: {self.name})"


def parse_version(version_str: str) -> tuple[int, int, int]:
    """
    Parse version string to tuple for comparison.

    Args:
        version_str: Version like "1.7.5"

    Returns:
        Tuple of (major, minor, patch)
    """
    parts = version_str.split(".")
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return (major, minor, patch)


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two version strings.

    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2
    """
    t1 = parse_version(v1)
    t2 = parse_version(v2)

    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    else:
        return 0
