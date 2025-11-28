"""
Database and data migration framework.

Provides automatic version tracking and migration execution on startup.
"""

from src.migrations.runner import run_migrations, get_migration_status

__all__ = ["run_migrations", "get_migration_status"]
